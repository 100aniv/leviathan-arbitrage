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

from collections import deque

import asyncio
import collections
import os
import random
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
from src.execution.paper import PaperExecutor, SlippageModel, OrderRejectedError
from src.friction.fee_model import FeeModel
from src.strategies.base import TradeRequest, TradeLeg
from src.infra.exchange.rate_limiter import TokenBucket
from src.infra.metrics import (
    COLLECTOR_MESSAGES,
    DRAWDOWN_CURRENT,
    EXCHANGE_HEALTH_SCORE,
    PNL_TOTAL,
    SIGNAL_COUNT,
    SIGNAL_PROCESSING_TIME,
    SIGNALS_TOTAL,
    SPREAD_BPS,
    STALE_ORDERBOOK_REJECTED,
    TRADE_LOSS_CAPPED,
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
        self._k = k if k is not None else float(os.getenv("POWERLAW_SLIPPAGE_K", "0.0"))
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
# Book-walk VWAP slippage model (SG-3)
# ---------------------------------------------------------------------------


class BookWalkSlippage(SlippageModel):
    """Orderbook depth-walking VWAP slippage model (SG-3).

    Walks real L2 orderbook levels to compute volume-weighted average
    fill price. More realistic than PowerLaw or zero slippage.
    """

    def __init__(
        self,
        books: dict[str, dict[str, Any]],
        fallback_bps: Decimal | None = None,
        depth_penalty_multiplier: float | None = None,
    ) -> None:
        super().__init__(base_slippage_pct=Decimal("0"))
        self._books = books
        self._fallback_bps = fallback_bps or Decimal(
            os.getenv("SHADOW_FALLBACK_SLIPPAGE_BPS", "10")
        )
        self._depth_penalty = depth_penalty_multiplier or float(
            os.getenv("SHADOW_DEPTH_PENALTY_MULTIPLIER", "2.0")
        )
        self._current_exchange: str = ""
        self._current_symbol: str = ""

    def set_context(self, exchange_id: str, symbol: str) -> None:
        """Set execution context before calling apply()."""
        self._current_exchange = exchange_id
        self._current_symbol = symbol

    def apply(
        self, base_price: Decimal, side: OrderSide, size: Decimal = Decimal("1")
    ) -> Decimal:
        book = self._books.get(self._current_symbol, {}).get(self._current_exchange)
        if book is None or not hasattr(book, "vwap_walk"):
            return self._apply_fallback(base_price, side)

        walk_side = "buy" if side == OrderSide.BUY else "sell"
        vwap_price, filled_qty = book.vwap_walk(walk_side, size)

        if filled_qty <= 0:
            return self._apply_fallback(base_price, side)

        if filled_qty < size:
            # Insufficient liquidity: penalize unfilled portion
            unfilled = size - filled_qty
            if side == OrderSide.BUY:
                penalty_price = vwap_price * Decimal(str(self._depth_penalty))
            else:
                penalty_price = vwap_price / Decimal(str(self._depth_penalty))
            logger.warning(
                "shadow_mode.book_walk_insufficient_depth",
                exchange=self._current_exchange,
                symbol=self._current_symbol,
                side=walk_side,
                requested=str(size),
                filled=str(filled_qty),
                unfilled=str(unfilled),
            )
            total_weighted = vwap_price * filled_qty + penalty_price * unfilled
            return total_weighted / size

        return vwap_price

    def _apply_fallback(self, base_price: Decimal, side: OrderSide) -> Decimal:
        """Conservative fallback when orderbook unavailable."""
        logger.warning(
            "shadow_mode.book_walk_fallback",
            exchange=self._current_exchange,
            symbol=self._current_symbol,
            side="buy" if side == OrderSide.BUY else "sell",
            fallback_bps=str(self._fallback_bps),
        )
        bps_fraction = self._fallback_bps / Decimal("10000")
        if side == OrderSide.BUY:
            return base_price * (Decimal("1") + bps_fraction)
        return base_price * (Decimal("1") - bps_fraction)


# ---------------------------------------------------------------------------
# Virtual balance tracker (SG-4)
# ---------------------------------------------------------------------------


class VirtualBalanceTracker:
    """Per-exchange virtual balance tracker for shadow mode (SG-4).

    Tracks simulated USDT balance per exchange. Prevents unrealistic
    infinite-capital trades in shadow mode.
    """

    def __init__(self, initial_balance_usdt: Decimal | None = None) -> None:
        self._initial: Decimal = initial_balance_usdt or Decimal(
            os.getenv("SHADOW_INITIAL_BALANCE_USDT", "10000000")
        )
        self._threshold_pct: Decimal = Decimal(
            os.getenv("SHADOW_REBALANCE_THRESHOLD_PCT", "0.10")
        )
        self._balances: dict[str, Decimal] = {}

    def get_balance(self, exchange_id: str) -> Decimal:
        """Return current balance for exchange, lazy-initialised to initial."""
        if exchange_id not in self._balances:
            self._balances[exchange_id] = self._initial
        return self._balances[exchange_id]

    def deduct(self, exchange_id: str, amount_usdt: Decimal) -> bool:
        """Deduct amount from exchange balance. Returns False if insufficient."""
        balance = self.get_balance(exchange_id)
        if balance < amount_usdt:
            logger.warning(
                "shadow_mode.insufficient_balance",
                exchange=exchange_id,
                balance=str(balance),
                required=str(amount_usdt),
            )
            return False
        self._balances[exchange_id] = balance - amount_usdt
        threshold = self._initial * self._threshold_pct
        if self._balances[exchange_id] < threshold:
            logger.warning(
                "shadow_mode.rebalance_needed",
                exchange=exchange_id,
                balance=str(self._balances[exchange_id]),
                threshold=str(threshold),
            )
        return True

    def credit(self, exchange_id: str, amount_usdt: Decimal) -> None:
        """Credit amount to exchange balance."""
        self._balances[exchange_id] = self.get_balance(exchange_id) + amount_usdt

    def reset(self) -> None:
        """Reset all balances to initial."""
        self._balances.clear()

    def summary(self) -> dict[str, str]:
        """Return balance summary as string values."""
        return {ex: str(bal) for ex, bal in self._balances.items()}


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
    rejections: int = 0
    partial_fills: int = 0
    # US-299: per-trade PnL history for Sharpe/MDD calculation (bounded for 72H)
    pnl_history: deque = field(default_factory=lambda: deque(maxlen=2000))


class ShadowRateLimiter:
    """Per-exchange token bucket rate limiter for shadow mode (SG-6).

    Simulates real exchange order rate limits using non-blocking try_acquire().
    env var overrides: SHADOW_RATE_LIMIT_UPBIT, SHADOW_RATE_LIMIT_BITHUMB, SHADOW_RATE_LIMIT_DEFAULT
    """

    EXCHANGE_ORDER_RATES: dict[str, tuple[float, int]] = {
        "binance": (5.0, 10),
        "binance_futures": (5.0, 10),
        "bybit": (5.0, 10),
        "okx": (6.0, 12),
        "bitget": (10.0, 20),
        "upbit": (8.0, 8),
        "bithumb": (3.0, 5),
        "coinone": (2.0, 3),
    }
    _DEFAULT_RATE: tuple[float, int] = (5.0, 10)

    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}
        # env var overrides
        self._env_overrides: dict[str, float] = {}
        for exchange_id in ("upbit", "bithumb", "default"):
            env_key = f"SHADOW_RATE_LIMIT_{exchange_id.upper()}"
            val = os.getenv(env_key)
            if val is not None:
                try:
                    self._env_overrides[exchange_id] = float(val)
                except ValueError:
                    pass

    def _normalize(self, exchange_id: str) -> str:
        """Strip paper_/sandbox_ prefix."""
        for prefix in ("paper_", "sandbox_"):
            if exchange_id.startswith(prefix):
                exchange_id = exchange_id[len(prefix):]
        return exchange_id

    def _get_bucket(self, exchange_id: str) -> TokenBucket:
        """Lazy-init token bucket for a given exchange."""
        key = self._normalize(exchange_id)
        if key not in self._buckets:
            if key in self._env_overrides:
                rate = self._env_overrides[key]
                default_rate, default_burst = self.EXCHANGE_ORDER_RATES.get(key, self._DEFAULT_RATE)
                burst = default_burst
            elif "default" in self._env_overrides:
                rate = self._env_overrides["default"]
                burst = self._DEFAULT_RATE[1]
            else:
                rate, burst = self.EXCHANGE_ORDER_RATES.get(key, self._DEFAULT_RATE)
            self._buckets[key] = TokenBucket(rate=rate, capacity=float(burst))
        return self._buckets[key]

    def try_acquire(self, exchange_id: str) -> bool:
        """Non-blocking rate limit check for an exchange order slot."""
        return self._get_bucket(exchange_id).try_acquire()

    def summary(self) -> dict[str, Any]:
        """Diagnostic summary of current bucket states."""
        return {k: {"tokens": round(b._tokens, 2), "rate": b.rate, "capacity": b.capacity}
                for k, b in self._buckets.items()}


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
    max_drawdown_pct: float = 0.0  # 0~1 range, percentage-based MDD (SSOT §4.6)
    last_daily_summary: datetime | None = None
    trades_rejected: int = 0
    trades_partial_fill: int = 0
    trades_rate_limited: int = 0
    # US-257: PnL accumulation for correct profit_factor (amount ratio, not count ratio)
    winning_pnl_sum: float = 0.0
    losing_pnl_sum: float = 0.0
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
        kill_switch: Any | None = None,
        regime_detector: Any | None = None,
        adaptive_threshold: Any | None = None,
        db_pool: Any | None = None,
        data_quality_manager: Any | None = None,
        strategy_filter: list[str] | None = None,
        portfolio_risk: Any | None = None,
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
            data_quality_manager: Optional DataQualityManager for US-286 quality checks.
            strategy_filter:   Optional allowlist of strategy_id strings. When set,
                               only signals whose strategy_id matches an entry are
                               processed; all others are skipped. None = no filter
                               (existing behaviour preserved).
            portfolio_risk:    Optional PortfolioRiskManager for US-300 portfolio-level
                               metrics (correlation, VaR, MDD). None = no-op (backward
                               compatible).
        """
        self._signal_generator = signal_generator
        self._multi_signal_producer = multi_signal_producer
        self._funding_rate_collector = funding_rate_collector
        self._strategy_manager = strategy_manager
        self._regime_detector = regime_detector
        self._adaptive_threshold = adaptive_threshold
        self._db_pool = db_pool  # US-256: peak_equity persistence
        self._data_quality_manager = data_quality_manager  # US-286
        # US-299: per-strategy filter allowlist (None = all strategies pass)
        self._strategy_filter: frozenset[str] | None = (
            frozenset(strategy_filter) if strategy_filter else None
        )
        # US-300: PortfolioRiskManager for portfolio-level metrics
        self._portfolio_risk = portfolio_risk
        # SIT-3: FlashGuard — set externally after construction via main.py
        self._flash_guard: Any | None = None
        # Shadow-local min_edge multiplier (CRISIS 레짐 시 2배 상향, log-only 모드)
        self._shadow_min_edge_factor: float = 1.0

        # Shadow mode: PaperExecutor with realistic slippage, zero flat fee.
        # k=1.0 matches CEXOrderbookSlippage's default (~10bps/side = 20bps round-trip).
        # k=0: zero slippage in PaperExecutor — SignalGenerator already applies
        # CEXOrderbookSlippage, so PaperExecutor must NOT add more (double-count).
        # FeeModel in _execute_shadow_trade handles per-exchange fees separately.
        # Parse env vars with validation (clamp to [0, 1], fallback on invalid)
        try:
            pfr = max(Decimal("0"), min(Decimal("1"), Decimal(os.environ.get("SHADOW_PARTIAL_FILL_RATE", "0.05"))))
        except Exception:
            pfr = Decimal("0.05")
        try:
            rr = max(Decimal("0"), min(Decimal("1"), Decimal(os.environ.get("SHADOW_REJECTION_RATE", "0.02"))))
        except Exception:
            rr = Decimal("0.02")
        # Orderbook store must be initialized before PaperExecutor so
        # BookWalkSlippage holds a reference to the live dict.
        # Structure: symbol -> exchange_id -> OrderBook
        self._books: dict[str, dict[str, Any]] = {}

        self._paper_executor: PaperExecutor = paper_executor or PaperExecutor(
            slippage_model=BookWalkSlippage(books=self._books),
            fee_rate=Decimal("0"),
            partial_fill_rate=pfr,
            rejection_rate=rr,
        )

        # Inter-leg execution delay simulation (SG-2)
        self._leg_delay_min_ms = float(os.environ.get("SHADOW_LEG_DELAY_MIN_MS", "50"))
        self._leg_delay_max_ms = float(os.environ.get("SHADOW_LEG_DELAY_MAX_MS", "300"))

        self._fee_model = FeeModel()
        self._market_recorder = market_recorder
        self._telegram = telegram
        self._symbols = symbols or ["BTC/USDT"]
        self._exchanges = exchanges

        # Futures orderbook store: symbol -> exchange_id -> OrderBook
        self._futures_books: dict[str, dict[str, Any]] = {}

        self._running = False
        self._stats = ShadowStats(start_time=time.monotonic())
        self._balance_tracker = VirtualBalanceTracker()
        self._rate_limiter = ShadowRateLimiter()
        # S10 fix: warmup guard for SignalGenerator path (disabled in test mode)
        _env = os.environ.get("ENGINE_ENV", "dev")
        self._signal_warmup_seconds: float = 5.0 if _env != "test" else 0.0

        # Background tasks
        self._daily_task: asyncio.Task[None] | None = None
        self._funding_rate_task: asyncio.Task[None] | None = None
        self._reconcile_task: asyncio.Task[None] | None = None

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
        # US-171: KillSwitch for KRW soft-block
        self._kill_switch = kill_switch
        self._sanity_reject_count: int = 0
        self._krw_stale: bool = False
        self._krw_stale_count: int = 0   # US-171: debounce counter
        self._krw_soft_blocked: bool = False  # US-171: soft-block KRW exchanges
        self._http_client: httpx.AsyncClient = httpx.AsyncClient(timeout=5.0)

        # Statistical arb state: rolling z-score window per symbol
        self._spread_history: dict[str, collections.deque] = {}
        self._stat_arb_window: int = 100

        # Latency tracking: exchange_id -> last update timestamp
        self._exchange_update_times: dict[str, float] = {}

        # Funding rates cache: exchange_id -> symbol -> rate
        self._funding_rates: dict[str, dict[str, float]] = {}

        # Futures exchanges for identification
        self._futures_exchanges: set[str] = {"binance_futures", "okx_futures", "bybit_futures"}

        # US-066: Stale orderbook defense — cross-validation + blacklist
        from src.core.stale_detector import StaleOrderbookDetector
        self._stale_detector = StaleOrderbookDetector(
            deviation_pct=float(os.getenv("STALE_CROSS_DEVIATION_PCT", "0.10")),
            blacklist_ttl_s=float(os.getenv("STALE_BLACKLIST_TTL_S", "300")),
        )

        # US-182: LatencyTracker for latency arb signal evaluation
        from src.core.latency_tracker import LatencyTracker
        self._latency_tracker = LatencyTracker()

        # RealDataSignalProducer: replaces inline _evaluate_* methods
        self._real_signal_producer: RealDataSignalProducer | None = None
        if self._multi_signal_producer is not None:
            self._real_signal_producer = RealDataSignalProducer(
                multi_signal_producer=self._multi_signal_producer,
                triangular_scanner=TriangularScanner(),
                futures_exchanges=self._futures_exchanges,
                latency_tracker=self._latency_tracker,
                stale_detector=self._stale_detector,
                regime_detector=self._regime_detector,
            )

        # US-066/US-156: Strategy blacklist — comma-separated strategy IDs to disable
        _disabled_raw = os.environ.get("SHADOW_DISABLED_STRATEGIES", "")
        _disabled_base: set[str] = {
            s.strip() for s in _disabled_raw.split(",") if s.strip()
        }
        # Also map registration IDs to signal IDs for dual-path blocking
        try:
            from src.modes.strategy_validation import STRATEGY_SIGNAL_ID_MAP
            _disabled_signal_ids = {
                STRATEGY_SIGNAL_ID_MAP.get(s, s) for s in _disabled_base
            }
            self._disabled_strategies: set[str] = _disabled_base | _disabled_signal_ids
        except ImportError:
            self._disabled_strategies = _disabled_base

        if self._disabled_strategies:
            logger.warning(
                "shadow_mode.strategies_disabled",
                disabled=sorted(self._disabled_strategies),
            )

        # US-066/US-224: Per-trade loss cap (hard ceiling on single-trade loss)
        # US-224: per-strategy caps via STRATEGY_LOSS_CAP_JSON or SHADOW_MAX_LOSS_PER_TRADE_USD
        self._max_loss_per_trade_usd: Decimal = Decimal(
            os.getenv("SHADOW_MAX_LOSS_PER_TRADE_USD", "10")
        )
        _loss_cap_json = os.getenv("STRATEGY_LOSS_CAP_JSON", "")
        _default_caps: dict[str, float] = {
            "futures_futures": 1.0,
            "cross_exchange": 5.0,
            "statistical_arb": 5.0,
        }
        try:
            import json as _json
            _user_caps: dict[str, float] = _json.loads(_loss_cap_json) if _loss_cap_json else {}
        except Exception:
            _user_caps = {}
        _merged = {**_default_caps, **_user_caps}
        self._strategy_loss_caps: dict[str, Decimal] = {
            k: Decimal(str(v)) for k, v in _merged.items()
        }

        # US-164: Temporary strategy disable map — strategy_id -> re-enable timestamp
        # Default 0 (disabled); set SHADOW_SINGLE_LOSS_DISABLE_SECONDS=600 in prod
        self._strategy_disable_until: dict[str, float] = {}
        self._single_loss_disable_seconds: float = float(
            os.getenv("SHADOW_SINGLE_LOSS_DISABLE_SECONDS", "0")
        )

        # US-066: Background task handle for periodic Bithumb REST refresh
        self._delta_refresh_task: asyncio.Task[None] | None = None

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
        # US-256: restore peak_equity from DB before any drawdown calculations
        await self._load_peak_equity_from_db()

        logger.info("shadow_mode.starting")

        # Send Telegram "started" notification (non-blocking; never crashes)
        if self._telegram is not None:
            try:
                await self._telegram.send_alert_kr("shadow_start", {})
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

        # US-066: Periodic REST refresh for delta exchanges (Bithumb)
        self._delta_refresh_task = asyncio.create_task(
            self._delta_refresh_loop(), name="shadow_delta_refresh"
        )

        # US-159: Periodic reconcile — position consistency check
        self._reconcile_task = asyncio.create_task(
            self._reconcile_loop(), name="shadow_reconcile"
        )

        # US-234: Regime check loop (60s periodic)
        self._regime_check_task: asyncio.Task[None] | None = None
        if self._regime_detector is not None:
            self._regime_check_task = asyncio.create_task(
                self._shadow_regime_check_loop(), name="shadow_regime_check"
            )

        # US-234: AdaptiveThreshold adjustment loop (300s periodic, shadow-local)
        self._shadow_adaptive_task: asyncio.Task[None] | None = None
        if self._adaptive_threshold is not None:
            self._shadow_adaptive_task = asyncio.create_task(
                self._shadow_adaptive_threshold_loop(), name="shadow_adaptive_threshold"
            )

        # US-234/US-258-a: ShadowMiniTuner (Optuna n_trials=20, 2h 경과 후 자동 트리거)
        self._shadow_mini_tuner = None
        try:
            from src.tuning.scheduled_tuner import ShadowMiniTuner
            self._shadow_mini_tuner = ShadowMiniTuner(
                hot_reload_callback=self._shadow_params_hot_reload
            )
            logger.info("shadow_mode.mini_tuner_initialized (will trigger after 2h)")
        except Exception as exc:
            logger.warning("shadow_mode.mini_tuner_init_failed: %s", exc)

        logger.info("shadow_mode.started", multi_strategy=self._multi_signal_producer is not None)

    async def stop(self) -> None:
        """Stop shadow mode: collectors, send final summary, clean up."""
        if not self._running:
            logger.warning("shadow_mode.not_running")
            return

        self._running = False
        logger.info("shadow_mode.stopping")

        # Cancel reconcile task (US-159)
        if self._reconcile_task is not None and not self._reconcile_task.done():
            self._reconcile_task.cancel()
            try:
                await self._reconcile_task
            except asyncio.CancelledError:
                pass
            self._reconcile_task = None

        # Cancel delta refresh task (US-066)
        if self._delta_refresh_task is not None and not self._delta_refresh_task.done():
            self._delta_refresh_task.cancel()
            try:
                await self._delta_refresh_task
            except asyncio.CancelledError:
                pass
            self._delta_refresh_task = None

        # Cancel US-234 shadow regime check task
        if getattr(self, "_regime_check_task", None) is not None and not self._regime_check_task.done():
            self._regime_check_task.cancel()
            try:
                await self._regime_check_task
            except asyncio.CancelledError:
                pass
            self._regime_check_task = None

        # Cancel US-234 shadow adaptive threshold task
        if getattr(self, "_shadow_adaptive_task", None) is not None and not self._shadow_adaptive_task.done():
            self._shadow_adaptive_task.cancel()
            try:
                await self._shadow_adaptive_task
            except asyncio.CancelledError:
                pass
            self._shadow_adaptive_task = None

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
        is_snapshot: bool = False,
    ) -> None:
        """Handle a new orderbook snapshot from a collector.

        Creates/updates the internal OrderBook for (exchange_id, symbol),
        feeds it to SignalGenerator, and executes any emitted signal.

        Never raises — all exceptions are caught and logged.
        """
        if not self._running:
            return

        # Yield to event loop every 5 updates to prevent API/telegram starvation
        self._ob_counter = getattr(self, "_ob_counter", 0) + 1
        if self._ob_counter % 5 == 0:
            await asyncio.sleep(0)

        # Normalize KRW prices to USDT for cross-exchange comparison
        # Korean exchanges (upbit, bithumb, coinone) quote in KRW
        if "/KRW" in symbol:
            if self._krw_stale:
                logger.info(
                    "shadow_mode.krw_stale_filtered",
                    exchange=exchange_id,
                    symbol=symbol,
                )
                return
            if self._krw_rate > 0:
                symbol = symbol.replace("/KRW", "/USDT")
                bids = [[str(float(b[0]) / self._krw_rate), str(b[1])] for b in bids]
                asks = [[str(float(a[0]) / self._krw_rate), str(a[1])] for a in asks]
            else:
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
            if existing is not None and exchange_id in DELTA_EXCHANGES and not is_snapshot:
                existing.apply_delta(bid_tuples, ask_tuples)
                book = existing
            else:
                book = self._orderbook_cls(symbol=symbol, exchange=exchange_id)
                book.apply_snapshot(bid_tuples, ask_tuples)
                self._books[symbol][exchange_id] = book

            # Cross-exchange price validation (US-066) — reject stale/drifted books
            if not self._stale_detector.check_cross_exchange(
                exchange_id, symbol, book, self._books
            ):
                STALE_ORDERBOOK_REJECTED.labels(
                    exchange=exchange_id, reason="cross_validation"
                ).inc()
                logger.info(
                    "shadow_mode.stale_cross_validation_rejected",
                    exchange=exchange_id,
                    symbol=symbol,
                )
                return

            # US-286: DataQualityManager central check (anomaly + freshness + bithumb)
            if self._data_quality_manager is not None:
                try:
                    _bid = book.best_bid()
                    _ask = book.best_ask()
                    if _bid is not None and _ask is not None:
                        _mid = float((_bid + _ask) / 2)
                        _spread = (float(_ask) - float(_bid)) / _mid if _mid > 0 else 0.0
                        dqm_result = self._data_quality_manager.check(
                            exchange_id, symbol, _mid, _spread,
                        )
                        if not dqm_result.ok:
                            STALE_ORDERBOOK_REJECTED.labels(
                                exchange=exchange_id, reason="data_quality"
                            ).inc()
                            logger.info(
                                "shadow_mode.data_quality_rejected",
                                exchange=exchange_id,
                                symbol=symbol,
                                reasons=dqm_result.reasons,
                                score=dqm_result.score,
                            )
                            return
                except Exception as exc:
                    logger.debug("dqm_check_error", exchange=exchange_id, symbol=symbol, error=str(exc))

            # SIT-3: FlashGuard price recording — detect rapid price movements
            if self._flash_guard is not None:
                try:
                    _fg_bid = book.best_bid()
                    _fg_ask = book.best_ask()
                    if _fg_bid is not None and _fg_ask is not None:
                        _fg_mid = (float(_fg_bid) + float(_fg_ask)) / 2
                        self._flash_guard.record_price(symbol, exchange_id, _fg_mid)
                except Exception:
                    pass

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

            # S10 fix: warmup guard — skip signal generation for first N seconds after start
            if (time.monotonic() - self._stats.start_time) < self._signal_warmup_seconds:
                return

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
                # Note: signal_found 알람 제거 — 시그널은 시간당 수백건으로 노이지.
                # 실제 체결(fill) 시에만 send_fill_kr()로 알람 (아래 _execute_shadow_trade에서 처리)

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
        """Delegate all multi-strategy evaluation to RealDataSignalProducer.

        Also feeds orderbook updates directly to StatisticalArbStrategy's
        on_orderbook_update() for cross-asset pair evaluation.  The stat_arb
        signals from RealDataSignalProducer are skipped here because they
        would be misrouted to the legacy on_signal() path which expects
        same-symbol cross-exchange data.
        """
        if self._real_signal_producer is None:
            return

        # Direct cross-asset stat_arb routing: feed mid_price to the strategy
        # so it can evaluate all configured pairs internally.
        await self._feed_stat_arb_orderbook(exchange_id, symbol, book)

        signals = await self._real_signal_producer.on_orderbook_update(
            exchange_id, symbol, book,
            self._books, self._futures_books,
        )
        for signal in signals:
            # Skip cross-asset stat_arb signals — already handled above via
            # direct on_orderbook_update() call to the strategy.
            if (
                signal.metadata.get("symbol2")
                and signal.strategy_id == "statistical_arb_zscore"
            ):
                continue
            if self._strategy_manager is not None:
                await self._route_signal_to_strategies(signal)
            else:
                await self._execute_shadow_trade(signal)

    async def _feed_stat_arb_orderbook(
        self, exchange_id: str, symbol: str, book: Any
    ) -> None:
        """Feed orderbook mid-price to StatisticalArbStrategy.on_orderbook_update().

        This routes cross-asset stat_arb data correctly — the strategy
        handles pair evaluation, z-score computation, and TradeRequest
        generation internally. Bypasses the broken on_signal() path which
        expects same-symbol cross-exchange data.
        """
        if self._strategy_manager is None:
            return

        bid = book.best_bid()
        ask = book.best_ask()
        if bid is None or ask is None:
            return
        mid_price = (float(bid) + float(ask)) / 2.0
        if mid_price <= 0:
            return

        from src.strategies.statistical_arb import StatisticalArbStrategy

        for strategy in self._strategy_manager._strategies.values():
            if not isinstance(strategy, StatisticalArbStrategy):
                continue
            if not strategy.is_active:
                continue
            try:
                requests = await strategy.on_orderbook_update(
                    exchange_id, symbol, mid_price
                )
                for req in requests:
                    await self._execute_shadow_trade_request(req)
            except Exception as exc:
                logger.warning(
                    "shadow_mode.stat_arb_orderbook_feed_failed",
                    exchange=exchange_id,
                    symbol=symbol,
                    error=str(exc),
                )

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

                # US-239: 30s polling for tighter settlement timing
                await asyncio.sleep(30.0)
        except asyncio.CancelledError:
            pass

    # NOTE: _evaluate_funding_rate_arb moved to RealDataSignalProducer

    # -----------------------------------------------------------------------
    # Delta exchange periodic REST refresh (US-066)
    # -----------------------------------------------------------------------

    async def _delta_refresh_loop(self) -> None:
        """Periodically re-fetch REST snapshots for delta exchanges (Bithumb).

        Bithumb sends incremental WS deltas that drift over time without
        periodic re-anchoring. This loop calls refresh_snapshots() every
        BITHUMB_REFRESH_INTERVAL_S seconds to reset the orderbook to ground truth.

        HTTP errors are logged and suppressed; the loop continues regardless.
        Never raises — exceptions are caught and logged.
        """
        interval = float(os.getenv("BITHUMB_REFRESH_INTERVAL_S", "60"))
        try:
            while self._running:
                await asyncio.sleep(interval)
                for eid in ("bithumb",):
                    collector = self._collector_manager.get_collector(eid)
                    if collector is not None and hasattr(collector, "refresh_snapshots"):
                        try:
                            count = await collector.refresh_snapshots()
                            logger.info(
                                "shadow_mode.delta_refresh_done",
                                exchange=eid,
                                refreshed=count,
                            )
                        except Exception as exc:
                            logger.warning(
                                "shadow_mode.delta_refresh_failed",
                                exchange=eid,
                                error=str(exc),
                            )
        except asyncio.CancelledError:
            pass

    async def _reconcile_loop(self) -> None:
        """US-159: Periodic position consistency check.

        Periodically verifies that virtual balance totals remain consistent
        with the recorded PnL. Logs warnings on drift but never raises.
        US-258-a: Also triggers ShadowMiniTuner after 2h elapsed.
        """
        interval = float(os.getenv("SHADOW_RECONCILE_INTERVAL_S", "60"))
        try:
            while self._running:
                await asyncio.sleep(interval)
                balance_summary = self._balance_tracker.summary()
                logger.debug(
                    "shadow_mode.reconcile_tick",
                    total_pnl=f"{self._stats.total_pnl:.4f}",
                    trades=self._stats.trades_executed,
                    balances=balance_summary,
                )

                # US-258-a: Trigger ShadowMiniTuner after 2h elapsed
                if self._shadow_mini_tuner is not None:
                    elapsed_s = time.monotonic() - self._stats.start_time
                    if elapsed_s >= 7200 and not self._shadow_mini_tuner._triggered:
                        trades = self._stats.trades_executed
                        wins = self._stats.trades_won
                        win_rate = wins / trades if trades > 0 else 0.5
                        logger.info(
                            "shadow_mode.mini_tuner_2h_trigger elapsed_s=%.0f trades=%d win_rate=%.2f",
                            elapsed_s, trades, win_rate,
                        )
                        self._shadow_mini_tuner.run_in_thread(
                            shadow_elapsed_seconds=elapsed_s,
                            win_rate=win_rate,
                            total_trades=trades,
                            expected_edge_bps=0.0,
                        )
        except asyncio.CancelledError:
            pass

    # -----------------------------------------------------------------------
    # Shadow trade execution
    # -----------------------------------------------------------------------

    def _get_loss_cap(self, strategy_id: str) -> Decimal:
        """US-224: Return per-strategy loss cap, falling back to global default."""
        base = strategy_id.split("_v")[0] if "_v" in strategy_id else strategy_id
        return self._strategy_loss_caps.get(base, self._max_loss_per_trade_usd)

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

        # Strategy blacklist check (US-066)
        sid_check = signal.strategy_id or self.STRATEGY_ID
        if sid_check in self._disabled_strategies:
            logger.debug("shadow_mode.strategy_disabled", strategy=sid_check)
            return

        # US-299: strategy_filter allowlist — skip signals not in the filter
        if self._strategy_filter is not None and sid_check not in self._strategy_filter:
            logger.debug("shadow_mode.strategy_filtered", strategy=sid_check)
            return

        # US-164: Temporary strategy disable check (10-min cooldown after single large loss)
        disable_until = self._strategy_disable_until.get(sid_check)
        if disable_until is not None and time.monotonic() < disable_until:
            logger.debug(
                "shadow_mode.strategy_temp_disabled",
                strategy=sid_check,
                seconds_remaining=f"{disable_until - time.monotonic():.1f}",
            )
            return

        # Rate limit check before balance deduct (SG-6)
        if not self._rate_limiter.try_acquire(signal.buy_exchange):
            self._stats.trades_rate_limited += 1
            logger.warning("shadow_mode.rate_limit_exceeded", exchange=signal.buy_exchange)
            return
        if not self._rate_limiter.try_acquire(signal.sell_exchange):
            self._stats.trades_rate_limited += 1
            logger.warning("shadow_mode.rate_limit_exceeded", exchange=signal.sell_exchange)
            return

        # Balance check before BUY (SG-4)
        notional = signal.buy_price * signal.volume
        if not self._balance_tracker.deduct(signal.buy_exchange, notional):
            return

        t0 = time.monotonic()
        self._stats.signals_detected += 1
        buy_trade = None  # Track which leg was rejected

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
            if hasattr(self._paper_executor, "slippage_model") and hasattr(self._paper_executor.slippage_model, "set_context"):
                self._paper_executor.slippage_model.set_context(signal.buy_exchange, signal.symbol)
            buy_trade = await self._paper_executor.execute(buy_order)

            # Simulate realistic inter-leg execution delay (SG-2)
            if self._leg_delay_max_ms > 0:
                delay_s = random.uniform(self._leg_delay_min_ms, self._leg_delay_max_ms) / 1000.0
                await asyncio.sleep(delay_s)

            # Detect partial fill on buy leg
            if buy_trade.amount < signal.volume:
                self._stats.trades_partial_fill += 1
                sid = signal.strategy_id or self.STRATEGY_ID
                if sid not in self._stats.by_strategy:
                    self._stats.by_strategy[sid] = StrategyStats()
                self._stats.by_strategy[sid].partial_fills += 1

            # Sell only what was bought (realistic arb)
            sell_order = Order(
                order_id=str(uuid.uuid4()),
                exchange_id=signal.sell_exchange,
                symbol=signal.symbol,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                price=signal.sell_price,
                amount=buy_trade.amount,
            )
            if hasattr(self._paper_executor, "slippage_model") and hasattr(self._paper_executor.slippage_model, "set_context"):
                self._paper_executor.slippage_model.set_context(signal.sell_exchange, signal.symbol)
            sell_trade = await self._paper_executor.execute(sell_order)
            # Credit sell proceeds to sell exchange balance (SG-4)
            self._balance_tracker.credit(signal.sell_exchange, sell_trade.price * sell_trade.amount)

        except OrderRejectedError as exc:
            sid = signal.strategy_id or self.STRATEGY_ID
            self._stats.trades_rejected += 1
            if sid not in self._stats.by_strategy:
                self._stats.by_strategy[sid] = StrategyStats()
            self._stats.by_strategy[sid].rejections += 1
            # Identify which leg was rejected (buy if buy_trade unset, else sell)
            rejected_leg = "buy" if buy_trade is None else "sell"
            logger.warning(
                "shadow_mode.order_rejected",
                strategy=sid,
                symbol=signal.symbol,
                rejected_leg=rejected_leg,
                error=str(exc),
            )
            return
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

        # Per-trade loss cap (US-066/US-224): per-strategy hard ceiling
        _sid_cap = signal.strategy_id or self.STRATEGY_ID
        max_loss = self._get_loss_cap(_sid_cap)
        if net_pnl < -max_loss:
            capped_pnl = -max_loss
            logger.warning(
                "shadow_mode.trade_loss_capped",
                symbol=signal.symbol,
                buy_exchange=signal.buy_exchange,
                sell_exchange=signal.sell_exchange,
                raw_pnl=f"{float(net_pnl):+.4f}",
                capped_pnl=f"{float(capped_pnl):+.4f}",
            )
            net_pnl = capped_pnl
            net_pnl_float = float(net_pnl)
            TRADE_LOSS_CAPPED.labels(exchange=signal.buy_exchange).inc()
            self._stale_detector.add_blacklist(signal.buy_exchange, signal.symbol)
            self._stale_detector.add_blacklist(signal.sell_exchange, signal.symbol)
            # US-164: Temporarily disable the offending strategy (if enabled)
            if self._single_loss_disable_seconds > 0:
                _sid_loss = signal.strategy_id or self.STRATEGY_ID
                _disable_until = time.monotonic() + self._single_loss_disable_seconds
                self._strategy_disable_until[_sid_loss] = _disable_until
                logger.warning(
                    "shadow_mode.strategy_temp_disabled_single_loss",
                    strategy=_sid_loss,
                    raw_pnl=f"{float(net_pnl):+.4f}",
                    threshold=f"{float(max_loss):.2f}",
                    disable_seconds=self._single_loss_disable_seconds,
                )

        # Per-strategy tracking
        sid = signal.strategy_id or self.STRATEGY_ID
        if sid not in self._stats.by_strategy:
            self._stats.by_strategy[sid] = StrategyStats()
        ss = self._stats.by_strategy[sid]
        ss.signals += 1
        ss.trades += 1
        ss.pnl += net_pnl_float
        ss.pnl_history.append(net_pnl_float)  # US-299: for Sharpe/MDD

        if net_pnl_float > 0:
            self._stats.trades_won += 1
            self._stats.winning_pnl_sum += net_pnl_float  # US-257
            ss.wins += 1
            result_label = "win"
        else:
            self._stats.trades_lost += 1
            self._stats.losing_pnl_sum += abs(net_pnl_float)  # US-257
            ss.losses += 1
            result_label = "loss"

        self._stats.total_pnl += net_pnl_float
        self._compute_drawdown()

        # 체결 알림 (시그널 아닌 실제 체결만)
        if self._telegram is not None:
            try:
                await self._telegram.send_fill_enhanced({
                    "strategy": sid,
                    "symbol": signal.symbol,
                    "buy_exchange": buy_ex,
                    "sell_exchange": sell_ex,
                    "pnl": net_pnl_float,
                    "spread_bps": float(signal.spread_pct) * 10000,
                    "fee": float(real_buy_fee + real_sell_fee),
                    "slippage_bps": 0.0,
                    "latency_ms": int((time.monotonic() - t0) * 1000),
                })
            except Exception:
                pass

        # US-300: update PortfolioRiskManager with per-strategy PnL
        if self._portfolio_risk is not None:
            try:
                self._portfolio_risk.update_returns(sid, net_pnl_float)
            except Exception as _prm_exc:
                logger.debug("shadow_mode.portfolio_risk_update_failed", error=str(_prm_exc))

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

        # Strategy blacklist check (US-066)
        if sid in self._disabled_strategies:
            logger.debug("shadow_mode.strategy_disabled", strategy=sid)
            return

        # US-299: strategy_filter allowlist
        if self._strategy_filter is not None and sid not in self._strategy_filter:
            logger.debug("shadow_mode.strategy_filtered", strategy=sid)
            return

        try:
            # Rate limit check: all legs must pass before executing any (SG-6)
            for leg in trade_request.legs:
                if not self._rate_limiter.try_acquire(leg.exchange_id):
                    self._stats.trades_rate_limited += 1
                    logger.warning("shadow_mode.rate_limit_exceeded", exchange=leg.exchange_id)
                    return

            trades = []
            had_partial = False
            for i, leg in enumerate(trade_request.legs):
                leg_price = leg.price or Decimal("0")
                if leg_price <= Decimal("0"):
                    logger.warning(
                        "shadow_mode.trade_leg_missing_price",
                        strategy_id=sid,
                        exchange_id=leg.exchange_id,
                        symbol=leg.symbol,
                        side=str(leg.side),
                    )
                order = Order(
                    order_id=str(uuid.uuid4()),
                    exchange_id=leg.exchange_id,
                    symbol=leg.symbol,
                    side=leg.side,
                    order_type=leg.order_type,
                    price=leg_price,
                    amount=leg.size,
                )
                if hasattr(self._paper_executor, "slippage_model") and hasattr(self._paper_executor.slippage_model, "set_context"):
                    self._paper_executor.slippage_model.set_context(leg.exchange_id, leg.symbol)
                trade = await self._paper_executor.execute(order)
                # Detect partial fill (count once per trade, not per leg)
                if trade.amount < leg.size:
                    had_partial = True
                trades.append((leg, trade))
                # Inter-leg delay (skip after last leg) (SG-2)
                if i < len(trade_request.legs) - 1 and self._leg_delay_max_ms > 0:
                    delay_s = random.uniform(self._leg_delay_min_ms, self._leg_delay_max_ms) / 1000.0
                    await asyncio.sleep(delay_s)
            if had_partial:
                self._stats.trades_partial_fill += 1
                if sid not in self._stats.by_strategy:
                    self._stats.by_strategy[sid] = StrategyStats()
                self._stats.by_strategy[sid].partial_fills += 1
        except OrderRejectedError as exc:
            self._stats.trades_rejected += 1
            if sid not in self._stats.by_strategy:
                self._stats.by_strategy[sid] = StrategyStats()
            self._stats.by_strategy[sid].rejections += 1
            logger.warning(
                "shadow_mode.trade_request_rejected",
                strategy_id=sid,
                error=str(exc),
            )
            return
        except Exception as exc:
            logger.error(
                "shadow_mode.trade_request_execution_failed",
                strategy_id=sid,
                error=str(exc),
            )
            return

        self._stats.trades_executed += 1

        # Compute net PnL across all legs
        # US-240: Cross-asset detection — if legs have different symbols,
        # the standard sell_notional - buy_notional calculation is meaningless
        # (comparing BTC price to ETH price). For cross-asset (stat_arb),
        # each leg is dollar-neutral by design, so net PnL = -(total fees).
        _is_cross_asset = trade_request.metadata.get("cross_asset") == "true"
        _leg_symbols = {leg.symbol for leg, _ in trades}
        if not _is_cross_asset and len(_leg_symbols) > 1:
            _is_cross_asset = True  # fallback detection

        net_pnl = Decimal("0")
        total_fees = Decimal("0")
        for leg, trade in trades:
            notional = trade.price * trade.amount
            ex = leg.exchange_id.removeprefix("paper_").removeprefix("sandbox_")
            try:
                fee = self._fee_model.taker_fee(ex, notional)
            except ValueError:
                fee = notional * Decimal("0.0025")
            total_fees += fee

            if _is_cross_asset:
                # Dollar-neutral: PnL comes only from spread convergence over time.
                # Individual entry/exit PnL = -(fees only).
                continue
            else:
                if leg.side == OrderSide.SELL:
                    net_pnl += notional - fee
                else:
                    net_pnl -= notional + fee

        if _is_cross_asset:
            # Cross-asset: use expected_profit_usdt from strategy (spread-based PnL)
            # Entry: expected_profit=0 → net_pnl = -fees (opening position)
            # Exit: expected_profit=spread_pnl → net_pnl = spread_pnl - fees
            _expected = trade_request.expected_profit_usdt or Decimal("0")
            # Sanity cap: no single trade can exceed $50 profit (prevents stale data artifacts)
            _MAX_SINGLE_TRADE_PNL = Decimal("50")
            if abs(_expected) > _MAX_SINGLE_TRADE_PNL:
                logger.warning(
                    "shadow_mode.cross_asset_pnl_capped",
                    raw_pnl=float(_expected),
                    capped=float(_MAX_SINGLE_TRADE_PNL),
                    strategy=sid,
                )
                _expected = _MAX_SINGLE_TRADE_PNL if _expected > 0 else -_MAX_SINGLE_TRADE_PNL
            net_pnl = _expected - total_fees

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

        # SIT-3 P3: funding_rate carry trade — Shadow 즉시 결산이 carry 수익 미반영.
        # metadata에 expected_funding_income이 있으면 carry 시뮬레이션 적용.
        if trade_request.metadata and "expected_funding_income" in trade_request.metadata:
            try:
                carry_income = Decimal(str(trade_request.metadata["expected_funding_income"]))
                net_pnl = carry_income - total_fees - (network_cost if 'network_cost' in dir() else Decimal("0"))
            except (ValueError, TypeError):
                pass

        net_pnl_float = float(net_pnl)

        # Per-trade loss cap (US-066/US-224): per-strategy hard ceiling
        max_loss = self._get_loss_cap(sid)
        if net_pnl < -max_loss:
            capped_pnl = -max_loss
            logger.warning(
                "shadow_mode.trade_request_loss_capped",
                strategy_id=sid,
                raw_pnl=f"{float(net_pnl):+.4f}",
                capped_pnl=f"{float(capped_pnl):+.4f}",
            )
            net_pnl = capped_pnl
            net_pnl_float = float(net_pnl)
            TRADE_LOSS_CAPPED.labels(
                exchange=trade_request.legs[0].exchange_id if trade_request.legs else "unknown"
            ).inc()
            if self._stale_detector is not None:
                for leg in trade_request.legs:
                    self._stale_detector.add_blacklist(leg.exchange_id, leg.symbol)

        # Per-strategy tracking
        if sid not in self._stats.by_strategy:
            self._stats.by_strategy[sid] = StrategyStats()
        ss = self._stats.by_strategy[sid]
        ss.signals += 1
        ss.trades += 1
        ss.pnl += net_pnl_float
        ss.pnl_history.append(net_pnl_float)  # US-299: for Sharpe/MDD

        if net_pnl_float > 0:
            self._stats.trades_won += 1
            self._stats.winning_pnl_sum += net_pnl_float  # US-257
            ss.wins += 1
            result_label = "win"
        else:
            self._stats.trades_lost += 1
            self._stats.losing_pnl_sum += abs(net_pnl_float)  # US-257
            ss.losses += 1
            result_label = "loss"

        self._stats.total_pnl += net_pnl_float
        self._compute_drawdown()

        # US-300: PortfolioRiskManager update (multi-leg path)
        if self._portfolio_risk is not None:
            try:
                self._portfolio_risk.update_returns(sid, net_pnl_float)
            except Exception as exc:
                logger.warning("portfolio_risk_update_failed_multileg", error=str(exc))

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

        # SIT-3 P4: 멀티레그 경로 텔레그램 체결 알림 (기존 2-leg 경로에만 있었음)
        if self._telegram is not None:
            try:
                await self._telegram.send_fill_enhanced({
                    "strategy": sid,
                    "symbol": trade_request.legs[0].symbol if trade_request.legs else "unknown",
                    "buy_exchange": buy_exs[0] if buy_exs else "unknown",
                    "sell_exchange": sell_exs[0] if sell_exs else "unknown",
                    "pnl": net_pnl_float,
                    "spread_bps": 0.0,
                    "fee": float(total_fees),
                    "slippage_bps": 0.0,
                    "latency_ms": int((time.monotonic() - t0) * 1000),
                })
            except Exception:
                pass

        # SIT-3 G1 Fix: multi-leg DB 기록 (기존 2-leg만 record_execution 호출)
        if self._market_recorder is not None and trades:
            try:
                first_leg = trade_request.legs[0] if trade_request.legs else None
                first_trade = trades[0][1] if trades else None
                if first_leg and first_trade:
                    self._market_recorder.record_execution(
                        strategy_id=sid,
                        buy_exchange=buy_exs[0] if buy_exs else "unknown",
                        sell_exchange=sell_exs[0] if sell_exs else "unknown",
                        symbol=first_leg.symbol,
                        buy_price=first_trade.price if first_trade.side == "buy" else Decimal("0"),
                        sell_price=first_trade.price if first_trade.side == "sell" else Decimal("0"),
                        size=first_trade.amount,
                        gross_spread_bps=None,
                        fee_total=total_fees,
                        slippage_total=Decimal("0"),
                        net_pnl=net_pnl,
                    )
            except Exception as exc:
                logger.debug("shadow_mode.record_execution_multileg_failed", error=str(exc))

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

                # Staleness check — halt KRW trading when rate is stale
                elapsed = time.monotonic() - self._krw_rate_updated_at
                if elapsed > 120:
                    if not self._krw_stale:
                        self._krw_stale = True
                        self._krw_stale_count = 0
                        logger.info(
                            "shadow_mode.krw_stale_entered",
                            seconds_since_update=elapsed,
                        )
                    else:
                        self._krw_stale_count += 1
                    logger.warning(
                        "shadow_mode.krw_rate_stale",
                        seconds_since_update=elapsed,
                        stale_count=self._krw_stale_count,
                    )
                    # US-171: Tiered response — soft-block first, KillSwitch for prolonged outage
                    # Phase 1: 3x stale (≈90s) → soft-block KRW exchanges only
                    if self._krw_stale_count >= 3 and not self._krw_soft_blocked:
                        self._krw_soft_blocked = True
                        logger.warning(
                            "shadow_mode.krw_soft_block_activated",
                            stale_seconds=elapsed,
                        )
                        if self._telegram is not None:
                            try:
                                asyncio.create_task(self._telegram.send_alert_kr(
                                    "krw_soft_block", {"stale_seconds": elapsed},
                                ))
                            except Exception:
                                pass
                    # Phase 2: 20x stale (≈10min) → full KillSwitch (prolonged outage = systemic risk)
                    if self._krw_stale_count >= 20 and self._kill_switch is not None:
                        logger.critical(
                            "shadow_mode.krw_prolonged_outage_killswitch",
                            stale_seconds=elapsed,
                            stale_count=self._krw_stale_count,
                        )
                        try:
                            asyncio.create_task(self._kill_switch.trigger())
                        except Exception as exc:
                            logger.error("shadow_mode.kill_switch_trigger_failed", error=str(exc))
                        if self._telegram is not None:
                            try:
                                asyncio.create_task(self._telegram.send_alert_kr(
                                    "krw_killswitch", {"stale_seconds": elapsed},
                                ))
                            except Exception:
                                pass
                elif self._krw_stale:
                    self._krw_stale = False
                    self._krw_stale_count = 0
                    # US-171: unblock on recovery
                    if self._krw_soft_blocked:
                        self._krw_soft_blocked = False
                        logger.info("shadow_mode.krw_soft_block_cleared")
                        if self._telegram is not None:
                            try:
                                asyncio.create_task(self._telegram.send_alert_kr(
                                    "krw_recovered", {},
                                ))
                            except Exception:
                                pass
                    logger.info("shadow_mode.krw_stale_recovered")

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
        for s_id, ss in sorted(list(stats.by_strategy.items())):
            s_wr = ss.wins / ss.trades if ss.trades > 0 else 0.0
            strategy_breakdown.append({
                "strategy_id": s_id,
                "trades": ss.trades,
                "wins": ss.wins,
                "losses": ss.losses,
                "win_rate": s_wr,
                "pnl": ss.pnl,
            })

        # US-258-b: Check #11 gate — warm-up / CRISIS exclusion for trade=0 strategies
        # Adds warmup_excluded / crisis_excluded flags to strategy_breakdown so
        # shadow-tester can properly skip them when evaluating check #11.
        try:
            _is_crisis = getattr(self, "_shadow_min_edge_factor", 1.0) >= 2.0
            _warmup_incomplete_ids: set[str] = set()
            for _strategy in self._strategy_manager._strategies.values():
                if hasattr(_strategy, "is_warmed_up") and not _strategy.is_warmed_up():
                    _warmup_incomplete_ids.add(_strategy.strategy_id)
            for _sb in strategy_breakdown:
                _sid = _sb["strategy_id"]
                _sb["warmup_excluded"] = _sid in _warmup_incomplete_ids
                _sb["crisis_excluded"] = _is_crisis
                if _sb["trades"] == 0:
                    if _sb["warmup_excluded"]:
                        logger.info(
                            "shadow_mode.strategy_trade_zero_warmup_incomplete strategy=%s", _sid
                        )
                    elif _is_crisis:
                        logger.info(
                            "shadow_mode.strategy_trade_zero_crisis_regime strategy=%s", _sid
                        )
                    else:
                        logger.warning(
                            "shadow_mode.strategy_trade_zero strategy=%s", _sid
                        )
        except Exception:
            pass

        # Profit factor (US-257)
        profit_factor = (
            stats.winning_pnl_sum / abs(stats.losing_pnl_sum)
            if stats.losing_pnl_sum != 0 else 0.0
        )
        # Sharpe estimate (annualized from per-trade returns)
        sharpe = 0.0
        if total_trades > 1:
            import statistics
            all_pnls = [ss.pnl / max(ss.trades, 1) for ss in stats.by_strategy.values() if ss.trades > 0]
            if len(all_pnls) > 1:
                try:
                    sharpe = statistics.mean(all_pnls) / statistics.stdev(all_pnls) * (252 ** 0.5)
                except (ZeroDivisionError, statistics.StatisticsError):
                    sharpe = 0.0

        summary_data: dict[str, Any] = {
            "date": now.strftime("%Y-%m-%d"),
            "strategy": self.STRATEGY_ID,
            "total_pnl": stats.total_pnl,
            "trades": total_trades,
            "win_rate": win_rate,
            "max_drawdown": stats.max_drawdown,
            "profit_factor": profit_factor,
            "sharpe": sharpe,
            "trades_rejected": stats.trades_rejected,
            "trades_partial_fill": stats.trades_partial_fill,
            "trades_rate_limited": stats.trades_rate_limited,
            "active_strategies": len([s for s in strategy_breakdown if s.get("trades", 0) > 0]),
            "exchange_status": {},  # populated below
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
                await self._telegram.send_daily_report_kr(summary_data)

                # Send per-strategy breakdown as separate message
                if strategy_breakdown:
                    await self._telegram.send_alert_kr("shadow_daily_breakdown", {
                        "strategies": strategy_breakdown,
                        "trades_rejected": stats.trades_rejected,
                        "trades_partial_fill": stats.trades_partial_fill,
                    })

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
            # US-256: persist new peak to DB (fire-and-forget, only when event loop running)
            try:
                asyncio.get_running_loop().create_task(self._save_peak_equity_to_db())
            except RuntimeError:
                pass  # no running loop (test/sync context) — skip DB save

        # Absolute drawdown in USD (not fraction — avoids blowup when peak is tiny)
        drawdown = self._stats.peak_pnl - pnl

        if drawdown > self._stats.max_drawdown:
            self._stats.max_drawdown = drawdown

        # Percentage-based MDD (SSOT §4.6: MDD = (Peak - PnL) / Peak)
        if self._stats.peak_pnl > 0.01:  # guard against tiny peak
            dd_pct = min(drawdown / self._stats.peak_pnl, 1.0)  # clamp to 0~1
            if dd_pct > self._stats.max_drawdown_pct:
                self._stats.max_drawdown_pct = dd_pct

    # -----------------------------------------------------------------------
    # US-256: peak_equity DB persistence
    # -----------------------------------------------------------------------

    async def _load_peak_equity_from_db(self) -> None:
        """Load peak_pnl from TimescaleDB shadow_peak_equity on startup."""
        if self._db_pool is None:
            logger.info("[peak_equity] no db_pool, using memory default")
            return
        try:
            async with self._db_pool.pool.acquire() as conn:
                row = await conn.fetchrow("SELECT peak_equity FROM shadow_peak_equity WHERE id = 1")
                if row and row["peak_equity"] > 0:
                    self._stats.peak_pnl = float(row["peak_equity"])
                    logger.info("[peak_equity] loaded from DB: $%.2f", self._stats.peak_pnl)
                else:
                    logger.info("[peak_equity] no prior peak_equity in DB, starting at 0")
        except Exception as exc:
            logger.warning("[peak_equity] DB load failed (non-fatal): %s", exc)

    async def _save_peak_equity_to_db(self) -> None:
        """Persist current peak_pnl to TimescaleDB."""
        if self._db_pool is None:
            return
        try:
            async with self._db_pool.pool.acquire() as conn:
                await conn.execute(
                    "UPDATE shadow_peak_equity SET peak_equity = $1, updated_at = now() WHERE id = 1",
                    self._stats.peak_pnl,
                )
        except Exception as exc:
            logger.debug("[peak_equity] DB save failed (non-fatal): %s", exc)

    # -----------------------------------------------------------------------
    # US-067: Strategy validation helpers
    # -----------------------------------------------------------------------

    def get_snapshot(self) -> dict[str, Any]:
        """Return current shadow stats as a serializable dict."""
        stats = self._stats
        total_trades = stats.trades_executed
        win_rate = stats.trades_won / total_trades if total_trades > 0 else 0.0
        uptime_s = time.monotonic() - stats.start_time
        by_strategy = []
        for s_id, ss in sorted(list(stats.by_strategy.items())):
            s_wr = ss.wins / ss.trades if ss.trades > 0 else 0.0
            by_strategy.append({
                "strategy_id": s_id, "trades": ss.trades,
                "wins": ss.wins, "losses": ss.losses,
                "win_rate": round(s_wr, 4), "pnl": round(ss.pnl, 6),
            })
        # US-300: portfolio-level metrics from PortfolioRiskManager
        portfolio_metrics: dict[str, Any] = {}
        if self._portfolio_risk is not None:
            try:
                _var = self._portfolio_risk.get_var()
                _vol = self._portfolio_risk.get_portfolio_volatility()
                _mdd_info = self._portfolio_risk.check_mdd_breach()
                portfolio_metrics = {
                    "portfolio_var_95": round(_var, 6) if _var is not None else None,
                    "portfolio_volatility": round(_vol, 6) if _vol is not None else None,
                    "portfolio_mdd_pct": _mdd_info.get("portfolio_mdd_pct"),
                    "portfolio_mdd_breach": _mdd_info.get("portfolio_breach", False),
                }
            except Exception as _prm_snap_exc:
                logger.debug("shadow_mode.portfolio_risk_snapshot_failed", error=str(_prm_snap_exc))

        return {
            "active": self._running, "uptime_seconds": round(uptime_s, 1),
            "signals_detected": stats.signals_detected,
            "trades_executed": total_trades,
            "trades_won": stats.trades_won, "trades_lost": stats.trades_lost,
            "win_rate": round(win_rate, 4),
            "total_pnl": round(stats.total_pnl, 6),
            "peak_pnl": round(stats.peak_pnl, 6),
            "max_drawdown": round(stats.max_drawdown, 6),
            "max_drawdown_pct": round(stats.max_drawdown_pct, 6),
            "trades_rejected": stats.trades_rejected,
            "trades_partial_fill": stats.trades_partial_fill,
            "trades_rate_limited": stats.trades_rate_limited,
            "by_strategy": by_strategy,
            **portfolio_metrics,
        }

    def reset_stats(self) -> None:
        """Reset stats for strategy validation. Preserves infrastructure (collectors, orderbooks, WS connections)."""
        self._stats = ShadowStats(start_time=time.monotonic())
        # Reset VirtualBalanceTracker to restore initial balances (architect recommendation)
        if hasattr(self, '_balance_tracker') and self._balance_tracker is not None:
            self._balance_tracker.reset()
        # Reset ShadowRateLimiter buckets (architect recommendation)
        if hasattr(self, '_rate_limiter') and self._rate_limiter is not None:
            self._rate_limiter._buckets = {}
        # Reset StaleOrderbookDetector blacklist (architect recommendation)
        if hasattr(self, '_stale_detector') and self._stale_detector is not None:
            if hasattr(self._stale_detector, '_blacklist'):
                self._stale_detector._blacklist.clear()
        logger.info("ShadowMode stats reset for strategy validation")

    def set_disabled_strategies(self, disabled: set[str]) -> None:
        """Dynamically update disabled strategies set."""
        self._disabled_strategies = disabled
        logger.info("shadow_mode.disabled_strategies_updated", disabled=list(disabled))

    def get_strategy_report(self) -> dict:
        """Return serializable per-strategy metrics dict (US-299: adds Sharpe/MDD/pass)."""
        import math

        report = {}
        for strategy_id, ss in self._stats.by_strategy.items():
            win_rate = float(ss.wins / ss.trades) if ss.trades > 0 else 0.0

            # Sharpe ratio from per-trade PnL history (annualised trade-count basis)
            sharpe = 0.0
            history = getattr(ss, "pnl_history", [])
            if len(history) >= 2:
                n = len(history)
                mean = sum(history) / n
                variance = sum((x - mean) ** 2 for x in history) / (n - 1)
                std = math.sqrt(variance) if variance > 0 else 0.0
                sharpe = round(mean / std, 4) if std > 0 else 0.0

            # Maximum drawdown from cumulative PnL curve
            mdd = 0.0
            if history:
                peak = 0.0
                cumulative = 0.0
                for pnl in history:
                    cumulative += pnl
                    if cumulative > peak:
                        peak = cumulative
                    dd = peak - cumulative
                    if dd > mdd:
                        mdd = dd
                mdd = round(mdd, 6)

            # PASS: min_trades >= 1 AND total PnL >= 0 (30-min validation criteria)
            passed = ss.trades >= 1 and ss.pnl >= 0.0

            report[strategy_id] = {
                "trades": ss.trades,
                "wins": ss.wins,
                "losses": ss.losses,
                "pnl": round(float(ss.pnl), 6),
                "win_rate": round(win_rate, 4),
                "sharpe": sharpe,
                "max_drawdown": mdd,
                "pass": passed,
            }
        return report

    # ------------------------------------------------------------------
    # US-234: Shadow-local regime check loop (60s periodic)
    # ------------------------------------------------------------------

    async def _shadow_regime_check_loop(self) -> None:
        """60초 주기로 regime_detector.detect()를 호출하고 CRISIS 시 min_edge 2배 상향.

        log-only 모드: 실제 SignalGenerator._config.min_edge를 직접 수정하지 않고
        self._shadow_min_edge_factor를 통해 로그로만 기록. CRISIS 해제 시 1.0으로 복원.
        """
        if not hasattr(self, "_regime_pnl_history"):
            self._regime_pnl_history: list[float] = []
        if not hasattr(self, "_regime_last_pnl"):
            self._regime_last_pnl: float = 0.0

        while self._running:
            try:
                await asyncio.sleep(60)
                if not self._running:
                    break
                if self._regime_detector is None:
                    break

                pnl_now = float(self._stats.total_pnl)
                pnl_delta = pnl_now - self._regime_last_pnl
                self._regime_last_pnl = pnl_now
                self._regime_pnl_history.append(pnl_delta)
                self._regime_pnl_history = self._regime_pnl_history[-60:]
                returns = list(self._regime_pnl_history)

                try:
                    from src.tuning.regime_detector import MarketRegime
                    regime = self._regime_detector.detect(returns)
                    logger.info(
                        "shadow.regime_check",
                        regime=str(regime),
                        pnl=pnl_now,
                        samples=len(returns),
                    )
                    if regime == MarketRegime.CRISIS:
                        if self._shadow_min_edge_factor < 2.0:
                            self._shadow_min_edge_factor = 2.0
                            logger.warning(
                                "shadow.crisis_regime_detected",
                                action="min_edge_factor_2x",
                                note="log-only: shadow params only, live unaffected",
                            )
                    else:
                        if self._shadow_min_edge_factor != 1.0:
                            self._shadow_min_edge_factor = 1.0
                            logger.info(
                                "shadow.regime_normalized",
                                action="min_edge_factor_reset_1x",
                            )
                except Exception as exc:
                    logger.warning("shadow.regime_check_error", error=str(exc))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("shadow._shadow_regime_check_loop error: %s", exc)

    # ------------------------------------------------------------------
    # US-234: Shadow-local adaptive threshold loop (300s periodic)
    # ------------------------------------------------------------------

    async def _shadow_adaptive_threshold_loop(self) -> None:
        """300초 주기로 adaptive_threshold.adjust()를 호출 (shadow 전용).

        live 파라미터 오염 방지: strategy_params.json 직접 수정 없이
        shadow 로컬 상태만 업데이트하고 로그 기록.
        """
        while self._running:
            try:
                await asyncio.sleep(300)
                if not self._running:
                    break
                if self._adaptive_threshold is None:
                    break

                trades = self._stats.trades_executed
                wins = self._stats.trades_won
                win_rate = float(wins / trades) if trades > 0 else 0.0

                # 총 PnL과 trade 수로 expected_edge 추정
                pnl_total = float(self._stats.total_pnl)
                expected_edge_bps = (pnl_total / trades * 10000) if trades > 0 else 0.0
                # US-257: profit_factor = gross_profit / gross_loss (amount ratio, not count ratio)
                _losing = self._stats.losing_pnl_sum
                profit_factor = (self._stats.winning_pnl_sum / _losing) if _losing > 0 else 10.0

                # US-255: per-strategy adjust if PerStrategyAdaptiveThreshold; fallback to global
                by_strategy = self._stats.by_strategy
                if by_strategy and hasattr(self._adaptive_threshold, "adjust"):
                    for _sid, _ss in list(by_strategy.items()):
                        if _ss.trades < 5:
                            continue
                        _wr = float(_ss.wins / _ss.trades)
                        _edge = (float(_ss.pnl) / _ss.trades * 10000)
                        self._adaptive_threshold.adjust(
                            strategy_id=_sid,
                            win_rate=_wr,
                            total_trades=_ss.trades,
                            expected_edge_bps=_edge,
                            profit_factor=profit_factor,
                        )
                new_edge = self._adaptive_threshold.adjust(
                    strategy_id="global",
                    win_rate=win_rate,
                    total_trades=trades,
                    expected_edge_bps=expected_edge_bps,
                    profit_factor=profit_factor,
                )
                logger.info(
                    "shadow.adaptive_threshold_adjusted",
                    new_edge_bps=new_edge,
                    win_rate=win_rate,
                    trades=trades,
                    note="shadow-local only, strategy_params.json not modified",
                )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("shadow._shadow_adaptive_threshold_loop error: %s", exc)

    def _shadow_params_hot_reload(self, shadow_params: dict) -> None:
        """US-234: Shadow 전용 파라미터 콜백 — live 파라미터 오염 방지.

        shadow_params: {"min_edge_bps": float, ...} 등 shadow 전용 dict.
        strategy_params.json 직접 수정 금지. shadow 인스턴스 내부 상태만 업데이트.
        """
        if not isinstance(shadow_params, dict):
            logger.warning("shadow._shadow_params_hot_reload: invalid params type")
            return

        new_edge = shadow_params.get("min_edge_bps")
        if new_edge is not None:
            try:
                new_edge_f = float(new_edge)
                if self._adaptive_threshold is not None:
                    # Bounds check: clamp to [min_edge, max_edge]
                    _min = getattr(self._adaptive_threshold, 'min_edge', 2.0)
                    _max = getattr(self._adaptive_threshold, 'max_edge', 50.0)
                    clamped = max(_min, min(_max, new_edge_f))
                    if clamped != new_edge_f:
                        logger.warning("shadow.hot_reload_clamped: %.2f -> %.2f", new_edge_f, clamped)
                    self._adaptive_threshold.current_edge_bps = clamped
                logger.info(
                    "shadow.params_hot_reloaded",
                    min_edge_bps=new_edge_f,
                    note="shadow-local only, live params unchanged",
                )
            except (TypeError, ValueError) as exc:
                logger.warning("shadow._shadow_params_hot_reload: invalid min_edge_bps: %s", exc)
