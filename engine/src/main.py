"""LEVIATHAN Engine Entry Point.

Engine lifecycle:
  1. Load configuration (Settings from env vars)
  2. Initialize infrastructure (EventBus — Redis or InMemory)
  3. Initialize exchange adapters (Paper, Sandbox, or Live)
  4. Initialize signal pipeline (PriceHub → CostCalculator → SignalGenerator)
  5. Initialize strategies (register with StrategyManager)
  6. Initialize risk (Guardian, CircuitBreaker, KillSwitch)
  7. Initialize execution (AtomicExecutor, TradeRequestConsumer)
  8. Start API server (REST + WebSocket)
  9. Start background loops (health, reconcile, heartbeat)
 10. Await shutdown signal (SIGTERM, SIGINT, Kill Switch)
 11. Graceful shutdown
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import uvicorn
from dotenv import load_dotenv

load_dotenv()  # Load .env before any os.getenv() calls

from src.api.server import EngineContext, create_app
from src.core.config import ExecutionMode, Settings, get_settings, load_trading_config

_s = get_settings().operational  # module-level operational settings shortcut

try:
    from src.tuning.scheduled_tuner import ScheduledTuner
    _HAS_TUNER = True
except ImportError:
    _HAS_TUNER = False

logger = logging.getLogger(__name__)

# Dynamic BTC reference price — read from env var, used for USDT→BTC position size conversion.
# Defaults to $50,000. Override via BTC_REFERENCE_PRICE env var for live/testnet.
_BTC_REFERENCE_PRICE = _s.btc_reference_price


class DataMode:
    """Data source mode for the engine."""
    SYNTHETIC = "synthetic"          # GBM paper data (default for PAPER mode)
    REAL_PUBLIC = "real_public"      # Real public WebSocket data (no API keys)
    REAL_AUTHENTICATED = "real_authenticated"  # Real data + trading API keys
    SHADOW = "shadow"                # Shadow mode: real data + paper execution + full metrics


@dataclass
class EngineState:
    """Internal engine lifecycle state."""
    running: bool = False
    kill_switch_active: bool = False
    background_tasks: list[Any] = field(default_factory=list)


class Engine:
    """
    LEVIATHAN engine orchestrator.

    Wires all subsystems together based on execution mode and data mode:
    - PAPER + SYNTHETIC:        InMemoryEventBus + PaperAdapters + GBM data
    - PAPER + REAL_PUBLIC:      InMemoryEventBus + PaperAdapters + real WebSocket data (no API keys)
    - PAPER + SHADOW:           InMemoryEventBus + PaperAdapters + real WS + ShadowMode + LiveGate
    - LIVE + REAL_AUTHENTICATED: Redis EventBus + NativeAdapters + real exchange data + API keys
    """

    RECONCILE_INTERVAL = 60
    HEALTH_CHECK_INTERVAL = 10
    HEARTBEAT_INTERVAL = 5
    SHUTDOWN_TIMEOUT = 10

    def __init__(self, context: EngineContext | None = None) -> None:
        self.context = context or EngineContext()
        self.state = EngineState()
        self._shutdown_event = asyncio.Event()

        # Subsystem references (populated during init)
        self._settings: Settings | None = None
        self._event_bus: Any = None
        self._exchanges: dict[str, Any] = {}
        self._price_hub: Any = None
        self._cost_calculator: Any = None
        self._signal_generator: Any = None
        self._strategy_manager: Any = None
        self._risk_guardian: Any = None
        self._circuit_breaker: Any = None
        self._executor: Any = None
        self._trade_consumer: Any = None
        self._position_manager: Any = None
        self._db_pool: Any = None
        self._market_recorder: Any = None
        self._telegram: Any = None
        self._collector_manager: Any = None
        self._paper_mode: Any = None
        self._live_gate: Any = None
        self._data_mode: str = DataMode.SYNTHETIC
        # US-114/115/117/118: Wave 3 modules
        self._correlation_monitor: Any = None
        self._slippage_feedback: Any = None
        self._dynamic_sizer: Any = None
        self._telegram_cmd_handler: Any = None
        # US-291: Phase S20 TradeBot (InfraBot/DevBot → bot-gateway)
        self._trade_bot: Any = None
        self._tca_analyzer: Any = None  # US-116
        self._rebalancer: Any = None  # US-120
        self._balance_tracker: Any = None  # US-120
        # US-129: Position tracking for RiskGuardian PortfolioState
        self._position_sizes: dict[str, Decimal] = {}   # symbol -> current notional exposure
        self._peak_equity: Decimal | None = None           # initialized to capital_total on first risk check
        self._total_pnl: Decimal = Decimal("0")          # cumulative realized PnL
        self._exchange_health: dict[str, Decimal] = {}   # exchange_id -> health score (0-1)
        # US-131: RegimeDetector reference (set during _init_signal_pipeline)
        self._regime_detector: Any = None
        # US-133: AtomicOrderExecutor for live IOC execution
        self._atomic_order_executor: Any = None
        # US-146: ScheduledTuner (optional, enabled via ENABLE_INLINE_TUNER)
        self._scheduled_tuner: Any = None
        # US-165: Redis client reference for explicit close on shutdown
        self._redis_client: Any = None
        # US-170: TriangularScanner for triangular arb signal detection
        self._triangular_scanner: Any = None
        # US-169/173: MultiStrategySignalProducer ref shared across loops
        self._multi_signal_producer: Any = None
        # US-174: AdaptiveThreshold for dynamic MIN_EDGE adjustment
        self._adaptive_threshold: Any = None
        # US-175: ExposureTracker for net exposure tracking
        self._exposure_tracker: Any = None
        # Bug 1-F: shared HTTP client for FundingRateCollector (initialized in _init_infrastructure)
        self._http_client: Any = None
        # US-250: PositionRecovery + PositionReconciler
        self._position_recovery: Any = None
        self._position_reconciler: Any = None
        # ER2-22: RecoveryManager (WAL replay on Redis restart)
        self._recovery_manager: Any = None
        # US-284-b/a: Attribution + CapitalAllocator (wired in _populate_context)
        self._attribution: Any = None
        self._capital_allocator: Any = None
        # US-277/278: PortfolioRiskManager
        self._portfolio_risk: Any = None
        # US-286: DataQualityManager
        self._data_quality_manager: Any = None
        # SIT-3: FlashGuard — rapid price movement detection
        self._flash_guard: Any = None
        # US-351: BacktestMode result (populated by _backtest_mode_task)
        self._backtest_result: Any = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Full engine startup sequence. Blocks until shutdown signal."""
        logger.info("LEVIATHAN engine starting...")
        self._setup_signal_handlers()
        # ER4-03: log WARNING when any asyncio callback exceeds 100ms
        loop = asyncio.get_event_loop()
        loop.slow_callback_duration = 0.1

        try:
            await self._init_config()
            await self._init_infrastructure()
            await self._init_exchanges()
            await self._init_signal_pipeline()
            await self._init_strategies()
            await self._init_risk()
            await self._init_execution()
            await self._populate_context()
            await self._startup_position_scan()
            await self._startup_compliance_audit()
            await self._start_background_tasks()
            await self._init_tuner()

            self.state.running = True
            self.context.running = True
            logger.info("Engine running in %s mode — waiting for shutdown signal",
                        self._settings.execution_mode if self._settings else "unknown")
            await self._shutdown_event.wait()
        except Exception as exc:
            logger.critical("Engine startup failed: %s", exc, exc_info=True)
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Graceful shutdown."""
        if not self.state.running and not self.state.background_tasks:
            return

        logger.info("Engine shutting down...")
        self.state.running = False
        self.context.running = False
        self._shutdown_event.set()

        # Stop trade consumer
        if self._trade_consumer:
            try:
                await self._trade_consumer.stop()
            except Exception as exc:
                logger.warning("TradeConsumer stop error: %s", exc)

        # Stop strategy manager
        if self._strategy_manager:
            try:
                await self._strategy_manager.stop()
            except Exception as exc:
                logger.warning("StrategyManager stop error: %s", exc)

        # US-155: Cancel open orders in live mode before disconnecting
        if (self._settings is not None
                and self._settings.execution_mode == "live"
                and self._exchanges):
            await self._cancel_open_orders()

        # Disconnect exchanges
        for eid, adapter in self._exchanges.items():
            try:
                await adapter.disconnect()
            except Exception as exc:
                logger.warning("Exchange %s disconnect error: %s", eid, exc)

        # Stop Shadow Mode
        if self._paper_mode:
            try:
                await self._paper_mode.stop()
            except Exception as exc:
                logger.warning("ShadowMode stop error: %s", exc)

        # Stop LiveGate
        if self._live_gate:
            try:
                await self._live_gate.stop_auto_evaluation()
            except Exception as exc:
                logger.warning("LiveGate stop error: %s", exc)

        # Stop MarketRecorder
        if self._market_recorder:
            try:
                await self._market_recorder.stop()
            except Exception as exc:
                logger.warning("MarketRecorder stop error: %s", exc)

        # Close DB pool
        if self._db_pool:
            try:
                await self._db_pool.close()
            except Exception as exc:
                logger.warning("DB pool close error: %s", exc)

        # Bug 1-F: Close shared HTTP client
        if self._http_client is not None:
            try:
                await self._http_client.aclose()
                logger.info("HTTP client closed")
            except Exception as exc:
                logger.warning("HTTP client close error: %s", exc)

        # US-165: Close Redis connection explicitly
        if self._redis_client:
            try:
                await self._redis_client.disconnect()
                logger.info("Redis connection closed")
            except Exception as exc:
                logger.warning("Redis disconnect error: %s", exc)

        # US-168: Close Telegram HTTP clients
        if hasattr(self, '_telegram') and self._telegram:
            try:
                await self._telegram.close()
            except Exception as exc:
                logger.warning("Telegram close error: %s", exc)
        # Phase S21: TelegramCommandHandler removed
        # Close TradeBot (InfraBot/DevBot → bot-gateway)
        if hasattr(self, '_trade_bot') and self._trade_bot:
            try:
                await self._trade_bot.close()
            except Exception as exc:
                logger.warning("TradeBot close error: %s", exc)

        # Stop ScheduledTuner
        if self._scheduled_tuner:
            try:
                self._scheduled_tuner.stop()
            except Exception as exc:
                logger.warning("ScheduledTuner stop error: %s", exc)

        # Cancel background tasks
        pending_tasks = [t for t in self.state.background_tasks if not t.done()]
        logger.info("shutdown_remaining_tasks count=%d", len(pending_tasks))
        for task in pending_tasks:
            task.cancel()
        if self.state.background_tasks:
            await asyncio.wait(self.state.background_tasks, timeout=self.SHUTDOWN_TIMEOUT)
        self.state.background_tasks.clear()

        logger.info("Engine shutdown complete")

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _setup_signal_handlers(self) -> None:
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._handle_signal)
            except (NotImplementedError, RuntimeError):
                pass

    def _handle_signal(self) -> None:
        logger.warning("Shutdown signal received")
        self._shutdown_event.set()

    # ------------------------------------------------------------------
    # Step 1: Configuration
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_trading_json_defaults(cfg: dict) -> None:
        """Inject trading.json values as env var defaults (env vars take priority)."""

        def _setdefault(key: str, value: object) -> None:
            if key not in os.environ:
                os.environ[key] = json.dumps(value) if isinstance(value, list) else str(value)

        if "active_exchanges" in cfg:
            _setdefault("TRADING_ACTIVE_EXCHANGES", cfg["active_exchanges"])

        sym = cfg.get("symbol_discovery", {})
        if "min_exchanges" in sym:
            _setdefault("TRADING_SYMBOL_MIN_EXCHANGES", sym["min_exchanges"])

        exe = cfg.get("execution", {})
        for _k, _env in [
            ("leg_timeout_ms", "LEG_TIMEOUT_MS"),
            ("rollback_timeout_ms", "ROLLBACK_TIMEOUT_MS"),
            ("reconciliation_interval_s", "RECONCILIATION_INTERVAL_S"),
        ]:
            if _k in exe:
                _setdefault(_env, exe[_k])

        pg = cfg.get("phase_gates", {})
        if "phase" in pg:
            _setdefault("CAPITAL_TIER", pg["phase"])
        if "alpha_capital_per_exchange" in pg:
            _setdefault("CAPITAL_INITIAL_CAPITAL", pg["alpha_capital_per_exchange"])

        risk = cfg.get("risk", {})
        if "max_rollback_threshold" in risk:
            _setdefault("RISK_MAX_ROLLBACK_THRESHOLD", risk["max_rollback_threshold"])

        # US-162: Volume filter threshold
        if "min_volume_usd" in cfg:
            _setdefault("SIGNAL_MIN_VOLUME_USD", cfg["min_volume_usd"])

        # US-156/164: Shadow disabled strategies and single loss defense
        if "disabled_strategies" in cfg:
            _setdefault("SHADOW_DISABLED_STRATEGIES", ",".join(cfg["disabled_strategies"]))
        if "max_single_loss_usd" in cfg:
            _setdefault("SHADOW_MAX_LOSS_PER_TRADE_USD", cfg["max_single_loss_usd"])

    async def _init_config(self) -> None:
        # Load non-sensitive config from trading.json; env vars (.env) take priority.
        _tcfg = load_trading_config()
        if _tcfg:
            self._apply_trading_json_defaults(_tcfg)

        # Convert TRADING_SYMBOLS=auto to valid JSON before pydantic-settings parsing.
        # pydantic-settings tries json.loads() on list[str] fields; "auto" is not valid JSON.
        raw_symbols = os.environ.get("TRADING_SYMBOLS", "").strip()
        if raw_symbols.lower() == "auto":
            os.environ["TRADING_SYMBOLS"] = '["auto"]'

        try:
            self._settings = get_settings()
            self.context.environment = self._settings.engine_env
            self.context.execution_mode = self._settings.execution_mode
            logger.info("Config loaded — env=%s mode=%s capital_tier=%s",
                        self._settings.engine_env,
                        self._settings.execution_mode,
                        self._settings.capital.tier)
        except Exception as exc:
            logger.warning("Config load failed (using defaults): %s", exc)
            self._settings = Settings()
            self.context.environment = "dev"
            self.context.execution_mode = "paper"

        # Auto-discover trading symbols from exchange APIs
        await self._resolve_symbols()

    async def _resolve_symbols(self) -> None:
        """Resolve 'auto' symbols to actual trading pairs via exchange API discovery.

        When TRADING_SYMBOLS=auto, queries Binance/Upbit/Bithumb APIs to find
        symbols common to >= min_exchanges. New listings are picked up on restart;
        delistings are excluded automatically.
        """
        if not self._settings or self._settings.trading.symbols != ["auto"]:
            return

        from src.collectors.symbol_discovery import discover_common_symbols

        min_ex = self._settings.trading.symbol_min_exchanges
        try:
            symbols = await discover_common_symbols(min_exchanges=min_ex)
            if symbols:
                self._settings.trading.symbols = symbols
                logger.info(
                    "Auto-discovered %d trading symbols (min_exchanges=%d)",
                    len(symbols), min_ex,
                )
            else:
                self._settings.trading.symbols = ["BTC/USDT", "ETH/USDT", "XRP/USDT"]
                logger.warning(
                    "Symbol auto-discovery returned empty — using fallback 3 symbols"
                )
        except Exception as exc:
            self._settings.trading.symbols = ["BTC/USDT", "ETH/USDT", "XRP/USDT"]
            logger.warning(
                "Symbol auto-discovery failed (using fallback): %s", exc
            )

        # US-241: Append triangular cross-pairs for cross-pair arbitrage
        _op = getattr(self._settings, "operational", None)
        cross_pairs_env = getattr(_op, "triangular_cross_pairs", None) if _op else None
        if cross_pairs_env and self._settings:
            cross_pairs = [p.strip() for p in cross_pairs_env.split(",") if p.strip()]
            existing = set(self._settings.trading.symbols)
            added = []
            for cp in cross_pairs:
                if cp not in existing:
                    self._settings.trading.symbols.append(cp)
                    existing.add(cp)
                    added.append(cp)
            if added:
                logger.info("US-241: Added %d triangular cross-pairs: %s", len(added), added)

    # ------------------------------------------------------------------
    # Step 2: Infrastructure (EventBus)
    # ------------------------------------------------------------------

    async def _init_infrastructure(self) -> None:
        from src.core.config import EngineMode, resolve_engine_mode, load_engine_config

        # Phase H-2: resolve mode from config/engine.json (not .env)
        _engine_cfg = load_engine_config()
        self._engine_mode = resolve_engine_mode(
            engine_mode=_engine_cfg.get("mode"),
        )

        # Bug 1-F: initialize shared HTTP client for FundingRateCollector
        import httpx
        self._http_client = httpx.AsyncClient(timeout=10.0)

        # EventBus: only LIVE mode uses Redis, all others use InMemory (direct routing)
        if self._engine_mode == EngineMode.LIVE:
            try:
                from urllib.parse import urlparse
                from src.infra.redis.client import RedisClient, RedisConfig
                from src.infra.redis.event_bus import EventBus
                _parsed = urlparse(self._settings.redis.url)
                redis_config = RedisConfig(
                    host=_parsed.hostname or "localhost",
                    port=_parsed.port or 6379,
                    db=int((_parsed.path or "/0").lstrip("/") or "0"),
                    password=_parsed.password,
                    max_connections=self._settings.redis.max_connections,
                )
                redis_client = RedisClient(redis_config)
                await redis_client.connect()
                self._redis_client = redis_client
                self._event_bus = EventBus(redis_client)
                logger.info("Redis EventBus initialized (live mode)")
            except Exception as exc:
                logger.warning("Redis init failed, falling back to InMemoryEventBus: %s", exc)
                from src.infra.redis.memory_bus import InMemoryEventBus
                self._event_bus = InMemoryEventBus()
        else:
            from src.infra.redis.memory_bus import InMemoryEventBus
            self._event_bus = InMemoryEventBus()
            logger.info("InMemoryEventBus initialized (engine_mode=%s)", self._engine_mode)

        # --- TimescaleDB + MarketRecorder ---
        await self._init_database()

        # --- Telegram Alerter ---
        self._init_telegram()

        # --- Rust PyO3 Bridge (log feature flags) ---
        self._init_rust_bridge()

    async def _init_database(self) -> None:
        """Initialize TimescaleDB connection pool, run schema migration, start MarketRecorder."""
        dsn = get_settings().operational.database_url
        if not dsn:
            logger.warning("DATABASE_URL not set — using default dev credentials")
            dsn = "postgresql://leviathan:leviathan@localhost:5432/leviathan"
        # asyncpg needs raw postgres:// DSN (not postgresql+asyncpg://)
        asyncpg_dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")

        try:
            from src.infra.db.connection import DatabasePool
            self._db_pool = DatabasePool(dsn=asyncpg_dsn, min_size=2, max_size=10)
            await self._db_pool.initialize()
            logger.info("TimescaleDB connection pool initialized")

            # Run schema migration
            try:
                from src.infra.db.migration_runner import run_migrations
                await run_migrations(self._db_pool.pool)
                logger.info("TimescaleDB schema migration applied")
            except Exception as exc:
                logger.warning("Schema migration failed (non-fatal): %s", exc)

            # Check .env sync (root vs engine)
            try:
                from src.modes.preflight import PreflightChecker
                PreflightChecker()._check_env_sync()
            except Exception:
                pass  # Non-fatal — preflight not required for startup

            # Start MarketRecorder
            try:
                from src.infra.db.market_recorder import MarketRecorder
                self._market_recorder = MarketRecorder(pool=self._db_pool.pool)
                await self._market_recorder.start()
                logger.info("MarketRecorder started (flush=%dms, buffer=%d)",
                            MarketRecorder.FLUSH_INTERVAL_MS, MarketRecorder.MAX_BUFFER_SIZE)
            except Exception as exc:
                logger.warning("MarketRecorder init failed (non-fatal): %s", exc)

            # Load historical trades into PerformanceAttribution
            try:
                from src.analysis.attribution import PerformanceAttribution
                self._attribution = PerformanceAttribution()
                await self._attribution.load_from_db(self._db_pool.pool)
            except Exception as exc:
                logger.warning("PerformanceAttribution init failed (non-fatal): %s", exc)

            # US-284-a: CapitalAllocator init
            _op = get_settings().operational
            if _op.capital_allocator_enabled:
                try:
                    from src.core.capital_allocator import CapitalAllocator
                    _max_pos = _op.max_position_usd
                    self._capital_allocator = CapitalAllocator(total_capital=_max_pos * 10)
                    logger.info("CapitalAllocator initialized: total_capital=%.0f", _max_pos * 10)
                except Exception as exc:
                    logger.warning("CapitalAllocator init failed (non-fatal): %s", exc)

            # US-277/278: PortfolioRiskManager init
            if _op.portfolio_risk_enabled:
                try:
                    from src.core.portfolio_risk import PortfolioRiskManager
                    self._portfolio_risk = PortfolioRiskManager()
                    logger.info("PortfolioRiskManager initialized")
                except Exception as exc:
                    logger.warning("PortfolioRiskManager init failed (non-fatal): %s", exc)
        except Exception as exc:
            logger.warning("TimescaleDB init failed (non-fatal, paper mode ok): %s", exc)

    def _init_telegram(self) -> None:
        """Initialize 3-Bot Telegram system (Trade/Infra/Dev) from environment variables.

        Phase S21: Legacy TelegramAlerter/SmartTelegramAlerter/TelegramCommandHandler removed.
        All alerting goes through the 3-Bot system.
        """
        # Trade봇: 거래 알림 + Kill Switch + 포지션/체결/전략 제어
        try:
            from src.infra.telegram_trade_bot import TradeTelegramBot
            self._trade_bot = TradeTelegramBot(engine_context=self)
            # Backward compat: self._telegram points to trade_bot for legacy callers
            self._telegram = self._trade_bot
            if self._trade_bot.enabled:
                logger.info("TradeTelegramBot enabled")
            else:
                logger.info("TradeTelegramBot disabled")
        except Exception as exc:
            logger.warning("TradeTelegramBot init failed (non-fatal): %s", exc)

        # Infra봇 + Dev봇: bot-gateway 독립 프로세스
        # See: python -m src.bot_gateway / docker compose up bot-gateway
        logger.info("InfraBot/DevBot → bot-gateway (독립 프로세스)")

    def _init_rust_bridge(self) -> None:
        """Log Rust PyO3 feature flag status."""
        try:
            from src.core.rust_bridge import get_feature_flags
            flags = get_feature_flags()
            logger.info("Rust bridge flags: %s", flags)
        except Exception as exc:
            logger.warning("Rust bridge init failed (non-fatal): %s", exc)

    async def _init_tuner(self) -> None:
        """Initialize ScheduledTuner if ENABLE_INLINE_TUNER is set (US-146)."""
        if not _HAS_TUNER:
            logger.info("ScheduledTuner not available (optuna/apscheduler not installed)")
            return
        if get_settings().operational.enable_inline_tuner.lower() not in ("true", "1", "yes"):
            logger.info("Inline tuner disabled (ENABLE_INLINE_TUNER not set)")
            return
        try:
            self._scheduled_tuner = ScheduledTuner()

            # US-179: Hot-reload callback — update SignalConfig.min_edge when params change
            def _tuner_reload_callback() -> None:
                try:
                    import json
                    import pathlib
                    params_path = pathlib.Path(__file__).parent.parent / "config" / "strategy_params.json"
                    if params_path.exists() and self._signal_generator is not None:
                        params = json.loads(params_path.read_text())
                        # Apply cross_exchange min_spread as the runtime min_edge if available
                        ce = params.get("cross_exchange", {})
                        if ce.get("status") in ("READY", "MONITOR") and "min_spread_bps" in ce:
                            new_edge = Decimal(str(ce["min_spread_bps"])) / Decimal("10000")
                            if hasattr(self._signal_generator, "_config"):
                                self._signal_generator._config.min_edge = new_edge
                                logger.info(
                                    "ScheduledTuner hot-reload: min_edge updated to %.2f bps",
                                    float(ce["min_spread_bps"]),
                                )
                        # US-326: hot-reload slippage_buffer
                        if ce.get("slippage_buffer_bps") is not None:
                            if hasattr(self._signal_generator, "_config"):
                                self._signal_generator._config.slippage_buffer_bps = Decimal(
                                    str(ce["slippage_buffer_bps"])
                                )
                except Exception as exc:
                    logger.warning("ScheduledTuner hot-reload failed: %s", exc)

            self._scheduled_tuner._reload_callback = _tuner_reload_callback
            self._scheduled_tuner.start_scheduler()
            logger.info("Scheduled tuner started (with hot-reload callback)")
        except Exception as exc:
            logger.warning("Failed to start scheduled tuner: %s", exc)

    # ------------------------------------------------------------------
    # Step 3: Exchange Adapters
    # ------------------------------------------------------------------

    async def _init_exchanges(self) -> None:
        from src.core.config import EngineMode
        capital = self._settings.capital.initial_capital if self._settings else Decimal("70")

        # Phase H-2: route by EngineMode from config/engine.json
        # Use getattr fallback for test scenarios where full _init_infrastructure hasn't run
        _engine_mode = getattr(self, "_engine_mode", None)
        if _engine_mode is None:
            # Derive from execution_mode when _engine_mode is not yet initialised
            from src.core.config import ExecutionMode
            _exec = getattr(self._settings, "execution_mode", None) if self._settings else None
            _engine_mode = (
                EngineMode.PAPER
                if _exec in (ExecutionMode.PAPER, None)
                else EngineMode.LIVE
            )
        if _engine_mode in (EngineMode.BACKTEST, EngineMode.PAPER):
            await self._init_paper_exchanges(capital)
        elif _engine_mode in (EngineMode.SHADOW, EngineMode.LIVE):
            await self._init_live_exchanges()

        logger.info("Initialized %d exchange adapters: %s",
                     len(self._exchanges), list(self._exchanges.keys()))

    async def _init_paper_exchanges(self, capital: Decimal) -> None:
        from src.execution.paper import PaperExecutor, SlippageModel
        from src.execution.paper_adapter import PaperExchangeAdapter

        # Create 2+ paper exchanges with different spread injection profiles
        # to simulate cross-exchange arbitrage opportunities
        configs = [
            {
                "exchange_id": "paper_binance",
                "spread_injection_rate": 0.03,  # 3% of ticks
                "spread_injection_bps": 25,
            },
            {
                "exchange_id": "paper_okx",
                "spread_injection_rate": 0.03,
                "spread_injection_bps": -25,  # Opposite direction
            },
        ]

        for cfg in configs:
            executor = PaperExecutor(
                fee_rate=Decimal("0.001"),
                slippage_model=SlippageModel(base_slippage_pct=Decimal("0.0005")),
            )
            adapter = PaperExchangeAdapter(
                exchange_id=cfg["exchange_id"],
                initial_capital=capital,
                paper_executor=executor,
                spread_injection_rate=cfg["spread_injection_rate"],
                spread_injection_bps=cfg["spread_injection_bps"],
                tick_interval=0.5,  # 500ms per tick for paper mode
            )
            await adapter.connect()
            self._exchanges[cfg["exchange_id"]] = adapter

    async def _init_sandbox_exchanges(self) -> None:
        use_native = (
            self._settings.trading.use_native_adapters if self._settings else False
        )
        exchanges = (
            self._settings.trading.active_exchanges if self._settings
            else ["binance", "bybit", "okx", "bitget"]
        )
        if use_native:
            await self._init_native_exchanges(exchanges, sandbox=True)
        else:
            logger.info("Sandbox exchange initialization — CCXTAdapter (sandbox=True)")
            # TODO: Phase 6 — create CCXTAdapters with sandbox=True

    async def _init_live_exchanges(self) -> None:
        from src.core.config import load_engine_config

        # Phase H-2: Shadow uses shadow.exchanges, Live uses live.exchanges from config
        _engine_cfg = load_engine_config()
        _em = getattr(self, "_engine_mode", None)
        _mode_cfg = _engine_cfg.get(_em.value, {}) if _em is not None else {}
        _cfg_exchanges = _mode_cfg.get("exchanges")

        exchanges = (
            _cfg_exchanges
            or (self._settings.trading.active_exchanges if self._settings
                else ["binance", "bybit", "okx", "bitget"])
        )

        # Native adapters are the default for shadow/live (ccxt-free)
        await self._init_native_exchanges(exchanges, sandbox=False)

    async def _init_native_exchanges(self, exchanges: list[str], sandbox: bool) -> None:
        """Create and connect native (ccxt-free) adapters for each exchange."""
        from src.infra.exchange import create_native_adapter

        ex_cfg = self._settings.exchange if self._settings else None
        for eid in exchanges:
            try:
                api_key = getattr(ex_cfg, f"{eid}_api_key", "") if ex_cfg else ""
                api_secret = getattr(ex_cfg, f"{eid}_api_secret", "") if ex_cfg else ""
                passphrase = getattr(ex_cfg, f"{eid}_passphrase", "") if ex_cfg else ""
                adapter = create_native_adapter(
                    exchange_id=eid,
                    api_key=api_key,
                    api_secret=api_secret,
                    passphrase=passphrase,
                    sandbox=sandbox,
                )
                await adapter.connect()
                self._exchanges[eid] = adapter
                logger.info("Native adapter connected: %s (sandbox=%s)", eid, sandbox)
            except ValueError as exc:
                logger.warning("Native adapter not available for %s: %s", eid, exc)
            except Exception as exc:
                logger.warning("Native adapter connect failed for %s: %s", eid, exc)

    # ------------------------------------------------------------------
    # Step 4: Signal Pipeline
    # ------------------------------------------------------------------

    async def _init_signal_pipeline(self) -> None:
        from src.core.price_hub import PriceHub
        from src.core.signal import SignalConfig, SignalGenerator
        from src.core.stale_detector import StaleOrderbookDetector
        from src.friction.cost_calculator import CostCalculator
        from src.friction.fee_model import FeeModel
        from src.friction.slippage_model import CEXOrderbookSlippage

        self._price_hub = PriceHub()

        # Build cost calculator with fee and slippage models
        try:
            fee_model = FeeModel()
            slippage_model = CEXOrderbookSlippage()
            self._cost_calculator = CostCalculator(
                fee_model=fee_model,
                slippage_model=slippage_model,
            )
        except Exception as exc:
            logger.warning("CostCalculator init failed, using stub: %s", exc)
            self._cost_calculator = None

        _op = get_settings().operational
        min_edge_bps = _op.min_edge_bps
        max_spread_pct = _op.max_spread_pct
        cooldown_sec = _op.signal_cooldown_sec
        min_price_usd = _op.min_price_usd
        # US-326/327: load slippage_buffer + active_hours from strategy_params.json
        _ce_params = self._load_strategy_params().get("cross_exchange", {})
        _slippage_buf = Decimal(str(_ce_params.get("slippage_buffer_bps", 0)))
        # US-327: MONITOR strategies get time-gated (KST 09-21); READY = always active
        _active_hours = (9, 21) if _ce_params.get("status") == "MONITOR" else None
        signal_config = SignalConfig(
            min_edge=Decimal(str(min_edge_bps)) / Decimal("10000"),  # bps → fraction
            max_spread_pct=Decimal(str(max_spread_pct)),
            cooldown_seconds=cooldown_sec,
            min_price_usd=min_price_usd,
            min_volume_usd=_op.signal_min_volume_usd,
            slippage_buffer_bps=_slippage_buf,  # US-326
            active_hours_kst=_active_hours,  # US-327
        )
        stale_detector = StaleOrderbookDetector(
            deviation_pct=_op.stale_cross_deviation_pct,
            blacklist_ttl_s=_op.stale_blacklist_ttl_s,
        )
        # US-131: RegimeDetector — try HMM first, fall back to threshold-based
        self._regime_detector = None
        try:
            from src.tuning.regime_detector import HMMRegimeDetector
            self._regime_detector = HMMRegimeDetector()
            logger.info("HMMRegimeDetector initialized")
        except (ImportError, Exception) as exc:
            logger.info("HMMRegimeDetector unavailable (%s), trying threshold-based", exc)
            try:
                from src.tuning.regime_detector import RegimeDetector
                self._regime_detector = RegimeDetector()
                logger.info("RegimeDetector (threshold-based) initialized")
            except Exception as exc2:
                logger.warning("RegimeDetector init failed (non-fatal): %s", exc2)

        # US-131: ONNXSignalScorer — graceful fallback if onnxruntime not installed
        ml_scorer = None
        try:
            from src.ml.onnx_runtime import ONNXSignalScorer
            ml_scorer = ONNXSignalScorer()
            logger.info("ONNXSignalScorer initialized")
        except ImportError:
            logger.info("ONNXSignalScorer not available (onnxruntime not installed)")
        except Exception as exc:
            logger.warning("ONNXSignalScorer init failed (non-fatal): %s", exc)

        # US-253: MLFeaturePipeline — graceful fallback
        ml_feature_pipeline = None
        try:
            from src.ml.feature_pipeline import MLFeaturePipeline
            ml_feature_pipeline = MLFeaturePipeline()
            logger.info("MLFeaturePipeline initialized")
        except ImportError:
            logger.info("MLFeaturePipeline not available")
        except Exception as exc:
            logger.warning("MLFeaturePipeline init failed (non-fatal): %s", exc)

        # US-253: MLCanary staged rollout (10% → 50% → 100%) — graceful fallback
        ml_canary = None
        try:
            from src.ml.canary import MLCanary
            ml_canary = MLCanary(
                ml_scorer=ml_scorer,
                min_signals_to_promote=50,
                min_pnl_delta=0.0,
                auto_promote=True,
            )
            if ml_scorer is not None:
                ml_canary.start()  # begin at 10% ML traffic
            logger.info("MLCanary initialized (stage=%s)", ml_canary.stage.value)
        except ImportError:
            logger.info("MLCanary not available")
        except Exception as exc:
            logger.warning("MLCanary init failed (non-fatal): %s", exc)

        # US-174/255: PerStrategyAdaptiveThreshold for dynamic MIN_EDGE per strategy
        # Must be created BEFORE SignalGenerator so it can be injected
        try:
            from src.tuning.adaptive_threshold import PerStrategyAdaptiveThreshold
            self._adaptive_threshold = PerStrategyAdaptiveThreshold(
                default_edge_bps=float(min_edge_bps),
            )
            logger.info("PerStrategyAdaptiveThreshold initialized (initial_edge_bps=%s)", min_edge_bps)
        except Exception as exc:
            logger.warning("AdaptiveThreshold init failed (non-fatal): %s", exc)

        # US-283: SlippageFeedbackCollector — per-exchange/pair slippage adjustment
        _slippage_fb_collector = None
        try:
            from src.friction.slippage_feedback import SlippageFeedbackCollector
            _slippage_fb_collector = SlippageFeedbackCollector()
            logger.info("SlippageFeedbackCollector initialized")
        except Exception as exc:
            logger.warning("SlippageFeedbackCollector init failed (non-fatal): %s", exc)

        self._signal_generator = SignalGenerator(
            price_hub=self._price_hub,
            cost_calculator=self._cost_calculator,
            config=signal_config,
            event_bus=self._event_bus,
            stale_detector=stale_detector,
            regime_detector=self._regime_detector,
            ml_scorer=ml_scorer,
            ml_feature_pipeline=ml_feature_pipeline,
            ml_canary=ml_canary,
            adaptive_threshold=self._adaptive_threshold,
            slippage_feedback=_slippage_fb_collector,
        )

        # US-170: TriangularScanner
        try:
            from src.core.triangular_scanner import TriangularScanner
            self._triangular_scanner = TriangularScanner(
                min_profit_bps=Decimal(str(min_edge_bps)),
            )
            logger.info("TriangularScanner initialized (min_profit_bps=%s)", min_edge_bps)
        except Exception as exc:
            logger.warning("TriangularScanner init failed (non-fatal): %s", exc)

        logger.info(
            "Signal pipeline initialized min_edge_bps=%s max_spread_pct=%s stale_deviation_pct=%s"
            " regime_detector=%s ml_scorer=%s",
            min_edge_bps, max_spread_pct, get_settings().operational.stale_cross_deviation_pct,
            type(self._regime_detector).__name__ if self._regime_detector else "None",
            type(ml_scorer).__name__ if ml_scorer else "None",
        )

    # ------------------------------------------------------------------
    # Step 5: Strategies
    # ------------------------------------------------------------------

    async def _init_strategies(self) -> None:
        from src.strategies.manager import StrategyManager

        # US-236: PositionRegistry for symbol-level lock in strategy dispatch
        try:
            from src.core.position_registry import PositionRegistry
            _position_registry = PositionRegistry()
            logger.info("PositionRegistry initialized for StrategyManager")
        except Exception as exc:
            logger.warning("PositionRegistry init failed (non-fatal): %s", exc)
            _position_registry = None

        self._strategy_manager = StrategyManager(
            event_bus=self._event_bus,
            consumer_name="manager-0",
            position_registry=_position_registry,
        )

        # Register default strategies based on available exchanges
        try:
            await self._register_default_strategies()
        except Exception as exc:
            logger.warning("Strategy registration failed: %s", exc)

        logger.info("StrategyManager initialized with %d strategies",
                     len(self._strategy_manager._strategies))

    def _load_strategy_params(self) -> dict:
        """Load tuned strategy parameters from config/strategy_params.json."""
        import json
        import pathlib
        params_path = pathlib.Path(__file__).parent.parent / "config" / "strategy_params.json"
        if not params_path.exists():
            logger.info("No tuned strategy params found at %s, using defaults", params_path)
            return {}
        try:
            with open(params_path) as f:
                params = json.load(f)
            logger.info("Loaded tuned strategy params from %s", params_path)
            return params
        except Exception as exc:
            logger.warning("Failed to load strategy params: %s", exc)
            return {}

    async def _register_default_strategies(self) -> None:
        """Register all 8 available strategies with tuned parameters."""
        from src.core.latency_tracker import LatencyTracker
        from src.strategies.cross_exchange import CrossExchangeConfig, CrossExchangeStrategy
        from src.strategies.spot_futures import SpotFuturesConfig, SpotFuturesStrategy
        from src.strategies.futures_futures import FuturesFuturesConfig, FuturesFuturesStrategy
        from src.strategies.triangular import TriangularConfig, TriangularStrategy
        from src.strategies.funding_rate import FundingRateConfig, FundingRateStrategy
        from src.strategies.statistical_arb import StatisticalArbStrategy
        # latency_arb merged into cross_exchange (US-194) — no separate import needed

        # Use a simple stub if CostCalculator didn't initialize
        cost_calc = self._cost_calculator
        if cost_calc is None:
            cost_calc = _StubCostCalculator()

        # Shared latency tracker for latency_boost mode in CrossExchangeStrategy (US-194)
        self._latency_tracker = LatencyTracker()

        # Load tuned parameters (READY/MONITOR strategies only)
        tuned = self._load_strategy_params()

        # Phase H-Final: Dynamic capital-based sizing
        # All position/depth limits are % of capital, not fixed USD
        from src.core.config import load_engine_config
        _ecfg = load_engine_config()
        _cap_cfg = _ecfg.get("capital", {})
        _tier = _cap_cfg.get("tier", "alpha")
        _capital_usd = Decimal(str(
            _cap_cfg.get("tiers", {}).get(_tier, {}).get("initial_usd", 70)
        ))
        _risk_cfg = _ecfg.get("dynamic_risk", {})
        _base_pos_pct = Decimal(str(_risk_cfg.get("base_position_pct", 3.0))) / Decimal("100")
        _max_pos_usd = _capital_usd * _base_pos_pct  # e.g., $34 × 3% = $1.02
        _book_depth_usd = max(Decimal("1"), _capital_usd * Decimal("0.01"))  # 1% of capital, min $1

        logger.info(
            "Strategy sizing: capital=$%.0f tier=%s max_position=$%.2f book_depth=$%.2f",
            _capital_usd, _tier, _max_pos_usd, _book_depth_usd,
        )

        # Build strategy configs from tuned params + dynamic capital sizing
        sf_p = tuned.get("spot_futures", {})
        sf_config = SpotFuturesConfig(
            min_basis_bps=Decimal(str(sf_p.get("min_basis_bps", 15))),
            max_position_size=_max_pos_usd / _BTC_REFERENCE_PRICE,
        ) if sf_p.get("status") in ("READY", "MONITOR") else None

        fr_p = tuned.get("funding_rate", {})
        fr_config = FundingRateConfig(
            min_funding_diff_bps=Decimal(str(fr_p.get("min_funding_diff_bps", 5))),
            max_position_size=_max_pos_usd / _BTC_REFERENCE_PRICE,
        ) if fr_p.get("status") in ("READY", "MONITOR") else None

        ce_p = tuned.get("cross_exchange", {})
        ce_config = CrossExchangeConfig(
            min_spread_bps=Decimal(str(ce_p.get("min_spread_bps", 10))),
            max_position_size=_max_pos_usd / _BTC_REFERENCE_PRICE,
            min_book_depth_usd=_book_depth_usd,
        ) if ce_p.get("status") in ("READY", "MONITOR") else None

        ff_p = tuned.get("futures_futures", {})
        ff_config = FuturesFuturesConfig(
            min_spread_bps=Decimal(str(ff_p.get("min_spread_bps", 8))),
            max_position_size=_max_pos_usd / _BTC_REFERENCE_PRICE,
            min_book_depth_usd=_book_depth_usd,
        ) if ff_p.get("status") in ("READY", "MONITOR") else None

        tri_p = tuned.get("triangular", {})
        tri_config = TriangularConfig(
            min_profit_bps=Decimal(str(tri_p.get("min_profit_bps", 10))),
            max_position_usdt=_max_pos_usd,
        ) if tri_p.get("status") in ("READY", "MONITOR") else None

        # Load disabled strategies from strategy_activation.json
        import pathlib
        _activation_path = pathlib.Path(__file__).parent.parent / "config" / "strategy_activation.json"
        _disabled_ids: set[str] = set()
        try:
            if _activation_path.exists():
                with open(_activation_path) as _f:
                    _activation = json.load(_f)
                _disabled_ids = set(_activation.get("disabled_strategies", []))
                if _disabled_ids:
                    logger.info("Skipping disabled strategies: %s", _disabled_ids)
        except Exception as _exc:
            logger.warning("Failed to load strategy_activation.json: %s", _exc)

        strategies = [
            s for s in [
                CrossExchangeStrategy("cross_exchange_v1", cost_calc, config=ce_config,
                                      latency_tracker=self._latency_tracker,
                                      regime_detector=self._regime_detector),
                SpotFuturesStrategy("spot_futures_v1", cost_calc, config=sf_config,
                                    regime_detector=self._regime_detector),
                FuturesFuturesStrategy("futures_futures_v1", cost_calc, config=ff_config,
                                       regime_detector=self._regime_detector),
                TriangularStrategy("triangular_v1", cost_calc, config=tri_config,
                                   regime_detector=self._regime_detector),
                FundingRateStrategy("funding_rate_v1", cost_calc, config=fr_config,
                                    regime_detector=self._regime_detector),
                *(
                    [StatisticalArbStrategy("statistical_arb_v1", cost_calc,
                                            regime_detector=self._regime_detector)]
                    if tuned.get("statistical_arb", {}).get("status") in ("READY", "MONITOR")
                    else []
                ),
            ]
            if s.strategy_id not in _disabled_ids
        ]

        # CexDex requires a DEXAdapter — register only if configured
        try:
            from src.strategies.cex_dex import CexDexStrategy
            dex_adapter = self._build_dex_adapter()
            if dex_adapter is not None:
                dex_cost = None
                try:
                    from src.friction.dex_cost import DEXCostCalculator
                    dex_cost = DEXCostCalculator()
                except Exception:
                    pass
                strategies.append(
                    CexDexStrategy(
                        "cex_dex_v1", cost_calc, dex_adapter,
                        cex_exchange_id=list(self._exchanges.keys())[0] if self._exchanges else "binance",
                        symbol="BTC/USDT",
                        dex_cost_calculator=dex_cost,
                    )
                )
        except Exception as exc:
            logger.info("CexDex strategy not registered (no DEX adapter): %s", exc)

        for strategy in strategies:
            self._strategy_manager.register(strategy)

        tuned_count = sum(1 for s in ["spot_futures", "funding_rate", "cross_exchange",
                                       "futures_futures", "triangular"] if tuned.get(s, {}).get("status") in ("READY", "MONITOR"))
        logger.info("Registered %d strategies (%d with tuned params)", len(strategies), tuned_count)

    def _build_dex_adapter(self):
        """Build DEX adapter if DEX configuration is available. Returns None if not configured.

        US-242: When DEX_RPC_URL is unset but SHADOW_MOCK_DEX=true, returns a
        MockDEXAdapter that derives prices from CEX mid-prices.
        """
        _op = get_settings().operational
        dex_rpc = _op.dex_rpc_url
        if not dex_rpc:
            # US-242: Check for mock DEX adapter in shadow mode
            if _op.paper_mock_dex:
                try:
                    from src.dex.mock_adapter import MockDEXAdapter
                    adapter = MockDEXAdapter()
                    logger.info("MockDEXAdapter initialized (SHADOW_MOCK_DEX=true)")
                    return adapter
                except Exception as exc:
                    logger.warning("MockDEXAdapter init failed: %s", exc)
            return None
        pool = _op.dex_pool_address
        if not pool:
            logger.info("DEX_RPC_URL set but DEX_POOL_ADDRESS missing")
            return None
        try:
            from src.infra.dex.uniswap_v3 import UniswapV3Adapter, UniswapV3Config
            config = UniswapV3Config(rpc_url=dex_rpc, pool_address=pool)
            adapter = UniswapV3Adapter(config)
            logger.info("UniswapV3Adapter initialized: pool=%s", pool[:10] + "...")
            return adapter
        except Exception as exc:
            logger.warning("DEX adapter init failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Step 6: Risk Management
    # ------------------------------------------------------------------

    async def _init_risk(self) -> None:
        try:
            from src.risk.circuit_breaker import CircuitBreaker

            # Wire Telegram into CircuitBreaker state changes
            cb_state_callback = None
            if self._telegram and self._telegram._enabled:
                def cb_state_callback(state, reason):
                    import asyncio
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(
                            self._telegram.send_circuit_breaker_event(state.value, reason)
                        )
                    except RuntimeError:
                        pass

            self._circuit_breaker = CircuitBreaker(
                consecutive_loss_limit=3,
                on_state_change=cb_state_callback,
            )
            logger.info("CircuitBreaker initialized")
        except Exception as exc:
            logger.warning("CircuitBreaker init failed: %s", exc)

        try:
            from src.risk.guardian import RiskGuardian
            _risk_cfg = (load_trading_config() or {}).get("risk", {})
            _use_pct = _risk_cfg.get("use_percentage", False)
            if _use_pct and "max_position_pct" in _risk_cfg:
                _max_pos_pct = Decimal(str(_risk_cfg["max_position_pct"])) / Decimal("100")
            else:
                _max_pos_pct = Decimal("0.10")  # fallback: 10%
            self._risk_guardian = RiskGuardian(
                circuit_breaker=self._circuit_breaker,
                max_position_pct=_max_pos_pct,
            )
            logger.info(
                "RiskGuardian initialized with 9 pre-trade checks, max_position_pct=%.1f%%",
                float(_max_pos_pct) * 100,
            )
        except Exception as exc:
            logger.warning("RiskGuardian init failed: %s", exc)

        # US-222/228: PerStrategyCB → Guardian integration
        try:
            from src.risk.per_strategy_cb import PerStrategyCB
            self._per_strategy_cb = PerStrategyCB()
            if self._risk_guardian is not None:
                self._risk_guardian.per_strategy_cb = self._per_strategy_cb
            logger.info("PerStrategyCB initialized (4-state: ACTIVE/THROTTLED/HALTED/SUSPENDED)")
        except Exception as exc:
            logger.warning("PerStrategyCB init failed (non-fatal): %s", exc)

        # US-118: CorrelationMonitor → Guardian integration
        try:
            from src.risk.correlation_monitor import CorrelationMonitor
            self._correlation_monitor = CorrelationMonitor(window=30, threshold=0.7)
            if self._risk_guardian is not None:
                self._risk_guardian.correlation_monitor = self._correlation_monitor
            logger.info("CorrelationMonitor initialized (window=30, threshold=0.7)")
        except Exception as exc:
            logger.warning("CorrelationMonitor init failed (non-fatal): %s", exc)

        # US-278: Wire PortfolioRiskManager into RiskGuardian
        if self._portfolio_risk is not None and self._risk_guardian is not None:
            self._risk_guardian.portfolio_risk = self._portfolio_risk

        # US-286: DataQualityManager → RiskGuardian Check #5
        try:
            from src.core.data_quality_manager import DataQualityManager
            from src.execution.paper_adapter import PaperExchangeAdapter
            self._data_quality_manager = DataQualityManager()
            # Register known exchanges (Paper adapters → always_healthy=True)
            for eid, adapter in self._exchanges.items():
                is_paper = isinstance(adapter, PaperExchangeAdapter)
                self._data_quality_manager.register_exchange(eid, always_healthy=is_paper)
            if self._risk_guardian is not None:
                self._risk_guardian.data_quality_manager = self._data_quality_manager
            logger.info("DataQualityManager initialized (%d exchanges)", len(self._exchanges))
        except Exception as exc:
            logger.warning("DataQualityManager init failed (non-fatal): %s", exc)

        # SIT-3: FlashGuard — rapid price movement detection (5min window, 3% threshold)
        try:
            from src.risk.flash_guard import FlashGuard
            self._flash_guard = FlashGuard()
            if self._risk_guardian is not None:
                self._risk_guardian.flash_guard = self._flash_guard
            logger.info("FlashGuard initialized (threshold=3%%, window=300s, cooldown=60s)")
        except Exception as exc:
            logger.warning("FlashGuard init failed (non-fatal): %s", exc)

        # US-175: ExposureTracker
        try:
            from src.risk.exposure_tracker import ExposureTracker
            if self._redis_client is not None:
                self._exposure_tracker = ExposureTracker(self._redis_client)
                logger.info("ExposureTracker initialized (Redis-backed)")
            else:
                self._exposure_tracker = ExposureTracker(None)
                logger.warning("ExposureTracker: Redis unavailable, using in-memory fallback")
        except Exception as exc:
            logger.warning("ExposureTracker init failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # Step 7: Execution Engine
    # ------------------------------------------------------------------

    async def _init_execution(self) -> None:
        from src.execution.executor import AtomicExecutor
        from src.execution.trade_consumer import TradeRequestConsumer

        # US-236: Initialize PositionManager (in-memory tracking; dual_writer=None in shadow mode)
        try:
            from src.risk.position_manager import PositionManager
            self._position_manager = PositionManager(
                dual_writer=None,
                redis_client=getattr(self, "_redis_client", None),
            )
            logger.info("PositionManager initialized (dual_writer=None, shadow mode)")
        except Exception as exc:
            logger.warning("PositionManager init failed (non-fatal): %s", exc)

        self._executor = AtomicExecutor(
            exchanges=self._exchanges,
        )

        # Build risk check function for TradeRequestConsumer
        risk_check = None
        if self._risk_guardian is not None:
            risk_check = self._build_risk_check_fn()

        self._trade_consumer = TradeRequestConsumer(
            event_bus=self._event_bus,
            executor=self._executor,
            risk_check=risk_check,
            on_result=self._on_execution_result,
        )
        logger.info("AtomicExecutor + TradeRequestConsumer initialized")

        # US-115: SlippageFeedbackLoop — tracks actual vs expected fills
        try:
            from src.risk.slippage import SlippageFeedbackLoop
            self._slippage_feedback = SlippageFeedbackLoop(alpha=0.1, window=100)
            logger.info("SlippageFeedbackLoop initialized (alpha=0.1, window=100)")
        except Exception as exc:
            logger.warning("SlippageFeedbackLoop init failed (non-fatal): %s", exc)

        # US-114: DynamicSizer — wraps PositionSizer with confidence × regime × liquidity
        try:
            from src.execution.sizer import DynamicSizer, PositionSizer, SizerConfig
            capital = self._settings.capital.initial_capital if self._settings else Decimal("70")
            base_sizer = PositionSizer(SizerConfig(capital=capital, tier="alpha"))
            self._dynamic_sizer = DynamicSizer(base_sizer=base_sizer)
            logger.info("DynamicSizer initialized (wrapping PositionSizer)")
        except Exception as exc:
            logger.warning("DynamicSizer init failed (non-fatal): %s", exc)

        # US-130: Wire DynamicSizer to SignalGenerator for regime-adaptive position sizing
        if self._dynamic_sizer is not None and self._signal_generator is not None:
            self._signal_generator._dynamic_sizer = self._dynamic_sizer
            logger.info("DynamicSizer wired to SignalGenerator")

        # US-133: AtomicOrderExecutor (IOC) — initialize for live execution mode
        execution_mode_env = get_settings().operational.execution_mode.lower()
        if execution_mode_env == "live":
            try:
                from src.execution.atomic import AtomicOrderExecutor
                self._atomic_order_executor = AtomicOrderExecutor(timeout_ms=1000)
                logger.info("AtomicOrderExecutor (IOC+market fallback) initialized for live mode")
            except Exception as exc:
                logger.warning("AtomicOrderExecutor init failed (non-fatal): %s", exc)
        else:
            logger.info(
                "EXECUTION_MODE=%s — paper/shadow execution active (AtomicOrderExecutor disabled)",
                execution_mode_env,
            )

        # US-116: TCAAnalyzer
        try:
            from src.analysis.tca import TCAAnalyzer
            self._tca_analyzer = TCAAnalyzer(window_size=1000)
            logger.info("TCAAnalyzer initialized (window=1000)")
        except Exception as exc:
            logger.warning("TCAAnalyzer init failed (non-fatal): %s", exc)

        # US-120: InventoryRebalancer
        try:
            from src.core.inventory_rebalancer import InventoryRebalancer
            from src.core.balance_tracker import BalanceTracker
            self._balance_tracker = BalanceTracker()
            _op = get_settings().operational
            self._rebalancer = InventoryRebalancer(
                tracker=self._balance_tracker,
                deviation_threshold=_op.rebalancer_deviation_threshold,
                check_interval_s=_op.rebalancer_check_interval_s,
                min_transfer_usd=_op.rebalancer_min_transfer_usd,
            )
            # Connect exchange balance feeds (US-QF: balance_feed NOT_CONNECTED 해소)
            if self._exchanges:
                await self._rebalancer.connect_exchange_feeds(self._exchanges)
                logger.info(
                    "InventoryRebalancer initialized (threshold=%.0f%%, interval=%.0fh, balance_feed=CONNECTED, exchanges=%d)",
                    self._rebalancer.deviation_threshold * 100,
                    self._rebalancer.check_interval_s / 3600,
                    len(self._exchanges),
                )
            else:
                logger.info(
                    "InventoryRebalancer initialized (threshold=%.0f%%, interval=%.0fh, balance_feed=NOT_CONNECTED)",
                    self._rebalancer.deviation_threshold * 100,
                    self._rebalancer.check_interval_s / 3600,
                )
        except Exception as exc:
            logger.warning("InventoryRebalancer init failed (non-fatal): %s", exc)

        # US-250: PositionRecovery (WAL-based orphan detection on startup)
        try:
            from src.execution.position_recovery import PositionRecovery
            self._position_recovery = PositionRecovery()
            logger.info("PositionRecovery initialized")
        except Exception as exc:
            logger.warning("PositionRecovery init failed (non-fatal): %s", exc)

        # ER2-22: RecoveryManager (WAL-based Redis state reconstruction on Redis restart)
        if self._db_pool is not None and self._redis_client is not None:
            try:
                from src.infra.db.recovery import RecoveryManager
                redis_raw = self._redis_client.redis
                self._recovery_manager = RecoveryManager(
                    db_pool=self._db_pool,
                    redis_client=redis_raw,
                    exchange_clients={ex_id: ex for ex_id, ex in self._exchanges.items()},
                )
                logger.info("RecoveryManager initialized")
            except Exception as exc:
                logger.warning("RecoveryManager init failed (non-fatal): %s", exc)

        # US-250: PositionReconciler (60s periodic engine-vs-exchange check)
        try:
            from src.execution.reconciler import PositionReconciler

            def _on_reconcile_discrepancy(result) -> None:
                if self._telegram:
                    summary = result.discrepancies[:3]
                    asyncio.ensure_future(self._telegram.send_alert_kr(
                        "position_discrepancy",
                        {"count": len(result.discrepancies), "summary": str(summary)},
                    ))

            self._position_reconciler = PositionReconciler(
                exchanges=list(self._exchanges.values()),
                on_discrepancy=_on_reconcile_discrepancy,
            )
            logger.info("PositionReconciler initialized (exchanges=%d)", len(self._exchanges))
        except Exception as exc:
            logger.warning("PositionReconciler init failed (non-fatal): %s", exc)

    def _build_risk_check_fn(self):
        """Create a risk check callable for the trade consumer (US-129: all 8 fields populated)."""
        from src.risk.guardian import PortfolioState, TradeProposal

        capital = self._settings.capital.initial_capital if self._settings else Decimal("70")

        def risk_check(trade_request) -> tuple[bool, str]:
            capital_total = capital * max(len(self._exchanges), 1)

            # US-129: used_capital from tracked position sizes
            used_capital = sum(self._position_sizes.values()) if self._position_sizes else Decimal("0")

            # US-129: drawdown from peak equity tracking (CRITICAL FIX: init to capital_total)
            if self._peak_equity is None:
                self._peak_equity = capital_total
            current_equity = capital_total + self._total_pnl
            current_drawdown_pct = max(
                Decimal("0"),
                (self._peak_equity - current_equity) / self._peak_equity,
            ) if self._peak_equity > Decimal("0") else Decimal("0")

            # US-129: exchange health scores — default 1.0 (healthy)
            exchange_health = {
                eid: self._exchange_health.get(eid, Decimal("1.0"))
                for eid in self._exchanges.keys()
            }

            portfolio = PortfolioState(
                total_capital=capital_total,
                used_capital=used_capital,
                current_drawdown_pct=current_drawdown_pct,
                total_exposure=used_capital,
                position_sizes=dict(self._position_sizes),
                exchange_health_scores=exchange_health,
                volatility_1min={},   # populated when live vol data available
                volatility_24h={},    # populated when live vol data available
            )
            # Check each leg
            for leg in trade_request.legs:
                price = leg.price or _BTC_REFERENCE_PRICE
                proposal = TradeProposal(
                    strategy_id=trade_request.strategy_id,
                    exchange_id=leg.exchange_id,
                    symbol=leg.symbol,
                    side=leg.side.value.upper(),
                    size=leg.size,
                    price=price,
                    position_value=price * leg.size,
                )
                result = self._risk_guardian.check(proposal, portfolio)
                if not result.approved:
                    return False, result.reason
            return True, ""

        return risk_check

    def _on_execution_result(self, trade_request, execution_result) -> None:
        """Callback after each trade execution."""
        logger.info(
            "Execution result: strategy=%s status=%s",
            trade_request.strategy_id,
            execution_result.status.value,
        )
        # US-129: Update position tracking and peak equity for RiskGuardian PortfolioState
        if getattr(execution_result.status, "value", str(execution_result.status)) == "success":
            try:
                for leg in getattr(execution_result, "legs", []):
                    trade = getattr(leg, "trade", None)
                    order = getattr(leg, "order", None)
                    if trade is not None and order is not None:
                        symbol = order.symbol
                        pos_value = trade.price * trade.amount
                        side = getattr(order.side, "value", str(order.side)).upper()
                        if side == "BUY":
                            self._position_sizes[symbol] = (
                                self._position_sizes.get(symbol, Decimal("0")) + pos_value
                            )
                        else:
                            current = self._position_sizes.get(symbol, Decimal("0"))
                            updated = max(Decimal("0"), current - pos_value)
                            if updated == Decimal("0"):
                                self._position_sizes.pop(symbol, None)
                            else:
                                self._position_sizes[symbol] = updated
                # Update peak equity
                capital = self._settings.capital.initial_capital if self._settings else Decimal("70")
                capital_total = capital * max(len(self._exchanges), 1)
                # Compute actual PnL from fills (HIGH FIX: don't use expected_profit)
                pnl_raw = getattr(execution_result, "pnl", None)
                if pnl_raw is None:
                    # Estimate from fill prices: sell proceeds - buy costs
                    pnl_estimate = Decimal("0")
                    for leg in getattr(execution_result, "legs", []):
                        t = getattr(leg, "trade", None)
                        o = getattr(leg, "order", None)
                        if t and o:
                            val = t.price * t.amount
                            s = getattr(o.side, "value", str(o.side)).upper()
                            pnl_estimate += val if s == "SELL" else -val
                    pnl_raw = pnl_estimate
                self._total_pnl += Decimal(str(pnl_raw))
                current_equity = capital_total + self._total_pnl
                if self._peak_equity is not None and current_equity > self._peak_equity:
                    self._peak_equity = current_equity
            except Exception as exc:
                logger.error("position_tracking_failed strategy=%s error=%s", trade_request.strategy_id, exc)
                self._position_tracking_errors = getattr(self, "_position_tracking_errors", 0) + 1
                if self._position_tracking_errors > 5 and self._telegram:
                    asyncio.ensure_future(self._telegram.send_alert_kr(
                        "position_tracking_fail",
                        {"error_count": self._position_tracking_errors},
                    ))
        # US-175: Update ExposureTracker on successful fills
        if (getattr(execution_result.status, "value", str(execution_result.status)) == "success"
                and self._exposure_tracker is not None):
            try:
                for leg in getattr(execution_result, "legs", []):
                    order = getattr(leg, "order", None)
                    trade = getattr(leg, "trade", None)
                    if order is not None and trade is not None and "/" in getattr(order, "symbol", ""):
                        base_asset = order.symbol.split("/")[0]
                        side = getattr(order.side, "value", str(order.side)).upper()
                        delta = trade.amount if side == "BUY" else -trade.amount
                        asyncio.create_task(
                            self._exposure_tracker.update_exposure(
                                order.exchange_id if hasattr(order, "exchange_id") else
                                getattr(leg, "exchange_id", "unknown"),
                                base_asset,
                                Decimal(str(delta)),
                            )
                        )
            except Exception:
                pass  # Non-critical: exposure tracking failure

        # US-115: Feed slippage data to feedback loop
        if self._slippage_feedback is not None and hasattr(execution_result, 'legs'):
            try:
                for leg in execution_result.legs:
                    if hasattr(leg, 'expected_price') and hasattr(leg, 'fill_price'):
                        self._slippage_feedback.record_fill(
                            expected_price=leg.expected_price,
                            actual_price=leg.fill_price,
                            side=leg.order.side.value.upper() if leg.order and hasattr(leg.order, 'side') else "BUY",
                        )
            except Exception:
                pass  # Non-critical: feedback tracking failure
        # US-118: Feed trade PnL to correlation monitor
        if self._correlation_monitor is not None:
            try:
                pnl = float(execution_result.pnl) if hasattr(execution_result, "pnl") else float(trade_request.expected_profit_usdt)
                self._correlation_monitor.record_trade_pnl(trade_request.strategy_id, pnl)
            except Exception:
                pass  # Non-critical: correlation tracking failure
        # US-116: Feed TCA data
        if self._tca_analyzer is not None:
            try:
                legs = getattr(execution_result, 'legs', [])
                for idx, leg in enumerate(legs):
                    trade = getattr(leg, 'trade', None)
                    if trade is not None:
                        # Latency: use execution_result duration if available, else 0
                        latency_ms = float(
                            getattr(execution_result, 'execution_duration_ms', 0)
                            or getattr(execution_result, 'duration_ms', 0)
                            or 0
                        )
                        # Expected price: use trade_request leg price (always populated)
                        expected = 0.0
                        if idx < len(trade_request.legs):
                            expected = float(trade_request.legs[idx].price or 0)
                        if expected <= 0:
                            expected = float(getattr(getattr(leg, 'order', None), 'price', 0) or 0)
                        if expected <= 0:
                            logger.debug("TCA: skipping leg %d — no expected price", idx)
                            continue
                        # US-329: pass signal_ts for timing decomposition
                        try:
                            _signal_ts = trade_request.timestamp.timestamp()
                        except (AttributeError, TypeError):
                            _signal_ts = 0.0
                        self._tca_analyzer.record_execution(
                            expected_price=expected,
                            fill_price=float(trade.price),
                            latency_ms=latency_ms,
                            filled_ratio=float(getattr(leg, 'filled_ratio', 1.0)),
                            strategy_id=trade_request.strategy_id,
                            signal_ts=_signal_ts,
                            fill_ts=time.time(),
                        )
            except Exception:
                pass  # Non-critical: TCA tracking failure
        # Record trade in context for dashboard API
        from datetime import datetime, timezone
        from uuid import uuid4
        try:
            self.context.trade_history.append({
                "id": str(uuid4()),
                "strategy_id": trade_request.strategy_id,
                "symbol": trade_request.legs[0].symbol if trade_request.legs else "UNKNOWN",
                "buy_exchange": next((l.exchange_id for l in trade_request.legs if l.side.value == "buy"), ""),
                "sell_exchange": next((l.exchange_id for l in trade_request.legs if l.side.value == "sell"), ""),
                "side": "arbitrage",
                "size": float(trade_request.legs[0].size) if trade_request.legs else 0,
                "entry_price": float(trade_request.legs[0].price or 0) if trade_request.legs else 0,
                "exit_price": float(trade_request.legs[-1].price or 0) if trade_request.legs else 0,
                "pnl": float(execution_result.pnl) if hasattr(execution_result, "pnl") else float(trade_request.expected_profit_usdt),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": execution_result.status.value,
            })
        except Exception as exc:
            logger.debug("Failed to record trade to context: %s", exc)

        # US-DW1: CircuitBreaker feedback — record win/loss after each execution
        if self._circuit_breaker is not None:
            try:
                status_val = getattr(execution_result.status, "value", str(execution_result.status))
                if status_val == "success":
                    # Compute drawdown for loss detection
                    pnl_val = getattr(execution_result, "pnl", None)
                    if pnl_val is not None and float(pnl_val) < 0:
                        # Loss: compute current drawdown pct
                        capital = self._settings.capital.initial_capital if self._settings else Decimal("70")
                        capital_total = capital * max(len(self._exchanges), 1)
                        dd_pct = float(abs(self._total_pnl) / capital_total) if capital_total > 0 and self._total_pnl < 0 else 0.0
                        asyncio.ensure_future(self._circuit_breaker.record_loss(drawdown_pct=dd_pct))
                    else:
                        asyncio.ensure_future(self._circuit_breaker.record_win())
                elif status_val in ("rolled_back", "rollback_failed", "timeout"):
                    # Real execution attempt that failed — count as loss
                    asyncio.ensure_future(self._circuit_breaker.record_loss())
                # else: "rejected" = infrastructure reject (no adapter, halted, health)
                # — do NOT count as consecutive_loss; it was never a trade attempt
            except Exception:
                pass  # Non-critical: CB feedback failure

        # US-DW8: Send Korean fill notification via Telegram
        if self._trade_bot is not None and getattr(execution_result.status, "value", str(execution_result.status)) == "success":
            try:
                fill_data = {
                    "strategy_id": trade_request.strategy_id,
                    "symbol": trade_request.legs[0].symbol if trade_request.legs else "UNKNOWN",
                    "buy_exchange": next((l.exchange_id for l in trade_request.legs if l.side.value == "buy"), ""),
                    "sell_exchange": next((l.exchange_id for l in trade_request.legs if l.side.value == "sell"), ""),
                    "size": float(trade_request.legs[0].size) if trade_request.legs else 0,
                    "pnl": float(execution_result.pnl) if hasattr(execution_result, "pnl") else float(trade_request.expected_profit_usdt),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                asyncio.ensure_future(self._trade_bot.send_fill_kr(fill_data))
            except Exception:
                pass  # Non-critical: Telegram fill notification failure

    async def _rebalancer_loop(self) -> None:
        """US-120: Periodic inventory rebalancing check + Telegram alert."""
        while self.state.running:
            try:
                await asyncio.sleep(self._rebalancer.check_interval_s)

                if self._rebalancer.has_critical_imbalance() and self._telegram:
                    try:
                        await self._telegram.send_alert_kr(
                            "inventory_critical", {},
                        )
                    except Exception:
                        pass

                suggestions = self._rebalancer.check_and_suggest()
                if suggestions and self._telegram:
                    try:
                        await self._telegram.send_alert_kr("inventory_rebalance", {
                            "suggestions": [
                                {"from": s.from_exchange, "to": s.to_exchange,
                                 "amount_usd": s.amount_usd, "reason": s.reason}
                                for s in suggestions
                            ],
                        })
                    except Exception:
                        pass

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("rebalancer_loop error: %s", exc)
                await asyncio.sleep(60)

    async def _cancel_open_orders(self) -> None:
        """US-155: Cancel all open/pending orders before shutdown (live mode only)."""
        logger.info("Cancelling open orders before shutdown...")
        total_cancelled = 0
        for eid, adapter in self._exchanges.items():
            if not hasattr(adapter, "get_open_orders"):
                logger.debug("Exchange %s does not support get_open_orders — skipping", eid)
                continue
            try:
                pending = await adapter.get_open_orders()
            except Exception as exc:
                logger.warning("Failed to fetch open orders for %s: %s", eid, exc)
                continue
            for order in pending:
                try:
                    symbol = getattr(order, "symbol", None)
                    await adapter.cancel_order(order.order_id, symbol=symbol)
                    logger.info("Cancelled order %s on %s (symbol=%s)", order.order_id, eid, symbol)
                    total_cancelled += 1
                except Exception as exc:
                    logger.error("Failed to cancel order %s on %s: %s", order.order_id, eid, exc)
                    if self._telegram:
                        try:
                            await self._telegram.send_alert_kr(
                                "order_cancel_fail",
                                {"exchange": eid, "order_id": str(order.order_id), "error": str(exc)},
                            )
                        except Exception:
                            pass
        logger.info("Open order cancellation complete: %d orders cancelled", total_cancelled)

    def _record_alert(self, alert_type: str, severity: str, message: str, metadata: dict | None = None) -> None:
        """Record a system alert for the dashboard API."""
        from datetime import datetime, timezone
        from uuid import uuid4
        self.context.alert_history.append({
            "id": str(uuid4()),
            "type": alert_type,
            "severity": severity,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
        })

    # ------------------------------------------------------------------
    # Step 8: Populate EngineContext for API
    # ------------------------------------------------------------------

    async def _populate_context(self) -> None:
        self.context.strategy_manager = self._strategy_manager
        self.context.risk_guardian = self._risk_guardian
        self.context.position_manager = self._position_manager
        self.context.trade_consumer = self._trade_consumer
        self.context.engine = self
        # US-284-b/a: Attribution + CapitalAllocator
        self.context.attribution = self._attribution
        self.context.capital_allocator = self._capital_allocator
        # US-277/278: PortfolioRiskManager
        self.context.portfolio_risk = self._portfolio_risk
        # Wave 3 modules
        self.context.correlation_monitor = self._correlation_monitor
        self.context.slippage_feedback = self._slippage_feedback
        self.context.dynamic_sizer = self._dynamic_sizer
        self.context.tca_analyzer = self._tca_analyzer
        self.context.rebalancer = self._rebalancer

        # Populate strategies dict for backward compatibility
        if self._strategy_manager:
            for sid in self._strategy_manager.list_strategies():
                s = self._strategy_manager.get_strategy(sid)
                self.context.strategies[sid] = {
                    "id": sid,
                    "type": getattr(s, "STRATEGY_TYPE", "unknown"),
                    "enabled": s.is_active if s else False,
                }

    # ------------------------------------------------------------------
    # Step 9: Background Tasks
    # ------------------------------------------------------------------

    async def _start_background_tasks(self) -> None:
        from src.core.config import EngineMode, resolve_engine_mode, load_engine_config

        # Phase H-2: Resolve unified EngineMode (backtest/paper/shadow/live)
        # Priority: ENGINE_MODE env > engine.json > legacy EXECUTION_MODE+DATA_MODE
        _engine_cfg = load_engine_config()
        _op = get_settings().operational
        self._engine_mode = resolve_engine_mode(
            execution_mode=_op.execution_mode or None,
            data_mode=_op.data_mode or None,
            engine_mode=get_settings().engine_mode.value if get_settings().engine_mode else _engine_cfg.get("mode"),
        )

        # Legacy compatibility: set _data_mode for code that still reads it
        _mode_to_data = {
            EngineMode.BACKTEST: DataMode.SYNTHETIC,
            EngineMode.PAPER: DataMode.SHADOW,
            EngineMode.SHADOW: DataMode.REAL_AUTHENTICATED,
            EngineMode.LIVE: DataMode.REAL_AUTHENTICATED,
        }
        self._data_mode = _mode_to_data.get(self._engine_mode, DataMode.SYNTHETIC)

        logger.info("engine_mode=%s (legacy data_mode=%s)", self._engine_mode, self._data_mode)

        # Common background tasks (all modes)
        tasks = [
            asyncio.create_task(self._trade_consumer_loop(), name="trade_consumer"),
            asyncio.create_task(self._health_check_loop(), name="health_check"),
            asyncio.create_task(self._reconcile_loop(), name="reconcile"),
            asyncio.create_task(self._heartbeat_loop(), name="ws_heartbeat"),
            asyncio.create_task(self._dashboard_feed_loop(), name="dashboard_feed"),
        ]

        # --- Single-axis mode routing (Phase H-2) ---
        if self._engine_mode == EngineMode.BACKTEST:
            # Backtest: TimescaleDB orderbook replay via BacktestMode + WalkForwardAnalyzer
            tasks.append(
                asyncio.create_task(self._backtest_mode_task(), name="backtest_mode")
            )
            logger.info("EngineMode: BACKTEST — TimescaleDB replay + WalkForwardAnalyzer")

        elif self._engine_mode == EngineMode.PAPER:
            # Paper: live WS data + SimExecutor (= old shadow mode)
            # Direct in-process routing (no Redis consumer loop)
            strategy_validation = get_settings().operational.strategy_validation
            shadow_progressive = get_settings().operational.paper_progressive
            if strategy_validation:
                tasks.append(
                    asyncio.create_task(self._strategy_validation_loop(), name="strategy_validation")
                )
                logger.info("EngineMode: PAPER (STRATEGY_VALIDATION)")
            elif shadow_progressive:
                tasks.append(
                    asyncio.create_task(self._progressive_shadow_loop(), name="progressive_shadow")
                )
                logger.info("EngineMode: PAPER (PROGRESSIVE)")
            else:
                tasks.append(
                    asyncio.create_task(self._paper_mode_loop(), name="paper_mode")
                )
                logger.info("EngineMode: PAPER — live WS data + SimExecutor")

        elif self._engine_mode == EngineMode.SHADOW:
            # Shadow: live WS data + AtomicExecutor small capital (canary)
            tasks.append(
                asyncio.create_task(self._live_mode_loop(), name="shadow_canary")
            )
            logger.info("EngineMode: SHADOW — live WS data + AtomicExecutor (canary)")

        elif self._engine_mode == EngineMode.LIVE:
            # Live: live WS data + AtomicExecutor full capital
            tasks.append(
                asyncio.create_task(self._strategy_manager_loop(), name="strategy_mgr")
            )
            tasks.append(
                asyncio.create_task(self._live_mode_loop(), name="live_mode")
            )
            logger.info("EngineMode: LIVE — live WS data + AtomicExecutor (full)")

        else:
            logger.warning("Unknown engine_mode=%s — falling back to BACKTEST", self._engine_mode)
            tasks.append(
                asyncio.create_task(self._orderbook_feed_loop(), name="orderbook_feed")
            )

        # Phase S21: TelegramCommandHandler removed (Dev봇에 통합됨)
        # TradeBot poll loop (InfraBot/DevBot → bot-gateway)
        if self._trade_bot and self._trade_bot.enabled:
            tasks.append(asyncio.create_task(self._trade_bot.poll_loop(), name="trade_bot"))
            logger.info("trade_bot poll_loop started")

        # TradeTelegramBot daily report scheduler
        if self._trade_bot and self._trade_bot.enabled:
            try:
                tasks.append(asyncio.create_task(self._trade_bot.schedule_daily_report(), name="daily_report"))
                logger.info("Daily report scheduler started (09:00 KST)")
            except Exception as exc:
                logger.warning("Daily report scheduler failed (non-fatal): %s", exc)

        # US-120: Inventory rebalancer background loop
        if self._rebalancer is not None:
            tasks.append(asyncio.create_task(
                self._rebalancer_loop(), name="rebalancer"
            ))

        # Phase S21: SmartTelegramAlerter removed (Trade봇에 통합)

        # US-173: RegimeDetector background task (60s periodic)
        if self._regime_detector is not None:
            tasks.append(asyncio.create_task(
                self._regime_detect_loop(), name="regime_detect"
            ))

        # US-174: AdaptiveThreshold adjustment task (1h periodic)
        if self._adaptive_threshold is not None:
            tasks.append(asyncio.create_task(
                self._adaptive_threshold_loop(), name="adaptive_threshold"
            ))

        # US-256: peak_equity DB persistence loop (5min periodic)
        tasks.append(asyncio.create_task(
            self._peak_equity_persist_loop(), name="peak_equity_persist"
        ))

        # US-251: HMM model retraining loop (24h cycle)
        tasks.append(asyncio.create_task(
            self._hmm_training_loop(), name="hmm_training"
        ))

        # US-252: XGBoost + ONNX training loop (24h cycle)
        tasks.append(asyncio.create_task(
            self._xgb_training_loop(), name="xgb_training"
        ))

        # US-280: LiveGate continuous monitor (all modes)
        if self._live_gate is not None:
            _op = get_settings().operational
            if _op.live_gate_continuous_enabled:
                _lg_interval = _op.live_gate_monitor_interval_s
                tasks.append(asyncio.create_task(
                    self._live_gate.start_continuous_monitor(
                        interval_s=_lg_interval,
                        risk_guardian=self._risk_guardian,
                    ),
                    name="live_gate_monitor",
                ))
                logger.info("LiveGate continuous monitor started (interval=%ds)", _lg_interval)

        self.state.background_tasks.extend(tasks)
        logger.info("Started %d background tasks", len(tasks))

    async def _strategy_manager_loop(self) -> None:
        """Start StrategyManager signal consumption."""
        try:
            await self._strategy_manager.start()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("StrategyManager loop error: %s", exc)

    async def _trade_consumer_loop(self) -> None:
        """Start TradeRequestConsumer."""
        try:
            await self._trade_consumer.start()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("TradeConsumer loop error: %s", exc)

    async def _backtest_mode_task(self) -> None:
        """Run BacktestMode replay + WFA for 6 strategies, save results, then shutdown."""
        import json
        import pathlib
        from src.modes.backtest import BacktestMode
        from src.analysis.walk_forward import WalkForwardAnalyzer

        settings = get_settings()
        backtest = BacktestMode(
            signal_generator=self._signal_generator,
            strategy_manager=self._strategy_manager,
            db_pool=self._db_pool,
            market_recorder=self._market_recorder,
            start_time=getattr(settings, "backtest_start", None),
            end_time=getattr(settings, "backtest_end", None),
            symbols=getattr(settings.operational, "symbols", None),
        )
        result = await backtest.run()
        self._backtest_result = result
        self.context.backtest_result = result

        # WFA 6-strategy loop (US-353)
        _STRATEGIES = [
            "cross_exchange", "spot_futures", "futures_futures",
            "triangular", "funding_rate", "statistical_arb",
        ]
        wfa_results: dict = {}
        if self._db_pool is not None:
            try:
                wfa = WalkForwardAnalyzer(self._db_pool.pool)
                for strategy_id in _STRATEGIES:
                    logger.info("wfa.starting strategy=%s", strategy_id)
                    try:
                        wfa_result = await wfa.analyze(strategy_id=strategy_id)
                        wfa_results[strategy_id] = {
                            "overall_sharpe": wfa_result.overall_sharpe,
                            "overall_mdd": wfa_result.overall_mdd,
                            "overall_trades": wfa_result.overall_trades,
                            "overall_pnl": wfa_result.overall_pnl,
                            "live_eligible": wfa_result.live_eligible,
                            "block_reason": wfa_result.block_reason,
                        }
                        logger.info(
                            "wfa.completed strategy=%s sharpe=%.2f trades=%d",
                            strategy_id, wfa_result.overall_sharpe, wfa_result.overall_trades,
                        )
                    except Exception as exc:
                        logger.warning("wfa.strategy_failed strategy=%s: %s", strategy_id, exc)
                        wfa_results[strategy_id] = {"error": str(exc)}
            except Exception as exc:
                logger.error("wfa.failed: %s", exc)

        self.context.wfa_results = wfa_results

        # Save results to .omc/state/backtest_results.json
        try:
            _project_root = pathlib.Path(__file__).parent.parent.parent
            state_dir = _project_root / ".omc" / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            output = {
                "backtest": {
                    "snapshots_replayed": result.snapshots_replayed,
                    "signals_generated": result.signals_generated,
                    "trades_executed": result.trades_executed,
                    "total_pnl": result.total_pnl,
                    "sharpe_ratio": result.sharpe_ratio,
                    "max_drawdown_pct": result.max_drawdown_pct,
                    "win_rate": result.win_rate,
                    "profit_factor": result.profit_factor,
                    "duration_s": result.duration_s,
                    "by_strategy": result.by_strategy,
                    "error": result.error,
                },
                "wfa": wfa_results,
            }
            results_path = state_dir / "backtest_results.json"
            results_path.write_text(json.dumps(output, indent=2, default=str))
            logger.info("backtest.results_saved path=%s", results_path)
        except Exception as exc:
            logger.error("backtest.save_results_failed: %s", exc)

        # ML A/B test (US-354): baseline vs ML-enhanced signal comparison
        try:
            import numpy as np
            from src.analysis.ml_backtest import MLSignalBacktester
            ml_backtester = MLSignalBacktester(ml_scorer=None)
            ml_ab_result = ml_backtester.ab_test(
                signals=[],
                prices=np.array([1.0]),
                features=None,
            )
            self.context.backtest_result = getattr(self.context, "backtest_result", None)
            # Store on context for API access
            if hasattr(self.context, "__dict__"):
                self.context.__dict__["ml_ab_result"] = ml_ab_result
            logger.info(
                "backtest.ml_ab_test_done comparison_valid=%s",
                ml_ab_result.comparison_valid,
            )
        except Exception as exc:
            logger.warning("backtest.ml_ab_test_failed: %s", exc)

        # Signal engine shutdown after backtest completes
        self._shutdown_event.set()

    async def _orderbook_feed_loop(self) -> None:
        """Subscribe to orderbook feeds from all exchanges and feed SignalGenerator."""
        from src.core.rust_bridge import get_orderbook_class

        CoreOrderBook = get_orderbook_class()

        symbols = self._settings.trading.symbols if self._settings else ["BTC/USDT"]

        all_books: dict[str, CoreOrderBook] = {}

        def make_callback(exchange_id: str, symbol: str):
            """Create callback that converts Pydantic OrderBook → core OrderBook."""
            def on_orderbook(pydantic_book) -> None:
                # Convert Pydantic OrderBook to core OrderBook
                core_book = CoreOrderBook(symbol=symbol, exchange=exchange_id)
                bids = [(str(level.price), str(level.amount)) for level in pydantic_book.bids]
                asks = [(str(level.price), str(level.amount)) for level in pydantic_book.asks]
                core_book.apply_snapshot(bids, asks)

                all_books[exchange_id] = core_book

                # Feed to SignalGenerator (fire and forget)
                if self._signal_generator and len(all_books) >= 2:
                    asyncio.create_task(
                        self._signal_generator.on_orderbook_update(
                            book=core_book,
                            books=all_books,
                        )
                    )
            return on_orderbook

        for exchange_id, adapter in self._exchanges.items():
            for symbol in symbols:
                callback = make_callback(exchange_id, symbol)
                await adapter.subscribe_orderbook(symbol, callback)
                logger.info("Subscribed to orderbook: %s %s", exchange_id, symbol)

        # Keep the task alive until cancelled
        try:
            while self.state.running:
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass

    async def _paper_signal_simulator_loop(self) -> None:
        """Run multi-strategy signal simulator for paper mode.

        Produces synthetic signals for SpotFutures, FundingRate, and Triangular
        strategies so ALL 8 strategies receive signals in paper mode.
        """
        from src.core.multi_signal import MultiStrategySignalProducer, PaperSignalSimulator

        producer = MultiStrategySignalProducer(
            event_bus=self._event_bus,
            latency_tracker=getattr(self, "_latency_tracker", None),
        )
        symbols = self._settings.trading.symbols if self._settings else ["BTC/USDT"]
        exchanges = list(self._exchanges.keys())

        simulator = PaperSignalSimulator(
            producer=producer,
            exchanges=exchanges,
            symbols=symbols,
            injection_rate=0.05,
        )
        await simulator.start()
        logger.info("Paper signal simulator started (exchanges=%s, symbols=%s)", exchanges, symbols)

        try:
            while self.state.running:
                await simulator.tick()
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass
        finally:
            await simulator.stop()

    async def _real_data_feed_loop(self) -> None:
        """Start real public WebSocket collectors and feed SignalGenerator.

        Collectors deliver raw orderbook data (no API keys needed).
        Data flows: WS → CoreOrderBook → SignalGenerator → PaperExecutor (observation).
        Optionally records to MarketRecorder (TimescaleDB).

        When USE_RUST_ORDERBOOK=true, uses Rust BTreeMap orderbook (<5μs updates).
        """
        from src.collectors.manager import CollectorManager
        from src.core.rust_bridge import get_orderbook_class

        CoreOrderBook = get_orderbook_class()

        all_books: dict[str, CoreOrderBook] = {}

        async def on_orderbook(exchange_id: str, symbol: str, bids: list, asks: list) -> None:
            """Callback from collectors: convert raw data → CoreOrderBook → SignalGenerator."""
            core_book = CoreOrderBook(symbol=symbol, exchange=exchange_id)
            # bids/asks are [[price_str, qty_str], ...]
            core_book.apply_snapshot(
                [(b[0], b[1]) for b in bids],
                [(a[0], a[1]) for a in asks],
            )
            all_books[exchange_id] = core_book

            # Record to TimescaleDB if available
            if self._market_recorder:
                best_bid = core_book.best_bid()
                best_ask = core_book.best_ask()
                if best_bid and best_ask:
                    self._market_recorder.record_orderbook(
                        exchange=exchange_id,
                        symbol=symbol,
                        bids=bids[:20],
                        asks=asks[:20],
                        best_bid=best_bid,
                        best_ask=best_ask,
                    )

            # US-170: TriangularScanner — detect triangular cycles
            if self._triangular_scanner is not None:
                try:
                    cycles = self._triangular_scanner.on_orderbook_update(
                        exchange_id=exchange_id, symbol=symbol, book=core_book
                    )
                    if cycles and self._multi_signal_producer is not None:
                        for cycle in cycles:
                            asyncio.create_task(
                                self._multi_signal_producer.produce_triangular_signal(cycle)
                            )
                except Exception as exc:
                    logger.debug("TriangularScanner error: %s", exc)

            # Feed to SignalGenerator when we have data from 2+ exchanges
            if self._signal_generator and len(all_books) >= 2:
                try:
                    sig = await self._signal_generator.on_orderbook_update(
                        book=core_book,
                        books=all_books,
                    )
                    if sig and self._telegram and self._telegram._enabled:
                        await self._telegram.send_signal_found(sig)
                except Exception as exc:
                    logger.warning("Signal generation error: %s", exc)

            # Update Prometheus metrics
            try:
                from src.infra.metrics import SIGNALS_TOTAL, EXCHANGE_HEALTH_SCORE
                EXCHANGE_HEALTH_SCORE.labels(exchange=exchange_id).set(1.0)
            except Exception:
                pass

        symbols = self._settings.trading.symbols if self._settings else ["BTC/USDT"]
        exchanges = self._settings.trading.active_exchanges if self._settings else ["binance", "bybit", "okx", "bitget"]

        self._collector_manager = CollectorManager(
            symbols=symbols,
            exchanges=exchanges,
            on_orderbook=on_orderbook,
        )
        await self._collector_manager.start()
        logger.info("Real data collectors started: %s for %s", exchanges, symbols)

        # Send Telegram notification
        if self._telegram and self._telegram._enabled:
            await self._telegram.send_alert_kr("data_collector_start", {
                "exchanges": ", ".join(exchanges),
                "symbols": ", ".join(symbols),
            })

        # Keep alive until cancelled
        try:
            while self.state.running:
                await asyncio.sleep(5.0)
                # Log collector stats periodically
                if self._collector_manager:
                    stats = self._collector_manager.stats
                    connected = self._collector_manager.connected_count
                    logger.debug("Collector stats: connected=%d/%d", connected, len(stats))
        except asyncio.CancelledError:
            pass
        finally:
            if self._collector_manager:
                await self._collector_manager.stop()

    async def _live_mode_loop(self) -> None:
        """Phase H: Live mode — direct in-process routing via LiveMode class.

        Uses the same proven architecture as ShadowMode:
        - Direct StrategyManager.route_signal() (no Redis dependency)
        - DI executor: PaperExecutor for validation, AtomicExecutor for live
        - All signal producers wired (cross_exchange, multi-strategy, real_signal)
        - LiveGate with safe Shadow fallback (not silent return)
        """
        from src.collectors.funding_rate_collector import FundingRateCollector
        from src.core.multi_signal import MultiStrategySignalProducer
        from src.modes.live import LiveMode, LiveGateFailed

        from src.core.config import load_engine_config

        _engine_cfg = load_engine_config()
        _mode_cfg = _engine_cfg.get(self._engine_mode.value, {}) if hasattr(self, '_engine_mode') else {}

        symbols = self._settings.trading.symbols if self._settings else ["BTC/USDT"]
        # Phase H-2: use mode-specific exchanges from config/engine.json
        exchanges = _mode_cfg.get("exchanges") or (
            self._settings.trading.active_exchanges if self._settings
            else ["binance", "bybit", "okx", "bitget"]
        )

        # Create MultiStrategySignalProducer
        self._multi_signal_producer = MultiStrategySignalProducer(
            event_bus=self._event_bus,
            latency_tracker=getattr(self, "_latency_tracker", None),
        )

        # Create FundingRateCollector
        funding_rate_collector = None
        try:
            funding_rate_collector = FundingRateCollector(
                symbols=symbols,
                http_client=getattr(self, "_http_client", None),
            )
        except Exception as exc:
            logger.warning("FundingRateCollector init failed (non-fatal): %s", exc)

        # Phase H-2: execution mode from EngineMode (config/engine.json)
        execution_mode = self._engine_mode.value if hasattr(self, '_engine_mode') else "paper"

        self._live_mode = LiveMode(
            signal_generator=self._signal_generator,
            executor=self._executor,
            strategy_manager=self._strategy_manager,
            symbols=symbols,
            exchanges=exchanges,
            multi_signal_producer=self._multi_signal_producer,
            funding_rate_collector=funding_rate_collector,
            market_recorder=self._market_recorder,
            telegram=self._telegram,
            live_gate=self._live_gate,
            risk_guardian=self._risk_guardian,
            kill_switch=getattr(self, "_kill_switch", None),
            circuit_breaker=self._circuit_breaker,
            regime_detector=self._regime_detector,
            event_bus=self._event_bus,
            db_pool=self._db_pool,
            data_quality_manager=self._data_quality_manager,
            flash_guard=getattr(self, "_flash_guard", None),
            portfolio_risk=getattr(self, "_portfolio_risk", None),
            execution_mode=execution_mode,
        )

        try:
            await self._live_mode.start()
            self.context.execution_mode = execution_mode

            # Keep alive until cancelled
            while self.state.running:
                await asyncio.sleep(5.0)
        except LiveGateFailed:
            logger.warning("LiveGate failed — falling back to Paper mode")
            await self._paper_mode_loop()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("_live_mode_loop FATAL error: %s", exc, exc_info=True)
        finally:
            if hasattr(self, "_live_mode") and self._live_mode is not None:
                await self._live_mode.stop()

    async def _regime_detect_loop(self) -> None:
        """US-173: 60s periodic regime detection using recent PnL returns."""
        while self.state.running:
            try:
                await asyncio.sleep(60.0)
                if self._regime_detector is None:
                    break
                # Build returns series from PnL deltas (not cumulative snapshots)
                pnl_now = float(self._total_pnl)
                if not hasattr(self, "_regime_pnl_history"):
                    self._regime_pnl_history: list[float] = []
                    self._regime_last_pnl: float = 0.0
                pnl_delta = pnl_now - self._regime_last_pnl
                self._regime_last_pnl = pnl_now
                if pnl_delta != 0.0:
                    self._regime_pnl_history.append(pnl_delta)
                # Keep last 60 data points (1 hour at 60s intervals)
                self._regime_pnl_history = self._regime_pnl_history[-60:]
                returns = self._regime_pnl_history.copy()
                if returns:
                    try:
                        # US-254 fix: HMMRegimeDetector has predict(), RegimeDetector has detect()
                        if hasattr(self._regime_detector, 'detect'):
                            self._regime_detector.detect(returns)
                        elif hasattr(self._regime_detector, 'predict'):
                            self._regime_detector.predict(returns)
                    except Exception:
                        pass
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("regime_detect_loop error: %s", exc)

    async def _adaptive_threshold_loop(self) -> None:
        """US-174: 1-hour periodic AdaptiveThreshold adjustment.

        Reads current win_rate from shadow stats (if available) or trade history,
        calls AdaptiveThreshold.adjust(), and updates SignalConfig.min_edge.
        """
        INTERVAL_S = get_settings().operational.adaptive_threshold_interval_s
        while self.state.running:
            try:
                # Bug 1-C: adjust() first so the first run is not delayed by INTERVAL_S
                if self._adaptive_threshold is None:
                    break

                # Collect win_rate and total_trades from available sources
                win_rate = 0.5
                total_trades = 0
                if self._paper_mode is not None and hasattr(self._paper_mode, "_stats"):
                    stats = self._paper_mode._stats
                    total_trades = getattr(stats, "total_trades", 0)
                    wins = getattr(stats, "profitable_trades", 0)
                    if total_trades > 0:
                        win_rate = wins / total_trades
                elif self.context.trade_history:
                    total_trades = len(self.context.trade_history)
                    wins = sum(1 for t in self.context.trade_history if t.get("pnl", 0) > 0)
                    if total_trades > 0:
                        win_rate = wins / total_trades

                # US-201: compute expected_edge_bps and profit_factor from trade history
                expected_edge_bps: float | None = None
                profit_factor: float | None = None
                if self.context.trade_history:
                    trades = self.context.trade_history
                    winning_pnl = [t.get("pnl", 0.0) for t in trades if t.get("pnl", 0.0) > 0]
                    losing_pnl = [t.get("pnl", 0.0) for t in trades if t.get("pnl", 0.0) < 0]
                    n = len(trades)
                    if n > 0:
                        wr = len(winning_pnl) / n
                        avg_win = sum(winning_pnl) / len(winning_pnl) if winning_pnl else 0.0
                        avg_loss = abs(sum(losing_pnl) / len(losing_pnl)) if losing_pnl else 0.0
                        expected_value_usd = (wr * avg_win) - ((1 - wr) * avg_loss)
                        # Normalize to bps: approximate average notional from avg trade size
                        avg_notional = (avg_win + avg_loss) / 2.0 if (avg_win + avg_loss) > 0 else 1.0
                        expected_edge_bps = (expected_value_usd / avg_notional) * 10000 if avg_notional > 0 else 0.0
                        if losing_pnl:
                            profit_factor = sum(winning_pnl) / abs(sum(losing_pnl)) if winning_pnl else 0.0

                new_edge_bps = self._adaptive_threshold.adjust(
                    "global",
                    win_rate=win_rate,
                    total_trades=total_trades,
                    expected_edge_bps=expected_edge_bps,
                    profit_factor=profit_factor,
                )

                # Update SignalConfig.min_edge at runtime
                if self._signal_generator is not None and hasattr(self._signal_generator, "_config"):
                    self._signal_generator._config.min_edge = (
                        Decimal(str(new_edge_bps)) / Decimal("10000")
                    )
                    logger.info(
                        "AdaptiveThreshold updated min_edge to %.2f bps (wr=%.1f%%, trades=%d)",
                        new_edge_bps, win_rate * 100, total_trades,
                    )

                # Persist history to DB if available
                if self._db_pool is not None:
                    try:
                        async with self._db_pool.pool.acquire() as conn:
                            await self._adaptive_threshold.save_history(conn)
                    except Exception:
                        pass
                await asyncio.sleep(INTERVAL_S)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("adaptive_threshold_loop error: %s", exc)

    async def _hmm_training_loop(self) -> None:
        """US-251: Background HMM model retraining (7-day cycle).

        Flow: wait 7 days → acquire DB conn → scheduled_train() →
        Performance Gate (is_fitted) → save_model() to .cache/hmm/.
        Graceful fallback: no DB or import error → loop exits silently.
        """
        try:
            from src.ml.hmm_trainer import HMMTrainer
        except ImportError as exc:
            logger.info("hmm_trainer_skipped (import): %s", exc)
            return

        # Share the live RegimeDetector if available so the trained model
        # is applied immediately without a process restart.
        trainer = HMMTrainer(
            hmm_detector=self._regime_detector if self._regime_detector is not None else None,
        )
        os.makedirs(".cache/hmm", exist_ok=True)

        INTERVAL_S = 7 * 24 * 3600  # 7 days
        RETRY_S = 3600               # 1 hour on failure

        # US-251: train immediately if no model file exists (avoid 7-day delay on first run)
        _hmm_first_run = not (os.path.exists(".cache/hmm") and os.listdir(".cache/hmm"))

        while not self._shutdown_event.is_set():
            if _hmm_first_run:
                _hmm_first_run = False
            else:
                try:
                    await asyncio.sleep(INTERVAL_S)
                except asyncio.CancelledError:
                    break

            if not self._db_pool:
                logger.debug("hmm_training_skipped: no DB pool")
                continue

            try:
                async with self._db_pool.pool.acquire() as conn:
                    trained = await trainer.scheduled_train(conn)

                if trained:
                    # Performance Gate: model must be fitted (HMM has no accuracy score)
                    if trainer.detector.is_fitted:
                        trainer.save_model()
                        logger.info(
                            "HMM model deployed: samples=%d, saved to %s",
                            trainer._train_samples, trainer._cache_dir,
                        )
                    else:
                        logger.warning("HMM model rejected: not fitted after training")
                else:
                    logger.debug("HMM training skipped: not due or insufficient data")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("HMM training failed: %s — retrying in 1h", exc)
                try:
                    await asyncio.sleep(RETRY_S)
                except asyncio.CancelledError:
                    break

    async def _xgb_training_loop(self) -> None:
        """US-252: Background XGBoost training + ONNX export (24h cycle).

        Flow: wait 24h → acquire DB conn → scheduled_train() →
        Performance Gate (best_score > 0.65) → ONNXExporter.export() →
        reload ONNXSignalScorer hot-swap.
        Graceful fallback: no DB / missing optional deps → loop exits silently.
        """
        try:
            from src.ml.xgb_trainer import XGBTrainer
            from src.ml.onnx_exporter import ONNXExporter
        except ImportError as exc:
            logger.info("xgb_trainer_skipped (import): %s", exc)
            return

        trainer = XGBTrainer()
        exporter = ONNXExporter()
        os.makedirs("models/latest", exist_ok=True)

        INTERVAL_S = 24 * 3600  # 24 hours
        RETRY_S = 3600          # 1 hour on failure
        ACCURACY_GATE = 0.65    # Performance Gate: AUC must exceed this

        # US-252: train immediately if no ONNX model exists (avoid 24-hour delay on first run)
        _xgb_first_run = not os.path.exists("models/latest/model.onnx")

        while not self._shutdown_event.is_set():
            if _xgb_first_run:
                _xgb_first_run = False
            else:
                try:
                    await asyncio.sleep(INTERVAL_S)
                except asyncio.CancelledError:
                    break

            if not self._db_pool:
                logger.debug("xgb_training_skipped: no DB pool")
                continue

            try:
                logger.info("xgb_training_loop_cycle_start")
                async with self._db_pool.pool.acquire() as conn:
                    trained = await trainer.scheduled_train(conn)

                if not trained:
                    logger.debug("XGBoost training skipped: not due or insufficient data")
                    continue

                # Performance Gate: AUC score must exceed threshold
                if trainer.best_score < ACCURACY_GATE:
                    logger.warning(
                        "XGBoost model rejected: best_score=%.4f < %.2f",
                        trainer.best_score, ACCURACY_GATE,
                    )
                    continue

                # ONNX export — requires onnxmltools (optional dep)
                try:
                    n_features = len(trainer._feature_names) if trainer._feature_names else 20
                    onnx_path = exporter.export(
                        trainer.model,
                        n_features=n_features,
                        feature_names=trainer._feature_names or None,
                    )
                    # Hot-reload ONNX scorer in SignalGenerator
                    if self._signal_generator is not None:
                        scorer = getattr(self._signal_generator, "_ml_scorer", None)
                        if scorer is not None and hasattr(scorer, "reload_model"):
                            scorer.reload_model(onnx_path)
                    logger.info(
                        "XGBoost model deployed + ONNX exported: path=%s, best_score=%.4f",
                        onnx_path, trainer.best_score,
                    )
                except Exception as export_exc:
                    logger.warning("ONNX export failed (model trained but not exported): %s", export_exc)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("XGBoost training failed: %s — retrying in 1h", exc)
                try:
                    await asyncio.sleep(RETRY_S)
                except asyncio.CancelledError:
                    break

    async def _paper_mode_loop(self) -> None:
        """Start Shadow Mode: real data + paper execution + full metrics.

        Creates a ShadowMode orchestrator wired to the engine's signal pipeline,
        paper executor (power-law slippage), market recorder, and telegram alerter.
        Optionally starts a LiveGate auto-evaluation loop.
        """
        try:
            from src.collectors.funding_rate_collector import FundingRateCollector
            from src.modes.shadow import ShadowMode
            from src.modes.paper import PaperMode

            symbols = self._settings.trading.symbols if self._settings else ["BTC/USDT"]
            exchanges = self._settings.trading.active_exchanges if self._settings else ["binance", "bybit", "okx", "bitget"]

            # Create MultiStrategySignalProducer for 6 additional strategies
            from src.core.multi_signal import MultiStrategySignalProducer

            multi_signal_producer = MultiStrategySignalProducer(
                event_bus=self._event_bus,
                latency_tracker=getattr(self, "_latency_tracker", None),
            )

            # Create FundingRateCollector with shared HTTP client (4 exchanges, all symbols)
            funding_rate_collector = FundingRateCollector(
                symbols=symbols,
                http_client=getattr(self, "_http_client", None),
            )

            # US-171: create KillSwitch for KRW staleness soft-block
            from src.risk.kill_switch import KillSwitch as _KillSwitch
            _shadow_kill_switch = _KillSwitch()

            # US-299: optional per-strategy filter from env var (comma-separated signal IDs)
            _shadow_strategy_filter_raw = get_settings().operational.paper_strategy_filter.strip()
            _shadow_strategy_filter = (
                [s.strip() for s in _shadow_strategy_filter_raw.split(",") if s.strip()]
                if _shadow_strategy_filter_raw else None
            )

            self._paper_mode = ShadowMode(
                signal_generator=self._signal_generator,
                paper_executor=None,  # auto-creates with PowerLawSlippage(gamma=0.5)
                collector_manager=None,  # auto-creates CollectorManager
                market_recorder=self._market_recorder,
                telegram=self._telegram,
                symbols=symbols,
                exchanges=exchanges,
                multi_signal_producer=multi_signal_producer,
                funding_rate_collector=funding_rate_collector,
                strategy_manager=self._strategy_manager,
                kill_switch=_shadow_kill_switch,
                regime_detector=self._regime_detector,
                adaptive_threshold=self._adaptive_threshold,
                db_pool=self._db_pool,  # US-256
                data_quality_manager=self._data_quality_manager,  # US-286
                strategy_filter=_shadow_strategy_filter,  # US-299
                portfolio_risk=self._portfolio_risk,  # US-300
            )
            # SIT-3: Wire FlashGuard into ShadowMode
            if self._flash_guard is not None:
                self._paper_mode._flash_guard = self._flash_guard

            # Set all registered strategies to shadow mode and start them
            if self._strategy_manager is not None:
                for sid in self._strategy_manager.list_strategies():
                    s = self._strategy_manager.get_strategy(sid)
                    if s:
                        s.paper_mode = True
                for sid in self._strategy_manager.list_strategies():
                    try:
                        await self._strategy_manager.start_strategy(sid)
                    except Exception as exc:
                        logger.warning("Shadow strategy %s start failed: %s", sid, exc)

            self.context.paper_mode = self._paper_mode  # set before start so API can see it
            try:
                await self._paper_mode.start()
            except Exception as exc:
                logger.error("paper_mode.start_failed error=%s", exc, exc_info=True)
                raise
            self.context.shadow_active = True
            self.context.execution_mode = "shadow"
            logger.info("Shadow Mode started: %s for %s", exchanges, symbols)

            # ER5-04: Warm-start restore — load previous shadow stats from DB
            if self._db_pool is not None:
                try:
                    async with self._db_pool.pool.acquire() as conn:
                        rows = await conn.fetch(
                            "SELECT key, value FROM engine_state"
                            " WHERE key IN ('shadow_total_pnl', 'shadow_trades_executed')"
                        )
                        for row in rows:
                            if row["key"] == "shadow_total_pnl":
                                self._paper_mode._stats.total_pnl = float(row["value"])
                                logger.info("shadow_total_pnl_restored value=%s", row["value"])
                            elif row["key"] == "shadow_trades_executed":
                                self._paper_mode._stats.trades_executed = int(row["value"])
                                logger.info("shadow_trades_executed_restored value=%s", row["value"])
                except Exception as exc:
                    logger.warning("shadow_stats_warm_start_failed error=%s", exc)

            # Start LiveGate auto-evaluation if DB is available
            if self._db_pool is not None:
                try:
                    from src.modes.live_gate import LiveGate
                    from src.risk.kill_switch import KillSwitch

                    kill_switch = KillSwitch()  # uses module-level halt flag
                    # US-286: DQM health scores as exchange_health_fn
                    _ehf = self._data_quality_manager.get_all_health_scores if self._data_quality_manager else None
                    self._live_gate = LiveGate(
                        pool=self._db_pool.pool,
                        telegram=self._telegram,
                        kill_switch=kill_switch,
                        circuit_breaker=self._circuit_breaker,
                        exchange_health_fn=_ehf,
                        settings=self._settings,
                    )
                    await self._live_gate.start_auto_evaluation()
                    logger.info("LiveGate auto-evaluation started (24h cycle)")
                except Exception as exc:
                    logger.warning("LiveGate init failed (non-fatal): %s", exc)

            # Send Telegram notification
            if self._telegram and self._telegram._enabled:
                await self._telegram.send_alert_kr("shadow_mode_start", {
                    "exchanges": ", ".join(exchanges),
                    "symbols": ", ".join(symbols),
                    "live_gate": "활성" if self._live_gate else "비활성",
                })
        except Exception as exc:
            logger.error("paper_mode_loop.failed error=%s", exc, exc_info=True)
            return

        # Keep alive until cancelled
        # NOTE: Cleanup is handled exclusively by Engine.stop() to avoid
        # double-cleanup race conditions. Do NOT add cleanup here.
        try:
            while self.state.running:
                await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            pass

    async def _strategy_validation_loop(self) -> None:
        """Per-strategy isolated Shadow validation (US-067).

        Creates ShadowMode and StrategyValidationOrchestrator, validates each strategy
        in isolation, then writes config/strategy_activation.json.
        Enabled when STRATEGY_VALIDATION=true (overrides SHADOW_PROGRESSIVE).
        """
        from src.collectors.funding_rate_collector import FundingRateCollector
        from src.core.multi_signal import MultiStrategySignalProducer
        from src.modes.shadow import ShadowMode
        from src.modes.strategy_validation import StrategyValidationOrchestrator

        symbols = self._settings.trading.symbols if self._settings else ["BTC/USDT"]
        exchanges = self._settings.trading.active_exchanges if self._settings else ["binance", "bybit", "okx", "bitget"]

        multi_signal_producer = MultiStrategySignalProducer(
            event_bus=self._event_bus,
            latency_tracker=getattr(self, "_latency_tracker", None),
        )

        funding_rate_collector = FundingRateCollector(
            symbols=symbols,
            http_client=getattr(self, "_http_client", None),
        )

        # US-299: optional per-strategy filter from env var (comma-separated signal IDs)
        _sf_raw = get_settings().operational.paper_strategy_filter.strip()
        _sf = [s.strip() for s in _sf_raw.split(",") if s.strip()] if _sf_raw else None

        shadow = ShadowMode(
            signal_generator=self._signal_generator,
            paper_executor=None,
            collector_manager=None,
            market_recorder=self._market_recorder,
            telegram=self._telegram,
            symbols=symbols,
            exchanges=exchanges,
            multi_signal_producer=multi_signal_producer,
            funding_rate_collector=funding_rate_collector,
            strategy_manager=self._strategy_manager,
            regime_detector=self._regime_detector,
            adaptive_threshold=self._adaptive_threshold,
            db_pool=self._db_pool,  # US-256
            data_quality_manager=self._data_quality_manager,  # US-286
            strategy_filter=_sf,  # US-299
            portfolio_risk=self._portfolio_risk,  # US-300
        )
        # SIT-3: Wire FlashGuard into ShadowMode
        if self._flash_guard is not None:
            shadow._flash_guard = self._flash_guard

        if self._strategy_manager is not None:
            for sid in self._strategy_manager.list_strategies():
                s = self._strategy_manager.get_strategy(sid)
                if s:
                    s.paper_mode = True
            for sid in self._strategy_manager.list_strategies():
                try:
                    await self._strategy_manager.start_strategy(sid)
                except Exception as exc:
                    logger.warning("Strategy validation: strategy %s start failed: %s", sid, exc)

        await shadow.start()
        logger.info("Strategy validation Shadow started: %s for %s", exchanges, symbols)

        try:
            orchestrator = StrategyValidationOrchestrator(
                paper_mode=shadow,
                telegram_sender=self._telegram,
            )
            report = await orchestrator.run()
            logger.info(
                "Strategy validation complete: %d profitable, active=%s",
                len(report.profitable), report.profitable,
            )
        finally:
            await shadow.stop()

    async def _progressive_shadow_loop(self) -> None:
        """Progressive Shadow: 6-stage automatic extension (1H→2H→6H→12H→24H→72H).

        Creates ShadowMode and ProgressiveShadowOrchestrator, runs all 6 stages.
        Enabled when SHADOW_PROGRESSIVE=true (default: false → _paper_mode_loop).
        """
        from src.collectors.funding_rate_collector import FundingRateCollector
        from src.modes.shadow import ShadowMode
        from src.modes.progressive_shadow import ProgressiveShadowOrchestrator

        symbols = self._settings.trading.symbols if self._settings else ["BTC/USDT"]
        exchanges = (
            self._settings.trading.active_exchanges
            if self._settings
            else ["binance", "bybit", "okx", "bitget"]
        )

        # Create MultiStrategySignalProducer for 6 additional strategies
        from src.core.multi_signal import MultiStrategySignalProducer

        multi_signal_producer = MultiStrategySignalProducer(
            event_bus=self._event_bus,
            latency_tracker=getattr(self, "_latency_tracker", None),
        )

        # Create FundingRateCollector with shared HTTP client (4 exchanges, all symbols)
        funding_rate_collector = FundingRateCollector(
            symbols=symbols,
            http_client=getattr(self, "_http_client", None),
        )

        # US-299: optional per-strategy filter from env var (comma-separated signal IDs)
        _sf2_raw = get_settings().operational.paper_strategy_filter.strip()
        _sf2 = [s.strip() for s in _sf2_raw.split(",") if s.strip()] if _sf2_raw else None

        self._paper_mode = ShadowMode(
            signal_generator=self._signal_generator,
            paper_executor=None,  # auto-creates with PowerLawSlippage(gamma=0.5)
            collector_manager=None,  # auto-creates CollectorManager
            market_recorder=self._market_recorder,
            telegram=self._telegram,
            symbols=symbols,
            exchanges=exchanges,
            multi_signal_producer=multi_signal_producer,
            funding_rate_collector=funding_rate_collector,
            strategy_manager=self._strategy_manager,
            regime_detector=self._regime_detector,
            adaptive_threshold=self._adaptive_threshold,
            db_pool=self._db_pool,  # US-256
            data_quality_manager=self._data_quality_manager,  # US-286
            strategy_filter=_sf2,  # US-299
            portfolio_risk=self._portfolio_risk,  # US-300
        )
        # SIT-3: Wire FlashGuard into ShadowMode
        if self._flash_guard is not None:
            self._paper_mode._flash_guard = self._flash_guard

        # Set all registered strategies to shadow mode
        if self._strategy_manager is not None:
            for sid in self._strategy_manager.list_strategies():
                s = self._strategy_manager.get_strategy(sid)
                if s:
                    s.paper_mode = True
            for sid in self._strategy_manager.list_strategies():
                try:
                    await self._strategy_manager.start_strategy(sid)
                except Exception as exc:
                    logger.warning("Shadow strategy %s start failed: %s", sid, exc)

        # Build LiveGate for Stage 6
        if self._db_pool is not None:
            try:
                from src.modes.live_gate import LiveGate
                from src.risk.kill_switch import KillSwitch

                kill_switch = KillSwitch()
                # US-286: DQM health scores as exchange_health_fn
                _ehf2 = self._data_quality_manager.get_all_health_scores if self._data_quality_manager else None
                self._live_gate = LiveGate(
                    pool=self._db_pool.pool,
                    telegram=self._telegram,
                    kill_switch=kill_switch,
                    circuit_breaker=self._circuit_breaker,
                    exchange_health_fn=_ehf2,
                    settings=self._settings,
                )
            except Exception as exc:
                logger.warning("LiveGate init failed (non-fatal): %s", exc)

        self.context.paper_mode = self._paper_mode
        self.context.shadow_active = True
        self.context.execution_mode = "shadow"

        orchestrator = ProgressiveShadowOrchestrator(
            shadow_mode=self._paper_mode,
            live_gate=self._live_gate,
            telegram=self._telegram,
            db_pool=self._db_pool,
        )

        try:
            results = await orchestrator.run()
        except asyncio.CancelledError:
            return

        passed_count = sum(1 for r in results if r.passed)
        logger.info(
            "progressive_shadow_loop.finished",
            stages_passed=passed_count,
            total_stages=len(results),
        )

    async def _health_check_loop(self) -> None:
        while self.state.running:
            try:
                await self._run_health_check()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Health check error: %s", exc)
            await asyncio.sleep(self.HEALTH_CHECK_INTERVAL)

    async def _run_health_check(self) -> None:
        # Log exchange health scores and update _exchange_health (CRITICAL FIX: was never populated)
        for eid, adapter in self._exchanges.items():
            score = adapter.health_score
            self._exchange_health[eid] = Decimal(str(score))
            # US-286: Sync health data to DataQualityManager
            if self._data_quality_manager is not None:
                self._data_quality_manager.record_heartbeat(eid)
            if score < 0.9:
                logger.warning("Exchange %s health_score=%.2f", eid, score)

        # US-286: Periodic DQM cleanup + stats logging
        if self._data_quality_manager is not None:
            cleaned = self._data_quality_manager.cleanup_expired()
            stats = self._data_quality_manager.get_stats()
            if stats["check_count"] > 0:
                logger.debug(
                    "dqm_stats",
                    checks=stats["check_count"],
                    rejects=stats["reject_count"],
                    blacklisted=stats["active_blacklist"],
                    cleaned=cleaned,
                )

        # Log trade consumer metrics
        if self._trade_consumer:
            metrics = self._trade_consumer
            logger.debug(
                "Health OK — trades: processed=%d success=%d rejected=%d",
                metrics.processed_count,
                metrics.execution_success_count,
                metrics.risk_rejected_count,
            )
        else:
            logger.debug("Health check OK")

    async def _startup_position_scan(self) -> None:
        """US-250: Scan for orphaned positions (WAL) on engine startup."""
        # ER2-22: WAL replay — reconstruct Redis state from PostgreSQL on startup
        if self._recovery_manager is not None:
            try:
                recovered = await self._recovery_manager.recover()
                if recovered:
                    logger.info("RecoveryManager WAL replay completed successfully")
                else:
                    logger.warning("RecoveryManager WAL replay: reconciliation failed, HALT flag set")
            except Exception as exc:
                logger.warning("RecoveryManager.recover() startup error: %s", exc)

        if self._position_recovery is None:
            return
        # Use public .redis property (raises RuntimeError if not connected)
        if self._redis_client is None:
            logger.debug("startup_position_scan skipped: no Redis client available")
            return
        try:
            redis_conn = self._redis_client.redis  # public property → aioredis.Redis
        except RuntimeError:
            logger.debug("startup_position_scan skipped: Redis not connected")
            return
        try:
            from src.execution.position_recovery import PositionRecovery
            recovery = PositionRecovery(redis=redis_conn)
            result = await recovery.scan()
            if result.positions_found > 0:
                logger.warning(
                    "startup_orphan_positions found=%d closed=%d resumed=%d skipped=%d",
                    result.positions_found, result.closed, result.resumed, result.skipped,
                )
                if self._telegram:
                    await self._telegram.send_alert_kr("orphan_positions", {
                        "found": result.positions_found,
                        "closed": result.closed,
                        "resumed": result.resumed,
                    })
            else:
                logger.info("startup_position_scan: no orphaned positions found")
            logger.info("[position_recovery] scan completed")
        except Exception as exc:
            logger.warning("startup_position_scan_error error=%s", exc)

    async def _startup_compliance_audit(self) -> None:
        """US-250-a: Run ComplianceChecker on engine startup (non-blocking)."""
        try:
            from src.infra.compliance import ComplianceChecker, ComplianceStatus
            checker = ComplianceChecker(
                db_pool=self._db_pool,
                kill_switch=None,
                circuit_breaker=self._circuit_breaker,
                telegram=self._telegram,
            )
            report = await checker.run_audit()
            if report.fail_count > 0:
                logger.error(
                    "compliance_startup_audit: FAIL=%d PARTIAL=%d PASS=%d score=%.1f%%",
                    report.fail_count, report.partial_count, report.pass_count, report.score_pct,
                )
                fail_names = [i.name for i in report.items if i.status == ComplianceStatus.FAIL]
                logger.error("compliance_failures: %s", fail_names)
            else:
                logger.info(
                    "compliance_startup_audit: PASS=%d PARTIAL=%d score=%.1f%%",
                    report.pass_count, report.partial_count, report.score_pct,
                )
        except ImportError:
            logger.debug("compliance_checker_not_available")
        except Exception as exc:
            logger.warning("compliance_startup_audit_error error=%s", exc)

    async def _reconcile_loop(self) -> None:
        interval = get_settings().operational.reconciliation_interval_s
        while self.state.running:
            try:
                await asyncio.sleep(interval)

                # Only reconcile when shadow mode is active and Redis is available
                if self._paper_mode is None or self._redis_client is None:
                    continue

                current: dict[str, str] = self._paper_mode._balance_tracker.summary()
                if not current:
                    continue

                # Read last saved snapshot from Redis
                raw = await self._redis_client.hgetall("leviathan:recovery:balances")
                if raw:
                    recovery = {
                        (k.decode() if isinstance(k, bytes) else k):
                        (v.decode() if isinstance(v, bytes) else v)
                        for k, v in raw.items()
                    }
                    mismatches = []
                    for ex_id, cur_str in current.items():
                        if ex_id in recovery:
                            try:
                                cur_val = float(cur_str)
                                rec_val = float(recovery[ex_id])
                                if rec_val > 0 and abs(cur_val - rec_val) / rec_val > 0.01:
                                    mismatches.append(
                                        f"{ex_id}: memory={cur_val:.4f} redis={rec_val:.4f}"
                                    )
                            except (ValueError, ZeroDivisionError):
                                pass
                    if mismatches:
                        msg = "잔고 불일치: " + ", ".join(mismatches)
                        logger.warning(msg)
                        if self._telegram:
                            try:
                                await self._telegram.send_alert_kr(
                                    "balance_mismatch", {"detail": msg},
                                )
                            except Exception:
                                pass

                # Save current state as the new recovery snapshot
                await self._redis_client.hset("leviathan:recovery:balances", current)
                logger.debug("Position reconciliation tick — snapshot saved (%d exchanges)", len(current))

                # US-250: PositionReconciler — compare engine vs exchange positions
                if self._position_reconciler is not None:
                    try:
                        from src.core.models import Position
                        engine_positions: dict[str, Position] = {}
                        if self._position_manager is not None:
                            for p in self._position_manager.get_all_positions():
                                key = f"{p.exchange_id}:{p.symbol}"
                                engine_positions[key] = Position(
                                    exchange_id=p.exchange_id,
                                    symbol=p.symbol,
                                    size=p.quantity,
                                )
                        result = await self._position_reconciler.reconcile(engine_positions)
                        if result.has_discrepancy:
                            logger.warning(
                                "position_reconciler_discrepancy count=%d",
                                len(result.discrepancies),
                            )
                    except Exception as exc:
                        logger.debug("position_reconciler_error: %s", exc)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Reconcile error: %s", exc)

    async def _peak_equity_persist_loop(self) -> None:
        """US-256: Persist peak_equity to TimescaleDB (primary) + JSON (backup) every 5 minutes."""
        import json
        import pathlib
        state_path = pathlib.Path(__file__).parent.parent / ".omc" / "state" / "peak_equity.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        _CREATE_TABLE = """
            CREATE TABLE IF NOT EXISTS engine_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """
        _UPSERT = """
            INSERT INTO engine_state (key, value, updated_at)
            VALUES ('peak_equity', $1, NOW())
            ON CONFLICT (key) DO UPDATE SET value = $1, updated_at = NOW()
        """

        # Restore on startup: DB first, then JSON fallback
        if self._peak_equity is None:
            if self._db_pool is not None:
                try:
                    async with self._db_pool.pool.acquire() as conn:
                        await conn.execute(_CREATE_TABLE)
                        row = await conn.fetchrow(
                            "SELECT value FROM engine_state WHERE key = 'peak_equity'"
                        )
                        if row is not None:
                            self._peak_equity = Decimal(row["value"])
                            logger.info("peak_equity_restored_from_db value=%s", row["value"])
                except Exception as exc:
                    logger.warning("peak_equity_db_restore_failed error=%s", exc)
            if self._peak_equity is None:
                try:
                    if state_path.exists():
                        data = json.loads(state_path.read_text())
                        stored = data.get("peak_equity")
                        if stored:
                            self._peak_equity = Decimal(str(stored))
                            logger.info("peak_equity_restored_from_file value=%s", stored)
                except Exception as exc:
                    logger.warning("peak_equity_file_restore_failed error=%s", exc)

        while self.state.running:
            await asyncio.sleep(300)  # 5 minutes
            try:
                if self._peak_equity is not None:
                    val_str = str(self._peak_equity)
                    # Primary: TimescaleDB
                    if self._db_pool is not None:
                        try:
                            async with self._db_pool.pool.acquire() as conn:
                                await conn.execute(_CREATE_TABLE)
                                await conn.execute(_UPSERT, val_str)
                            logger.debug("peak_equity_persisted_to_db value=%s", val_str)
                        except Exception as exc:
                            logger.warning("peak_equity_db_persist_failed error=%s", exc)
                    # Backup: JSON file (dual write)
                    try:
                        state_path.write_text(json.dumps({"peak_equity": val_str}))
                        logger.debug("peak_equity_persisted_to_file value=%s", val_str)
                    except Exception as exc:
                        logger.debug("peak_equity_file_persist_error error=%s", exc)
                # ER5-04: Persist shadow stats alongside peak_equity
                if self._paper_mode is not None and self._db_pool is not None:
                    try:
                        stats = self._paper_mode._stats
                        async with self._db_pool.pool.acquire() as conn:
                            await conn.execute(_CREATE_TABLE)
                            _UPSERT_SHADOW = """
                                INSERT INTO engine_state (key, value, updated_at)
                                VALUES ($1, $2, NOW())
                                ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = NOW()
                            """
                            await conn.execute(_UPSERT_SHADOW, "shadow_total_pnl", str(stats.total_pnl))
                            await conn.execute(_UPSERT_SHADOW, "shadow_trades_executed", str(stats.trades_executed))
                        logger.debug("shadow_stats_persisted trades=%s pnl=%s", stats.trades_executed, stats.total_pnl)
                    except Exception as exc:
                        logger.debug("shadow_stats_persist_error error=%s", exc)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("peak_equity_persist_error error=%s", exc)

    async def _heartbeat_loop(self) -> None:
        while self.state.running:
            try:
                await asyncio.sleep(self.HEARTBEAT_INTERVAL)
                if self.context.ws_manager:
                    await self.context.ws_manager.send_heartbeat()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Heartbeat error: %s", exc)


    async def _dashboard_feed_loop(self) -> None:
        """Broadcast engine state to all WebSocket clients every second."""
        FEED_INTERVAL = 1.0
        while self.state.running:
            try:
                await asyncio.sleep(FEED_INTERVAL)
                ws = self.context.ws_manager
                if not ws or ws.connection_count == 0:
                    continue

                # Strategy status
                strategies = []
                for sid, info in self.context.strategies.items():
                    strategies.append({
                        "id": sid,
                        "enabled": info.get("enabled", False),
                        "type": info.get("type", "unknown"),
                    })

                # PnL
                realized = float(self.context.realized_pnl)
                unrealized = float(self.context.unrealized_pnl)

                # Positions
                positions = []
                if self.context.position_manager:
                    try:
                        for p in self.context.position_manager.get_all_positions():
                            positions.append({
                                "strategy_id": p.strategy_id,
                                "exchange_id": p.exchange_id,
                                "symbol": p.symbol,
                                "side": p.side,
                                "pnl": float(p.unrealized_pnl),
                            })
                    except Exception:
                        pass

                shadow_stats = None
                if self._paper_mode and hasattr(self._paper_mode, 'get_snapshot'):
                    try:
                        shadow_stats = self._paper_mode.get_snapshot()
                    except Exception:
                        pass

                # US-210: Compute extended fields
                total_equity = realized + unrealized
                active_strategy_count = sum(
                    1 for info in self.context.strategies.values()
                    if info.get("enabled", False)
                )
                # Win rate from shadow stats or 0
                feed_win_rate = 0.0
                if shadow_stats:
                    feed_win_rate = float(shadow_stats.get("win_rate", 0.0))

                await ws.broadcast({
                    "type": "state_update",
                    "data": {
                        "running": self.context.running,
                        "kill_switch": self.context.kill_switch_active,
                        "mode": self.context.execution_mode,
                        "strategy_count": len(strategies),
                        "strategies": strategies,
                        "pnl": {
                            "realized": realized,
                            "unrealized": unrealized,
                            "total": realized + unrealized,
                        },
                        "positions": positions,
                        "position_count": len(positions),
                        "shadow_stats": shadow_stats,
                        # US-210: Extended fields
                        "total_equity": total_equity,
                        "win_rate": feed_win_rate,
                        "active_strategy_count": active_strategy_count,
                    },
                    "ts": time.time(),
                })
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Dashboard feed error: %s", exc)


class _StubCostCalculator:
    """Minimal cost calculator for when the real one fails to init."""

    def estimate_cost(
        self,
        exchange_id: str,
        symbol: str,
        side: Any,
        size: Decimal,
        price: Decimal,
    ) -> Decimal:
        # Default 0.1% fee per leg
        return price * size * Decimal("0.001")


def build_app() -> Any:
    """Build FastAPI app for use with uvicorn (called by ASGI server)."""
    context = EngineContext()
    return create_app(context)


async def main() -> None:
    """Async entry point for direct execution."""
    context = EngineContext()
    app = create_app(context)
    engine = Engine(context=context)

    _op = get_settings().operational
    host = _op.api_host
    server_config = uvicorn.Config(
        app=app,
        host=host,
        port=_op.api_port,
        log_level="info",
    )
    server = uvicorn.Server(server_config)

    await asyncio.gather(
        engine.run(),
        server.serve(),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
