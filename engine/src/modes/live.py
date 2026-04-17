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
  - paper mode: BookWalkSlippage(books=self._books) wired into PaperExecutor (US-348)
  - live mode: AtomicExecutor — real exchange execution, no slippage simulation
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

from src.core.config import get_settings
from src.core.exchanges import KRW_EXCHANGES
from src.core.models import Order, OrderSide, OrderType, Signal
from src.core.rust_bridge import get_orderbook_class
from src.friction.fee_model import FeeModel
from src.modes.base import BaseMode
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
    trades_margin_blocked: int = 0  # BUG-74: entry blocked due to low futures margin
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


class LiveMode(BaseMode):
    """Live Mode orchestrator — mirrors ShadowMode with real execution.

    Lifecycle: init → start() → [runs continuously] → stop()

    Key architectural choice: DIRECT in-process signal routing
    (StrategyManager.route_signal()) instead of Redis Streams.
    Redis is used only for observability/dashboard.
    """

    STRATEGY_ID = "live_arb_v1"
    _MIN_MARGIN_ENTRY_USD: float = 3.0  # BUG-74: block new futures entries below this free margin

    def __init__(
        self,
        signal_generator: Any,
        executor: Any | None = None,  # ExecutorProtocol — AtomicExecutor or PaperExecutor; None = auto-wire
        strategy_manager: Any = None,
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
        tca_analyzer: Any | None = None,
        slippage_feedback_collector: Any | None = None,
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
        self._tca_analyzer = tca_analyzer  # TCAAnalyzer (US-116) — injected from main.py
        self._slippage_feedback_collector = slippage_feedback_collector  # US-283: SlippageFeedbackCollector

        self._symbols = symbols or ["BTC/USDT"]
        self._exchanges = exchanges or ["binance"]
        self._running = False
        self._first_trade_recorded = False
        self._settlement_tasks: list[asyncio.Task] = []  # tracked fire-and-forget exit tasks
        self._stats = LiveModeStats(start_time=time.monotonic())
        self._fee_model = FeeModel()

        # BUG-22: total capital for RiskGuardian.check_trade_request() — read from config tier
        try:
            from src.core.config_loader import get_config as _gc
            _tier = _gc("capital.tier", default="step2_1")
            self._total_capital_usd: float = float(
                _gc(f"capital.tiers.{_tier}.initial_usd", default=120.0)
            )
            # Session loss hard stop: live.max_daily_loss_pct from engine.json (default 5%)
            self._max_session_loss_usd: float = self._total_capital_usd * float(
                _gc("live.max_daily_loss_pct", default=5.0)
            ) / 100.0
        except Exception:
            self._total_capital_usd = 120.0
            self._max_session_loss_usd = 6.0  # 5% of $120

        # Slippage > Alpha auto-kill: track cumulative slippage per strategy
        self._strategy_slippage_window: dict[str, deque] = {}
        try:
            from src.core.config_loader import get_config as _gc_slip
            self._max_cumulative_slippage_bps: float = float(
                _gc_slip("risk.max_cumulative_slippage_bps") or 50.0
            )
            self._slippage_window_trades: int = int(
                _gc_slip("risk.slippage_window_trades") or 10
            )
            self._limit_fallback_spread_bps: float = float(
                _gc_slip("execution.limit_fallback_spread_bps") or 30.0
            )
        except Exception:
            self._max_cumulative_slippage_bps = 50.0
            self._slippage_window_trades = 10
            self._limit_fallback_spread_bps = 30.0

        # Orderbook store: symbol -> exchange_id -> OrderBook
        self._books: dict[str, dict[str, Any]] = {}
        self._orderbook_cls = get_orderbook_class()

        # US-348: BookWalkSlippage wiring for paper execution_mode.
        # Auto-wire PaperExecutor(BookWalkSlippage) only when no executor is injected.
        # If an executor is explicitly provided (e.g. in tests or production DI),
        # respect it and skip the auto-wire so the DI contract is honoured.
        self._book_walk_slippage: Any | None = None
        if execution_mode == "paper":
            try:
                from src.modes.shadow import BookWalkSlippage
                from src.execution.paper import PaperExecutor
                _op = get_settings().operational
                try:
                    pfr = max(Decimal("0"), min(Decimal("1"), _op.paper_partial_fill_rate))
                except Exception:
                    pfr = Decimal("0.05")
                try:
                    rr = max(Decimal("0"), min(Decimal("1"), _op.paper_rejection_rate))
                except Exception:
                    rr = Decimal("0.02")
                self._book_walk_slippage = BookWalkSlippage(books=self._books)
                self._executor = PaperExecutor(
                    slippage_model=self._book_walk_slippage,
                    fee_rate=Decimal("0.001"),  # Taker rate (Binance/Bitget 0.10%)
                    partial_fill_rate=pfr,
                    rejection_rate=rr,
                )
                logger.info(
                    "live_mode.book_walk_slippage_wired execution_mode=paper "
                    "executor=PaperExecutor(BookWalkSlippage)"
                )
            except Exception as exc:
                logger.warning(
                    "live_mode.book_walk_slippage_wire_failed (non-fatal): %s — "
                    "keeping original executor",
                    exc,
                )

        # Futures exchanges for identification — dynamic from FUTURES_TO_SPOT SSOT
        from src.core.exchanges import FUTURES_TO_SPOT
        self._futures_exchanges: set[str] = set(FUTURES_TO_SPOT.keys())

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
        self._signal_warmup_seconds: float = 5.0 if get_settings().engine_env != "test" else 0.0

        # Background tasks
        self._collector_manager: Any | None = None
        self._daily_task: asyncio.Task | None = None
        self._funding_rate_task: asyncio.Task | None = None
        self._dedup_cleanup_task: asyncio.Task | None = None
        self._trade_reconciler_task: asyncio.Task | None = None
        self._margin_refresh_task: asyncio.Task | None = None

        # Collision detection: (symbol, exchange_pair) -> last_trade_time
        from src.execution.dedup import DeduplicationGate
        self._dedup_gate = DeduplicationGate(window_s=10.0)
        # Keep _recent_trades for backwards compat but _dedup_gate is the atomic version
        self._recent_trades: dict[str, float] = {}
        self._collision_window_s: float = 10.0

        # MarginTracker: in-flight margin reservation (Bug 29)
        from src.execution.margin_tracker import MarginTracker
        self._margin_tracker = MarginTracker()
        # CRITICAL: share this instance with the executor so strategy-layer and
        # executor-layer track the same in-flight reservations (prevents dual-tracking divergence).
        if hasattr(self._executor, "set_margin_tracker"):
            self._executor.set_margin_tracker(self._margin_tracker)

        # BUG-18 fix: cached margin_available per futures exchange (refreshed every 60s)
        # produce_futures_futures_signal() has no adapter access, so we inject here.
        self._cached_margin: dict[str, Decimal] = {}

        # TradeReconciler: exchange fill reconciliation (PHOENIX v18 P0)
        from src.execution.trade_reconciler import TradeReconciler
        self._trade_reconciler = TradeReconciler(db_pool=db_pool, telegram=telegram)
        # Symbol window: recently-seen symbols with expiry timestamps (20-minute TTL).
        # Ensures recently-closed positions are still reconciled even after _open_positions
        # is cleared — prevents blind spot where reconciler reports "all clear" on empty symbols.
        self._recon_symbol_window: dict[str, float] = {}  # symbol → last_seen_epoch

        # KRW/USDT normalization (ported from ShadowMode)
        _raw_krw_rate = get_settings().operational.krw_usdt_rate
        if _raw_krw_rate <= 0:
            _raw_krw_rate = 1380.0
        self._krw_rate: float = _raw_krw_rate
        self._krw_rate_task: asyncio.Task | None = None
        self._krw_stale: bool = False
        self._krw_fail_count: int = 0  # BUG-88: require 5 consecutive failures before marking stale
        self._krw_exchanges: set[str] = set(KRW_EXCHANGES)

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
        _op = get_settings().operational
        self._single_loss_disable_seconds: float = _op.live_single_loss_disable_seconds
        self._max_loss_per_trade_usd: Decimal = _op.live_max_loss_per_trade_usd

        # Global concurrency semaphore (config: execution.max_concurrent_trades)
        from src.core.config_loader import get_config as _get_cfg
        _max_concurrent = int(_get_cfg("execution.max_concurrent_trades") or 2)
        self._trade_semaphore = asyncio.Semaphore(_max_concurrent)
        # Per-symbol cooldown (config: execution.symbol_cooldown_s)
        self._symbol_last_trade: dict[str, float] = {}
        self._symbol_cooldown_s: float = float(_get_cfg("execution.symbol_cooldown_s") or 30)

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

        # Step 1.5: Telegram approval gate (fail-closed, US-364)
        try:
            from src.infra.approval_gate import request_live_approval  # noqa: PLC0415
            approved = await request_live_approval(
                stage="K-L",
                details="Live 모드 진입 준비 완료 (K-L). 승인 시 실거래 시작.",
            )
            if not approved:
                logger.warning("live_mode.approval_rejected — aborting live start")
                raise LiveGateFailed("Live approval rejected or timed out")
        except LiveGateFailed:
            raise
        except Exception as exc:  # noqa: BLE001
            raise LiveGateFailed(f"approval_gate_error: {exc}") from exc

        # Step 1.9: Preflight position check — MUST run before starting strategies/collectors
        # (moved here from post-collector position to prevent race condition: tasks were
        #  executing live trades before the check could fire LiveGateFailed)
        if self._execution_mode == "live":
            _preflight_adapters = getattr(self._executor, "_exchanges", {})
            if not isinstance(_preflight_adapters, dict):
                _preflight_adapters = {}
            _futures_adapters = {k: v for k, v in _preflight_adapters.items() if "futures" in k}

            # BUG-111: Bitget REST API has >30s stale-data delay after position close.
            # 5× retries × 20s = 100s total. First attempt: auto-close stale positions.
            # Only ABORT if stale positions persist through all retries.
            _PREFLIGHT_RETRIES = 5
            _PREFLIGHT_DELAY_S = 20
            for _pf_attempt in range(_PREFLIGHT_RETRIES):
                if _pf_attempt > 0:
                    logger.info("preflight_retry attempt=%d/%d — waiting %ds for exchange API to settle",
                                _pf_attempt + 1, _PREFLIGHT_RETRIES, _PREFLIGHT_DELAY_S)
                    # MEDIUM-2: check kill-switch each second rather than one big sleep
                    for _wait_s in range(_PREFLIGHT_DELAY_S):
                        await asyncio.sleep(1)
                        if (self._kill_switch is not None
                                and hasattr(self._kill_switch, "is_halted")
                                and self._kill_switch.is_halted()):
                            raise LiveGateFailed("HALT raised during preflight wait — aborting startup")
                _stranded: list[str] = []
                for _eid, _adapter in _futures_adapters.items():
                    try:
                        _positions = await _adapter.get_positions()
                        _open_pos = [p for p in _positions if p.size != 0]
                        for p in _open_pos:
                            _stranded.append(f"{_eid}:{p.symbol}:{p.size}")
                    except Exception as _exc:
                        logger.warning("preflight_position_check_failed exchange=%s error=%s", _eid, _exc)

                    # Bitget: raw API check to catch stale total=0 positions (Bug 28)
                    if "bitget" in _eid and hasattr(_adapter, "_request"):
                        try:
                            _raw_resp = await _adapter._request(
                                "GET", "/api/v2/mix/position/all-position",
                                params={"productType": "USDT-FUTURES", "marginCoin": "USDT"},
                                signed=True,
                            )
                            for _item in (_raw_resp.get("data") or []):
                                _hold_side = _item.get("holdSide", "")
                                _raw_sym = _item.get("symbol", "")
                                _total = float(_item.get("total", 0) or 0)
                                _available = float(_item.get("available", 0) or 0)
                                if _hold_side and _raw_sym and (_total != 0 or _available != 0):
                                    _label = f"{_eid}:{_raw_sym}:stale(hold={_hold_side},total={_total})"
                                    if not any(_raw_sym in s for s in _stranded):
                                        _stranded.append(_label)
                                        logger.warning(
                                            "preflight_bitget_stale_position detected symbol=%s holdSide=%s total=%s",
                                            _raw_sym, _hold_side, _total,
                                        )
                        except Exception as _exc:
                            logger.warning("preflight_bitget_raw_check_failed error=%s", _exc)

                if _stranded:
                    _is_last = (_pf_attempt == _PREFLIGHT_RETRIES - 1)
                    if _is_last:
                        _msg = f"pre-existing positions detected: {_stranded}. Run close_positions.py --execute first."
                        logger.critical("live_preflight_ABORT %s", _msg)
                        raise LiveGateFailed(_msg)
                    # Not last attempt: log and try auto-close on first occurrence
                    logger.warning(
                        "preflight_stale_positions_found attempt=%d/%d: %s — auto-close 시도 후 재확인",
                        _pf_attempt + 1, _PREFLIGHT_RETRIES, _stranded,
                    )
                    if _pf_attempt == 0:
                        # First detection: attempt to close stale positions via adapters
                        for _eid, _adapter in _futures_adapters.items():
                            if "bitget" in _eid and hasattr(_adapter, "_request"):
                                # BUG-115: Bitget requires POST body (data=) + per-symbol call.
                                # Bulk close via params= always returns 400172.
                                try:
                                    _raw_close = await _adapter._request(
                                        "GET", "/api/v2/mix/position/all-position",
                                        params={"productType": "USDT-FUTURES", "marginCoin": "USDT"},
                                        signed=True,
                                    )
                                    for _pos_item in (_raw_close.get("data") or []):
                                        _ps = _pos_item.get("symbol", "")
                                        _ph = _pos_item.get("holdSide", "")
                                        _pt = float(_pos_item.get("total", 0) or 0)
                                        if _ps and _ph and _pt > 0:
                                            try:
                                                await asyncio.sleep(0.5)  # Bitget rate limit: 2 req/s
                                                await _adapter._request(
                                                    "POST", "/api/v2/mix/order/close-positions",
                                                    data={"symbol": _ps, "productType": "USDT-FUTURES", "holdSide": _ph},
                                                    signed=True,
                                                )
                                                logger.info(
                                                    "preflight_bitget_auto_close exchange=%s symbol=%s holdSide=%s size=%s",
                                                    _eid, _ps, _ph, _pt,
                                                )
                                            except Exception as _pce:
                                                logger.warning(
                                                    "preflight_bitget_per_sym_close_failed exchange=%s sym=%s err=%s",
                                                    _eid, _ps, _pce,
                                                )
                                except Exception as _ce:
                                    logger.warning("preflight_auto_close_error exchange=%s err=%s", _eid, _ce)
                            elif "binance" in _eid and hasattr(_adapter, "_signed_request"):
                                # MEDIUM-3: Binance has no bulk-close API — close each position individually
                                try:
                                    _bin_pos = await _adapter.get_positions()
                                    for _p in _bin_pos:
                                        if _p.size != 0:
                                            _sym_norm = _p.symbol.replace("/", "").upper()
                                            _close_side = "SELL" if _p.size > 0 else "BUY"
                                            # Use g-format: removes trailing zeros, keeps precision
                                            _qty_str = f"{abs(float(_p.size)):.8g}"
                                            await asyncio.sleep(0.2)  # Binance rate-limit parity with Bitget guard
                                            await _adapter._signed_request("POST", "/fapi/v1/order", params={
                                                "symbol": _sym_norm,
                                                "side": _close_side,
                                                "type": "MARKET",
                                                "quantity": _qty_str,
                                                "reduceOnly": "true",
                                            })
                                            logger.info(
                                                "preflight_binance_auto_close exchange=%s symbol=%s side=%s size=%s",
                                                _eid, _p.symbol, _close_side, _p.size,
                                            )
                                except Exception as _ce:
                                    logger.warning("preflight_auto_close_error exchange=%s err=%s", _eid, _ce)
                    continue
                # All clean on this attempt — exit preflight loop
                logger.info("preflight_clean attempt=%d/%d exchanges=%s",
                            _pf_attempt + 1, _PREFLIGHT_RETRIES, list(_futures_adapters.keys()))
                break  # No stale positions — no need to wait for further retries

            logger.info("live_preflight.positions_clean exchanges=%s", list(_futures_adapters.keys()))

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

        # Step 2.5: Reconcile exchange positions into strategy state
        await self._reconcile_positions_on_startup()

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

        # PHOENIX §8.3 Tier1 patch 3-3: HTTP pre-warm
        # Force TLS/TCP handshake on all adapters before first real order
        # to remove ~5-15ms cold-start tax from initial trade.
        await self._prewarm_connections()

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

        # DeduplicationGate periodic cleanup
        self._dedup_cleanup_task = asyncio.create_task(self._dedup_cleanup_loop(), name="live_dedup_cleanup")

        # TradeReconciler 10-min periodic loop (PHOENIX v18 P0)
        self._trade_reconciler_task = asyncio.create_task(self._trade_reconciler_loop(), name="live_trade_recon")

        # BUG-18 fix: margin cache refresh loop (every 60s)
        self._margin_refresh_task = asyncio.create_task(self._margin_refresh_loop(), name="live_margin_refresh")

        # Inject MarginTracker into futures_futures strategy
        if self._strategy_manager is not None:
            for sid in self._strategy_manager.list_strategies():
                strategy = self._strategy_manager.get_strategy(sid)
                if strategy is not None and hasattr(strategy, 'set_margin_tracker'):
                    strategy.set_margin_tracker(self._margin_tracker)

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

    async def _prewarm_connections(self) -> None:
        """PHOENIX §8.3 patch 3-3: Pre-warm HTTP connections to reduce first-order latency.

        Sends a lightweight dummy request to each adapter so the TCP/TLS handshake
        is paid up-front. Failures are silently ignored — the goal is just to
        prime the connection pool, not to validate endpoints.

        Defensive against mocked executors in tests: only iterates if the
        adapter container is a real dict.
        """
        adapters_attr: Any = getattr(self, "_adapter_dict", None)
        if not isinstance(adapters_attr, dict) or not adapters_attr:
            # Some live modes use _executor._exchanges instead of _adapter_dict.
            # Use object.__getattribute__-equivalent guard so AsyncMock auto-attrs
            # (which return coroutines) don't break iteration.
            executor = getattr(self, "_executor", None)
            candidate = getattr(executor, "_exchanges", None) if executor else None
            adapters_attr = candidate if isinstance(candidate, dict) else {}

        if not adapters_attr:
            logger.info("http_prewarm_skip — no adapters available")
            return

        async def _prewarm_one(ex_id: str, adapter: Any) -> None:
            try:
                http = getattr(adapter, "_http", None)
                if http is None:
                    return
                await asyncio.wait_for(http.get("/"), timeout=2.0)
            except Exception:
                pass  # Expected to fail on most '/' endpoints — TCP/TLS still warmed

        tasks = [
            asyncio.create_task(_prewarm_one(ex_id, adapter))
            for ex_id, adapter in adapters_attr.items()
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.info("http_prewarm_complete exchanges=%d", len(tasks))

    async def _reconcile_positions_on_startup(self) -> None:
        """Sync pre-existing exchange positions into strategy._open_positions.

        On engine restart, strategy._open_positions starts empty while the exchange
        may still hold positions from the previous session. This method queries each
        futures adapter and injects discovered positions into the matching strategy
        so that reconciler/ghost-clear can track them correctly.

        Conservative policy: without metadata to distinguish FF from FR, all paired
        futures positions are assigned to the futures_futures strategy.
        """
        if self._strategy_manager is None:
            return

        _adapters = getattr(self._executor, "_exchanges", {})
        if not isinstance(_adapters, dict):
            return
        _futures_adapters = {k: v for k, v in _adapters.items() if "futures" in k}
        if not _futures_adapters:
            logger.info("reconciler.no_futures_adapters — skipping position sync")
            return

        # Collect open positions from all futures exchanges
        all_positions: dict[str, list] = {}  # exchange_id → list[Position]
        for eid, adapter in _futures_adapters.items():
            try:
                positions = await adapter.get_positions()
                open_pos = [p for p in positions if p.size != 0]
                if open_pos:
                    all_positions[eid] = open_pos
            except Exception as exc:
                logger.warning(
                    "reconciler.get_positions_failed exchange=%s error=%s", eid, exc,
                )

        if not all_positions:
            logger.info("reconciler.no_positions")
            return

        # Group by symbol across exchanges: symbol → [(exchange_id, Position)]
        by_symbol: dict[str, list[tuple[str, Any]]] = {}
        for eid, positions in all_positions.items():
            for pos in positions:
                by_symbol.setdefault(pos.symbol, []).append((eid, pos))

        # Find FF and FR strategies
        ff_strategy: Any = None
        fr_strategy: Any = None
        for sid in self._strategy_manager.list_strategies():
            s = self._strategy_manager.get_strategy(sid)
            if s is None:
                continue
            stype = getattr(s, "STRATEGY_TYPE", "")
            if stype == "futures_futures":
                ff_strategy = s
            elif stype == "funding_rate_arb":
                fr_strategy = s

        synced = 0
        for symbol, pos_list in by_symbol.items():
            longs = [(eid, p) for eid, p in pos_list if p.size > 0]
            shorts = [(eid, p) for eid, p in pos_list if p.size < 0]

            if not longs or not shorts:
                logger.warning(
                    "reconciler.unpaired_position symbol=%s longs=%d shorts=%d — skipping",
                    symbol, len(longs), len(shorts),
                )
                continue

            long_eid, long_pos = longs[0]
            short_eid, short_pos = shorts[0]

            # Conservative: assign to FF (no metadata to distinguish FF from FR)
            if ff_strategy is not None and hasattr(ff_strategy, "inject_position"):
                ff_strategy.inject_position(symbol, {
                    "buy_ex": long_eid,
                    "sell_ex": short_eid,
                    "size": abs(short_pos.size),
                    "entry_time": time.monotonic(),
                })
                synced += 1
                logger.info(
                    "reconciler.positions_synced exchange_long=%s exchange_short=%s "
                    "symbol=%s strategy=futures_futures size=%s",
                    long_eid, short_eid, symbol, abs(short_pos.size),
                )

        if synced:
            logger.info("reconciler.startup_sync_complete synced=%d", synced)
        else:
            logger.info("reconciler.no_positions")

    async def stop(self) -> None:
        """Stop live mode gracefully."""
        if not self._running:
            return

        self._running = False
        logger.info("live_mode.stopping")

        # Cancel background tasks (BUG-87: include dedup/reconciler/margin tasks)
        for task in [
            self._daily_task, self._funding_rate_task, self._krw_rate_task,
            self._dedup_cleanup_task, self._trade_reconciler_task, self._margin_refresh_task,
        ]:
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Drain in-flight settlement exit tasks (fire-and-forget closes that must complete
        # to avoid stranded one-legged positions on exchange shutdown).
        if self._settlement_tasks:
            live_tasks = [t for t in self._settlement_tasks if not t.done()]
            if live_tasks:
                logger.info("live_mode.draining_settlement_tasks count=%d", len(live_tasks))
                _done, _pending = await asyncio.wait(live_tasks, timeout=10.0)
                for t in _pending:
                    logger.warning("live_mode.settlement_task_timeout — cancelling")
                    t.cancel()

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
                # BUG-45: was debug — any FF/SF evaluation exception silently lost
                logger.warning("live_mode.real_signal_producer_error: %s", exc, exc_info=True)

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

        # BUG-18 fix: inject cached margin_available into futures signal metadata.
        # produce_futures_futures_signal() has no adapter access at signal time,
        # so margin_available would always be "0" → margin check silently skipped.
        if signal.strategy_id == "futures_futures_spread" and signal.buy_exchange:
            buy_margin = self._cached_margin.get(signal.buy_exchange) or Decimal("0")
            sell_margin = (
                self._cached_margin.get(signal.sell_exchange) or Decimal("0")
                if signal.sell_exchange else Decimal("0")
            )
            # BUG-82: Both exchanges need margin (BUY long + SELL short).
            # BUG-115: Always inject margin_available (even "0") so futures_futures.py
            # can BLOCK the trade when either side has no margin/is not cached yet.
            # Previously, skipping injection → strategy skipped margin check → uncapped
            # position size → Binance -2019 "Margin is insufficient".
            if signal.sell_exchange:
                effective_margin = min(buy_margin, sell_margin)
            else:
                # Spot-only signal with no sell-side margin requirement
                effective_margin = buy_margin
            signal.metadata["margin_available"] = str(effective_margin)

        trade_requests: list = []
        _routing_succeeded = False
        try:
            trade_requests = await self._strategy_manager.route_signal(signal)
            _routing_succeeded = True
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
            # HIGH-2: only fallback if route_signal() itself failed (routing_succeeded=False).
            # If routing succeeded but execution loop raised, requests were already dispatched —
            # calling _execute_direct_signal would create a duplicate trade.
            if not _routing_succeeded:
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
        logger.info("live_mode.execute_trade_entry strategy=%s legs=%d", sid, len(trade_request.legs))

        # --- Strategy filter allowlist ---
        if self._strategy_filter is not None and sid not in self._strategy_filter:
            # MEDIUM-1: exit/close orders must bypass strategy filter — stuck positions
            # must always be closeable regardless of filter state (same pattern as cooldown).
            _is_exit_filter = self._is_reduceonly_request(trade_request)
            if not _is_exit_filter:
                logger.info("live_mode.strategy_filtered strategy=%s", sid)
                self._notify_pre_exec_rollback(trade_request, sid)
                return
            logger.debug("live_mode.strategy_filter_bypassed_exit strategy=%s", sid)

        # --- Strategy loss cooldown (US-164) ---
        if sid in self._strategy_disable_until:
            if time.monotonic() < self._strategy_disable_until[sid]:
                # CRITICAL: exit/close orders bypass cooldown — stuck positions must
                # be closeable regardless of loss cooldown state. Only block new entries.
                _is_exit_req = self._is_reduceonly_request(trade_request)
                if not _is_exit_req:
                    logger.debug("live_mode.strategy_cooldown strategy=%s", sid)
                    self._notify_pre_exec_rollback(trade_request, sid)
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
                self._notify_pre_exec_rollback(trade_request, sid)
                return

        # --- Circuit breaker check ---
        if self._circuit_breaker is not None:
            try:
                if hasattr(self._circuit_breaker, 'is_open') and self._circuit_breaker.is_open():
                    logger.warning("live_mode.circuit_breaker_open — skipping trade strategy=%s", sid)
                    self._notify_pre_exec_rollback(trade_request, sid)
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
                    self._notify_pre_exec_rollback(trade_request, sid)
                    return

        # --- FlashGuard check ---
        if self._flash_guard is not None:
            try:
                if hasattr(self._flash_guard, 'check'):
                    blocked = self._flash_guard.check(trade_request)
                    if blocked:
                        logger.warning("live_mode.flash_guard_blocked strategy=%s", sid)
                        self._notify_pre_exec_rollback(trade_request, sid)
                        return
            except Exception as exc:
                logger.debug("live_mode.flash_guard_check_error: %s", exc)

        # --- Session loss hard stop (max_daily_loss_pct from engine.json) ---
        # Fires KillSwitch halt when cumulative session PnL exceeds the loss limit.
        # CB auto-recovers after 5min but this check requires manual clear_halt() to resume.
        _session_loss = -self._stats.total_pnl  # positive means loss
        if _session_loss >= self._max_session_loss_usd:
            from src.risk.kill_switch import halt_local
            halt_local()
            logger.critical(
                "live_mode.session_loss_limit_exceeded loss=%.2f limit=%.2f — HALT",
                _session_loss, self._max_session_loss_usd,
            )
            if self._telegram is not None:
                try:
                    await self._telegram.send_alert(
                        f"🚨 SESSION LOSS LIMIT: ${_session_loss:.2f} >= ${self._max_session_loss_usd:.2f} — ENGINE HALTED"
                    )
                except Exception:
                    pass
            self._stats.trades_risk_blocked += 1
            self._notify_pre_exec_rollback(trade_request, sid)
            return

        # --- Risk guardian check ---
        if self._risk_guardian is not None:
            try:
                approved = True
                if hasattr(self._risk_guardian, 'check_trade_request'):
                    approved = self._risk_guardian.check_trade_request(
                        trade_request, self._total_capital_usd
                    )
                elif hasattr(self._risk_guardian, 'approve'):
                    approved = self._risk_guardian.approve(trade_request)
                if not approved:
                    self._stats.trades_risk_blocked += 1
                    strat_stats.rejections += 1
                    logger.info("live_mode.risk_rejected strategy=%s", sid)
                    self._notify_pre_exec_rollback(trade_request, sid)
                    return
            except Exception as exc:
                logger.warning("live_mode.risk_check_error: %s", exc)

        # --- Per-symbol cooldown (v7: prevent same-symbol burst) ---
        # BUG-36: skip cooldown for close/exit orders so rapid exits aren't blocked
        # FIX: check ALL leg symbols — spot_futures has different symbols per leg
        # (legs[0]=spot, legs[1]=futures); only checking legs[0] left futures leg
        # unprotected after ROLLBACK_FAILED cooldown was set on both legs.
        # HIGH-1: use all() (consistent with trade_consumer + line 894). any() would
        # bypass cooldown for mixed orders where only one leg is reduceOnly.
        _is_close_req = self._is_reduceonly_request(trade_request)
        _sym_keys = [l.symbol for l in trade_request.legs if l.symbol]
        if _sym_keys and not _is_close_req:
            _now = time.monotonic()
            for _sk in _sym_keys:
                _last = self._symbol_last_trade.get(_sk, 0.0)
                if _now - _last < self._symbol_cooldown_s:
                    logger.debug(
                        "live_mode.symbol_cooldown symbol=%s cooldown_s=%.0f",
                        _sk, self._symbol_cooldown_s,
                    )
                    self._notify_pre_exec_rollback(trade_request, sid)
                    return
            for _sk in _sym_keys:
                self._symbol_last_trade[_sk] = _now

        # --- BUG-74: Margin guard — block new ENTRY trades on margin-exhausted futures exchanges ---
        # Prevents -2019 retry loops (e.g. FR 0G/USDT 185+ rollbacks when Binance margin < $1).
        # Reduce-only (exit) trades are exempt: they don't consume margin.
        # BUG-78: Do NOT call _notify_pre_exec_rollback — that clears the strategy's
        # _open_positions entry, allowing the same symbol to pass duplicate_guard and
        # generate a new TradeRequest on the next signal (hot retry loop, JTO 12+x in v95).
        # Instead, leave the phantom entry in _open_positions as a soft block.
        # It will be cleaned at next settlement (_check_settlement_release clears all).
        if not _is_close_req:
            for _leg in trade_request.legs:
                if _leg.exchange_id and "futures" in _leg.exchange_id:
                    _cached = float(self._cached_margin.get(_leg.exchange_id, float("inf")))
                    if _cached < self._MIN_MARGIN_ENTRY_USD:
                        logger.warning(
                            "live_mode.entry_blocked_margin_low ex=%s margin=%.2f < %.2f",
                            _leg.exchange_id, _cached, self._MIN_MARGIN_ENTRY_USD,
                        )
                        self._stats.trades_margin_blocked += 1
                        return

        # --- Collision detection (DeduplicationGate: atomic check-and-register) ---
        collision_key = self._build_collision_key(trade_request)
        if not await self._dedup_gate.check_and_register(collision_key):
            # BUG-79: For close/exit orders, dedup block means the first exit is still
            # in flight and has already moved the position to _pending_exits.
            # Calling _notify_pre_exec_rollback would erroneously restore the position
            # from _pending_exits → thrash loop. Only notify rollback for entry orders.
            # HIGH-1: use all() consistent with line 894 and trade_consumer.
            _is_close_req = self._is_reduceonly_request(trade_request)
            if _is_close_req:
                logger.warning("live_mode.dedup_blocked_close key=%s — first exit still in flight", collision_key)
            else:
                logger.debug("live_mode.dedup_blocked key=%s", collision_key)
                self._notify_pre_exec_rollback(trade_request, sid)
            return

        # --- Execute via DI executor (v7: global semaphore — max 2 concurrent) ---
        await self._trade_semaphore.acquire()
        try:
            # PHOENIX: Filter trades where any leg notional < min (config-driven)
            # Prevents imbalanced positions from per-adapter leg-level adjustments.
            from src.core.config_loader import get_config
            _MIN_TRADE_NOTIONAL = Decimal(str(get_config("execution.min_trade_notional_usd") or 5))
            _USD_QUOTES = {"USDT", "USDC", "USD", "BUSD", "DAI"}
            _small_legs = [
                leg for leg in trade_request.legs
                if leg.price and leg.price > 0
                and leg.symbol.split("/")[-1].upper() in _USD_QUOTES
                and leg.size * leg.price < _MIN_TRADE_NOTIONAL
            ]
            if _small_legs:
                logger.debug(
                    "live_mode.min_notional_filtered strategy=%s small_legs=%d max_notional=%.2f",
                    sid, len(_small_legs),
                    float(max(l.size * l.price for l in _small_legs if l.price)),
                )
                self._notify_pre_exec_rollback(trade_request, sid)
                return

            orders = self._legs_to_orders(trade_request)
            if not orders:
                logger.warning("live_mode.no_valid_orders strategy=%s", sid)
                self._notify_pre_exec_rollback(trade_request, sid)
                return

            # --- PRE-TRADE: BookWalk market impact check (live mode only) ---
            # Walks the real L2 orderbook to estimate VWAP fill price before
            # submitting a MARKET order. Rejects if estimated slippage exceeds
            # max_market_impact_bps. Exit (reduceOnly) orders are exempt.
            if self._execution_mode == "live" and not _is_close_req:
                from src.core.config_loader import get_config as _gc_impact
                _max_impact_bps = float(_gc_impact("strategy_filters.max_market_impact_bps", default=20))
                _impact_rejected = False
                for _ord in orders:
                    _book = self._books.get(_ord.symbol, {}).get(_ord.exchange_id)
                    if _book is None or not hasattr(_book, "vwap_walk") or not hasattr(_book, "best_bid"):
                        continue  # no book data — let executor handle it
                    _mid_bid = _book.best_bid()
                    _mid_ask = _book.best_ask()
                    if not _mid_bid or not _mid_ask or _mid_bid <= 0:
                        continue
                    _mid = (_mid_bid + _mid_ask) / Decimal("2")
                    _walk_side = "buy" if _ord.side == OrderSide.BUY else "sell"
                    _vwap, _filled = _book.vwap_walk(_walk_side, _ord.amount)
                    if _filled <= 0 or _vwap <= 0:
                        continue
                    _impact_bps = float(abs(_vwap - _mid) / _mid * Decimal("10000"))
                    if _impact_bps > _max_impact_bps:
                        logger.warning(
                            "live_mode.market_impact_rejected strategy=%s exchange=%s "
                            "symbol=%s side=%s impact_bps=%.1f > max=%.1f vwap=%.6f mid=%.6f",
                            sid, _ord.exchange_id, _ord.symbol, _walk_side,
                            _impact_bps, _max_impact_bps, float(_vwap), float(_mid),
                        )
                        _impact_rejected = True
                        break
                if _impact_rejected:
                    self._notify_pre_exec_rollback(trade_request, sid)
                    return

            try:
                exec_result = await self._route_to_executor(trade_request, orders)
            except Exception as _exec_exc:
                # Unhandled executor exception: notify strategy to clear _open_positions
                # so a phantom position record doesn't block re-entry for 30min (BUG-MAJOR-15).
                logger.error(
                    "live_mode.executor_unhandled_error strategy=%s error=%s — notifying rollback",
                    sid, _exec_exc,
                )
                self._notify_pre_exec_rollback(trade_request, sid)
                return

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
                    # Bug 25: ROLLBACK_FAILED = stranded position risk → urgent Telegram alert
                    if exec_result.status == ExecutionStatus.ROLLBACK_FAILED and self._telegram is not None:
                        try:
                            await self._telegram.send_alert_kr("rollback_failed", {
                                "strategy": sid,
                                "status": str(exec_result.status),
                                "mode": self._execution_mode,
                            })
                        except Exception:
                            pass
                    # DEFENSE-IN-DEPTH: ROLLBACK_FAILED = stranded position on exchange.
                    # BUG-84's strategy-level guard prevents re-entry via _open_positions, but
                    # set LiveMode-level symbol cooldown as backup (e.g. on strategy restart).
                    # Cover ALL legs — spot_futures has legs[0]=spot, legs[1]=futures with
                    # different symbols; only cooling legs[0] leaves the futures leg unprotected.
                    if exec_result.status == ExecutionStatus.ROLLBACK_FAILED:
                        _rf_now = time.monotonic()
                        _rf_symbols = [l.symbol for l in trade_request.legs if l.symbol]
                        for _rf_sym in _rf_symbols:
                            self._symbol_last_trade[_rf_sym] = _rf_now
                        if _rf_symbols:
                            logger.warning(
                                "live_mode.rollback_failed_cooldown_set symbols=%s cooldown_s=%.0f",
                                _rf_symbols, self._symbol_cooldown_s,
                            )
                    # HIGH-1: ROLLBACK_FAILED on EXIT order → restore _pending_exits snapshot.
                    # BUG-84 prohibition applies to ENTRY only (stranded entry = don't re-enter).
                    # Exit ROLLBACK_FAILED = partial exchange close failed, but internal tracking
                    # already moved pos to _pending_exits/_open_positions cleared. Must restore.
                    if exec_result.status == ExecutionStatus.ROLLBACK_FAILED:
                        _is_exit_rf = self._is_reduceonly_request(trade_request)
                        if _is_exit_rf and self._strategy_manager is not None:
                            _strat_rf = self._strategy_manager.get_strategy(sid)
                            # Only call for strategies with _pending_exits tracking (futures_futures).
                            # spot_futures/_funding_rate docstrings say "ROLLBACK_FAILED: do not call"
                            # and lack _pending_exits, so calling on them violates the contract.
                            if _strat_rf is not None:
                                for _rf_sym_ex in {l.symbol for l in trade_request.legs if l.symbol}:
                                    try:
                                        # WS-2.6: Exit rollback = position still on exchange → restore
                                        _strat_rf.handle_exit_rollback(_rf_sym_ex)
                                        logger.warning(
                                            "live_mode.exit_rollback_failed_pending_restored "
                                            "symbol=%s — stranded! verify exchange positions.",
                                            _rf_sym_ex,
                                        )
                                    except Exception as _ex_rb_err:
                                        logger.error(
                                            "live_mode.exit_rollback_restore_error symbol=%s err=%s",
                                            _rf_sym_ex, _ex_rb_err,
                                        )
                    # BUG-2 fix: notify strategy on successful rollback so it clears
                    # _open_positions and allows re-entry (prevents 30min lockout)
                    # BUG-31: also clear on REJECTED (no orders placed, pre-validation failed)
                    # BUG-84: ROLLBACK_FAILED must NOT notify for ENTRY — a stranded entry position
                    # exists and clearing _open_positions would allow duplicate re-entry.
                    if exec_result.status in (ExecutionStatus.ROLLED_BACK, ExecutionStatus.REJECTED):
                        # HIGH-3: iterate ALL leg symbols, not just legs[0].
                        # For cross-exchange arb legs can have different symbols (e.g. spot_futures).
                        # Clearing only legs[0] leaves legs[1] locked in _open_positions for 30min.
                        _rb_syms = {leg.symbol for leg in trade_request.legs if leg.symbol}
                        if _rb_syms and self._strategy_manager is not None:
                            _strat = self._strategy_manager.get_strategy(sid)
                            if _strat is not None:
                                for _rb_sym in _rb_syms:
                                    try:
                                        # WS-2.6: Entry rollback = position never opened → clear
                                        _strat.handle_entry_rollback(_rb_sym)
                                    except Exception as _rb_err:
                                        logger.warning(
                                            "live_mode.rollback_notify_failed strategy=%s symbol=%s err=%s",
                                            sid, _rb_sym, _rb_err,
                                        )
                        # HIGH: post-rollback re-entry race prevention.
                        # After ROLLED_BACK, the exchange is processing an unwind order.
                        # Reset per-symbol cooldown so the strategy cannot re-enter
                        # the same symbol until exchange unwind has settled (cooldown_s).
                        # REJECTED has no exchange activity → no cooldown needed.
                        # Asymmetry note: ROLLED_BACK uses legs[0].symbol only (unwind is
                        # a single-leg cancel/reverse on the filled leg). ROLLBACK_FAILED
                        # (above) covers ALL legs because both legs may be stranded.
                        if exec_result.status == ExecutionStatus.ROLLED_BACK and trade_request.legs:
                            _rb_sym0 = trade_request.legs[0].symbol
                            if _rb_sym0:
                                self._symbol_last_trade[_rb_sym0] = time.monotonic()
                                logger.debug(
                                    "live_mode.rollback_cooldown_set symbol=%s cooldown_s=%.0f",
                                    _rb_sym0, self._symbol_cooldown_s,
                                )
                    strat_stats.rejections += 1
                    return

            # --- Notify strategy of successful execution (BUG-80: clean _pending_exits) ---
            _success_symbol = trade_request.legs[0].symbol if trade_request.legs else None
            if _success_symbol and self._strategy_manager is not None:
                _strat_s = self._strategy_manager.get_strategy(sid)
                if _strat_s is not None and hasattr(_strat_s, "on_execution_success"):
                    try:
                        _strat_s.on_execution_success(_success_symbol)
                    except Exception as _se_err:
                        logger.debug("live_mode.success_notify_failed strategy=%s err=%s", sid, _se_err)

            # --- Record trade result ---
            self._stats.trades_executed += 1
            strat_stats.trades += 1

            # Compute PnL from ACTUAL fill prices (not estimates)
            pnl = self._compute_pnl_from_result(exec_result, trade_request)

            # Extract actual fill prices + fees from exec_result for recording
            _buy_fill_price: Decimal | None = None
            _sell_fill_price: Decimal | None = None
            _buy_fill_from_result: bool = False  # True only if price came from actual executor fill
            _sell_fill_from_result: bool = False
            _fee_total: Decimal = Decimal("0")
            if exec_result is not None and hasattr(exec_result, 'legs'):
                for _lr in exec_result.legs:
                    _t = getattr(_lr, 'trade', None)
                    if _t is None:
                        continue
                    # BUG-67: LegResult has no .side attr — use .order.side
                    _lr_order = getattr(_lr, 'order', None)
                    _lr_side = getattr(_lr_order, 'side', None)
                    _fee_total += Decimal(str(getattr(_t, 'fee', 0) or 0))
                    # BUG-37: Bitget market order polling may return price=0 on miss.
                    # Only accept fill price > 0 as a real fill; 0 falls through to fallback.
                    _raw_price = Decimal(str(_t.price or 0))
                    if _lr_side == OrderSide.SELL:
                        if _raw_price > 0:
                            _sell_fill_price = _raw_price
                            _sell_fill_from_result = True
                    else:
                        if _raw_price > 0:
                            _buy_fill_price = _raw_price
                            _buy_fill_from_result = True
            # Fallback to request leg prices (used for PnL/spread only, NOT for IS calculation)
            if not _buy_fill_price:
                _bl = [l for l in (trade_request.legs or []) if l.side == OrderSide.BUY]
                _buy_fill_price = _bl[0].price if _bl and _bl[0].price else None
            if not _sell_fill_price:
                _sl = [l for l in (trade_request.legs or []) if l.side == OrderSide.SELL]
                _sell_fill_price = _sl[0].price if _sl and _sl[0].price else None
            # Gross spread bps from signal or fill prices
            _signal = getattr(trade_request, 'signal', None)
            _spread_bps_val: float = 0.0
            if _signal is not None and hasattr(_signal, 'spread_pct') and _signal.spread_pct:
                _spread_bps_val = float(_signal.spread_pct) * 10000
            elif _buy_fill_price and _sell_fill_price and _buy_fill_price > 0:
                _spread_bps_val = float((_sell_fill_price - _buy_fill_price) / _buy_fill_price * 10000)
            _gross_spread_bps: Decimal | None = (
                Decimal(str(round(_spread_bps_val, 4))) if _spread_bps_val else None
            )

            # US-056: Record first live trade
            if not self._first_trade_recorded and self._execution_mode == "live":
                self._record_first_trade(trade_request, float(pnl))
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

            # Estimate IS (implementation shortfall) in bps from fill prices vs expected
            _slippage_bps_est: float = 0.0
            if trade_request.legs:
                _exp_buy = next((l.price for l in trade_request.legs if l.side == OrderSide.BUY and l.price), None)
                _exp_sell = next((l.price for l in trade_request.legs if l.side == OrderSide.SELL and l.price), None)
                _is_parts: list[float] = []
                if _buy_fill_from_result and _buy_fill_price and _exp_buy and _exp_buy > 0:
                    _is_parts.append(float(abs(_buy_fill_price - _exp_buy) / _exp_buy * 10000))
                if _sell_fill_from_result and _sell_fill_price and _exp_sell and _exp_sell > 0:
                    _is_parts.append(float(abs(_sell_fill_price - _exp_sell) / _exp_sell * 10000))
                if _is_parts:
                    _slippage_bps_est = sum(_is_parts)

            # Telegram alert for fills (send_fill_enhanced — shadow/paper와 동일 포맷)
            if self._telegram is not None:
                try:
                    _first_leg = trade_request.legs[0] if trade_request.legs else None
                    _buy_ex_tg = next((l.exchange_id for l in trade_request.legs if l.side == OrderSide.BUY), "unknown")
                    _sell_ex_tg = next((l.exchange_id for l in trade_request.legs if l.side == OrderSide.SELL), "unknown")
                    _mode_label = "🔴 [LIVE]" if self._execution_mode == "live" else "🟢 [PAPER]"
                    await self._telegram.send_fill_enhanced({
                        "mode": _mode_label,
                        "strategy": sid,
                        "symbol": _first_leg.symbol if _first_leg else "unknown",
                        "buy_exchange": _buy_ex_tg,
                        "sell_exchange": _sell_ex_tg,
                        "pnl": float(pnl),
                        "spread_bps": _spread_bps_val,
                        "fee": float(_fee_total),
                        "slippage_bps": _slippage_bps_est,
                        "latency_ms": int((time.monotonic() - t0) * 1000),
                    })
                except Exception:
                    pass

            # Publish to Redis for dashboard (non-critical)
            await self._publish_trade_for_observability(trade_record)

            # Record execution to TimescaleDB (US-358) ⚡ WIRING 호출
            if self._market_recorder is not None and trade_request.legs:
                try:
                    buy_legs = [l for l in trade_request.legs if l.side == OrderSide.BUY]
                    sell_legs = [l for l in trade_request.legs if l.side == OrderSide.SELL]
                    first_leg = trade_request.legs[0]
                    # IS (Implementation Shortfall) 계산: fill price vs expected leg price
                    _expected_buy = buy_legs[0].price if buy_legs and buy_legs[0].price else None
                    _expected_sell = sell_legs[0].price if sell_legs and sell_legs[0].price else None
                    _is_buy_bps: Decimal | None = None
                    _is_sell_bps: Decimal | None = None
                    # Only compute IS when fill price came from actual executor result.
                    # Fallback prices equal expected prices → IS=0 which is misleadingly accurate.
                    if _buy_fill_from_result and _buy_fill_price and _expected_buy and _expected_buy > 0:
                        _is_buy_bps = abs(_buy_fill_price - _expected_buy) / _expected_buy * Decimal("10000")
                    if _sell_fill_from_result and _sell_fill_price and _expected_sell and _expected_sell > 0:
                        _is_sell_bps = abs(_sell_fill_price - _expected_sell) / _expected_sell * Decimal("10000")
                    # IS total is only meaningful when BOTH legs have actual fill prices.
                    # When only one leg fills (partial fill / rollback), recording buy+0
                    # would understate true IS — emit None to flag as incomplete.
                    _is_total_bps: Decimal | None = (
                        _is_buy_bps + _is_sell_bps
                        if (_is_buy_bps is not None and _is_sell_bps is not None) else None
                    )
                    self._market_recorder.record_execution(
                        strategy_id=sid,
                        buy_exchange=buy_legs[0].exchange_id if buy_legs else "unknown",
                        sell_exchange=sell_legs[0].exchange_id if sell_legs else "unknown",
                        symbol=first_leg.symbol,
                        buy_price=_buy_fill_price or (buy_legs[0].price if buy_legs and buy_legs[0].price else Decimal("0")),
                        sell_price=_sell_fill_price or (sell_legs[0].price if sell_legs and sell_legs[0].price else Decimal("0")),
                        size=first_leg.size,
                        net_pnl=pnl,
                        gross_spread_bps=_gross_spread_bps,
                        fee_total=_fee_total if _fee_total > 0 else None,
                        slippage_total=_is_total_bps,
                        mode=self._execution_mode,
                        status="filled",
                    )
                    # TCAAnalyzer: feed IS + latency data for real-time percentile tracking (US-116)
                    # Lives inside the try block so _expected_buy is guaranteed to be defined.
                    _latency_ms = (time.monotonic() - t0) * 1000
                    _signal_ts = trade_request.metadata.get("signal_ts", 0.0) if trade_request.metadata else 0.0
                    _fill_ts = time.time()
                    if self._tca_analyzer is not None and _buy_fill_price and _expected_buy and _expected_buy > 0:
                        self._tca_analyzer.record_execution(
                            expected_price=float(_expected_buy),
                            fill_price=float(_buy_fill_price),
                            latency_ms=_latency_ms,
                            filled_ratio=1.0,
                            strategy_id=sid,
                            signal_ts=_signal_ts,
                            fill_ts=_fill_ts,
                        )
                    # Sell-side TCA recording (was missing — only buy side tracked)
                    if self._tca_analyzer is not None and _sell_fill_price and _expected_sell and _expected_sell > 0:
                        self._tca_analyzer.record_execution(
                            expected_price=float(_expected_sell),
                            fill_price=float(_sell_fill_price),
                            latency_ms=_latency_ms,
                            filled_ratio=1.0,
                            strategy_id=sid,
                            signal_ts=_signal_ts,
                            fill_ts=_fill_ts,
                        )
                except Exception as exc:
                    logger.debug("live_mode.record_execution_failed error=%s", exc)

            # US-283: SlippageFeedbackCollector — record predicted vs actual slippage per leg
            if self._slippage_feedback_collector is not None and trade_request.legs:
                try:
                    for _fb_leg in trade_request.legs:
                        _fb_expected = float(_fb_leg.price) if _fb_leg.price else 0.0
                        if _fb_leg.side == OrderSide.BUY and _buy_fill_from_result and _buy_fill_price and _fb_expected > 0:
                            _pred_bps = 0.0  # predicted slippage (pre-trade estimate was 0 without feedback)
                            _act_bps = abs(float(_buy_fill_price) - _fb_expected) / _fb_expected * 10000
                            self._slippage_feedback_collector.record(
                                exchange=_fb_leg.exchange_id, pair=_fb_leg.symbol,
                                predicted_bps=_pred_bps, actual_bps=_act_bps,
                            )
                        elif _fb_leg.side == OrderSide.SELL and _sell_fill_from_result and _sell_fill_price and _fb_expected > 0:
                            _pred_bps = 0.0
                            _act_bps = abs(float(_sell_fill_price) - _fb_expected) / _fb_expected * 10000
                            self._slippage_feedback_collector.record(
                                exchange=_fb_leg.exchange_id, pair=_fb_leg.symbol,
                                predicted_bps=_pred_bps, actual_bps=_act_bps,
                            )
                except Exception as _fb_exc:
                    logger.debug("live_mode.slippage_feedback_failed error=%s", _fb_exc)

            # TCA: expected vs actual PnL comparison — critical monitoring for profit leakage
            _expected_profit = float(trade_request.expected_profit_usdt)
            _actual_pnl = float(pnl)
            _pnl_slippage_usd = _expected_profit - _actual_pnl
            logger.info(
                "live_mode.tca_pnl_compare strategy=%s expected=%.4f actual=%.4f "
                "slippage_usd=%.4f slippage_bps=%.1f latency_ms=%.1f",
                sid, _expected_profit, _actual_pnl, _pnl_slippage_usd,
                _slippage_bps_est, (time.monotonic() - t0) * 1000,
            )

            # Slippage > Alpha auto-kill: track cumulative slippage and halt strategy.
            # BUG-89: Only apply to spread-arb strategies (FF, cross_exchange).
            # BUG-90: Exclude ROLLED_BACK/REJECTED trades — rollback is defensive, not slippage.
            # Edge_evaporated rollback costs were inflating cumulative slippage → false halt.
            _is_spread_arb_strategy = "futures_futures" in sid or "cross_exchange" in sid
            _is_successful_trade = exec_result is None or (
                hasattr(exec_result, 'status') and str(exec_result.status) not in ('rolled_back', 'rejected', 'rollback_failed')
            )
            if _slippage_bps_est > 0 and _is_spread_arb_strategy and _is_successful_trade:
                if sid not in self._strategy_slippage_window:
                    self._strategy_slippage_window[sid] = deque(
                        maxlen=self._slippage_window_trades,
                    )
                self._strategy_slippage_window[sid].append(_slippage_bps_est)
                _cum_slip = sum(self._strategy_slippage_window[sid])
                if _cum_slip > self._max_cumulative_slippage_bps:
                    logger.critical(
                        "live_mode.slippage_exceeds_alpha strategy=%s cumulative_bps=%.1f "
                        "threshold=%.1f window=%d — HALTING STRATEGY",
                        sid, _cum_slip, self._max_cumulative_slippage_bps,
                        len(self._strategy_slippage_window[sid]),
                    )
                    if (
                        self._risk_guardian is not None
                        and hasattr(self._risk_guardian, "per_strategy_cb")
                    ):
                        _pscb = self._risk_guardian.per_strategy_cb
                        if _pscb is not None and hasattr(_pscb, "force_halt"):
                            _pscb.force_halt(
                                sid,
                                reason=f"cumulative_slippage_{_cum_slip:.1f}bps",
                            )

            logger.info(
                "live_mode.trade_executed strategy=%s pnl=%.4f total_pnl=%.2f mode=%s latency_ms=%.1f",
                sid, float(pnl), self._stats.total_pnl,
                self._execution_mode, (time.monotonic() - t0) * 1000,
            )

        except Exception as exc:
            logger.error("live_mode.execution_failed strategy=%s error=%s", sid, exc, exc_info=True)
            if LIVE_TRADES_TOTAL is not None:
                LIVE_TRADES_TOTAL.labels(strategy=sid, result="error").inc()
        finally:
            self._trade_semaphore.release()

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
                    order_type=OrderType.MARKET,
                ),
                TradeLeg(
                    exchange_id=signal.sell_exchange,
                    symbol=signal.symbol,
                    side=OrderSide.SELL,
                    size=signal.volume or Decimal("0.001"),
                    price=signal.sell_price,
                    order_type=OrderType.MARKET,
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

    @staticmethod
    def _is_reduceonly_request(trade_request: TradeRequest) -> bool:
        """Return True if all legs are reduceOnly (exit/close order).

        Used to bypass strategy_filter, cooldown, and dedup restrictions
        that must not block position-close orders.
        """
        return bool(trade_request.legs) and all(
            leg.metadata.get("reduceOnly") for leg in trade_request.legs
        )

    def _legs_to_orders(self, trade_request: TradeRequest) -> list[Order]:
        """Convert TradeRequest legs to Order objects.

        Limit order fallback: when orderbook spread > limit_fallback_spread_bps,
        convert MARKET orders to LIMIT at mid-price to avoid adverse fills.
        """
        orders = []
        for leg in trade_request.legs:
            price = leg.price or Decimal("0")
            otype = leg.order_type
            if price <= 0:
                logger.debug(
                    "live_mode.leg_market_order exchange=%s symbol=%s (price=None → market order, expected)",
                    leg.exchange_id, leg.symbol,
                )
            # Limit order fallback: wide spread → LIMIT at mid-price
            if otype == OrderType.MARKET and price > 0:
                book = self._books.get(leg.symbol, {}).get(leg.exchange_id)
                if book is not None:
                    try:
                        _bb = book.best_bid()
                        _ba = book.best_ask()
                        if _bb and _ba:
                            _bid_d = Decimal(str(_bb))
                            _ask_d = Decimal(str(_ba))
                            if _bid_d > 0 and _ask_d > 0:
                                _sp_bps = float((_ask_d - _bid_d) / _bid_d * 10000)
                                if _sp_bps > self._limit_fallback_spread_bps:
                                    mid = (_bid_d + _ask_d) / 2
                                    otype = OrderType.LIMIT
                                    price = mid
                                    logger.info(
                                        "live_mode.limit_fallback exchange=%s symbol=%s "
                                        "spread_bps=%.1f > %.1f — LIMIT@%.6f",
                                        leg.exchange_id, leg.symbol,
                                        _sp_bps, self._limit_fallback_spread_bps,
                                        float(mid),
                                    )
                    except Exception:
                        pass  # orderbook API error — keep original MARKET
            orders.append(Order(
                order_id=str(uuid.uuid4()),
                exchange_id=leg.exchange_id,
                symbol=leg.symbol,
                side=leg.side,
                order_type=otype,
                price=price,
                amount=leg.size,
                metadata=leg.metadata or {},
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
            # Cross-exchange — edge check only for spread-arb strategies (FF).
            # FR (funding_rate) is carry trade: profits from funding rate diff, not spread.
            # Edge check would incorrectly block FR entries when spread is negative.
            from src.core.config_loader import get_config as _gc_edge
            _is_spread_arb = "futures_futures" in sid
            _min_edge = (
                Decimal(str(_gc_edge("strategy_filters.futures_min_edge_bps", default=10))) / Decimal("10000")
                if _is_spread_arb else Decimal("0")
            )
            return await self._executor.execute_cross_exchange(
                leg1_order=orders[0],
                leg2_order=orders[1],
                strategy_id=sid,
                min_edge=_min_edge,
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
                # BUG-92: execute_same_exchange(order, order) doubles the position.
                # execute_multi_leg always available on AtomicExecutor — use it.
                if hasattr(self._executor, 'execute_multi_leg'):
                    result = await self._executor.execute_multi_leg(
                        exchange_id=order.exchange_id,
                        orders=[order],
                        strategy_id=sid,
                    )
                else:
                    logger.error(
                        "live_mode.fallback_no_multi_leg_executor exchange=%s strategy=%s — skipping leg",
                        order.exchange_id, sid,
                    )
                    continue
                results.append(result)
            return results

    def _notify_pre_exec_rollback(self, trade_request: TradeRequest, sid: str) -> None:
        """Pre-execution guard rollback: clear optimistic _open_positions on early return.

        FuturesFutures (and other strategies) may set _open_positions[symbol] inside
        evaluate() before returning the TradeRequest. If any pre-execution guard rejects
        the trade, we must notify the strategy to clear that entry — otherwise the symbol
        is locked out for max_hold_seconds.

        BUG-36 follow-up: notify ALL leg symbols (not just legs[0]) so spot_futures
        (which has different symbols on each leg) fully clears both leg positions.
        """
        if not trade_request.legs or self._strategy_manager is None:
            return
        _strat = self._strategy_manager.get_strategy(sid)
        if _strat is None:
            return
        _syms = {leg.symbol for leg in trade_request.legs if leg.symbol}
        for _sym in _syms:
            try:
                # WS-2.6: Pre-exec rejection = entry never happened → clear
                _strat.handle_entry_rollback(_sym)
            except Exception as _e:
                logger.debug("live_mode.pre_exec_rollback_notify_failed strategy=%s sym=%s err=%s", sid, _sym, _e)

    def _build_collision_key(self, trade_request: TradeRequest) -> str:
        """Build collision detection key from trade request.

        BUG-35: differentiate entry vs exit so close orders aren't blocked
        by the 10-second dedup window of the preceding entry.
        """
        symbols = sorted({leg.symbol for leg in trade_request.legs})
        exchanges = sorted({leg.exchange_id for leg in trade_request.legs})
        # BUG-75/HIGH-1: use all() consistent with executor.py — a mixed-leg trade
        # with only one reduceOnly leg must not be misclassified as close.
        # Guard bool(legs): all() on empty iterable returns True (misclassifies as close).
        _is_close = self._is_reduceonly_request(trade_request)
        suffix = ":close" if _is_close else ":open"
        # Include strategy_id so FF/FR don't block each other for the same symbol
        strategy = getattr(trade_request, "strategy_id", "")
        return f"{strategy}:{','.join(symbols)}|{','.join(exchanges)}{suffix}"

    def _record_first_trade(self, trade_request: TradeRequest, pnl: float) -> None:
        """Save first live trade to .omc/state/live-first-trade.json — US-056."""
        import json  # noqa: PLC0415
        import pathlib  # noqa: PLC0415
        self._first_trade_recorded = True
        try:
            _state_dir = pathlib.Path(__file__).resolve().parents[3] / ".omc" / "state"
            _state_dir.mkdir(parents=True, exist_ok=True)
            first_leg = trade_request.legs[0] if trade_request.legs else None
            record = {
                "exchange": first_leg.exchange_id if first_leg else "",
                "strategy": trade_request.strategy_id or "",
                "side": first_leg.side.value if first_leg else "",
                "qty": float(first_leg.size) if first_leg else 0.0,
                "price": float(first_leg.price) if first_leg and first_leg.price else 0.0,
                "pnl_usd": pnl,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            (_state_dir / "live-first-trade.json").write_text(
                json.dumps(record, indent=2)
            )
            logger.info(
                "live_mode.first_trade_recorded exchange=%s strategy=%s pnl=%.4f",
                record["exchange"], record["strategy"], pnl,
            )
        except Exception as exc:
            logger.warning("live_mode.first_trade_record_failed: %s", exc)

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
            has_zero_price_leg = False
            for leg_result in exec_result.legs:
                trade = getattr(leg_result, 'trade', None)
                if trade is None:
                    continue
                has_trades = True
                fill_price = Decimal(str(trade.price))
                if fill_price <= Decimal("0"):
                    # Ghost fill or market order with unknown fill price — cannot compute
                    # meaningful PnL from this leg. Fall through to expected_profit_usdt.
                    logger.warning(
                        "live_mode.zero_price_leg_pnl_abort strategy=%s symbol=%s expected_profit=%s",
                        getattr(trade_request, "strategy_id", "unknown"),
                        getattr(getattr(leg_result, "order", None), "symbol", "unknown"),
                        trade_request.expected_profit_usdt,
                    )
                    has_zero_price_leg = True
                    break
                fill_amount = Decimal(str(trade.amount))
                fill_fee = Decimal(str(getattr(trade, 'fee', 0)))
                notional = fill_price * fill_amount
                # MEDIUM-2: LegResult has no .side attr (BUG-67). Use .order.side instead.
                side = getattr(getattr(leg_result, 'order', None), 'side', None) or getattr(trade, 'side', None)
                side_str = str(side).upper() if side else ""

                if "SELL" in side_str:
                    net_pnl += notional - fill_fee
                else:
                    net_pnl -= notional + fill_fee

            if has_trades and not has_zero_price_leg:
                return net_pnl
            if has_zero_price_leg:
                # Return expected profit from trade request (0 for exits) rather than
                # a misleading fill-price-based PnL that inflates to full position value.
                return trade_request.expected_profit_usdt

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
        interval = get_settings().operational.funding_rate_interval_s
        try:
            while self._running:
                try:
                    if self._funding_rate_collector is not None:
                        rates = await self._funding_rate_collector.poll_once()
                        logger.info(
                            "live_mode.fr_loop_poll rates_exchanges=%s rates_empty=%s rsp_is_none=%s",
                            list(rates.keys()) if rates else "None",
                            not bool(rates),
                            rates is None,
                        )
                        if self._real_signal_producer is not None and rates:
                            # Convert FundingRateEntry → float for RealDataSignalProducer
                            float_rates = {
                                ex: {sym: e.rate for sym, e in syms.items()}
                                for ex, syms in rates.items()
                            }
                            fr_signals = await self._real_signal_producer.on_funding_rates_updated(
                                float_rates, self._books
                            )
                            for _fr_sig in (fr_signals or []):
                                if self._strategy_manager is not None:
                                    await self._route_signal_to_strategies(_fr_sig)
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
                                    self._krw_fail_count = 0  # BUG-88: reset on success
                                    logger.debug("live_mode.krw_rate_updated rate=%.2f", new_rate)
                        else:
                            logger.debug("live_mode.krw_rate_fetch_http_error status=%d", resp.status_code)
                    except Exception as exc:
                        logger.debug("live_mode.krw_rate_fetch_error: %s", exc)
                        # BUG-88: only mark stale after 5 consecutive failures
                        self._krw_fail_count += 1
                        if self._krw_fail_count >= 5:
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

    async def _dedup_cleanup_loop(self) -> None:
        """Periodically clean up stale dedup gate entries and drain settlement exit requests.

        Settlement exit requests generated by strategies (e.g., FundingRateStrategy at
        settlement time) must be routed through LiveMode._execute_trade_request to get
        full pipeline treatment: kill switch, circuit breaker, Telegram alerts, PnL tracking.
        main.py._strategy_exit_poll_loop is gated to skip Live/Paper mode to avoid
        dual-drain race conditions.
        """
        _dedup_clean_counter = 0
        try:
            while self._running:
                # MEDIUM-3: drain exit requests every 10s (was 60s — max 120s latency).
                # Dedup gate cleanup still runs every 60s (6 × 10s cycles).
                await asyncio.sleep(10.0)
                _dedup_clean_counter += 1
                if _dedup_clean_counter >= 6:
                    await self._dedup_gate.cleanup_stale()
                    _dedup_clean_counter = 0
                # Drain settlement/holding-timeout exit requests from strategies
                if self._strategy_manager is not None:
                    for _sid in self._strategy_manager.list_strategies():
                        _strat = self._strategy_manager.get_strategy(_sid)
                        if _strat is not None and hasattr(_strat, "pop_exit_requests"):
                            for _exit_req in _strat.pop_exit_requests():
                                # BUG-92: Before sending exit, verify exchange has position.
                                # Ghost root cause: entry leg2 fails → half position → exchange auto-closes
                                # → timeout exit hits no position → ghost-clear cascade.
                                # BUG-92: Before sending exit, verify exchange has position.
                                _skip_exit = False
                                if _exit_req.legs and self._executor:
                                    _ex_id = _exit_req.legs[0].exchange_id
                                    _sym = _exit_req.legs[0].symbol
                                    try:
                                        _adapter = getattr(self._executor, '_exchanges', {}).get(_ex_id)
                                        if _adapter and hasattr(_adapter, 'get_positions'):
                                            _positions = await _adapter.get_positions()
                                            # GAP#3: get_positions() returns [] on API failure
                                            # → false ghost detection. None = API error → don't skip.
                                            if _positions is None:
                                                logger.warning(
                                                    "exit_position_check_api_null strategy=%s symbol=%s — "
                                                    "proceeding with exit (API returned None)",
                                                    _sid, _sym,
                                                )
                                            elif not any(
                                                getattr(p, 'symbol', '') == _sym
                                                for p in (_positions if isinstance(_positions, list) else [])
                                            ):
                                                logger.info(
                                                    "live_dedup_cleanup.exit_skip_no_position strategy=%s symbol=%s exchange=%s",
                                                    _sid, _sym, _ex_id,
                                                )
                                                _skip_exit = True
                                                # WS-2.8: Use clear_ghost() instead of direct dict access.
                                                # clear_ghost removes ALL tracking state for the symbol.
                                                _strat.clear_ghost(_sym)
                                    except Exception as _pos_err:
                                        logger.warning("exit_position_check_failed: %s", _pos_err)
                                if _skip_exit:
                                    continue
                                logger.info(
                                    "live_dedup_cleanup.settlement_exit strategy=%s legs=%d reason=%s",
                                    _sid,
                                    len(_exit_req.legs),
                                    _exit_req.metadata.get("reason", "unknown"),
                                )
                                _task = asyncio.create_task(
                                    self._execute_trade_request(_exit_req),
                                    name="settlement_exit_trade",
                                )
                                self._settlement_tasks.append(_task)
                                _task.add_done_callback(
                                    lambda t: self._settlement_tasks.remove(t)  # noqa: B023
                                    if t in self._settlement_tasks else None
                                )
        except asyncio.CancelledError:
            pass

    async def _margin_refresh_loop(self) -> None:
        """BUG-18 fix: every 60s, refresh cached margin_available per futures exchange.

        produce_futures_futures_signal() has no adapter access, so margin_available is
        injected into signal metadata in _route_signal_to_strategies() from this cache.
        """
        try:
            while self._running:
                try:
                    executor = self._executor
                    exchanges_dict: dict = getattr(executor, "_exchanges", None) or {}
                    # Naming convention: futures adapters must have "futures" in their key
                    # (e.g. "binance_futures", "bitget_futures"). Adapters named otherwise
                    # will not receive margin caching and will block futures_futures trades.
                    futures_adapters = {k: v for k, v in exchanges_dict.items() if "futures" in k}
                    for ex_id, adapter in futures_adapters.items():
                        try:
                            balances = await adapter.get_balances()
                            usdt = balances.get("USDT")
                            if usdt is not None:
                                self._cached_margin[ex_id] = usdt.free
                                _margin_f = float(usdt.free)
                                if _margin_f < 5.0:
                                    logger.warning(
                                        "live_mode.futures_margin_low ex=%s margin=%.2f "
                                        "— low free margin (< $5); trades may be margin-constrained",
                                        ex_id, _margin_f,
                                    )
                                else:
                                    logger.debug(
                                        "live_mode.margin_cache_updated ex=%s margin=%.2f",
                                        ex_id, _margin_f,
                                    )
                        except Exception as exc:
                            logger.debug("live_mode.margin_refresh_failed ex=%s error=%s", ex_id, exc)
                except Exception as exc:
                    logger.debug("live_mode.margin_refresh_loop_error error=%s", exc)
                await asyncio.sleep(60.0)
        except asyncio.CancelledError:
            pass

    async def _trade_reconciler_loop(self) -> None:
        """Every 10 minutes: reconcile internal execution_log vs exchange fill history (PHOENIX v18 P0)."""
        import time
        try:
            while self._running:
                await asyncio.sleep(600.0)
                if not self._running:
                    break
                try:
                    executor = self._executor
                    exchanges_dict: dict = getattr(executor, "_exchanges", None) or {}
                    # Include both futures AND spot adapters that implement get_trades()
                    futures_adapters = {k: v for k, v in exchanges_dict.items()
                                        if hasattr(v, "get_trades")}
                    since_ms = int((time.time() - 1200) * 1000)  # BUG-83: match _ttl=1200s to avoid unmatched false alarms
                    # Collect tracked symbols from strategies + 20-min rolling window.
                    # The window prevents the blind spot where _open_positions is empty (position just
                    # closed) but a late exchange fill or phantom DB record still needs reconciliation.
                    _now = time.time()
                    _ttl = 1200.0  # 20 minutes — covers 2 full reconciliation cycles
                    tracked_symbols: list[str] = []
                    if self._strategy_manager is not None:
                        for sid in self._strategy_manager.list_strategies():
                            strategy = self._strategy_manager.get_strategy(sid)
                            if strategy is not None:
                                open_pos = getattr(strategy, "_open_positions", {})
                                for sym in open_pos.keys():
                                    tracked_symbols.append(sym)
                                    self._recon_symbol_window[sym] = _now  # refresh TTL
                    # Add still-warm symbols from the rolling window
                    for sym, last_seen in list(self._recon_symbol_window.items()):
                        if _now - last_seen <= _ttl:
                            tracked_symbols.append(sym)
                        else:
                            del self._recon_symbol_window[sym]  # expire
                    symbols_arg = list(dict.fromkeys(tracked_symbols)) or None  # dedupe, None if empty

                    for eid, adapter in futures_adapters.items():
                        try:
                            await self._trade_reconciler.reconcile_period(
                                exchange_adapter=adapter,
                                exchange_id=eid,
                                since_ms=since_ms,
                                symbols=symbols_arg,
                            )
                        except Exception as exc:
                            logger.warning("trade_recon_loop_error exchange=%s error=%s", eid, exc)
                except Exception as exc:
                    logger.warning("trade_recon_loop_unexpected_error error=%s — continuing", exc)
        except asyncio.CancelledError:
            pass

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
