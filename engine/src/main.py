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
from src.core.config import ExecutionMode, Settings, get_settings

logger = logging.getLogger(__name__)

# Dynamic BTC reference price — read from env var, used for USDT→BTC position size conversion.
# Defaults to $50,000. Override via BTC_REFERENCE_PRICE env var for live/testnet.
_BTC_REFERENCE_PRICE = Decimal(os.environ.get("BTC_REFERENCE_PRICE", "50000"))


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
        self._shadow_mode: Any = None
        self._live_gate: Any = None
        self._data_mode: str = DataMode.SYNTHETIC

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Full engine startup sequence. Blocks until shutdown signal."""
        logger.info("LEVIATHAN engine starting...")
        self._setup_signal_handlers()

        try:
            await self._init_config()
            await self._init_infrastructure()
            await self._init_exchanges()
            await self._init_signal_pipeline()
            await self._init_strategies()
            await self._init_risk()
            await self._init_execution()
            await self._populate_context()
            await self._start_background_tasks()

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

        # Disconnect exchanges
        for eid, adapter in self._exchanges.items():
            try:
                await adapter.disconnect()
            except Exception as exc:
                logger.warning("Exchange %s disconnect error: %s", eid, exc)

        # Stop Shadow Mode
        if self._shadow_mode:
            try:
                await self._shadow_mode.stop()
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

        # Cancel background tasks
        for task in self.state.background_tasks:
            if not task.done():
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

    async def _init_config(self) -> None:
        try:
            self._settings = get_settings()
            self.context.environment = self._settings.engine_env
            self.context.execution_mode = self._settings.execution_mode.value
            logger.info("Config loaded — env=%s mode=%s capital_tier=%s",
                        self._settings.engine_env,
                        self._settings.execution_mode.value,
                        self._settings.capital.tier)
        except Exception as exc:
            logger.warning("Config load failed (using defaults): %s", exc)
            self._settings = Settings()
            self.context.environment = "dev"
            self.context.execution_mode = "paper"

    # ------------------------------------------------------------------
    # Step 2: Infrastructure (EventBus)
    # ------------------------------------------------------------------

    async def _init_infrastructure(self) -> None:
        mode = self._settings.execution_mode if self._settings else ExecutionMode.PAPER

        if mode == ExecutionMode.PAPER:
            from src.infra.redis.memory_bus import InMemoryEventBus
            self._event_bus = InMemoryEventBus()
            logger.info("InMemoryEventBus initialized (paper mode)")
        else:
            try:
                from src.infra.redis.client import RedisClient
                from src.infra.redis.event_bus import EventBus
                redis_client = RedisClient(self._settings.redis.url)
                await redis_client.connect()
                self._event_bus = EventBus(redis_client)
                logger.info("Redis EventBus initialized")
            except Exception as exc:
                logger.warning("Redis init failed, falling back to InMemoryEventBus: %s", exc)
                from src.infra.redis.memory_bus import InMemoryEventBus
                self._event_bus = InMemoryEventBus()

        # --- TimescaleDB + MarketRecorder ---
        await self._init_database()

        # --- Telegram Alerter ---
        self._init_telegram()

        # --- Rust PyO3 Bridge (log feature flags) ---
        self._init_rust_bridge()

    async def _init_database(self) -> None:
        """Initialize TimescaleDB connection pool, run schema migration, start MarketRecorder."""
        import os
        dsn = os.getenv("DATABASE_URL", "")
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
                import pathlib
                migration_path = (
                    pathlib.Path(__file__).parent / "infra" / "db" / "migrations"
                    / "001_init_schema.sql"
                )
                if migration_path.exists():
                    sql = migration_path.read_text()
                    async with self._db_pool.pool.acquire() as conn:
                        await conn.execute(sql)
                    logger.info("TimescaleDB schema migration applied")
                else:
                    logger.warning("Schema migration file not found: %s", migration_path)
            except Exception as exc:
                logger.warning("Schema migration failed (non-fatal): %s", exc)

            # Start MarketRecorder
            try:
                from src.infra.db.market_recorder import MarketRecorder
                self._market_recorder = MarketRecorder(pool=self._db_pool.pool)
                await self._market_recorder.start()
                logger.info("MarketRecorder started (flush=%dms, buffer=%d)",
                            MarketRecorder.FLUSH_INTERVAL_MS, MarketRecorder.MAX_BUFFER_SIZE)
            except Exception as exc:
                logger.warning("MarketRecorder init failed (non-fatal): %s", exc)
        except Exception as exc:
            logger.warning("TimescaleDB init failed (non-fatal, paper mode ok): %s", exc)

    def _init_telegram(self) -> None:
        """Initialize Telegram alerter from environment variables."""
        try:
            from src.infra.telegram import get_telegram_alerter
            self._telegram = get_telegram_alerter()
            if self._telegram._enabled:
                logger.info("Telegram alerter enabled")
            else:
                logger.info("Telegram alerter disabled (set TELEGRAM_ENABLED=true to enable)")
        except Exception as exc:
            logger.warning("Telegram alerter init failed (non-fatal): %s", exc)

    def _init_rust_bridge(self) -> None:
        """Log Rust PyO3 feature flag status."""
        try:
            from src.core.rust_bridge import get_feature_flags
            flags = get_feature_flags()
            logger.info("Rust bridge flags: %s", flags)
        except Exception as exc:
            logger.warning("Rust bridge init failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # Step 3: Exchange Adapters
    # ------------------------------------------------------------------

    async def _init_exchanges(self) -> None:
        mode = self._settings.execution_mode if self._settings else ExecutionMode.PAPER
        capital = self._settings.capital.initial_capital if self._settings else Decimal("70")

        if mode == ExecutionMode.PAPER:
            await self._init_paper_exchanges(capital)
        elif mode == ExecutionMode.SANDBOX:
            await self._init_sandbox_exchanges()
        else:
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
        use_native = (
            self._settings.trading.use_native_adapters if self._settings else False
        )
        exchanges = (
            self._settings.trading.active_exchanges if self._settings
            else ["binance", "bybit", "okx", "bitget"]
        )
        if use_native:
            await self._init_native_exchanges(exchanges, sandbox=False)
        else:
            logger.info("Live exchange initialization — CCXTAdapter")
            # TODO: create CCXTAdapters with real credentials

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

        min_edge_bps = int(os.environ.get("MIN_EDGE_BPS", "40"))
        max_spread_pct = float(os.environ.get("MAX_SPREAD_PCT", "0.05"))
        cooldown_sec = float(os.environ.get("SIGNAL_COOLDOWN_SEC", "2.0"))
        min_price_usd = Decimal(os.environ.get("MIN_PRICE_USD", "0.10"))
        signal_config = SignalConfig(
            min_edge=Decimal(str(min_edge_bps)) / Decimal("10000"),  # bps → fraction
            max_spread_pct=Decimal(str(max_spread_pct)),
            cooldown_seconds=cooldown_sec,
            min_price_usd=min_price_usd,
        )
        self._signal_generator = SignalGenerator(
            price_hub=self._price_hub,
            cost_calculator=self._cost_calculator,
            config=signal_config,
            event_bus=self._event_bus,
        )
        logger.info(
            "Signal pipeline initialized min_edge_bps=%s max_spread_pct=%s",
            min_edge_bps, max_spread_pct,
        )

    # ------------------------------------------------------------------
    # Step 5: Strategies
    # ------------------------------------------------------------------

    async def _init_strategies(self) -> None:
        from src.strategies.manager import StrategyManager

        self._strategy_manager = StrategyManager(
            event_bus=self._event_bus,
            consumer_name="manager-0",
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
        from src.strategies.latency_arb import LatencyArbStrategy

        # Use a simple stub if CostCalculator didn't initialize
        cost_calc = self._cost_calculator
        if cost_calc is None:
            cost_calc = _StubCostCalculator()

        # Shared latency tracker for LatencyArb strategy
        self._latency_tracker = LatencyTracker()

        # Load tuned parameters (READY/MONITOR strategies only)
        tuned = self._load_strategy_params()

        # Build strategy configs from tuned params (fall back to defaults)
        sf_p = tuned.get("spot_futures", {})
        sf_config = SpotFuturesConfig(
            min_basis_bps=Decimal(str(sf_p.get("min_basis_bps", 15))),
            max_position_size=Decimal(str(sf_p.get("max_position_size_usdt", 5484))) / _BTC_REFERENCE_PRICE,
        ) if sf_p.get("status") in ("READY", "MONITOR") else None

        fr_p = tuned.get("funding_rate", {})
        fr_config = FundingRateConfig(
            min_funding_diff_bps=Decimal(str(fr_p.get("min_funding_diff_bps", 5))),
            max_position_size=Decimal(str(fr_p.get("max_position_size_usdt", 8928))) / _BTC_REFERENCE_PRICE,
        ) if fr_p.get("status") in ("READY", "MONITOR") else None

        ce_p = tuned.get("cross_exchange", {})
        ce_config = CrossExchangeConfig(
            min_spread_bps=Decimal(str(ce_p.get("min_spread_bps", 10))),
            max_position_size=Decimal(str(ce_p.get("max_position_size_usdt", 9767))) / _BTC_REFERENCE_PRICE,
        ) if ce_p.get("status") in ("READY", "MONITOR") else None

        ff_p = tuned.get("futures_futures", {})
        ff_config = FuturesFuturesConfig(
            min_spread_bps=Decimal(str(ff_p.get("min_spread_bps", 8))),
            max_position_size=Decimal(str(ff_p.get("max_position_size_usdt", 1738))) / _BTC_REFERENCE_PRICE,
        ) if ff_p.get("status") in ("READY", "MONITOR") else None

        tri_p = tuned.get("triangular", {})
        tri_config = TriangularConfig(
            min_profit_bps=Decimal(str(tri_p.get("min_profit_bps", 10))),
            max_position_usdt=Decimal(str(tri_p.get("max_position_usdt", 1000))),
        ) if tri_p.get("status") in ("READY", "MONITOR") else None

        strategies = [
            CrossExchangeStrategy("cross_exchange_v1", cost_calc, config=ce_config),
            SpotFuturesStrategy("spot_futures_v1", cost_calc, config=sf_config),
            FuturesFuturesStrategy("futures_futures_v1", cost_calc, config=ff_config),
            TriangularStrategy("triangular_v1", cost_calc, config=tri_config),
            FundingRateStrategy("funding_rate_v1", cost_calc, config=fr_config),
            StatisticalArbStrategy("statistical_arb_v1", cost_calc),
            LatencyArbStrategy("latency_arb_v1", cost_calc, self._latency_tracker),
        ]

        # CexDex requires a DEXAdapter — register only if configured
        try:
            from src.strategies.cex_dex import CexDexStrategy
            dex_adapter = self._build_dex_adapter()
            if dex_adapter is not None:
                strategies.append(
                    CexDexStrategy(
                        "cex_dex_v1", cost_calc, dex_adapter,
                        cex_exchange_id=list(self._exchanges.keys())[0] if self._exchanges else "binance",
                        symbol="BTC/USDT",
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
        """Build DEX adapter if DEX configuration is available. Returns None if not configured."""
        import os
        dex_rpc = os.getenv("DEX_RPC_URL", "")
        if not dex_rpc:
            return None
        # Future: return a real Uniswap/Curve adapter here
        logger.info("DEX_RPC_URL set but no concrete DEX adapter implemented yet")
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

            self._circuit_breaker = CircuitBreaker(on_state_change=cb_state_callback)
            logger.info("CircuitBreaker initialized")
        except Exception as exc:
            logger.warning("CircuitBreaker init failed: %s", exc)

        try:
            from src.risk.guardian import RiskGuardian
            self._risk_guardian = RiskGuardian(
                circuit_breaker=self._circuit_breaker,
            )
            logger.info("RiskGuardian initialized with 9 pre-trade checks")
        except Exception as exc:
            logger.warning("RiskGuardian init failed: %s", exc)

    # ------------------------------------------------------------------
    # Step 7: Execution Engine
    # ------------------------------------------------------------------

    async def _init_execution(self) -> None:
        from src.execution.executor import AtomicExecutor
        from src.execution.trade_consumer import TradeRequestConsumer

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

    def _build_risk_check_fn(self):
        """Create a risk check callable for the trade consumer."""
        from src.risk.guardian import PortfolioState, TradeProposal

        capital = self._settings.capital.initial_capital if self._settings else Decimal("70")

        def risk_check(trade_request) -> tuple[bool, str]:
            portfolio = PortfolioState(
                total_capital=capital * len(self._exchanges),
                used_capital=Decimal("0"),
                current_drawdown_pct=Decimal("0"),
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

    # ------------------------------------------------------------------
    # Step 8: Populate EngineContext for API
    # ------------------------------------------------------------------

    async def _populate_context(self) -> None:
        self.context.strategy_manager = self._strategy_manager
        self.context.risk_guardian = self._risk_guardian
        self.context.position_manager = self._position_manager
        self.context.trade_consumer = self._trade_consumer
        self.context.engine = self

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
        import os
        self._data_mode = os.getenv("DATA_MODE", DataMode.SYNTHETIC).lower()

        tasks = [
            asyncio.create_task(self._strategy_manager_loop(), name="strategy_mgr"),
            asyncio.create_task(self._trade_consumer_loop(), name="trade_consumer"),
            asyncio.create_task(self._health_check_loop(), name="health_check"),
            asyncio.create_task(self._reconcile_loop(), name="reconcile"),
            asyncio.create_task(self._heartbeat_loop(), name="ws_heartbeat"),
            asyncio.create_task(self._dashboard_feed_loop(), name="dashboard_feed"),
        ]

        if self._data_mode == DataMode.SHADOW:
            # Shadow mode: real data + paper execution + full metrics
            tasks.append(
                asyncio.create_task(self._shadow_mode_loop(), name="shadow_mode")
            )
            logger.info("Data mode: SHADOW — starting Shadow Mode orchestrator")
        elif self._data_mode == DataMode.REAL_PUBLIC:
            # Real public WebSocket data — no API keys, observation mode
            tasks.append(
                asyncio.create_task(self._real_data_feed_loop(), name="real_data_feed")
            )
            logger.info("Data mode: REAL_PUBLIC — starting WebSocket collectors")
        else:
            # Synthetic paper mode — use PaperExchangeAdapter orderbook feed
            mode = self._settings.execution_mode if self._settings else ExecutionMode.PAPER
            if mode in (ExecutionMode.PAPER, ExecutionMode.SANDBOX):
                tasks.append(
                    asyncio.create_task(self._orderbook_feed_loop(), name="orderbook_feed")
                )
                # Multi-strategy signal simulator for paper mode
                tasks.append(
                    asyncio.create_task(self._paper_signal_simulator_loop(), name="multi_signal")
                )
            logger.info("Data mode: %s", self._data_mode)

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
            await self._telegram.send_alert(
                f"Real data collection started\n"
                f"Exchanges: {', '.join(exchanges)}\n"
                f"Symbols: {', '.join(symbols)}",
                level="INFO",
            )

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

    async def _shadow_mode_loop(self) -> None:
        """Start Shadow Mode: real data + paper execution + full metrics.

        Creates a ShadowMode orchestrator wired to the engine's signal pipeline,
        paper executor (power-law slippage), market recorder, and telegram alerter.
        Optionally starts a LiveGate auto-evaluation loop.
        """
        from src.modes.shadow import ShadowMode

        symbols = self._settings.trading.symbols if self._settings else ["BTC/USDT"]
        exchanges = self._settings.trading.active_exchanges if self._settings else ["binance", "bybit", "okx", "bitget"]

        # Create MultiStrategySignalProducer for 6 additional strategies
        from src.core.multi_signal import MultiStrategySignalProducer

        multi_signal_producer = MultiStrategySignalProducer(
            event_bus=self._event_bus,
            latency_tracker=getattr(self, "_latency_tracker", None),
        )

        self._shadow_mode = ShadowMode(
            signal_generator=self._signal_generator,
            paper_executor=None,  # auto-creates with PowerLawSlippage(gamma=0.5)
            collector_manager=None,  # auto-creates CollectorManager
            market_recorder=self._market_recorder,
            telegram=self._telegram,
            symbols=symbols,
            exchanges=exchanges,
            multi_signal_producer=multi_signal_producer,
        )

        await self._shadow_mode.start()
        logger.info("Shadow Mode started: %s for %s", exchanges, symbols)

        # Start LiveGate auto-evaluation if DB is available
        if self._db_pool is not None:
            try:
                from src.modes.live_gate import LiveGate
                from src.risk.kill_switch import KillSwitch

                kill_switch = KillSwitch()  # uses module-level halt flag
                self._live_gate = LiveGate(
                    pool=self._db_pool.pool,
                    telegram=self._telegram,
                    kill_switch=kill_switch,
                    circuit_breaker=self._circuit_breaker,
                    settings=self._settings,
                )
                await self._live_gate.start_auto_evaluation()
                logger.info("LiveGate auto-evaluation started (24h cycle)")
            except Exception as exc:
                logger.warning("LiveGate init failed (non-fatal): %s", exc)

        # Send Telegram notification
        if self._telegram and self._telegram._enabled:
            await self._telegram.send_alert(
                f"Shadow Mode active\n"
                f"Exchanges: {', '.join(exchanges)}\n"
                f"Symbols: {', '.join(symbols)}\n"
                f"LiveGate: {'enabled' if self._live_gate else 'disabled'}",
                level="INFO",
            )

        # Keep alive until cancelled
        # NOTE: Cleanup is handled exclusively by Engine.stop() to avoid
        # double-cleanup race conditions. Do NOT add cleanup here.
        try:
            while self.state.running:
                await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            pass

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
        # Log exchange health scores
        for eid, adapter in self._exchanges.items():
            score = adapter.health_score
            if score < 0.9:
                logger.warning("Exchange %s health_score=%.2f", eid, score)

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

    async def _reconcile_loop(self) -> None:
        while self.state.running:
            try:
                await asyncio.sleep(self.RECONCILE_INTERVAL)
                logger.debug("Position reconciliation tick")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Reconcile error: %s", exc)

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

    host = os.getenv("API_HOST", "0.0.0.0")
    server_config = uvicorn.Config(
        app=app,
        host=host,
        port=int(os.getenv("PORT", "8000")),
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
