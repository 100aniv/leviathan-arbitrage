"""LEVIATHAN Shadow Mode — Real Data + Paper Execution (Multi-Strategy).

Runs the full pipeline with real market data and paper execution:
  1. WebSocket collectors receive real orderbook data (spot + futures)
  2. SignalGenerator evaluates cross-exchange arbitrage opportunities
  3. MultiStrategySignalProducer evaluates 6 additional strategies:
     - triangular, statistical_arb, latency_arb (spot)
     - spot_futures, funding_rate, futures_futures (futures)
  4. PaperExecutor simulates trade execution with power-law slippage
  5. All results recorded to TimescaleDB + Prometheus metrics
  6. Daily summary sent via Telegram

Shadow mode is the final validation before live trading.
"""
from __future__ import annotations

import asyncio
import collections
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx
import structlog

from src.core.models import Order, OrderSide, OrderType, Signal
from src.core.order_book import OrderBook
from src.core.rust_bridge import get_orderbook_class
from src.execution.paper import PaperExecutor, SlippageModel
from src.friction.fee_model import FeeModel
from src.strategies.base import TradeRequest, TradeLeg
from src.infra.metrics import (
    COLLECTOR_MESSAGES,
    DRAWDOWN_CURRENT,
    EXCHANGE_HEALTH_SCORE,
    PNL_TOTAL,
    SIGNAL_COUNT,
    SIGNAL_PROCESSING_TIME,
    SIGNALS_TOTAL,
    SPREAD_BPS,
    TRADES_TOTAL,
)
from prometheus_client import Counter as PromCounter

ROUTING_FALLBACK_TOTAL = PromCounter(
    "shadow_routing_fallback_total",
    "Number of times signal routing fell back to direct execution",
    ["reason"],
)
from src.core.real_signal_producer import RealDataSignalProducer
from src.core.triangular_scanner import TriangularScanner

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Power-law slippage model (gamma=0.5 per Blueprint)
# ---------------------------------------------------------------------------


class PowerLawSlippage(SlippageModel):
    """Power-law slippage: slippage = k * size^gamma.

    gamma=0.5 per Blueprint. Conservative estimate for shadow mode.
    Larger orders receive proportionally more slippage.
    """

    def __init__(self, k: float | None = None, gamma: float = 0.5) -> None:
        super().__init__(base_slippage_pct=Decimal("0.001"))
        self._k = k if k is not None else float(os.getenv("SLIPPAGE_K_DEFAULT", "5.0"))
        self._gamma = gamma

    def apply(
        self, base_price: Decimal, side: OrderSide, size: Decimal = Decimal("1")
    ) -> Decimal:
        """Return fill price with power-law adverse slippage applied.

        Formula: impact = k * size^gamma (gamma=0.5 per Blueprint)
        Larger orders receive proportionally more slippage.

        Buy  → price increases.
        Sell → price decreases.
        Random factor [0.5, 1.5] adds realism without determinism.
        """
        import random

        impact = Decimal(str(self._k)) * Decimal(
            str(float(size) ** self._gamma)
        )
        random_factor = Decimal(str(random.uniform(0.5, 1.5)))
        slippage = self.base_slippage_pct * impact * random_factor
        if side == OrderSide.BUY:
            return base_price * (Decimal("1") + slippage)
        return base_price * (Decimal("1") - slippage)


# ---------------------------------------------------------------------------
# Stats dataclass
# ---------------------------------------------------------------------------


@dataclass
class StrategyStats:
    """Per-strategy metrics."""

    signals: int = 0
    trades: int = 0
    wins: int = 0
    losses: int = 0
    pnl: float = 0.0


@dataclass
class ShadowStats:
    """Cumulative metrics tracked across the shadow mode session."""

    start_time: float  # time.monotonic()
    signals_detected: int = 0
    trades_executed: int = 0
    trades_won: int = 0
    trades_lost: int = 0
    total_pnl: float = 0.0
    peak_pnl: float = 0.0
    max_drawdown: float = 0.0
    last_daily_summary: datetime | None = None
    # Per-strategy breakdown
    by_strategy: dict[str, StrategyStats] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ShadowMode orchestrator
# ---------------------------------------------------------------------------


class ShadowMode:
    """Shadow Mode orchestrator.

    Lifecycle: init → start() → [runs continuously] → stop()

    Attributes:
        _signal_generator: SignalGenerator instance
        _paper_executor: PaperExecutor with power-law slippage (gamma=0.5)
        _collector_manager: CollectorManager for real WS data
        _market_recorder: MarketRecorder for TimescaleDB persistence
        _telegram: TelegramAlerter for notifications
        _running: bool flag
        _stats: ShadowStats dataclass tracking cumulative metrics
    """

    # Strategy label used for all Prometheus metric labels
    STRATEGY_ID = "shadow_arb_v1"

    def __init__(
        self,
        signal_generator: Any,
        paper_executor: PaperExecutor | None = None,
        collector_manager: Any | None = None,
        market_recorder: Any | None = None,
        telegram: Any | None = None,
        symbols: list[str] | None = None,
        exchanges: list[str] | None = None,
        multi_signal_producer: Any | None = None,
        funding_rate_collector: Any | None = None,
        strategy_manager: Any | None = None,
    ) -> None:
        """Initialise the shadow mode orchestrator.

        Args:
            signal_generator:  Configured SignalGenerator instance.
            paper_executor:    PaperExecutor; if None, one with PowerLawSlippage
                               (gamma=0.5) is created automatically.
            collector_manager: CollectorManager for WebSocket data. If None,
                               one is created using symbols/exchanges args.
            market_recorder:   Optional MarketRecorder for TimescaleDB writes.
            telegram:          Optional TelegramAlerter for notifications.
            symbols:           Trading pairs (default ["BTC/USDT"]).
            exchanges:         Exchange IDs (default CollectorManager defaults).
            multi_signal_producer: Optional MultiStrategySignalProducer for
                               additional strategy signals (triangular, stat_arb,
                               latency_arb, spot_futures, funding_rate, futures_futures).
            funding_rate_collector: Optional FundingRateCollector. If provided,
                               replaces the inline funding-rate HTTP loop. If None,
                               falls back to the built-in Binance+Bybit polling.
            strategy_manager:  Optional StrategyManager. If provided, signals
                               are also routed to registered strategies and their
                               TradeRequests are paper-executed (N-leg support).
        """
        self._signal_generator = signal_generator
        self._multi_signal_producer = multi_signal_producer
        self._funding_rate_collector = funding_rate_collector
        self._strategy_manager = strategy_manager

        # Shadow mode: PaperExecutor with realistic slippage, zero flat fee.
        # k=1.0 matches CEXOrderbookSlippage's default (~10bps/side = 20bps round-trip).
        # k=5.0 was 100bps round-trip (absurd), k=0 gives fake 100% WR.
        # FeeModel in _execute_shadow_trade handles per-exchange fees + network cost.
        self._paper_executor: PaperExecutor = paper_executor or PaperExecutor(
            slippage_model=PowerLawSlippage(k=1.0, gamma=0.5),
            fee_rate=Decimal("0"),
        )

        self._fee_model = FeeModel()
        self._market_recorder = market_recorder
        self._telegram = telegram
        self._symbols = symbols or ["BTC/USDT"]
        self._exchanges = exchanges

        # Orderbook store: exchange_id -> OrderBook (keyed per symbol internally)
        # Structure: symbol -> exchange_id -> OrderBook
        self._books: dict[str, dict[str, Any]] = {}

        # Futures orderbook store: symbol -> exchange_id -> OrderBook
        self._futures_books: dict[str, dict[str, Any]] = {}

        self._running = False
        self._stats = ShadowStats(start_time=time.monotonic())

        # Background tasks
        self._daily_task: asyncio.Task[None] | None = None
        self._funding_rate_task: asyncio.Task[None] | None = None

        # Resolve orderbook class (Rust or Python)
        self._orderbook_cls = get_orderbook_class()

        # KRW/USDT dynamic rate (fetched from Upbit+Bithumb every 30s)
        _raw_krw_rate = float(os.getenv("KRW_USDT_RATE", "1380"))
        if _raw_krw_rate <= 0:
            logger.warning(
                "shadow_mode.invalid_krw_rate", raw=_raw_krw_rate, fallback=1380.0
            )
            _raw_krw_rate = 1380.0
        self._krw_rate: float = _raw_krw_rate
        self._krw_rate_task: asyncio.Task[None] | None = None
        self._krw_rate_updated_at: float = time.monotonic()
        self._sanity_reject_count: int = 0
        self._http_client: httpx.AsyncClient = httpx.AsyncClient(timeout=5.0)

        # Statistical arb state: rolling z-score window per symbol
        self._spread_history: dict[str, collections.deque] = {}
        self._stat_arb_window: int = 100

        # Latency tracking: exchange_id -> last update timestamp
        self._exchange_update_times: dict[str, float] = {}

        # Funding rates cache: exchange_id -> symbol -> rate
        self._funding_rates: dict[str, dict[str, float]] = {}

        # Futures exchanges for identification
        self._futures_exchanges: set[str] = {"binance_futures"}

        # RealDataSignalProducer: replaces inline _evaluate_* methods
        self._real_signal_producer: RealDataSignalProducer | None = None
        if self._multi_signal_producer is not None:
            self._real_signal_producer = RealDataSignalProducer(
                multi_signal_producer=self._multi_signal_producer,
                triangular_scanner=TriangularScanner(),
                futures_exchanges=self._futures_exchanges,
            )

        # Build collector manager if not supplied
        if collector_manager is not None:
            self._collector_manager = collector_manager
        else:
            from src.collectors.manager import CollectorManager

            self._collector_manager = CollectorManager(
                symbols=self._symbols,
                exchanges=self._exchanges,
                on_orderbook=self._on_orderbook,
            )

        logger.info(
            "shadow_mode.init",
            symbols=self._symbols,
            exchanges=self._exchanges,
            multi_strategy=multi_signal_producer is not None,
            orderbook_backend=self._orderbook_cls.__name__,
        )

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    async def start(self) -> None:
        """Start shadow mode: collectors, daily summary loop, Telegram alert."""
        if self._running:
            logger.warning("shadow_mode.already_running")
            return

        self._running = True
        self._stats = ShadowStats(start_time=time.monotonic())

        logger.info("shadow_mode.starting")

        # Send Telegram "started" notification (non-blocking; never crashes)
        if self._telegram is not None:
            try:
                await self._telegram.send_alert(
                    "Shadow Mode started. Real data + paper execution active.",
                    level="INFO",
                )
            except Exception as exc:
                logger.warning("shadow_mode.telegram_start_alert_failed", error=str(exc))

        # Wire up the orderbook callback if manager was pre-supplied
        # (If we created it ourselves it already has the callback.)
        if hasattr(self._collector_manager, "_on_orderbook"):
            if self._collector_manager._on_orderbook is None:
                self._collector_manager._on_orderbook = self._on_orderbook

        # Start collectors
        try:
            await self._collector_manager.start()
            logger.info("shadow_mode.collectors_started")
        except Exception as exc:
            logger.error("shadow_mode.collectors_start_failed", error=str(exc))
            self._running = False
            raise

        # Start KRW/USDT rate updater (fetches from Upbit every 60s)
        self._krw_rate_task = asyncio.create_task(
            self._krw_rate_loop(), name="shadow_krw_rate"
        )

        # Start funding rate polling loop (for spot_futures + funding_rate strategies)
        if self._multi_signal_producer is not None:
            self._funding_rate_task = asyncio.create_task(
                self._funding_rate_loop(), name="shadow_funding_rate"
            )

        # Start daily summary background task
        self._daily_task = asyncio.create_task(
            self._daily_summary_loop(), name="shadow_daily_summary"
        )

        logger.info("shadow_mode.started", multi_strategy=self._multi_signal_producer is not None)

    async def stop(self) -> None:
        """Stop shadow mode: collectors, send final summary, clean up."""
        if not self._running:
            logger.warning("shadow_mode.not_running")
            return

        self._running = False
        logger.info("shadow_mode.stopping")

        # Cancel KRW rate task
        if self._krw_rate_task is not None and not self._krw_rate_task.done():
            self._krw_rate_task.cancel()
            try:
                await self._krw_rate_task
            except asyncio.CancelledError:
                pass
            self._krw_rate_task = None

        # Cancel funding rate task
        if self._funding_rate_task is not None and not self._funding_rate_task.done():
            self._funding_rate_task.cancel()
            try:
                await self._funding_rate_task
            except asyncio.CancelledError:
                pass
            self._funding_rate_task = None

        # Cancel daily summary task
        if self._daily_task is not None and not self._daily_task.done():
            self._daily_task.cancel()
            try:
                await self._daily_task
            except asyncio.CancelledError:
                pass
            self._daily_task = None

        # Close persistent HTTP client
        try:
            await self._http_client.aclose()
        except Exception as exc:
            logger.warning("shadow_mode.http_client_close_failed", error=str(exc))

        # Stop collectors
        try:
            await self._collector_manager.stop()
        except Exception as exc:
            logger.error("shadow_mode.collectors_stop_failed", error=str(exc))

        # Send final summary
        if self._telegram is not None:
            try:
                await self._send_summary()
            except Exception as exc:
                logger.warning("shadow_mode.final_summary_failed", error=str(exc))

        logger.info(
            "shadow_mode.stopped",
            uptime_s=time.monotonic() - self._stats.start_time,
            signals=self._stats.signals_detected,
            trades=self._stats.trades_executed,
            total_pnl=self._stats.total_pnl,
            max_drawdown=self._stats.max_drawdown,
        )

    # -----------------------------------------------------------------------
    # Orderbook callback
    # -----------------------------------------------------------------------

    async def _on_orderbook(
        self,
        exchange_id: str,
        symbol: str,
        bids: list[list[Any]],
        asks: list[list[Any]],
    ) -> None:
        """Handle a new orderbook snapshot from a collector.

        Creates/updates the internal OrderBook for (exchange_id, symbol),
        feeds it to SignalGenerator, and executes any emitted signal.

        Never raises — all exceptions are caught and logged.
        """
        if not self._running:
            return

        # Normalize KRW prices to USDT for cross-exchange comparison
        # Korean exchanges (upbit, bithumb, coinone) quote in KRW
        if "/KRW" in symbol and self._krw_rate > 0:
            symbol = symbol.replace("/KRW", "/USDT")
            bids = [[str(float(b[0]) / self._krw_rate), str(b[1])] for b in bids]
            asks = [[str(float(a[0]) / self._krw_rate), str(a[1])] for a in asks]
        elif "/KRW" in symbol:
            logger.warning(
                "shadow_mode.krw_rate_zero_skip",
                exchange=exchange_id,
                symbol=symbol,
            )
            return

        try:
            # Normalise to list-of-tuples
            bid_tuples = [(str(b[0]), str(b[1])) for b in bids]
            ask_tuples = [(str(a[0]), str(a[1])) for a in asks]

            # Bithumb sends incremental deltas, not full snapshots.
            # For delta exchanges, accumulate updates on existing books.
            DELTA_EXCHANGES = {"bithumb"}

            if symbol not in self._books:
                self._books[symbol] = {}

            existing = self._books[symbol].get(exchange_id)
            if existing is not None and exchange_id in DELTA_EXCHANGES:
                existing.apply_delta(bid_tuples, ask_tuples)
                book = existing
            else:
                book = self._orderbook_cls(symbol=symbol, exchange=exchange_id)
                book.apply_snapshot(bid_tuples, ask_tuples)
                self._books[symbol][exchange_id] = book

            # Record to TimescaleDB (best_bid / best_ask; skip if missing)
            if self._market_recorder is not None:
                try:
                    best_bid = book.best_bid()
                    best_ask = book.best_ask()
                    if best_bid is not None and best_ask is not None:
                        self._market_recorder.record_orderbook(
                            exchange=exchange_id,
                            symbol=symbol,
                            bids=bids,
                            asks=asks,
                            best_bid=Decimal(str(best_bid)),
                            best_ask=Decimal(str(best_ask)),
                        )
                except Exception as exc:
                    logger.warning(
                        "shadow_mode.record_orderbook_failed",
                        exchange=exchange_id,
                        symbol=symbol,
                        error=str(exc),
                    )

            # Prometheus: collector message counter + health score + spread
            try:
                COLLECTOR_MESSAGES.labels(exchange=exchange_id).inc()
                EXCHANGE_HEALTH_SCORE.labels(exchange=exchange_id).set(1.0)
                # Spread metrics (float math — no Decimal needed for gauge)
                best_bid_val = book.best_bid()
                best_ask_val = book.best_ask()
                if best_bid_val is not None and best_ask_val is not None:
                    fb = float(best_bid_val)
                    fa = float(best_ask_val)
                    mid = (fb + fa) * 0.5
                    if mid > 0:
                        spread_bps = (fa - fb) / mid * 10000
                        SPREAD_BPS.labels(exchange_pair=exchange_id).observe(spread_bps)
            except Exception:
                pass

            # Feed to SignalGenerator
            t0 = time.monotonic()
            try:
                signal: Signal | None = await self._signal_generator.on_orderbook_update(
                    book,
                    self._books.get(symbol, {}),
                )
            except Exception as exc:
                logger.warning(
                    "shadow_mode.signal_generator_error",
                    exchange=exchange_id,
                    symbol=symbol,
                    error=str(exc),
                )
                signal = None

            elapsed = time.monotonic() - t0
            try:
                SIGNAL_PROCESSING_TIME.labels(strategy=self.STRATEGY_ID).observe(elapsed)
                if signal is not None:
                    SIGNALS_TOTAL.labels(strategy=self.STRATEGY_ID, decision="emit").inc()
                    SIGNAL_COUNT.labels(
                        exchange_pair=f"{signal.buy_exchange}-{signal.sell_exchange}"
                    ).inc()
            except Exception:
                pass

            if signal is not None:
                # Telegram signal notification (fire-and-forget)
                if self._telegram is not None:
                    try:
                        await self._telegram.send_signal_found(signal)
                    except Exception as exc:
                        logger.warning(
                            "shadow_mode.telegram_signal_notify_failed", error=str(exc)
                        )

                # Route through Strategy objects when StrategyManager is available;
                # otherwise fall back to direct 2-leg execution (backward compat).
                if self._strategy_manager is not None:
                    logger.debug(
                        "shadow_mode.routing_via_strategy_manager",
                        signal_strategy=signal.strategy_id,
                        symbol=signal.symbol,
                    )
                    await self._route_signal_to_strategies(signal)
                else:
                    await self._execute_shadow_trade(signal)

            # --- Multi-strategy evaluation ---
            if self._multi_signal_producer is not None:
                try:
                    self._multi_signal_producer.on_orderbook(exchange_id, symbol, book)
                except Exception as exc:
                    logger.warning(
                        "shadow_mode.multi_signal_on_orderbook_error",
                        exchange=exchange_id, symbol=symbol, error=str(exc),
                    )

                # Store futures books separately for spot_futures/futures_futures
                if exchange_id in self._futures_exchanges:
                    if symbol not in self._futures_books:
                        self._futures_books[symbol] = {}
                    self._futures_books[symbol][exchange_id] = book

                # Track exchange update timestamps for latency arb
                self._exchange_update_times[exchange_id] = time.monotonic()

                # Evaluate multi-strategy signals
                await self._evaluate_multi_strategies(exchange_id, symbol, book)

        except Exception as exc:
            logger.error(
                "shadow_mode.on_orderbook_unhandled_error",
                exchange=exchange_id,
                symbol=symbol,
                error=str(exc),
                exc_info=True,
            )

    # -----------------------------------------------------------------------
    # Multi-strategy evaluation
    # -----------------------------------------------------------------------

    async def _safe_eval(self, coro: Any, name: str) -> None:
        """Wrap a strategy evaluation coroutine with exception handling."""
        try:
            await coro
        except Exception as exc:
            logger.warning(f"shadow_mode.{name}_eval_error", error=str(exc))

    async def _evaluate_multi_strategies(
        self, exchange_id: str, symbol: str, book: Any
    ) -> None:
        """Delegate all multi-strategy evaluation to RealDataSignalProducer."""
        if self._real_signal_producer is None:
            return
        signals = await self._real_signal_producer.on_orderbook_update(
            exchange_id, symbol, book,
            self._books, self._futures_books,
        )
        for signal in signals:
            if self._strategy_manager is not None:
                await self._route_signal_to_strategies(signal)
            else:
                await self._execute_shadow_trade(signal)

    # NOTE: _evaluate_triangular, _evaluate_statistical_arb, _evaluate_latency_arb,
    # _evaluate_spot_futures, _evaluate_futures_futures moved to RealDataSignalProducer

    # -----------------------------------------------------------------------
    # Funding rate polling loop
    # -----------------------------------------------------------------------

    async def _funding_rate_loop(self) -> None:
        """Poll funding rates from exchanges every 60 seconds.

        Results are stored in self._funding_rates. Funding rate arb signals
        are generated via RealDataSignalProducer.on_funding_rates_updated().
        Never raises — exceptions are caught and logged.
        """
        try:
            while self._running:
                rates_by_exchange: dict[str, dict[str, float]] = {}

                if self._funding_rate_collector is not None:
                    # Delegate to injected FundingRateCollector
                    try:
                        fetched = await self._funding_rate_collector.poll_once()
                        for ex_id, sym_map in fetched.items():
                            for sym, entry in sym_map.items():
                                rates_by_exchange.setdefault(ex_id, {})[sym] = entry.rate
                    except Exception as exc:
                        logger.debug("shadow_mode.funding_collector_failed", error=str(exc))
                else:
                    # Inline fallback: Binance Futures funding rates
                    try:
                        resp = await self._http_client.get(
                            "https://fapi.binance.com/fapi/v1/premiumIndex",
                            params={"symbol": "BTCUSDT"},
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            rate = float(data.get("lastFundingRate", 0))
                            rates_by_exchange.setdefault("binance_futures", {})["BTC/USDT"] = rate
                    except Exception as exc:
                        logger.debug("shadow_mode.funding_binance_failed", error=str(exc))

                    # Inline fallback: Bybit funding rates
                    try:
                        resp = await self._http_client.get(
                            "https://api.bybit.com/v5/market/tickers",
                            params={"category": "linear", "symbol": "BTCUSDT"},
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            result_list = data.get("result", {}).get("list", [])
                            if result_list:
                                rate = float(result_list[0].get("fundingRate", 0))
                                rates_by_exchange.setdefault("bybit", {})["BTC/USDT"] = rate
                    except Exception as exc:
                        logger.debug("shadow_mode.funding_bybit_failed", error=str(exc))

                # Update cached rates
                self._funding_rates.update(rates_by_exchange)

                # Generate funding rate arbitrage signals if differential exists
                if self._real_signal_producer is not None:
                    try:
                        signals = await self._real_signal_producer.on_funding_rates_updated(
                            rates_by_exchange, self._books,
                        )
                        for signal in signals:
                            if self._strategy_manager is not None:
                                await self._route_signal_to_strategies(signal)
                            else:
                                await self._execute_shadow_trade(signal)
                    except Exception as exc:
                        logger.warning("shadow_mode.funding_rate_arb_error", error=str(exc))

                if rates_by_exchange:
                    logger.debug(
                        "shadow_mode.funding_rates_updated",
                        exchanges=list(rates_by_exchange.keys()),
                    )

                await asyncio.sleep(60.0)
        except asyncio.CancelledError:
            pass

    # NOTE: _evaluate_funding_rate_arb moved to RealDataSignalProducer

    # -----------------------------------------------------------------------
    # Shadow trade execution
    # -----------------------------------------------------------------------

    async def _execute_shadow_trade(self, signal: Signal) -> None:
        """Paper-execute a signal: buy + sell orders with power-law slippage.

        Computes net PnL, updates stats, records to TimescaleDB + Prometheus.
        Never raises — exceptions are caught and logged.
        """
        # Defense-in-depth: reject signals where buy >= sell (guaranteed loss)
        # Pairs-trading strategies (stat_arb z<0) require position holding,
        # which shadow mode doesn't support — skip them.
        if signal.buy_price >= signal.sell_price:
            logger.debug(
                "shadow_mode.skip_negative_spread",
                strategy=signal.strategy_id,
                symbol=signal.symbol,
                buy_price=str(signal.buy_price),
                sell_price=str(signal.sell_price),
            )
            return

        t0 = time.monotonic()
        self._stats.signals_detected += 1

        try:
            buy_order = Order(
                order_id=str(uuid.uuid4()),
                exchange_id=signal.buy_exchange,
                symbol=signal.symbol,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                price=signal.buy_price,
                amount=signal.volume,
            )
            sell_order = Order(
                order_id=str(uuid.uuid4()),
                exchange_id=signal.sell_exchange,
                symbol=signal.symbol,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                price=signal.sell_price,
                amount=signal.volume,
            )

            buy_trade = await self._paper_executor.execute(buy_order)
            sell_trade = await self._paper_executor.execute(sell_order)

        except Exception as exc:
            logger.error(
                "shadow_mode.trade_execution_failed",
                strategy=signal.strategy_id,
                symbol=signal.symbol,
                error=str(exc),
            )
            return

        self._stats.trades_executed += 1

        # Recalculate fees using per-exchange FeeModel (replaces flat 0.10%)
        buy_ex = signal.buy_exchange.removeprefix("paper_").removeprefix("sandbox_")
        sell_ex = signal.sell_exchange.removeprefix("paper_").removeprefix("sandbox_")
        buy_notional = buy_trade.price * buy_trade.amount
        sell_notional = sell_trade.price * sell_trade.amount

        # Derive transfer coin from symbol (e.g., "BTC/USDT" → "BTC")
        # For cross-exchange arb, the base asset is what gets transferred
        transfer_coin = signal.symbol.split("/")[0] if "/" in signal.symbol else "XRP"

        try:
            real_buy_fee = self._fee_model.taker_fee(buy_ex, buy_notional)
            real_sell_fee = self._fee_model.taker_fee(sell_ex, sell_notional)
            network_cost = self._fee_model.network_cost(buy_ex, sell_ex, transfer_coin)
        except ValueError:
            # Unknown exchange: conservative 0.25% taker (highest known rate)
            real_buy_fee = buy_notional * Decimal("0.0025")
            real_sell_fee = sell_notional * Decimal("0.0025")
            network_cost = Decimal("1.00")

        # Net PnL = sell proceeds - buy cost - real fees - network cost
        net_pnl = (
            sell_notional - real_sell_fee
            - buy_notional - real_buy_fee
            - network_cost
        )
        net_pnl_float = float(net_pnl)

        # Per-strategy tracking
        sid = signal.strategy_id or self.STRATEGY_ID
        if sid not in self._stats.by_strategy:
            self._stats.by_strategy[sid] = StrategyStats()
        ss = self._stats.by_strategy[sid]
        ss.signals += 1
        ss.trades += 1
        ss.pnl += net_pnl_float

        if net_pnl_float > 0:
            self._stats.trades_won += 1
            ss.wins += 1
            result_label = "win"
        else:
            self._stats.trades_lost += 1
            ss.losses += 1
            result_label = "loss"

        self._stats.total_pnl += net_pnl_float
        self._compute_drawdown()

        # Record to TimescaleDB
        if self._market_recorder is not None:
            try:
                gross_spread = signal.sell_price - signal.buy_price
                mid_price = (signal.buy_price + signal.sell_price) / 2
                gross_spread_bps = (
                    gross_spread / mid_price * Decimal("10000") if mid_price > 0 else None
                )
                fee_total = real_buy_fee + real_sell_fee + network_cost
                slippage_buy = abs(buy_trade.price - signal.buy_price)
                slippage_sell = abs(sell_trade.price - signal.sell_price)
                slippage_total = slippage_buy + slippage_sell

                self._market_recorder.record_execution(
                    strategy_id=signal.strategy_id,
                    buy_exchange=signal.buy_exchange,
                    sell_exchange=signal.sell_exchange,
                    symbol=signal.symbol,
                    buy_price=buy_trade.price,
                    sell_price=sell_trade.price,
                    size=buy_trade.amount,
                    signal_id=None,
                    gross_spread_bps=gross_spread_bps,
                    fee_total=fee_total,
                    slippage_total=slippage_total,
                    net_pnl=net_pnl,
                    status="filled",
                    metadata={
                        "buy_trade_id": buy_trade.trade_id,
                        "sell_trade_id": sell_trade.trade_id,
                        "signal_spread_pct": str(signal.spread_pct),
                        "signal_confidence": signal.confidence,
                    },
                )
            except Exception as exc:
                logger.warning(
                    "shadow_mode.record_execution_failed",
                    symbol=signal.symbol,
                    error=str(exc),
                )

        # Prometheus metrics (use signal's strategy_id for per-strategy tracking)
        strategy_label = signal.strategy_id or self.STRATEGY_ID
        exchange_pair = f"{signal.buy_exchange}-{signal.sell_exchange}"
        try:
            TRADES_TOTAL.labels(
                strategy=strategy_label,
                exchange_pair=exchange_pair,
                result=result_label,
            ).inc()
            PNL_TOTAL.labels(strategy=strategy_label).set(self._stats.total_pnl)
            DRAWDOWN_CURRENT.labels(strategy=strategy_label).set(
                self._stats.max_drawdown
            )
        except Exception:
            pass

        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "shadow_mode.trade_executed",
            symbol=signal.symbol,
            buy_exchange=signal.buy_exchange,
            sell_exchange=signal.sell_exchange,
            buy_price=str(buy_trade.price),
            sell_price=str(sell_trade.price),
            net_pnl=f"{net_pnl_float:+.4f}",
            result=result_label,
            total_pnl=f"{self._stats.total_pnl:+.4f}",
            max_drawdown=f"{self._stats.max_drawdown:.4f}",
            elapsed_ms=f"{elapsed_ms:.2f}",
        )

    # -----------------------------------------------------------------------
    # Strategy routing (StrategyManager integration)
    # -----------------------------------------------------------------------

    async def _route_signal_to_strategies(self, signal: Signal) -> None:
        """Route signal to matching strategies via StrategyManager.route_signal().

        Delegates type-based matching to StrategyManager._should_route().
        Falls back to _execute_shadow_trade() on routing failure.

        Empty list from route_signal() = normal filtering, NO fallback.
        Exception from route_signal() = routing mechanism failure, fallback triggered.
        """
        if self._strategy_manager is None:
            return

        try:
            trade_requests = await self._strategy_manager.route_signal(signal)
            for request in trade_requests:
                await self._execute_shadow_trade_request(request)

            logger.debug(
                "shadow_mode.signal_routed",
                signal_strategy=signal.strategy_id,
                symbol=signal.symbol,
                requests_generated=len(trade_requests),
            )
        except Exception as exc:
            ROUTING_FALLBACK_TOTAL.labels(reason="routing_exception").inc()
            logger.warning(
                "shadow_mode.strategy_routing_failed",
                signal_strategy=signal.strategy_id,
                error=str(exc),
            )
            # Fallback: prevent signal loss on routing mechanism failure
            await self._execute_shadow_trade(signal)

    async def _execute_shadow_trade_request(self, trade_request: TradeRequest) -> None:
        """Paper-execute an N-leg TradeRequest from a strategy.

        Iterates over trade_request.legs, creates an Order for each leg,
        paper-executes it, and computes net PnL across all legs using FeeModel.
        Updates stats with trade_request.strategy_id. Never raises.
        """
        t0 = time.monotonic()
        self._stats.signals_detected += 1
        sid = trade_request.strategy_id or self.STRATEGY_ID

        try:
            trades = []
            for leg in trade_request.legs:
                order = Order(
                    order_id=str(uuid.uuid4()),
                    exchange_id=leg.exchange_id,
                    symbol=leg.symbol,
                    side=leg.side,
                    order_type=leg.order_type,
                    price=leg.price or Decimal("0"),
                    amount=leg.size,
                )
                trade = await self._paper_executor.execute(order)
                trades.append((leg, trade))
        except Exception as exc:
            logger.error(
                "shadow_mode.trade_request_execution_failed",
                strategy_id=sid,
                error=str(exc),
            )
            return

        self._stats.trades_executed += 1

        # Compute net PnL across all legs
        net_pnl = Decimal("0")
        for leg, trade in trades:
            notional = trade.price * trade.amount
            ex = leg.exchange_id.removeprefix("paper_").removeprefix("sandbox_")
            try:
                fee = self._fee_model.taker_fee(ex, notional)
            except ValueError:
                fee = notional * Decimal("0.0025")

            if leg.side == OrderSide.SELL:
                net_pnl += notional - fee
            else:
                net_pnl -= notional + fee

        # Network cost between first buy and first sell exchange
        buy_exs = [l.exchange_id.removeprefix("paper_").removeprefix("sandbox_")
                   for l, _ in trades if l.side == OrderSide.BUY]
        sell_exs = [l.exchange_id.removeprefix("paper_").removeprefix("sandbox_")
                    for l, _ in trades if l.side == OrderSide.SELL]
        if buy_exs and sell_exs:
            first_symbol = trade_request.legs[0].symbol
            transfer_coin = first_symbol.split("/")[0] if "/" in first_symbol else "XRP"
            try:
                network_cost = self._fee_model.network_cost(buy_exs[0], sell_exs[0], transfer_coin)
            except ValueError:
                network_cost = Decimal("1.00")
            net_pnl -= network_cost

        net_pnl_float = float(net_pnl)

        # Per-strategy tracking
        if sid not in self._stats.by_strategy:
            self._stats.by_strategy[sid] = StrategyStats()
        ss = self._stats.by_strategy[sid]
        ss.signals += 1
        ss.trades += 1
        ss.pnl += net_pnl_float

        if net_pnl_float > 0:
            self._stats.trades_won += 1
            ss.wins += 1
            result_label = "win"
        else:
            self._stats.trades_lost += 1
            ss.losses += 1
            result_label = "loss"

        self._stats.total_pnl += net_pnl_float
        self._compute_drawdown()

        # Prometheus metrics
        strategy_label = sid
        try:
            TRADES_TOTAL.labels(
                strategy=strategy_label,
                exchange_pair=f"{buy_exs[0]}-{sell_exs[0]}" if buy_exs and sell_exs else "unknown",
                result=result_label,
            ).inc()
            PNL_TOTAL.labels(strategy=strategy_label).set(self._stats.total_pnl)
            DRAWDOWN_CURRENT.labels(strategy=strategy_label).set(
                self._stats.max_drawdown
            )
        except Exception:
            pass

        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "shadow_mode.trade_request_executed",
            strategy_id=sid,
            legs=len(trade_request.legs),
            net_pnl=f"{net_pnl_float:+.4f}",
            result=result_label,
            total_pnl=f"{self._stats.total_pnl:+.4f}",
            elapsed_ms=f"{elapsed_ms:.2f}",
        )

    # -----------------------------------------------------------------------
    # KRW/USDT dynamic rate loop
    # -----------------------------------------------------------------------

    async def _krw_rate_loop(self) -> None:
        """Fetch KRW/USDT rate from Upbit + Bithumb every 30 seconds.

        Uses average of valid sources. Falls back to env var
        KRW_USDT_RATE if both APIs are unreachable. Detects staleness
        after 120 seconds and rejects >10% sanity-bound changes.
        After 5 consecutive rejections, forces acceptance to escape lockout.
        Never raises — exceptions are caught and logged.
        """
        try:
            while self._running:
                rates: list[float] = []

                # Upbit source
                try:
                    resp = await self._http_client.get(
                        "https://api.upbit.com/v1/ticker",
                        params={"markets": "KRW-USDT"},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        if data and len(data) > 0:
                            price = float(data[0].get("trade_price", 0))
                            if price > 0:
                                rates.append(price)
                except Exception as exc:
                    logger.debug("shadow_mode.krw_upbit_failed", error=str(exc))

                # Bithumb source
                try:
                    resp = await self._http_client.get(
                        "https://api.bithumb.com/public/ticker/USDT_KRW",
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        closing = float(
                            data.get("data", {}).get("closing_price", 0)
                        )
                        if closing > 0:
                            rates.append(closing)
                except Exception as exc:
                    logger.debug("shadow_mode.krw_bithumb_failed", error=str(exc))

                if rates:
                    new_rate = sum(rates) / len(rates)  # average of valid sources
                    # Sanity bound: reject >10% change from current rate
                    if (
                        self._krw_rate > 0
                        and abs(new_rate - self._krw_rate) / self._krw_rate > 0.10
                    ):
                        self._sanity_reject_count += 1
                        logger.warning(
                            "shadow_mode.krw_rate_sanity_rejected",
                            new_rate=new_rate,
                            current_rate=self._krw_rate,
                            reject_count=self._sanity_reject_count,
                        )
                        # Lockout escape: force-accept after 5 consecutive rejections
                        if self._sanity_reject_count >= 5:
                            logger.warning(
                                "shadow_mode.krw_rate_lockout_override",
                                new_rate=new_rate,
                                reject_count=self._sanity_reject_count,
                            )
                            self._krw_rate = new_rate
                            self._krw_rate_updated_at = time.monotonic()
                            self._sanity_reject_count = 0
                    else:
                        self._sanity_reject_count = 0
                        old_rate = self._krw_rate
                        self._krw_rate = new_rate
                        self._krw_rate_updated_at = time.monotonic()
                        if abs(old_rate - new_rate) > 1:
                            logger.info(
                                "shadow_mode.krw_rate_updated",
                                old_rate=old_rate,
                                new_rate=new_rate,
                                sources=len(rates),
                            )

                # Staleness check
                elapsed = time.monotonic() - self._krw_rate_updated_at
                if elapsed > 120:
                    logger.warning(
                        "shadow_mode.krw_rate_stale",
                        seconds_since_update=elapsed,
                    )

                await asyncio.sleep(30.0)
        except asyncio.CancelledError:
            pass

    # -----------------------------------------------------------------------
    # Daily summary loop
    # -----------------------------------------------------------------------

    async def _daily_summary_loop(self) -> None:
        """Send a Telegram daily summary every 24 hours.

        Runs as a background task until shadow mode stops.
        Never raises — exceptions are caught and logged.
        """
        DAILY_INTERVAL_S = 86_400  # 24 hours

        while self._running:
            try:
                await asyncio.sleep(DAILY_INTERVAL_S)
                if not self._running:
                    break
                await self._send_summary()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(
                    "shadow_mode.daily_summary_loop_error", error=str(exc), exc_info=True
                )

    async def _send_summary(self) -> None:
        """Compute and dispatch the daily summary via Telegram + Prometheus."""
        now = datetime.now(tz=timezone.utc)
        stats = self._stats

        total_trades = stats.trades_executed
        win_rate = (
            stats.trades_won / total_trades if total_trades > 0 else 0.0
        )

        # Build per-strategy breakdown
        strategy_breakdown: list[dict[str, Any]] = []
        for s_id, ss in sorted(stats.by_strategy.items()):
            s_wr = ss.wins / ss.trades if ss.trades > 0 else 0.0
            strategy_breakdown.append({
                "strategy_id": s_id,
                "trades": ss.trades,
                "wins": ss.wins,
                "losses": ss.losses,
                "win_rate": s_wr,
                "pnl": ss.pnl,
            })

        summary_data: dict[str, Any] = {
            "date": now.strftime("%Y-%m-%d"),
            "strategy": self.STRATEGY_ID,
            "total_pnl": stats.total_pnl,
            "trades": total_trades,
            "win_rate": win_rate,
            "max_drawdown": stats.max_drawdown,
            "by_strategy": strategy_breakdown,
        }

        # Update Prometheus gauges (overall + per-strategy)
        try:
            PNL_TOTAL.labels(strategy=self.STRATEGY_ID).set(stats.total_pnl)
            DRAWDOWN_CURRENT.labels(strategy=self.STRATEGY_ID).set(stats.max_drawdown)
            for s_id, ss in stats.by_strategy.items():
                PNL_TOTAL.labels(strategy=s_id).set(ss.pnl)
        except Exception:
            pass

        if self._telegram is not None:
            try:
                await self._telegram.send_daily_summary(summary_data)

                # Send per-strategy breakdown as separate message
                if strategy_breakdown:
                    lines = ["Strategy Breakdown:"]
                    for sb in strategy_breakdown:
                        lines.append(
                            f"  {sb['strategy_id']}: "
                            f"{sb['trades']}T / "
                            f"{sb['win_rate']:.0%} WR / "
                            f"${sb['pnl']:+.4f}"
                        )
                    await self._telegram.send_alert("\n".join(lines), level="INFO")

                stats.last_daily_summary = now
                logger.info(
                    "shadow_mode.daily_summary_sent",
                    date=summary_data["date"],
                    total_pnl=stats.total_pnl,
                    trades=total_trades,
                    win_rate=win_rate,
                    strategies=len(strategy_breakdown),
                )
            except Exception as exc:
                logger.error(
                    "shadow_mode.daily_summary_send_failed", error=str(exc)
                )

    # -----------------------------------------------------------------------
    # Drawdown tracking
    # -----------------------------------------------------------------------

    def _compute_drawdown(self) -> None:
        """Update peak_pnl and max_drawdown from current total_pnl (absolute USD)."""
        pnl = self._stats.total_pnl
        if pnl > self._stats.peak_pnl:
            self._stats.peak_pnl = pnl

        # Absolute drawdown in USD (not fraction — avoids blowup when peak is tiny)
        drawdown = self._stats.peak_pnl - pnl

        if drawdown > self._stats.max_drawdown:
            self._stats.max_drawdown = drawdown
