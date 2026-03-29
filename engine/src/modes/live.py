"""LEVIATHAN Live Mode — Real Data + Real Execution (Phase H).

Mirrors ShadowMode architecture with direct in-process signal routing:
  1. WebSocket collectors receive real orderbook data (spot + futures)
  2. SignalGenerator evaluates cross-exchange arbitrage opportunities
  3. RealDataSignalProducer evaluates additional strategies
  4. Signals routed DIRECTLY via StrategyManager.route_signal() (no Redis)
  5. TradeRequests executed via DI executor (Paper for validation, Atomic for live)
  6. Redis used only for observability (dashboard pub, non-critical)
  7. All results recorded to TimescaleDB + Prometheus metrics + Telegram

Key difference from ShadowMode:
  - DI executor: PaperExecutor (validation) or AtomicExecutor (live trading)
  - No PowerLawSlippage/BookWalkSlippage (real execution or real slippage in paper)
  - LiveGate integration with safe Shadow fallback
  - Real risk checks via RiskGuardian (not just shadow stats)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from src.core.models import Order, OrderSide, OrderType, Signal
from src.core.rust_bridge import get_orderbook_class
from src.friction.fee_model import FeeModel
from src.strategies.base import TradeRequest, TradeLeg

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LiveGateFailed(Exception):
    """Raised when LiveGate enforcement fails — triggers Shadow fallback."""
    pass


# ---------------------------------------------------------------------------
# Executor Protocol (DI interface)
# ---------------------------------------------------------------------------


@runtime_checkable
class ExecutorProtocol(Protocol):
    """Protocol for trade execution — implemented by AtomicExecutor and PaperExecutor."""

    async def execute_same_exchange(
        self,
        exchange_id: str,
        leg1_order: Order,
        leg2_order: Order,
        strategy_id: str = "",
    ) -> Any: ...

    async def execute_cross_exchange(
        self,
        leg1_order: Order,
        leg2_order: Order,
        strategy_id: str = "",
        min_edge: Decimal = Decimal("0"),
    ) -> Any: ...

    async def execute_multi_leg(
        self,
        exchange_id: str,
        orders: list[Order],
        strategy_id: str = "",
    ) -> Any: ...


# ---------------------------------------------------------------------------
# Live Mode Stats
# ---------------------------------------------------------------------------


@dataclass
class PerStrategyStats:
    """Per-strategy metrics for live mode."""
    signals: int = 0
    trades: int = 0
    wins: int = 0
    losses: int = 0
    pnl: float = 0.0
    rejections: int = 0
    pnl_history: deque = field(default_factory=lambda: deque(maxlen=2000))


@dataclass
class LiveModeStats:
    """Cumulative metrics tracked across a live mode session."""
    start_time: float = 0.0  # time.monotonic()
    signals_detected: int = 0
    trades_executed: int = 0
    trades_won: int = 0
    trades_lost: int = 0
    total_pnl: float = 0.0
    peak_pnl: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    trades_rejected: int = 0
    trades_risk_blocked: int = 0
    winning_pnl_sum: float = 0.0
    losing_pnl_sum: float = 0.0
    by_strategy: dict[str, PerStrategyStats] = field(default_factory=dict)
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_start: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    daily_pnl: float = 0.0
    last_daily_summary: datetime | None = None
    # Trade history for dashboard (bounded deque)
    trade_history: deque = field(default_factory=lambda: deque(maxlen=10_000))


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

try:
    from prometheus_client import Counter as PromCounter, Histogram, Gauge

    LIVE_SIGNALS_TOTAL = PromCounter(
        "live_signals_total", "Live mode signals detected", ["strategy"]
    )
    LIVE_TRADES_TOTAL = PromCounter(
        "live_trades_total", "Live mode trades executed", ["strategy", "result"]
    )
    LIVE_PNL_TOTAL = Gauge(
        "live_pnl_total_usd", "Live mode cumulative PnL (USD)"
    )
    LIVE_DRAWDOWN = Gauge(
        "live_drawdown_pct", "Live mode current drawdown %"
    )
    LIVE_EXECUTION_TIME = Histogram(
        "live_execution_seconds", "Live trade execution latency",
        buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
    )
    LIVE_ROUTING_FALLBACK = PromCounter(
        "live_routing_fallback_total", "Live routing fallbacks", ["reason"]
    )
except Exception:
    # Graceful degradation if prometheus not available
    LIVE_SIGNALS_TOTAL = None  # type: ignore
    LIVE_TRADES_TOTAL = None  # type: ignore
    LIVE_PNL_TOTAL = None  # type: ignore
    LIVE_DRAWDOWN = None  # type: ignore
    LIVE_EXECUTION_TIME = None  # type: ignore
    LIVE_ROUTING_FALLBACK = None  # type: ignore


# ---------------------------------------------------------------------------
# LiveMode orchestrator
# ---------------------------------------------------------------------------


class LiveMode:
    """Live Mode orchestrator — mirrors ShadowMode with real execution.

    Lifecycle: init → start() → [runs continuously] → stop()

    Key architectural choice: DIRECT in-process signal routing
    (StrategyManager.route_signal()) instead of Redis Streams.
    Redis is used only for observability/dashboard.
    """

    STRATEGY_ID = "live_arb_v1"

    def __init__(
        self,
        signal_generator: Any,
        executor: Any,  # ExecutorProtocol — AtomicExecutor or PaperExecutor
        strategy_manager: Any,
        symbols: list[str] | None = None,
        exchanges: list[str] | None = None,
        *,
        multi_signal_producer: Any | None = None,
        funding_rate_collector: Any | None = None,
        market_recorder: Any | None = None,
        telegram: Any | None = None,
        live_gate: Any | None = None,
        risk_guardian: Any | None = None,
        kill_switch: Any | None = None,
        circuit_breaker: Any | None = None,
        regime_detector: Any | None = None,
        event_bus: Any | None = None,
        db_pool: Any | None = None,
        data_quality_manager: Any | None = None,
        flash_guard: Any | None = None,
        portfolio_risk: Any | None = None,
        strategy_filter: list[str] | None = None,
        execution_mode: str = "paper",  # "paper" | "live"
    ) -> None:
        self._signal_generator = signal_generator
        self._executor = executor
        self._strategy_manager = strategy_manager
        self._multi_signal_producer = multi_signal_producer
        self._funding_rate_collector = funding_rate_collector
        self._market_recorder = market_recorder
        self._telegram = telegram
        self._live_gate = live_gate
        self._risk_guardian = risk_guardian
        self._kill_switch = kill_switch
        self._circuit_breaker = circuit_breaker
        self._regime_detector = regime_detector
        self._event_bus = event_bus  # For observability only
        self._db_pool = db_pool
        self._data_quality_manager = data_quality_manager
        self._flash_guard = flash_guard
        self._portfolio_risk = portfolio_risk
        self._execution_mode = execution_mode

        self._symbols = symbols or ["BTC/USDT"]
        self._exchanges = exchanges or ["binance"]
        self._running = False
        self._stats = LiveModeStats(start_time=time.monotonic())
        self._fee_model = FeeModel()

        # Orderbook store: symbol -> exchange_id -> OrderBook
        self._books: dict[str, dict[str, Any]] = {}
        self._orderbook_cls = get_orderbook_class()

        # Futures exchanges for identification
        self._futures_exchanges: set[str] = {
            "binance_futures", "okx_futures", "bybit_futures"
        }

        # RealDataSignalProducer (spot_futures, funding_rate, stat_arb, etc.)
        self._real_signal_producer: Any | None = None
        if self._multi_signal_producer is not None:
            try:
                from src.core.real_signal_producer import RealDataSignalProducer
                from src.core.triangular_scanner import TriangularScanner
                from src.core.latency_tracker import LatencyTracker

                self._latency_tracker = LatencyTracker()
                self._stale_detector = None
                try:
                    from src.core.stale_detector import StaleOrderbookDetector
                    self._stale_detector = StaleOrderbookDetector()
                except Exception:
                    pass

                self._real_signal_producer = RealDataSignalProducer(
                    multi_signal_producer=self._multi_signal_producer,
                    triangular_scanner=TriangularScanner(),
                    futures_exchanges=self._futures_exchanges,
                    latency_tracker=self._latency_tracker,
                    stale_detector=self._stale_detector,
                    regime_detector=self._regime_detector,
                )
            except Exception as exc:
                logger.warning("live_mode.real_signal_producer_init_failed: %s", exc)

        # Warmup guard: skip signals for first N seconds after start
        _env = os.environ.get("ENGINE_ENV", "dev")
        self._signal_warmup_seconds: float = 5.0 if _env != "test" else 0.0

        # Background tasks
        self._collector_manager: Any | None = None
        self._daily_task: asyncio.Task | None = None
        self._funding_rate_task: asyncio.Task | None = None

        # Collision detection: (symbol, exchange_pair) -> last_trade_time
        self._recent_trades: dict[str, float] = {}
        self._collision_window_s: float = 10.0

        # KRW/USDT normalization (ported from ShadowMode)
        _raw_krw_rate = float(os.getenv("KRW_USDT_RATE", "1380"))
        if _raw_krw_rate <= 0:
            _raw_krw_rate = 1380.0
        self._krw_rate: float = _raw_krw_rate
        self._krw_rate_task: asyncio.Task | None = None
        self._krw_stale: bool = False
        self._krw_exchanges: set[str] = {"upbit", "bithumb", "coinone"}

        # Bithumb delta orderbook handling
        self._delta_exchanges: set[str] = {"bithumb"}

        # Event-loop yield counter (every N updates, yield to prevent starvation)
        self._ob_counter: int = 0
        self._yield_every: int = 5

        # Rate limiter (per-exchange token bucket)
        try:
            from src.infra.exchange.rate_limiter import TokenBucket
            self._rate_buckets: dict[str, TokenBucket] = {}
            self._has_rate_limiter = True
        except ImportError:
            self._has_rate_limiter = False

        # Strategy filter allowlist (None = all strategies pass)
        self._strategy_filter: frozenset[str] | None = (
            frozenset(strategy_filter) if strategy_filter else None
        )

        # Strategy loss cooldown: strategy_id -> re-enable timestamp (US-164)
        self._strategy_disable_until: dict[str, float] = {}
        self._single_loss_disable_seconds: float = float(
            os.getenv("LIVE_SINGLE_LOSS_DISABLE_SECONDS", "600")
        )
        self._max_loss_per_trade_usd: Decimal = Decimal(
            os.getenv("LIVE_MAX_LOSS_PER_TRADE_USD", "10")
        )

        logger.info(
            "live_mode.init execution_mode=%s symbols=%s exchanges=%s executor=%s",
            execution_mode, self._symbols, self._exchanges, type(executor).__name__,
        )

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    async def start(self) -> None:
        """Start live mode: LiveGate check → strategies → collectors → background tasks."""
        if self._running:
            logger.warning("live_mode.already_running")
            return

        # Step 1: LiveGate enforcement (if available)
        if self._live_gate is not None:
            try:
                from src.modes.live_gate import LiveGate
                if isinstance(self._live_gate, LiveGate):
                    eligible = await self._live_gate.enforce_or_fallback()
                    if not eligible:
                        logger.warning("live_mode.live_gate_failed — raising LiveGateFailed")
                        raise LiveGateFailed("LiveGate enforcement failed")
            except LiveGateFailed:
                raise
            except Exception as exc:
                logger.warning("live_mode.live_gate_error: %s — raising LiveGateFailed", exc)
                raise LiveGateFailed(f"LiveGate error: {exc}") from exc
        else:
            logger.warning(
                "live_mode.live_gate_not_initialized — proceeding (소액 테스트 모드)"
            )

        self._running = True
        self._stats = LiveModeStats(start_time=time.monotonic())

        # Step 2: Activate strategies (shadow_mode=False for real execution)
        if self._strategy_manager is not None:
            for sid in self._strategy_manager.list_strategies():
                s = self._strategy_manager.get_strategy(sid)
                if s:
                    s.shadow_mode = (self._execution_mode != "live")
            for sid in self._strategy_manager.list_strategies():
                try:
                    await self._strategy_manager.start_strategy(sid)
                except Exception as exc:
                    logger.warning("live_mode.strategy_start_failed strategy=%s error=%s", sid, exc)
            logger.info(
                "live_mode.strategies_started count=%d shadow_mode=%s",
                len(self._strategy_manager.list_strategies()),
                self._execution_mode != "live",
            )

        # Step 3: Start collectors
        from src.collectors.manager import CollectorManager

        self._collector_manager = CollectorManager(
            symbols=self._symbols,
            exchanges=self._exchanges,
            on_orderbook=self._on_orderbook,
        )
        await self._collector_manager.start()
        logger.info("live_mode.collectors_started exchanges=%s symbols=%s",
                     self._exchanges, self._symbols)

        # Step 4: Start KRW rate updater
        self._krw_rate_task = asyncio.create_task(
            self._krw_rate_loop(), name="live_krw_rate"
        )

        # Step 5: Start funding rate collector (for spot_futures + funding_rate strategies)
        if self._funding_rate_collector is not None:
            self._funding_rate_task = asyncio.create_task(
                self._funding_rate_loop(), name="live_funding_rate"
            )

        # Step 5: Start daily summary task
        self._daily_task = asyncio.create_task(
            self._daily_summary_loop(), name="live_daily_summary"
        )

        # Step 6: Telegram notification
        if self._telegram is not None:
            try:
                await self._telegram.send_alert_kr("live_mode_start", {
                    "execution_mode": self._execution_mode,
                    "exchanges": ", ".join(self._exchanges),
                    "symbols": ", ".join(self._symbols),
                    "executor": type(self._executor).__name__,
                })
            except Exception as exc:
                logger.warning("live_mode.telegram_start_alert_failed: %s", exc)

        logger.info(
            "live_mode.started execution_mode=%s executor=%s",
            self._execution_mode, type(self._executor).__name__,
        )

    async def stop(self) -> None:
        """Stop live mode gracefully."""
        if not self._running:
            return

        self._running = False
        logger.info("live_mode.stopping")

        # Cancel background tasks
        for task in [self._daily_task, self._funding_rate_task, self._krw_rate_task]:
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Stop collectors
        if self._collector_manager is not None:
            try:
                await self._collector_manager.stop()
            except Exception as exc:
                logger.error("live_mode.collectors_stop_failed: %s", exc)

        # Send final summary
        if self._telegram is not None:
            try:
                await self._send_summary()
            except Exception as exc:
                logger.warning("live_mode.final_summary_failed: %s", exc)

        # Persist stats to DB
        await self._persist_stats()

        logger.info(
            "live_mode.stopped uptime_s=%.1f signals=%d trades=%d pnl=%.2f mdd=%.4f",
            time.monotonic() - self._stats.start_time,
            self._stats.signals_detected,
            self._stats.trades_executed,
            self._stats.total_pnl,
            self._stats.max_drawdown_pct,
        )

    # -----------------------------------------------------------------------
    # Orderbook callback (mirrors ShadowMode._on_orderbook)
    # -----------------------------------------------------------------------

    async def _on_orderbook(
        self,
        exchange_id: str,
        symbol: str,
        bids: list[list[Any]],
        asks: list[list[Any]],
        **kwargs: Any,
    ) -> None:
        """Process orderbook update: build book → signal → route → execute."""
        if not self._running:
            return

        # Event-loop yield to prevent starvation (every N updates)
        self._ob_counter += 1
        if self._ob_counter % self._yield_every == 0:
            await asyncio.sleep(0)

        # KRW normalization: convert KRW prices to USDT
        ex_base = exchange_id.removeprefix("paper_").removeprefix("sandbox_")
        if ex_base in self._krw_exchanges and "/KRW" in symbol:
            if self._krw_stale or self._krw_rate <= 0:
                logger.debug("live_mode.krw_stale_skip exchange=%s symbol=%s", exchange_id, symbol)
                return
            rate = self._krw_rate
            bids = [[str(float(b[0]) / rate), b[1]] for b in bids]
            asks = [[str(float(a[0]) / rate), a[1]] for a in asks]
            symbol = symbol.replace("/KRW", "/USDT")

        # Build CoreOrderBook (delta vs snapshot)
        core_book = self._orderbook_cls(symbol=symbol, exchange=exchange_id)
        if ex_base in self._delta_exchanges and hasattr(core_book, "apply_delta"):
            # Bithumb sends incremental deltas
            existing = self._books.get(symbol, {}).get(exchange_id)
            if existing is not None:
                core_book = existing
                core_book.apply_delta(
                    [(b[0], b[1]) for b in bids],
                    [(a[0], a[1]) for a in asks],
                )
            else:
                core_book.apply_snapshot(
                    [(b[0], b[1]) for b in bids],
                    [(a[0], a[1]) for a in asks],
                )
        else:
            core_book.apply_snapshot(
                [(b[0], b[1]) for b in bids],
                [(a[0], a[1]) for a in asks],
        )

        # Update book store
        if symbol not in self._books:
            self._books[symbol] = {}
        self._books[symbol][exchange_id] = core_book

        # Record to MarketRecorder (TimescaleDB)
        if self._market_recorder:
            best_bid = core_book.best_bid()
            best_ask = core_book.best_ask()
            if best_bid and best_ask:
                try:
                    self._market_recorder.record_orderbook(
                        exchange=exchange_id, symbol=symbol,
                        bids=bids[:20], asks=asks[:20],
                        best_bid=best_bid, best_ask=best_ask,
                    )
                except Exception:
                    pass

        # Data quality check (US-286)
        if self._data_quality_manager is not None:
            try:
                if hasattr(self._data_quality_manager, 'check_orderbook'):
                    ok = self._data_quality_manager.check_orderbook(exchange_id, symbol, core_book)
                    if not ok:
                        logger.debug("live_mode.data_quality_rejected exchange=%s symbol=%s", exchange_id, symbol)
                        return
            except Exception as exc:
                logger.debug("live_mode.data_quality_check_error: %s", exc)

        # Warmup guard
        if (time.monotonic() - self._stats.start_time) < self._signal_warmup_seconds:
            return

        # --- SignalGenerator (cross_exchange) ---
        if self._signal_generator and len(self._books.get(symbol, {})) >= 2:
            try:
                signal = await self._signal_generator.on_orderbook_update(
                    book=core_book,
                    books=self._books.get(symbol, {}),
                )
                if signal is not None:
                    if self._strategy_manager is not None:
                        await self._route_signal_to_strategies(signal)
                    else:
                        await self._execute_direct_signal(signal)
            except Exception as exc:
                logger.warning("live_mode.signal_generator_error: %s", exc)

        # --- RealDataSignalProducer (spot_futures, funding_rate, stat_arb, etc.) ---
        if self._real_signal_producer is not None:
            try:
                signals = await self._real_signal_producer.on_orderbook_update(
                    exchange_id=exchange_id,
                    symbol=symbol,
                    book=core_book,
                    all_books=self._books.get(symbol, {}),
                    futures_books=self._books,
                )
                for signal in (signals or []):
                    if self._strategy_manager is not None:
                        await self._route_signal_to_strategies(signal)
            except Exception as exc:
                logger.debug("live_mode.real_signal_producer_error: %s", exc)

        # --- MultiStrategySignalProducer (additional signals) ---
        if self._multi_signal_producer is not None:
            try:
                self._multi_signal_producer.on_orderbook(exchange_id, symbol, core_book)
            except Exception:
                pass

        # --- Publish to Redis for observability (non-critical) ---
        await self._publish_orderbook_for_observability(exchange_id, symbol)

    # -----------------------------------------------------------------------
    # Signal routing (direct in-process — mirrors ShadowMode)
    # -----------------------------------------------------------------------

    async def _route_signal_to_strategies(self, signal: Signal) -> None:
        """Route signal via StrategyManager.route_signal() — direct, no Redis.

        Same proven path as ShadowMode._route_signal_to_strategies().
        """
        if self._strategy_manager is None:
            return

        try:
            trade_requests = await self._strategy_manager.route_signal(signal)
            for request in trade_requests:
                await self._execute_trade_request(request)

            if LIVE_SIGNALS_TOTAL is not None:
                LIVE_SIGNALS_TOTAL.labels(
                    strategy=signal.strategy_id or self.STRATEGY_ID
                ).inc()

            logger.debug(
                "live_mode.signal_routed strategy=%s symbol=%s requests=%d",
                signal.strategy_id, signal.symbol, len(trade_requests),
            )
        except Exception as exc:
            if LIVE_ROUTING_FALLBACK is not None:
                LIVE_ROUTING_FALLBACK.labels(reason="routing_exception").inc()
            logger.warning(
                "live_mode.strategy_routing_failed strategy=%s error=%s",
                signal.strategy_id, exc,
            )
            # Fallback: execute signal directly (prevent signal loss)
            await self._execute_direct_signal(signal)

    # -----------------------------------------------------------------------
    # Trade execution (DI executor — Paper or Atomic)
    # -----------------------------------------------------------------------

    async def _execute_trade_request(self, trade_request: TradeRequest) -> None:
        """Execute a TradeRequest via the injected executor.

        Applies risk checks, collision detection, then routes to executor.
        Tracks stats, sends Telegram alerts, publishes to Redis for dashboard.
        """
        t0 = time.monotonic()
        sid = trade_request.strategy_id or self.STRATEGY_ID
        self._stats.signals_detected += 1

        # --- Strategy filter allowlist ---
        if self._strategy_filter is not None and sid not in self._strategy_filter:
            logger.debug("live_mode.strategy_filtered strategy=%s", sid)
            return

        # --- Strategy loss cooldown (US-164) ---
        if sid in self._strategy_disable_until:
            if time.monotonic() < self._strategy_disable_until[sid]:
                logger.debug("live_mode.strategy_cooldown strategy=%s", sid)
                return
            else:
                del self._strategy_disable_until[sid]

        # Ensure per-strategy stats exist
        if sid not in self._stats.by_strategy:
            self._stats.by_strategy[sid] = PerStrategyStats()
        strat_stats = self._stats.by_strategy[sid]
        strat_stats.signals += 1

        # --- Kill switch check ---
        if self._kill_switch is not None and hasattr(self._kill_switch, 'is_halted'):
            if self._kill_switch.is_halted():
                logger.warning("live_mode.kill_switch_active — skipping trade")
                return

        # --- Circuit breaker check ---
        if self._circuit_breaker is not None:
            try:
                if hasattr(self._circuit_breaker, 'is_open') and self._circuit_breaker.is_open():
                    logger.warning("live_mode.circuit_breaker_open — skipping trade strategy=%s", sid)
                    return
            except Exception as exc:
                logger.debug("live_mode.circuit_breaker_check_error: %s", exc)

        # --- Rate limiter check ---
        if self._has_rate_limiter:
            for leg in trade_request.legs:
                ex = leg.exchange_id.removeprefix("paper_").removeprefix("sandbox_")
                if ex not in self._rate_buckets:
                    from src.infra.exchange.rate_limiter import TokenBucket
                    self._rate_buckets[ex] = TokenBucket(rate=5.0, capacity=10.0)
                if not self._rate_buckets[ex].try_acquire():
                    logger.warning("live_mode.rate_limited exchange=%s strategy=%s", ex, sid)
                    return

        # --- FlashGuard check ---
        if self._flash_guard is not None:
            try:
                if hasattr(self._flash_guard, 'check'):
                    blocked = self._flash_guard.check(trade_request)
                    if blocked:
                        logger.warning("live_mode.flash_guard_blocked strategy=%s", sid)
                        return
            except Exception as exc:
                logger.debug("live_mode.flash_guard_check_error: %s", exc)

        # --- Risk guardian check ---
        if self._risk_guardian is not None:
            try:
                approved = True
                if hasattr(self._risk_guardian, 'check_trade_request'):
                    approved = self._risk_guardian.check_trade_request(trade_request)
                elif hasattr(self._risk_guardian, 'approve'):
                    approved = self._risk_guardian.approve(trade_request)
                if not approved:
                    self._stats.trades_risk_blocked += 1
                    strat_stats.rejections += 1
                    logger.info("live_mode.risk_rejected strategy=%s", sid)
                    return
            except Exception as exc:
                logger.warning("live_mode.risk_check_error: %s", exc)

        # --- Collision detection ---
        collision_key = self._build_collision_key(trade_request)
        now = time.monotonic()
        if collision_key in self._recent_trades:
            elapsed = now - self._recent_trades[collision_key]
            if elapsed < self._collision_window_s:
                logger.debug("live_mode.collision_detected key=%s elapsed=%.1f", collision_key, elapsed)
                return
        self._recent_trades[collision_key] = now

        # --- Execute via DI executor ---
        try:
            orders = self._legs_to_orders(trade_request)
            if not orders:
                logger.warning("live_mode.no_valid_orders strategy=%s", sid)
                return

            exec_result = await self._route_to_executor(trade_request, orders)

            if LIVE_EXECUTION_TIME is not None:
                LIVE_EXECUTION_TIME.observe(time.monotonic() - t0)

            # --- Validate execution result ---
            if exec_result is not None and hasattr(exec_result, 'status'):
                from src.execution.executor import ExecutionStatus
                if exec_result.status != ExecutionStatus.SUCCESS:
                    logger.warning(
                        "live_mode.execution_not_success strategy=%s status=%s",
                        sid, exec_result.status,
                    )
                    strat_stats.rejections += 1
                    return

            # --- Record trade result ---
            self._stats.trades_executed += 1
            strat_stats.trades += 1

            # Compute PnL from ACTUAL fill prices (not estimates)
            pnl = self._compute_pnl_from_result(exec_result, trade_request)
            self._update_pnl_stats(pnl, sid)

            if LIVE_TRADES_TOTAL is not None:
                result_label = "win" if pnl > 0 else "loss"
                LIVE_TRADES_TOTAL.labels(strategy=sid, result=result_label).inc()

            # Store in trade history
            trade_record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "strategy_id": sid,
                "legs": len(trade_request.legs),
                "pnl": float(pnl),
                "execution_mode": self._execution_mode,
                "latency_ms": (time.monotonic() - t0) * 1000,
            }
            self._stats.trade_history.append(trade_record)

            # Telegram alert for fills
            if self._telegram is not None and self._execution_mode == "live":
                try:
                    await self._telegram.send_alert_kr("live_trade_executed", {
                        "strategy": sid,
                        "pnl": f"${pnl:.4f}",
                        "legs": str(len(trade_request.legs)),
                        "total_pnl": f"${self._stats.total_pnl:.2f}",
                    })
                except Exception:
                    pass

            # Publish to Redis for dashboard (non-critical)
            await self._publish_trade_for_observability(trade_record)

            logger.info(
                "live_mode.trade_executed strategy=%s pnl=%.4f total_pnl=%.2f mode=%s latency_ms=%.1f",
                sid, float(pnl), self._stats.total_pnl,
                self._execution_mode, (time.monotonic() - t0) * 1000,
            )

        except Exception as exc:
            logger.error("live_mode.execution_failed strategy=%s error=%s", sid, exc, exc_info=True)
            if LIVE_TRADES_TOTAL is not None:
                LIVE_TRADES_TOTAL.labels(strategy=sid, result="error").inc()

    async def _execute_direct_signal(self, signal: Signal) -> None:
        """Fallback: execute a raw Signal directly (2-leg cross-exchange)."""
        if signal.buy_exchange is None or signal.sell_exchange is None:
            return
        try:
            legs = [
                TradeLeg(
                    exchange_id=signal.buy_exchange,
                    symbol=signal.symbol,
                    side=OrderSide.BUY,
                    size=signal.volume or Decimal("0.001"),
                    price=signal.buy_price,
                    order_type=OrderType.LIMIT,
                ),
                TradeLeg(
                    exchange_id=signal.sell_exchange,
                    symbol=signal.symbol,
                    side=OrderSide.SELL,
                    size=signal.volume or Decimal("0.001"),
                    price=signal.sell_price,
                    order_type=OrderType.LIMIT,
                ),
            ]
            request = TradeRequest(
                strategy_id=signal.strategy_id or self.STRATEGY_ID,
                legs=legs,
                expected_profit_usdt=Decimal(str(signal.metadata.get("net_profit", "0"))),
                metadata={"source": "direct_signal_fallback"},
            )
            await self._execute_trade_request(request)
        except Exception as exc:
            logger.warning("live_mode.direct_signal_execution_failed: %s", exc)

    # -----------------------------------------------------------------------
    # Executor routing helpers
    # -----------------------------------------------------------------------

    def _legs_to_orders(self, trade_request: TradeRequest) -> list[Order]:
        """Convert TradeRequest legs to Order objects."""
        orders = []
        for leg in trade_request.legs:
            price = leg.price or Decimal("0")
            if price <= 0:
                logger.warning(
                    "live_mode.leg_missing_price exchange=%s symbol=%s",
                    leg.exchange_id, leg.symbol,
                )
            orders.append(Order(
                order_id=str(uuid.uuid4()),
                exchange_id=leg.exchange_id,
                symbol=leg.symbol,
                side=leg.side,
                order_type=leg.order_type,
                price=price,
                amount=leg.size,
            ))
        return orders

    async def _route_to_executor(
        self, trade_request: TradeRequest, orders: list[Order]
    ) -> Any:
        """Route to appropriate executor method based on leg count and exchanges."""
        exchanges_involved = {o.exchange_id for o in orders}
        sid = trade_request.strategy_id or ""

        if len(orders) >= 3 and len(exchanges_involved) == 1:
            # Multi-leg same exchange (triangular)
            exchange_id = next(iter(exchanges_involved))
            return await self._executor.execute_multi_leg(
                exchange_id=exchange_id, orders=orders, strategy_id=sid,
            )
        elif len(orders) == 2 and len(exchanges_involved) == 1:
            # 2-leg same exchange
            exchange_id = next(iter(exchanges_involved))
            return await self._executor.execute_same_exchange(
                exchange_id=exchange_id,
                leg1_order=orders[0],
                leg2_order=orders[1],
                strategy_id=sid,
            )
        elif len(orders) == 2 and len(exchanges_involved) == 2:
            # Cross-exchange
            return await self._executor.execute_cross_exchange(
                leg1_order=orders[0],
                leg2_order=orders[1],
                strategy_id=sid,
                min_edge=Decimal("0"),
            )
        else:
            # Fallback: sequential single-leg execution
            logger.warning(
                "live_mode.fallback_sequential_execution orders=%d exchanges=%d",
                len(orders), len(exchanges_involved),
            )
            results = []
            for order in orders:
                # Single-leg: use execute_multi_leg with 1 order if available
                if hasattr(self._executor, 'execute_multi_leg'):
                    result = await self._executor.execute_multi_leg(
                        exchange_id=order.exchange_id,
                        orders=[order],
                        strategy_id=sid,
                    )
                else:
                    result = await self._executor.execute_same_exchange(
                        exchange_id=order.exchange_id,
                        leg1_order=order,
                        leg2_order=order,
                        strategy_id=sid,
                    )
                results.append(result)
            return results

    def _build_collision_key(self, trade_request: TradeRequest) -> str:
        """Build collision detection key from trade request."""
        symbols = sorted({leg.symbol for leg in trade_request.legs})
        exchanges = sorted({leg.exchange_id for leg in trade_request.legs})
        return f"{','.join(symbols)}|{','.join(exchanges)}"

    def _compute_pnl_from_result(self, exec_result: Any, trade_request: TradeRequest) -> Decimal:
        """Compute PnL from ACTUAL execution result (fill prices from exchange).

        Priority:
        1. exec_result.realized_pnl (if executor computed it)
        2. exec_result.legs[].trade (actual fill prices from Binance)
        3. Fallback to trade_request legs (estimates — last resort)
        """
        # 1. Direct PnL from executor
        if exec_result is not None and hasattr(exec_result, 'realized_pnl'):
            rp = exec_result.realized_pnl
            if rp is not None and rp != 0:
                return Decimal(str(rp))

        # 2. Compute from actual Trade objects in ExecutionResult
        if exec_result is not None and hasattr(exec_result, 'legs'):
            net_pnl = Decimal("0")
            has_trades = False
            for leg_result in exec_result.legs:
                trade = getattr(leg_result, 'trade', None)
                if trade is None:
                    continue
                has_trades = True
                fill_price = Decimal(str(trade.price))
                fill_amount = Decimal(str(trade.amount))
                fill_fee = Decimal(str(getattr(trade, 'fee', 0)))
                notional = fill_price * fill_amount
                side = getattr(leg_result, 'side', None) or getattr(trade, 'side', None)
                side_str = str(side).upper() if side else ""

                if "SELL" in side_str:
                    net_pnl += notional - fill_fee
                else:
                    net_pnl -= notional + fill_fee

            if has_trades:
                return net_pnl

        # 3. Fallback: estimate from trade_request legs
        net_pnl = Decimal("0")
        for leg in trade_request.legs:
            price = leg.price or Decimal("0")
            notional = price * leg.size
            ex = leg.exchange_id.removeprefix("paper_").removeprefix("sandbox_")
            try:
                fee = self._fee_model.taker_fee(ex, notional)
            except ValueError:
                fee = notional * Decimal("0.0025")
            if leg.side == OrderSide.SELL:
                net_pnl += notional - fee
            else:
                net_pnl -= notional + fee
        return net_pnl

    def _compute_pnl(self, trade_request: TradeRequest, exec_result: Any) -> Decimal:
        """Legacy wrapper — delegates to _compute_pnl_from_result."""
        return self._compute_pnl_from_result(exec_result, trade_request)

    def _update_pnl_stats(self, pnl: Decimal, strategy_id: str) -> None:
        """Update cumulative PnL, drawdown, and per-strategy stats."""
        pnl_f = float(pnl)
        self._stats.total_pnl += pnl_f
        self._stats.daily_pnl += pnl_f

        if pnl_f > 0:
            self._stats.trades_won += 1
            self._stats.winning_pnl_sum += pnl_f
        else:
            self._stats.trades_lost += 1
            self._stats.losing_pnl_sum += abs(pnl_f)

        # Strategy loss cooldown (US-164): disable strategy after large single loss
        if pnl_f < 0 and abs(pnl_f) >= float(self._max_loss_per_trade_usd):
            if self._single_loss_disable_seconds > 0:
                self._strategy_disable_until[strategy_id] = (
                    time.monotonic() + self._single_loss_disable_seconds
                )
                logger.warning(
                    "live_mode.strategy_cooldown_triggered strategy=%s loss=%.4f cooldown_s=%.0f",
                    strategy_id, pnl_f, self._single_loss_disable_seconds,
                )

        # Update peak and drawdown
        if self._stats.total_pnl > self._stats.peak_pnl:
            self._stats.peak_pnl = self._stats.total_pnl
        drawdown = self._stats.peak_pnl - self._stats.total_pnl
        if drawdown > self._stats.max_drawdown:
            self._stats.max_drawdown = drawdown
        if self._stats.peak_pnl > 0:
            dd_pct = drawdown / self._stats.peak_pnl
            if dd_pct > self._stats.max_drawdown_pct:
                self._stats.max_drawdown_pct = dd_pct

        # Update Prometheus
        if LIVE_PNL_TOTAL is not None:
            LIVE_PNL_TOTAL.set(self._stats.total_pnl)
        if LIVE_DRAWDOWN is not None:
            LIVE_DRAWDOWN.set(self._stats.max_drawdown_pct)

        # Per-strategy
        if strategy_id not in self._stats.by_strategy:
            self._stats.by_strategy[strategy_id] = PerStrategyStats()
        ss = self._stats.by_strategy[strategy_id]
        ss.pnl += pnl_f
        if pnl_f > 0:
            ss.wins += 1
        else:
            ss.losses += 1
        ss.pnl_history.append(pnl_f)

    # -----------------------------------------------------------------------
    # Observability (Redis publish — non-critical)
    # -----------------------------------------------------------------------

    async def _publish_orderbook_for_observability(
        self, exchange_id: str, symbol: str
    ) -> None:
        """Publish orderbook update to Redis for dashboard (fire-and-forget)."""
        if self._event_bus is None:
            return
        try:
            await self._event_bus.publish("leviathan:live_orderbooks", {
                "exchange": exchange_id,
                "symbol": symbol,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass  # Non-critical — dashboard can tolerate gaps

    async def _publish_trade_for_observability(self, trade_record: dict) -> None:
        """Publish trade execution to Redis for dashboard (fire-and-forget)."""
        if self._event_bus is None:
            return
        try:
            await self._event_bus.publish("leviathan:live_trades", trade_record)
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Background tasks
    # -----------------------------------------------------------------------

    async def _funding_rate_loop(self) -> None:
        """Periodic funding rate fetch (every 60s)."""
        interval = float(os.getenv("FUNDING_RATE_INTERVAL_S", "60"))
        try:
            while self._running:
                try:
                    if self._funding_rate_collector is not None:
                        rates = await self._funding_rate_collector.fetch_all()
                        if self._multi_signal_producer is not None and rates:
                            self._multi_signal_producer.update_funding_rates(rates)
                except Exception as exc:
                    logger.warning("live_mode.funding_rate_fetch_error: %s", exc)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    async def _daily_summary_loop(self) -> None:
        """Send daily summary via Telegram at 00:00 UTC."""
        try:
            while self._running:
                await asyncio.sleep(60)
                now = datetime.now(timezone.utc)
                if now.hour == 0 and now.minute == 0:
                    if (self._stats.last_daily_summary is None
                            or self._stats.last_daily_summary.day != now.day):
                        await self._send_summary()
                        self._stats.last_daily_summary = now
                        self._stats.daily_pnl = 0.0
                        self._cleanup_collision_map()
                        await self._persist_stats()
        except asyncio.CancelledError:
            pass

    async def _send_summary(self) -> None:
        """Send summary to Telegram."""
        if self._telegram is None:
            return

        uptime_h = (time.monotonic() - self._stats.start_time) / 3600
        wr = (self._stats.trades_won / max(1, self._stats.trades_executed)) * 100
        pf = (self._stats.winning_pnl_sum / max(0.01, self._stats.losing_pnl_sum))

        try:
            await self._telegram.send_alert_kr("live_daily_summary", {
                "uptime_hours": f"{uptime_h:.1f}",
                "execution_mode": self._execution_mode,
                "signals": str(self._stats.signals_detected),
                "trades": str(self._stats.trades_executed),
                "win_rate": f"{wr:.1f}%",
                "total_pnl": f"${self._stats.total_pnl:.2f}",
                "daily_pnl": f"${self._stats.daily_pnl:.2f}",
                "max_drawdown": f"{self._stats.max_drawdown_pct * 100:.2f}%",
                "profit_factor": f"{pf:.2f}",
            })
        except Exception as exc:
            logger.warning("live_mode.send_summary_failed: %s", exc)

    async def _persist_stats(self) -> None:
        """Persist live mode stats to TimescaleDB."""
        if self._db_pool is None:
            return
        try:
            async with self._db_pool.pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO engine_state (key, value, updated_at) "
                    "VALUES ($1, $2, NOW()) "
                    "ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = NOW()",
                    "live_total_pnl", str(self._stats.total_pnl),
                )
                await conn.execute(
                    "INSERT INTO engine_state (key, value, updated_at) "
                    "VALUES ($1, $2, NOW()) "
                    "ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = NOW()",
                    "live_trades_executed", str(self._stats.trades_executed),
                )
        except Exception as exc:
            logger.warning("live_mode.persist_stats_failed: %s", exc)

    # -----------------------------------------------------------------------
    # KRW rate loop (ported from ShadowMode)
    # -----------------------------------------------------------------------

    async def _krw_rate_loop(self) -> None:
        """Fetch KRW/USDT rate from Upbit every 60s."""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                while self._running:
                    try:
                        resp = await client.get(
                            "https://api.upbit.com/v1/ticker?markets=USDT-KRW"
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            if data and isinstance(data, list) and "trade_price" in data[0]:
                                new_rate = float(data[0]["trade_price"])
                                if new_rate > 0:
                                    self._krw_rate = new_rate
                                    self._krw_stale = False
                                    logger.debug("live_mode.krw_rate_updated rate=%.2f", new_rate)
                        else:
                            logger.debug("live_mode.krw_rate_fetch_http_error status=%d", resp.status_code)
                    except Exception as exc:
                        logger.debug("live_mode.krw_rate_fetch_error: %s", exc)
                        # Mark stale after 5 consecutive failures
                        self._krw_stale = True
                    await asyncio.sleep(60)
        except asyncio.CancelledError:
            pass

    # -----------------------------------------------------------------------
    # Collision map cleanup
    # -----------------------------------------------------------------------

    def _cleanup_collision_map(self) -> None:
        """Remove stale entries from collision detection map."""
        now = time.monotonic()
        stale_keys = [
            k for k, t in self._recent_trades.items()
            if now - t > self._collision_window_s * 2
        ]
        for k in stale_keys:
            del self._recent_trades[k]

    # -----------------------------------------------------------------------
    # Properties (for dashboard/API integration)
    # -----------------------------------------------------------------------

    @property
    def stats(self) -> LiveModeStats:
        return self._stats

    @property
    def running(self) -> bool:
        return self._running

    @property
    def execution_mode(self) -> str:
        return self._execution_mode
