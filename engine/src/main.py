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
        exchanges = self._active_exchanges or _get_fallback_exchanges()

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
        exchanges = _mode_cfg.get("exchanges") or self._active_exchanges or _get_fallback_exchanges()

        # Create MultiStrategySignalProducer
        self._multi_signal_producer = MultiStrategySignalProducer(
            event_bus=self._event_bus,
            latency_tracker=getattr(self, "_latency_tracker", None),
        )

        # Dynamic symbol + exchange discovery — reads engine.json at runtime.
        funding_rate_collector = None
        try:
            _fr_symbols = await FundingRateCollector.fetch_paired_symbols(
                http_client=getattr(self, "_http_client", None),
            )
            funding_rate_collector = FundingRateCollector(
                symbols=_fr_symbols,
                exchanges=FundingRateCollector.get_poll_exchanges(),
                http_client=getattr(self, "_http_client", None),
            )
        except Exception as exc:
            logger.warning("FundingRateCollector init failed (non-fatal): %s", exc)

        # Phase H-2: execution mode from EngineMode (config/engine.json)
        execution_mode = self._engine_mode.value if hasattr(self, '_engine_mode') else "paper"

        # BUG-228c: runtime per-exchange min_notional registry (replaces hardcoded
        # execution.exchange_min_notional). Registry holds reference to self._exchanges
        # so late-registered adapters are visible automatically.
        from src.infra.exchange.min_notional_registry import MinNotionalRegistry
        self._min_notional_registry = MinNotionalRegistry(self._exchanges)

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
            tca_analyzer=getattr(self, "_tca_analyzer", None),
            slippage_feedback_collector=getattr(self, "_slippage_fb_collector", None),
            position_manager=self._position_manager,
            cost_feedback=getattr(self, "_cost_feedback", None),  # WS-B: shared TCAAdaptiveFeedback
            min_notional_registry=self._min_notional_registry,  # BUG-228c: runtime min_notional
        )
        from src.reconciliation import ExchangePnLSnapshot, PnLLedger, PnLReconciler  # Path-B Day-1
        self._pnl_snapshot = ExchangePnLSnapshot(adapters=list(self._exchanges.values()), db_pool=self._db_pool)
        self._pnl_ledger = PnLLedger(snapshot=self._pnl_snapshot, engine_pnl_getter=lambda: getattr(self._live_mode._stats, "total_pnl", 0.0))
        self._pnl_reconciler = PnLReconciler(snapshot=self._pnl_snapshot, engine_pnl_getter=lambda: getattr(self._live_mode._stats, "total_pnl", 0.0), ledger=self._pnl_ledger, telegram=self._telegram)
        self._live_mode._pnl_ledger = self._pnl_ledger  # inject post-init (frozen live.py contract)

        try:
            await self._live_mode.start()
            self.context.execution_mode = execution_mode

            # Keep alive until cancelled
            while self.state.running:
                await asyncio.sleep(5.0)
        except LiveGateFailed as _lgf:
            logger.critical(
                "live_mode_aborted — pre-existing positions detected. "
                "Run close_positions.py --execute first, then restart engine. err=%s", _lgf
            )
            if hasattr(self, "state"):
                self.state.running = False
            return  # stop cleanly — do NOT fall back to paper with live tasks running
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("_live_mode_loop FATAL error: %s", exc, exc_info=True)
        finally:
            if hasattr(self, "_live_mode") and self._live_mode is not None:
                await self._live_mode.stop()

    async def _regime_detect_loop(self) -> None:
        """Phase 4: thin wrapper → src.runtime.ml_loops.regime_detect_loop"""
        from src.runtime.ml_loops import regime_detect_loop
        await regime_detect_loop(self)

    async def _adaptive_threshold_loop(self) -> None:
        """Phase 4: thin wrapper → src.runtime.ml_loops.adaptive_threshold_loop"""
        from src.runtime.ml_loops import adaptive_threshold_loop
        await adaptive_threshold_loop(self)

    async def _hmm_training_loop(self) -> None:
        """Phase 4: thin wrapper → src.runtime.ml_loops.hmm_training_loop"""
        from src.runtime.ml_loops import hmm_training_loop
        await hmm_training_loop(self)

    async def _xgb_training_loop(self) -> None:
        """Phase 4: thin wrapper → src.runtime.ml_loops.xgb_training_loop"""
        from src.runtime.ml_loops import xgb_training_loop
        await xgb_training_loop(self)

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
            exchanges = self._active_exchanges or _get_fallback_exchanges()

            # Create MultiStrategySignalProducer for 6 additional strategies
            from src.core.multi_signal import MultiStrategySignalProducer

            multi_signal_producer = MultiStrategySignalProducer(
                event_bus=self._event_bus,
                latency_tracker=getattr(self, "_latency_tracker", None),
            )

            # Dynamic symbol + exchange discovery — reads engine.json at runtime.
            # No hardcoding: adding a *_futures exchange to engine.json auto-activates it.
            _fr_symbols = await FundingRateCollector.fetch_paired_symbols(
                http_client=getattr(self, "_http_client", None),
            )
            funding_rate_collector = FundingRateCollector(
                symbols=_fr_symbols,
                exchanges=FundingRateCollector.get_poll_exchanges(),
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

            # 2026-04-26 Phase 2B: paper에서 Day 6 (ExecutionJournal) + Day 12 (PreTradeValidator) 활성화
            # paper/live 단일 배관 통합. flag (engine.json.feature_flags) 활성 시에만 inject.
            from src.core.config_loader import get_config as _shadow_get_cfg
            _shadow_pre_trade_validator = None
            _shadow_execution_journal = None
            if get_bool_flag("EXECUTION_JOURNAL_ENABLED"):
                try:
                    import pathlib as _pl
                    from src.execution.journal import ExecutionJournal
                    # review fix: 2-hop (engine/src/main.py → engine/) 로 일관 (line 117 동일 패턴)
                    _journal_path = _pl.Path(__file__).parent.parent / "logs" / "paper_execution_journal.db"
                    _journal_path.parent.mkdir(parents=True, exist_ok=True)
                    _shadow_execution_journal = ExecutionJournal(db_path=_journal_path)
                    # review fix CRITICAL: ExecutionJournal는 start()이지 initialize() 아님
                    await _shadow_execution_journal.start()
                    logger.info("paper_mode.execution_journal_enabled path=%s", _journal_path)
                except Exception as exc:
                    # review fix: traceback 포함 (silent CRITICAL 차단)
                    logger.warning("paper_mode.execution_journal_init_failed: %r", exc, exc_info=True)
            if get_bool_flag("EXECUTION_PRETRADE_VALIDATOR_ENABLED"):
                try:
                    from src.execution.pre_trade_validator import PreTradeValidator
                    from src.execution.dedup import DeduplicationGate
                    # review fix CRITICAL: halt_local은 module-level 함수 (KillSwitch attribute 아님)
                    from src.risk.kill_switch import halt_local as _shadow_halt_local
                    _shadow_dedup = DeduplicationGate(window_s=10.0)
                    # 2026-04-26 fix v10: min_notional_registry stub (paper 자체 추적, gate skip)
                    class _PaperMinNotionalStub:
                        async def get(self, exchange_id, symbol):
                            return 0.0  # paper에서 min_notional 게이트 무용 (실거래 없음)
                    _shadow_min_notional = _PaperMinNotionalStub()
                    _shadow_strategy_filter_frozen = (
                        frozenset(_shadow_strategy_filter) if _shadow_strategy_filter else None
                    )
                    _total_capital = float(_shadow_get_cfg("portfolio.total_capital_usdt", default=140.0))
                    _max_session_loss = float(_shadow_get_cfg("risk.max_session_loss_usd", default=10.0))
                    _shadow_pre_trade_validator = PreTradeValidator(
                        strategy_filter=_shadow_strategy_filter_frozen,
                        strategy_disable_until={},
                        kill_switch=_shadow_kill_switch,
                        circuit_breaker=None,
                        rate_buckets=None,
                        flash_guard=self._flash_guard,
                        # 2026-04-26 fix: paper에서 risk_guardian=None (live 인스턴스 공유 시 100% reject 발견 v9).
                        # paper는 자체 risk 추적 (loss cap + portfolio_risk + flash_guard).
                        # live mode에서만 risk_guardian gate 활성.
                        risk_guardian=None,
                        dedup_gate=_shadow_dedup,
                        symbol_last_trade={},
                        symbol_cooldown_s=float(_shadow_get_cfg("execution.symbol_cooldown_s", default=30.0)),
                        cached_margin={},
                        min_notional_registry=_shadow_min_notional,  # paper stub
                        get_config=lambda key, default=None: _shadow_get_cfg(key, default=default),
                        total_capital_usd=_total_capital,
                        max_session_loss_usd=_max_session_loss,
                        # review fix HIGH: paper에서도 session loss 추적. _stats.total_pnl post-construction wired
                        session_loss_supplier=lambda: -float(getattr(getattr(self, "_paper_mode", None), "_stats", None) and self._paper_mode._stats.total_pnl or 0.0),
                        build_collision_key=lambda req: f"{req.strategy_id}_{req.legs[0].symbol if req.legs else 'none'}",
                        is_reduceonly_request=lambda req: False,
                        halt_local=_shadow_halt_local,  # review fix CRITICAL: module-level 함수
                        telegram=self._telegram,
                    )
                    logger.info("paper_mode.pre_trade_validator_enabled")
                except Exception as exc:
                    # review fix: traceback (silent CRITICAL 차단)
                    logger.warning("paper_mode.pre_trade_validator_init_failed: %r", exc, exc_info=True)

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
                pre_trade_validator=_shadow_pre_trade_validator,  # Phase 2B
                execution_journal=_shadow_execution_journal,  # Phase 2B
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
            self.context.execution_mode = "paper"
            logger.info("Paper Mode started: %s for %s", exchanges, symbols)

            # ER5-04: Warm-start restore — load previous shadow stats from DB
            if self._db_pool is not None:
                try:
                    async with self._db_pool.pool.acquire() as conn:
                        rows = await conn.fetch(
                            "SELECT key, value FROM engine_state"
                            " WHERE key IN ('paper_total_pnl', 'paper_trades_executed',"
                            " 'shadow_total_pnl', 'shadow_trades_executed')"
                        )
                        for row in rows:
                            if row["key"] in ("paper_total_pnl", "shadow_total_pnl"):
                                self._paper_mode._stats.total_pnl = float(row["value"])
                                logger.info("paper_total_pnl_restored value=%s", row["value"])
                            elif row["key"] in ("paper_trades_executed", "shadow_trades_executed"):
                                self._paper_mode._stats.trades_executed = int(row["value"])
                                logger.info("paper_trades_executed_restored value=%s", row["value"])
                except Exception as exc:
                    logger.warning("shadow_stats_warm_start_failed error=%s", exc)

            # Start LiveGate auto-evaluation if DB is available
            if self._db_pool is not None:
                try:
                    from src.modes.live_gate import LiveGate
                    from src.risk.kill_switch import KillSwitch

                    # Wire with ALL exchange adapters for Tier 2/3 to function
                    kill_switch = KillSwitch(
                        redis_client=getattr(self, '_redis_client', None),
                        exchanges=list(self._exchanges.values()),
                    )
                    self._kill_switch = kill_switch  # store for shutdown and compliance
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
        exchanges = self._active_exchanges or _get_fallback_exchanges()

        multi_signal_producer = MultiStrategySignalProducer(
            event_bus=self._event_bus,
            latency_tracker=getattr(self, "_latency_tracker", None),
        )

        _fr_symbols_sv = await FundingRateCollector.fetch_paired_symbols(
            http_client=getattr(self, "_http_client", None),
        )
        funding_rate_collector = FundingRateCollector(
            symbols=_fr_symbols_sv,
            exchanges=FundingRateCollector.get_poll_exchanges(),
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
        exchanges = self._active_exchanges or _get_fallback_exchanges()

        # Create MultiStrategySignalProducer for 6 additional strategies
        from src.core.multi_signal import MultiStrategySignalProducer

        multi_signal_producer = MultiStrategySignalProducer(
            event_bus=self._event_bus,
            latency_tracker=getattr(self, "_latency_tracker", None),
        )

        # Dynamic symbol + exchange discovery — reads engine.json at runtime.
        _fr_symbols_ps = await FundingRateCollector.fetch_paired_symbols(
            http_client=getattr(self, "_http_client", None),
        )
        funding_rate_collector = FundingRateCollector(
            symbols=_fr_symbols_ps,
            exchanges=FundingRateCollector.get_poll_exchanges(),
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

                # Wire with ALL exchange adapters for Tier 2/3 to function
                kill_switch = KillSwitch(
                    redis_client=getattr(self, '_redis_client', None),
                    exchanges=list(self._exchanges.values()),
                )
                self._kill_switch = kill_switch  # store for shutdown and compliance
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
        self.context.execution_mode = "paper"

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
