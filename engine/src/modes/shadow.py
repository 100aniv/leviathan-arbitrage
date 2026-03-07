"""LEVIATHAN Shadow Mode — Real Data + Paper Execution.

Runs the full pipeline with real market data and paper execution:
  1. WebSocket collectors receive real orderbook data
  2. SignalGenerator evaluates cross-exchange arbitrage opportunities
  3. PaperExecutor simulates trade execution with power-law slippage
  4. All results recorded to TimescaleDB + Prometheus metrics
  5. Daily summary sent via Telegram

Shadow mode is the final validation before live trading.
"""
from __future__ import annotations

import asyncio
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

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Power-law slippage model (gamma=0.5 per Blueprint)
# ---------------------------------------------------------------------------


class PowerLawSlippage(SlippageModel):
    """Power-law slippage: slippage = k * size^gamma.

    gamma=0.5 per Blueprint. Conservative estimate for shadow mode.
    Larger orders receive proportionally more slippage.
    """

    def __init__(self, k: float = 1.0, gamma: float = 0.5) -> None:
        super().__init__(base_slippage_pct=Decimal("0.001"))
        self._k = k
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
        """
        self._signal_generator = signal_generator

        # If no executor provided, create one with power-law slippage
        self._paper_executor: PaperExecutor = paper_executor or PaperExecutor(
            slippage_model=PowerLawSlippage(k=1.0, gamma=0.5),
            fee_rate=Decimal("0.001"),
        )

        self._market_recorder = market_recorder
        self._telegram = telegram
        self._symbols = symbols or ["BTC/USDT"]
        self._exchanges = exchanges

        # Orderbook store: exchange_id -> OrderBook (keyed per symbol internally)
        # Structure: symbol -> exchange_id -> OrderBook
        self._books: dict[str, dict[str, Any]] = {}

        self._running = False
        self._stats = ShadowStats(start_time=time.monotonic())

        # Background tasks
        self._daily_task: asyncio.Task[None] | None = None

        # Resolve orderbook class (Rust or Python)
        self._orderbook_cls = get_orderbook_class()

        # KRW/USDT dynamic rate (fetched from Upbit every 60s)
        self._krw_rate: float = float(os.getenv("KRW_USDT_RATE", "1380"))
        self._krw_rate_task: asyncio.Task[None] | None = None

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

        # Start daily summary background task
        self._daily_task = asyncio.create_task(
            self._daily_summary_loop(), name="shadow_daily_summary"
        )

        logger.info("shadow_mode.started")

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

        # Cancel daily summary task
        if self._daily_task is not None and not self._daily_task.done():
            self._daily_task.cancel()
            try:
                await self._daily_task
            except asyncio.CancelledError:
                pass
            self._daily_task = None

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

        try:
            # Build or update local orderbook
            book = self._orderbook_cls(symbol=symbol, exchange=exchange_id)
            # Normalise to list-of-tuples for apply_snapshot
            bid_tuples = [(str(b[0]), str(b[1])) for b in bids]
            ask_tuples = [(str(a[0]), str(a[1])) for a in asks]
            book.apply_snapshot(bid_tuples, ask_tuples)

            # Store in per-symbol registry
            if symbol not in self._books:
                self._books[symbol] = {}
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

            # Prometheus: collector message counter + health score
            try:
                COLLECTOR_MESSAGES.labels(exchange=exchange_id).inc()
                EXCHANGE_HEALTH_SCORE.labels(exchange=exchange_id).set(1.0)
            except Exception:
                pass

            # Record spread metrics
            try:
                best_bid_val = book.best_bid()
                best_ask_val = book.best_ask()
                if best_bid_val is not None and best_ask_val is not None:
                    mid = (Decimal(str(best_bid_val)) + Decimal(str(best_ask_val))) / 2
                    if mid > 0:
                        spread_bps = float(
                            (Decimal(str(best_ask_val)) - Decimal(str(best_bid_val)))
                            / mid
                            * 10000
                        )
                        exchange_pair = f"{exchange_id}"
                        SPREAD_BPS.labels(exchange_pair=exchange_pair).observe(spread_bps)
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
            except Exception:
                pass

            if signal is not None:
                try:
                    SIGNALS_TOTAL.labels(strategy=self.STRATEGY_ID, decision="emit").inc()
                    exchange_pair = f"{signal.buy_exchange}-{signal.sell_exchange}"
                    SIGNAL_COUNT.labels(exchange_pair=exchange_pair).inc()
                except Exception:
                    pass

                # Telegram signal notification (fire-and-forget)
                if self._telegram is not None:
                    try:
                        await self._telegram.send_signal_found(signal)
                    except Exception as exc:
                        logger.warning(
                            "shadow_mode.telegram_signal_notify_failed", error=str(exc)
                        )

                await self._execute_shadow_trade(signal)

        except Exception as exc:
            logger.error(
                "shadow_mode.on_orderbook_unhandled_error",
                exchange=exchange_id,
                symbol=symbol,
                error=str(exc),
                exc_info=True,
            )

    # -----------------------------------------------------------------------
    # Shadow trade execution
    # -----------------------------------------------------------------------

    async def _execute_shadow_trade(self, signal: Signal) -> None:
        """Paper-execute a signal: buy + sell orders with power-law slippage.

        Computes net PnL, updates stats, records to TimescaleDB + Prometheus.
        Never raises — exceptions are caught and logged.
        """
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

        # Net PnL = sell proceeds - sell fee - buy cost - buy fee
        net_pnl = (
            sell_trade.price * sell_trade.amount
            - sell_trade.fee
            - buy_trade.price * buy_trade.amount
            - buy_trade.fee
        )
        net_pnl_float = float(net_pnl)

        if net_pnl_float >= 0:
            self._stats.trades_won += 1
            result_label = "win"
        else:
            self._stats.trades_lost += 1
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
                fee_total = buy_trade.fee + sell_trade.fee
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

        # Prometheus metrics
        exchange_pair = f"{signal.buy_exchange}-{signal.sell_exchange}"
        try:
            TRADES_TOTAL.labels(
                strategy=self.STRATEGY_ID,
                exchange_pair=exchange_pair,
                result=result_label,
            ).inc()
            PNL_TOTAL.labels(strategy=self.STRATEGY_ID).set(self._stats.total_pnl)
            DRAWDOWN_CURRENT.labels(strategy=self.STRATEGY_ID).set(
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
    # KRW/USDT dynamic rate loop
    # -----------------------------------------------------------------------

    async def _krw_rate_loop(self) -> None:
        """Fetch KRW/USDT rate from Upbit every 60 seconds.

        Falls back to env var KRW_USDT_RATE if API is unreachable.
        Never raises — exceptions are caught and logged.
        """
        try:
            while self._running:
                try:
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        resp = await client.get(
                            "https://api.upbit.com/v1/ticker",
                            params={"markets": "KRW-USDT"},
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            if data and len(data) > 0:
                                trade_price = float(data[0].get("trade_price", 0))
                                if trade_price > 0:
                                    old_rate = self._krw_rate
                                    self._krw_rate = trade_price
                                    if abs(old_rate - trade_price) > 1:
                                        logger.info(
                                            "shadow_mode.krw_rate_updated",
                                            old_rate=old_rate,
                                            new_rate=trade_price,
                                        )
                except Exception as exc:
                    logger.debug("shadow_mode.krw_rate_fetch_failed", error=str(exc))

                await asyncio.sleep(60.0)
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

        summary_data: dict[str, Any] = {
            "date": now.strftime("%Y-%m-%d"),
            "strategy": self.STRATEGY_ID,
            "total_pnl": stats.total_pnl,
            "trades": total_trades,
            "win_rate": win_rate,
            "max_drawdown": stats.max_drawdown,
        }

        # Update Prometheus gauges
        try:
            PNL_TOTAL.labels(strategy=self.STRATEGY_ID).set(stats.total_pnl)
            DRAWDOWN_CURRENT.labels(strategy=self.STRATEGY_ID).set(stats.max_drawdown)
        except Exception:
            pass

        if self._telegram is not None:
            try:
                await self._telegram.send_daily_summary(summary_data)
                stats.last_daily_summary = now
                logger.info(
                    "shadow_mode.daily_summary_sent",
                    date=summary_data["date"],
                    total_pnl=stats.total_pnl,
                    trades=total_trades,
                    win_rate=win_rate,
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
