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
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import uvicorn
from dotenv import load_dotenv

load_dotenv()  # Load .env before any os.getenv() calls

from src.api.server import EngineContext, create_app
from src.core.config import ExecutionMode, Settings, get_settings
from src.core.config_loader import get_bool_flag

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


def _get_fallback_exchanges() -> list[str]:
    """engine.json의 active 거래소 중 spot 거래소만 반환 (fallback 용).
    WS-1.5: load_engine_config() 경유 (직접 파일 읽기 제거).
    """
    try:
        from src.core.config import load_engine_config
        cfg = load_engine_config()
        active = cfg.get("exchanges", {}).get("active", [])
        return [ex for ex in active if not ex.endswith("_futures")] or ["binance", "bitget"]
    except Exception:
        return ["binance", "bitget"]


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
    SHUTDOWN_TIMEOUT = 30  # PHOENIX: 10→30s graceful shutdown timeout

    def __init__(self, context: EngineContext | None = None) -> None:
        self.context = context or EngineContext()
        self.state = EngineState()
        # Phase 5.2.2: 16 mutable runtime fields → EngineStateRuntime dataclass.
        # legacy state(EngineState: running/kill_switch_active/background_tasks)와 별개.
        # 16 fields: total_pnl, peak_equity, position_sizes, cross_exchange_positions,
        # cross_gross_exposure, exchange_health, position_tracking_errors, pm_drain_errors,
        # prev_reconciler_orphans, regime_pnl_history, regime_last_pnl, ...
        from src.core.engine_state import EngineState as _RuntimeEngineState
        self._state: _RuntimeEngineState = _RuntimeEngineState()
        self._shutdown_event = asyncio.Event()
        # BUG-83: engine.json exchanges.active is the SOLE source of truth for active exchanges.
        # Pydantic default only has spot exchanges (missing futures). Load once, use everywhere.
        try:
            from src.core.config import load_engine_config as _lec_init
            self._active_exchanges: list[str] = _lec_init().get("exchanges", {}).get("active", [])
        except Exception:
            self._active_exchanges = ["binance", "bitget", "binance_futures", "bitget_futures"]

        # Path-B Day-5 fail-fast boot guard: validate engine.json against pydantic
        # schema before any subsystem initializes. Catches config drift (BUG-228 class)
        # at boot time rather than mid-trade. Opt-in via env var; if parse fails, logs
        # but does not raise yet — Day 6 promotes to hard-fail.
        try:
            import os
            if os.environ.get("LEVIATHAN_STRICT_CONFIG", "1") == "1":
                from src.core.config_service import ConfigService
                import pathlib
                _cs_path = pathlib.Path(__file__).parent.parent / "config" / "engine.json"
                _cs = ConfigService(_cs_path)
                _cs.load()  # pydantic validation; raises ValueError if schema violation
                logger.info("engine.config_service_validated path=%s mode=%s", _cs_path, _cs.current.mode)
        except Exception as _cfg_err:
            logger.warning(
                "engine.config_service_validation_failed err=%s — Day-5 soft fail, Day-6 will hard-fail",
                _cfg_err,
            )

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
        self._position_sizes: dict[str, Decimal] = {}   # symbol -> current notional exposure (nets out for hedged)
        self._cross_exchange_positions: set[str] = set()  # symbols with active cross-exchange hedged positions
        self._cross_gross_exposure: Decimal = Decimal("0")  # gross capital in delta-neutral hedges (both legs)
        # NOTE: _position_sizes nets BUY/SELL for same symbol (correct for directional exposure / Check#1),
        # but yields len=0 for delta-neutral hedged positions (funding_rate, spot_futures).
        # _cross_exchange_positions tracks these separately for Check #10 (max concurrent).
        # _cross_gross_exposure tracks total capital deployed in hedges for Check #3 (total exposure).
        self._peak_equity: Decimal | None = None           # initialized to capital_total on first risk check
        self._total_pnl: Decimal = Decimal("0")          # cumulative realized PnL
        self._exchange_health: dict[str, Decimal] = {}   # exchange_id -> health score (0-1)
        # US-131: RegimeDetector reference (set during _init_signal_pipeline)
        self._regime_detector: Any = None
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
        # BUG-04: declare kill_switch so _redis_halt_watch_loop doesn't AttributeError before mode loop sets it
        self._kill_switch: Any = None
        # US-351: BacktestMode result (populated by _backtest_mode_task)
        self._backtest_result: Any = None
        self._supervisor: Any = None  # Day 15: SUPERVISOR_ACTIVE-gated
        # Phase 6 Step 1: ExecutionResultDispatcher (default None until _init_listeners)
        self._listener_dispatcher: Any = None

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
            # Phase 6 Step 1: build 14 listeners + dispatcher (LOW risk, env flag gated)
            self._init_listeners()
            await self._start_background_tasks()
            await self._init_tuner()

            self.state.running = True
            self.context.running = True
            logger.info("Engine running in %s mode — waiting for shutdown signal",
                        self._engine_mode.value if hasattr(self, '_engine_mode') else (
                            self._settings.execution_mode if self._settings else "unknown"
                        ))
            if get_bool_flag("SUPERVISOR_ACTIVE"):
                from src.core.supervisor import TradingSupervisor  # noqa: PLC0415
                self._supervisor = TradingSupervisor(self._settings)
            await self._shutdown_event.wait()
        except Exception as exc:
            logger.critical("Engine startup failed: %s", exc, exc_info=True)
        finally:
            if self._supervisor is not None:
                try: await self._supervisor.stop()  # Day 15: supervisor-owned stop
                except Exception as e: logger.warning("supervisor_stop err=%r", e)
            await self.stop()

    async def stop(self) -> None:
        """Graceful shutdown."""
        if not self.state.running and not self.state.background_tasks:
            return

        logger.info("Engine shutting down...")
        self.state.running = False
        self.context.running = False
        self._shutdown_event.set()

        # BUG-82: Stop strategy manager FIRST so in-flight signals are drained
        # before trade_consumer shuts down (prevents lost TradeRequests)
        if self._strategy_manager:
            try:
                await self._strategy_manager.stop()
            except Exception as exc:
                logger.warning("StrategyManager stop error: %s", exc)

        # Stop trade consumer after strategies are silent
        if self._trade_consumer:
            try:
                await self._trade_consumer.stop()
            except Exception as exc:
                logger.warning("TradeConsumer stop error: %s", exc)

        # Stop LiveMode BEFORE cancelling orders — sets _running=False which gates
        # _on_orderbook() preventing new signals from being processed after we start
        # cancelling orders (avoids race where new orders open during shutdown cleanup).
        if getattr(self, '_live_mode', None) is not None:
            try:
                await self._live_mode.stop()
            except Exception as exc:
                logger.warning("LiveMode stop error: %s", exc)

        # US-155: Cancel open orders in live mode before disconnecting
        # BUG-8 fix: use _engine_mode (engine.json 기준) instead of execution_mode (.env 기준)
        from src.core.config import EngineMode
        if (getattr(self, '_engine_mode', None) == EngineMode.LIVE
                and self._exchanges):
            await self._cancel_open_orders()

        # Close open positions in live mode after cancelling orders
        if (getattr(self, '_engine_mode', None) == EngineMode.LIVE
                and self._exchanges):
            await self._close_all_positions_on_shutdown()

        # Stop collector manager (BUG-06: not stopped in live mode, leaked WS connections)
        if self._collector_manager:
            try:
                await self._collector_manager.stop()
            except Exception as exc:
                logger.warning("CollectorManager stop error: %s", exc)

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
        from src.runtime.bootstrap import setup_signal_handlers
        setup_signal_handlers(self)

    def _handle_signal(self) -> None:
        from src.runtime.bootstrap import handle_signal
        handle_signal(self)

    # ------------------------------------------------------------------
    # Step 1: Configuration
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_trading_json_defaults(cfg: dict) -> None:
        """Phase 4: thin wrapper → src.runtime.bootstrap.apply_trading_json_defaults"""
        from src.runtime.bootstrap import apply_trading_json_defaults
        apply_trading_json_defaults(cfg)

    async def _init_config(self) -> None:
        from src.runtime.bootstrap import init_config
        await init_config(self)

    def _validate_config(self) -> None:
        from src.runtime.bootstrap import validate_config
        validate_config(self)

    async def _resolve_symbols(self) -> None:
        from src.runtime.bootstrap import resolve_symbols
        await resolve_symbols(self)

    async def _init_infrastructure(self) -> None:
        from src.runtime.bootstrap import init_infrastructure
        await init_infrastructure(self)

    async def _init_database(self) -> None:
        from src.runtime.bootstrap import init_database
        await init_database(self)

    def _init_telegram(self) -> None:
        from src.runtime.bootstrap import init_telegram
        init_telegram(self)

    def _init_rust_bridge(self) -> None:
        from src.runtime.bootstrap import init_rust_bridge
        init_rust_bridge(self)

    async def _init_tuner(self) -> None:
        from src.runtime.bootstrap import init_tuner
        await init_tuner(self)

    # ------------------------------------------------------------------
    # Step 3: Exchange Adapters
    # ------------------------------------------------------------------

    async def _init_exchanges(self) -> None:
        from src.runtime.exchange_init import init_exchanges
        await init_exchanges(self)

    async def _init_paper_exchanges(self, capital: Decimal) -> None:
        from src.runtime.exchange_init import init_paper_exchanges
        await init_paper_exchanges(self, capital)

    async def _init_sandbox_exchanges(self) -> None:
        from src.runtime.exchange_init import init_sandbox_exchanges
        await init_sandbox_exchanges(self)

    async def _init_live_exchanges(self) -> None:
        from src.runtime.exchange_init import init_live_exchanges
        await init_live_exchanges(self)

    async def _init_native_exchanges(self, exchanges: list[str], sandbox: bool) -> None:
        from src.runtime.exchange_init import init_native_exchanges
        await init_native_exchanges(self, exchanges, sandbox)

    # ------------------------------------------------------------------
    # Step 4: Signal Pipeline
    # ------------------------------------------------------------------

    async def _init_signal_pipeline(self) -> None:
        from src.runtime.pipeline_init import init_signal_pipeline
        await init_signal_pipeline(self)

    async def _init_strategies(self) -> None:
        from src.runtime.pipeline_init import init_strategies
        await init_strategies(self)

    def _load_strategy_params(self) -> dict:
        from src.runtime.pipeline_init import load_strategy_params
        return load_strategy_params(self)

    def _load_activation_disabled_ids(self) -> set[str]:
        from src.runtime.pipeline_init import load_activation_disabled_ids
        return load_activation_disabled_ids(self)

    async def _register_default_strategies(self) -> None:
        from src.runtime.pipeline_init import register_default_strategies
        await register_default_strategies(self)

    def _build_dex_adapter(self):
        from src.runtime.pipeline_init import build_dex_adapter
        return build_dex_adapter(self)

    # ------------------------------------------------------------------
    # Step 6: Risk Management
    # ------------------------------------------------------------------

    async def _init_risk(self) -> None:
        from src.runtime.risk_execution import init_risk
        await init_risk(self)

    async def _init_execution(self) -> None:
        from src.runtime.risk_execution import init_execution
        await init_execution(self)

    def _build_risk_check_fn(self):
        from src.runtime.risk_execution import build_risk_check_fn
        return build_risk_check_fn(self)

    def _on_execution_result(self, trade_request, execution_result) -> None:
        from src.runtime.risk_execution import on_execution_result
        on_execution_result(self, trade_request, execution_result)

    async def _rebalancer_loop(self) -> None:
        from src.runtime.background_loops import rebalancer_loop
        await rebalancer_loop(self)

    async def _cancel_open_orders(self) -> None:
        from src.runtime.background_loops import cancel_open_orders
        await cancel_open_orders(self)

    async def _close_all_positions_on_shutdown(self) -> None:
        from src.runtime.background_loops import close_all_positions_on_shutdown
        await close_all_positions_on_shutdown(self)

    def _record_alert(self, alert_type: str, severity: str, message: str, metadata: dict | None = None) -> None:
        from src.runtime.background_loops import record_alert
        record_alert(self, alert_type, severity, message, metadata)

    async def _populate_context(self) -> None:
        from src.runtime.background_loops import populate_context
        await populate_context(self)

    async def _start_background_tasks(self) -> None:
        from src.runtime.background_loops import start_background_tasks
        await start_background_tasks(self)

    async def _strategy_manager_loop(self) -> None:
        from src.runtime.background_loops import strategy_manager_loop
        await strategy_manager_loop(self)

    async def _trade_consumer_loop(self) -> None:
        from src.runtime.background_loops import trade_consumer_loop
        await trade_consumer_loop(self)


    async def _backtest_mode_task(self) -> None:
        from src.runtime.mode_loops import backtest_mode_task
        await backtest_mode_task(self)

    async def _orderbook_feed_loop(self) -> None:
        from src.runtime.mode_loops import orderbook_feed_loop
        await orderbook_feed_loop(self)

    def _init_listeners(self) -> None:
        """Phase 6 Step 1: 14 ExecutionResultListener + Dispatcher 빌드.

        env flag EXECUTION_DISPATCHER_ENABLED 활성 시에만 빌드.
        default false → paper canary 영향 0 (legacy on_execution_result 사용).
        """
        if not get_bool_flag("EXECUTION_DISPATCHER_ENABLED"):
            logger.debug("listener_dispatcher.disabled (EXECUTION_DISPATCHER_ENABLED=false)")
            return
        try:
            from src.listeners.factory import build_dispatcher_from_engine
            self._listener_dispatcher = build_dispatcher_from_engine(self)
            logger.info(
                "ListenerFactory.built %d listeners",
                self._listener_dispatcher.listener_count,
            )
        except Exception as exc:
            logger.warning("listener_dispatcher.init_failed: %s", exc)

    async def _real_data_feed_loop(self) -> None:
        from src.runtime.mode_loops import real_data_feed_loop
        await real_data_feed_loop(self)

    async def _live_mode_loop(self) -> None:
        from src.runtime.mode_loops import live_mode_loop
        await live_mode_loop(self)

    async def _paper_mode_loop(self) -> None:
        from src.runtime.mode_loops import paper_mode_loop
        await paper_mode_loop(self)

    async def _strategy_validation_loop(self) -> None:
        from src.runtime.mode_loops import strategy_validation_loop
        await strategy_validation_loop(self)

    async def _progressive_shadow_loop(self) -> None:
        from src.runtime.mode_loops import progressive_shadow_loop
        await progressive_shadow_loop(self)

    # Phase 4-1 ml_loops thin wrappers (re-added after 4-7 boundary cleanup)
    async def _regime_detect_loop(self) -> None:
        from src.runtime.ml_loops import regime_detect_loop
        await regime_detect_loop(self)

    async def _adaptive_threshold_loop(self) -> None:
        from src.runtime.ml_loops import adaptive_threshold_loop
        await adaptive_threshold_loop(self)

    async def _hmm_training_loop(self) -> None:
        from src.runtime.ml_loops import hmm_training_loop
        await hmm_training_loop(self)

    async def _xgb_training_loop(self) -> None:
        from src.runtime.ml_loops import xgb_training_loop
        await xgb_training_loop(self)

    # Phase 4-6 background_loops thin wrappers
    async def _health_check_loop(self) -> None:
        from src.runtime.background_loops import health_check_loop
        await health_check_loop(self)

    async def _run_health_check(self) -> None:
        from src.runtime.background_loops import run_health_check
        await run_health_check(self)

    async def _startup_position_scan(self) -> None:
        from src.runtime.background_loops import startup_position_scan
        await startup_position_scan(self)

    async def _startup_compliance_audit(self) -> None:
        from src.runtime.background_loops import startup_compliance_audit
        await startup_compliance_audit(self)

    async def _strategy_exit_poll_loop(self) -> None:
        from src.runtime.background_loops import strategy_exit_poll_loop
        await strategy_exit_poll_loop(self)

    async def _reconcile_loop(self) -> None:
        from src.runtime.background_loops import reconcile_loop
        await reconcile_loop(self)

    async def _peak_equity_persist_loop(self) -> None:
        from src.runtime.background_loops import peak_equity_persist_loop
        await peak_equity_persist_loop(self)

    async def _heartbeat_loop(self) -> None:
        from src.runtime.background_loops import heartbeat_loop
        await heartbeat_loop(self)

    async def _pm_drain_loop(self) -> None:
        from src.runtime.background_loops import pm_drain_loop
        await pm_drain_loop(self)

    async def _redis_halt_watch_loop(self) -> None:
        from src.runtime.background_loops import redis_halt_watch_loop
        await redis_halt_watch_loop(self)

    async def _btc_price_update_loop(self) -> None:
        from src.runtime.background_loops import btc_price_update_loop
        await btc_price_update_loop(self)

    async def _dashboard_feed_loop(self) -> None:
        from src.runtime.background_loops import dashboard_feed_loop
        await dashboard_feed_loop(self)


class _StubCostCalculator:
    """Test stub fallback when CostCalculator instance is None."""

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
    # PHOENIX §8.3 Tier1 patch 3-2: uvloop for ~10-20ms scheduling latency reduction
    try:
        import uvloop
        uvloop.run(main())
    except ImportError:
        logger.info("uvloop not available, falling back to asyncio default loop")
        asyncio.run(main())
