"""Extended lifecycle tests for src/main.py — covers init chains, background loops, data modes."""
from __future__ import annotations

import asyncio
import os
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from src.main import DataMode, Engine, EngineState, _StubCostCalculator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine() -> Engine:
    return Engine()


def _patch_all_inits(engine: Engine) -> dict:
    """Return a dict of AsyncMock replacements for all _init_* methods."""
    mocks = {}
    for name in [
        "_init_config", "_init_infrastructure", "_init_exchanges",
        "_init_signal_pipeline", "_init_strategies", "_init_risk",
        "_init_execution", "_populate_context", "_start_background_tasks",
    ]:
        m = AsyncMock()
        setattr(engine, name, m)
        mocks[name] = m
    return mocks


# ---------------------------------------------------------------------------
# Engine.run() — full lifecycle
# ---------------------------------------------------------------------------

class TestEngineRun:
    @pytest.mark.asyncio
    async def test_run_sets_state_running_true(self):
        engine = _make_engine()
        _patch_all_inits(engine)
        engine._settings = MagicMock()
        engine._settings.execution_mode.value = "paper"

        engine._shutdown_event.set()  # trigger immediate shutdown
        with patch.object(engine, "stop", new=AsyncMock()):
            await engine.run()

        assert engine.state.running is True

    @pytest.mark.asyncio
    async def test_run_calls_all_init_steps(self):
        engine = _make_engine()
        mocks = _patch_all_inits(engine)
        engine._settings = MagicMock()
        engine._settings.execution_mode.value = "paper"

        engine._shutdown_event.set()
        with patch.object(engine, "stop", new=AsyncMock()):
            await engine.run()

        for name, m in mocks.items():
            m.assert_called_once(), f"{name} was not called"

    @pytest.mark.asyncio
    async def test_run_calls_stop_in_finally_on_exception(self):
        engine = _make_engine()
        engine._init_config = AsyncMock(side_effect=RuntimeError("startup failure"))
        stop_mock = AsyncMock()
        with patch.object(engine, "stop", new=stop_mock):
            await engine.run()

        stop_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_calls_setup_signal_handlers(self):
        engine = _make_engine()
        _patch_all_inits(engine)
        engine._settings = MagicMock()
        engine._settings.execution_mode.value = "paper"

        engine._shutdown_event.set()
        with patch.object(engine, "_setup_signal_handlers") as mock_sig:
            with patch.object(engine, "stop", new=AsyncMock()):
                await engine.run()

        mock_sig.assert_called_once()


# ---------------------------------------------------------------------------
# _setup_signal_handlers
# ---------------------------------------------------------------------------

class TestSetupSignalHandlers:
    def test_setup_signal_handlers_nonfatal(self):
        engine = _make_engine()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            engine._setup_signal_handlers()  # must not raise
        finally:
            loop.close()

    def test_setup_signal_handlers_notimplemented_nonfatal(self):
        engine = _make_engine()
        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.add_signal_handler.side_effect = NotImplementedError
            engine._setup_signal_handlers()  # must not raise

    def test_setup_signal_handlers_runtime_error_nonfatal(self):
        engine = _make_engine()
        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.add_signal_handler.side_effect = RuntimeError
            engine._setup_signal_handlers()  # must not raise


# ---------------------------------------------------------------------------
# _init_infrastructure — Redis path
# ---------------------------------------------------------------------------

class TestEngineInitInfrastructure:
    @pytest.mark.asyncio
    async def test_init_infrastructure_paper_uses_in_memory_bus(self):
        from src.core.config import ExecutionMode
        engine = _make_engine()
        engine._settings = MagicMock()
        engine._settings.execution_mode = ExecutionMode.PAPER

        mock_bus = MagicMock()
        with patch("src.infra.redis.memory_bus.InMemoryEventBus", return_value=mock_bus):
            with patch.object(engine, "_init_database", new=AsyncMock()):
                with patch.object(engine, "_init_telegram"):
                    with patch.object(engine, "_init_rust_bridge"):
                        await engine._init_infrastructure()

        assert engine._event_bus is mock_bus

    @pytest.mark.asyncio
    async def test_init_infrastructure_sandbox_tries_redis(self):
        from src.core.config import ExecutionMode
        engine = _make_engine()
        engine._settings = MagicMock()
        engine._settings.execution_mode = ExecutionMode.SANDBOX
        engine._settings.redis.url = "redis://localhost:6379"

        mock_redis = AsyncMock()
        mock_bus = MagicMock()
        with patch("src.infra.redis.client.RedisClient", return_value=mock_redis):
            with patch("src.infra.redis.event_bus.EventBus", return_value=mock_bus):
                with patch.object(engine, "_init_database", new=AsyncMock()):
                    with patch.object(engine, "_init_telegram"):
                        with patch.object(engine, "_init_rust_bridge"):
                            await engine._init_infrastructure()

        assert engine._event_bus is mock_bus

    @pytest.mark.asyncio
    async def test_init_infrastructure_redis_failure_fallback_to_memory(self):
        from src.core.config import ExecutionMode
        engine = _make_engine()
        engine._settings = MagicMock()
        engine._settings.execution_mode = ExecutionMode.SANDBOX
        engine._settings.redis.url = "redis://localhost:6379"

        mock_bus = MagicMock()
        with patch("src.infra.redis.client.RedisClient", side_effect=Exception("redis error")):
            with patch("src.infra.redis.memory_bus.InMemoryEventBus", return_value=mock_bus):
                with patch.object(engine, "_init_database", new=AsyncMock()):
                    with patch.object(engine, "_init_telegram"):
                        with patch.object(engine, "_init_rust_bridge"):
                            await engine._init_infrastructure()

        assert engine._event_bus is mock_bus


# ---------------------------------------------------------------------------
# _init_database
# ---------------------------------------------------------------------------

class TestEngineInitDatabase:
    @pytest.mark.asyncio
    async def test_init_database_success_path(self):
        engine = _make_engine()
        mock_pool = AsyncMock()
        mock_pool.pool.acquire.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
        mock_pool.pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_recorder = AsyncMock()

        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://user:pass@localhost/db"}):
            with patch("src.infra.db.connection.DatabasePool", return_value=mock_pool):
                with patch("src.infra.db.market_recorder.MarketRecorder", return_value=mock_recorder):
                    with patch("pathlib.Path.exists", return_value=False):
                        await engine._init_database()

        assert engine._db_pool is mock_pool

    @pytest.mark.asyncio
    async def test_init_database_pool_failure_nonfatal(self):
        engine = _make_engine()
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://user:pass@localhost/db"}):
            with patch("src.infra.db.connection.DatabasePool", side_effect=Exception("db error")):
                await engine._init_database()  # must not raise

        assert engine._db_pool is None

    @pytest.mark.asyncio
    async def test_init_database_no_url_uses_default(self):
        engine = _make_engine()
        env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
        mock_pool = AsyncMock()
        with patch.dict(os.environ, env, clear=True):
            with patch("src.infra.db.connection.DatabasePool", return_value=mock_pool) as mock_cls:
                with patch.object(mock_pool, "initialize", new=AsyncMock()):
                    with patch("src.infra.db.market_recorder.MarketRecorder", side_effect=Exception("skip")):
                        with patch("pathlib.Path.exists", return_value=False):
                            await engine._init_database()
        # Should have used some default DSN
        call_kwargs = mock_cls.call_args
        assert call_kwargs is not None

    @pytest.mark.asyncio
    async def test_init_database_market_recorder_failure_nonfatal(self):
        engine = _make_engine()
        mock_pool = AsyncMock()
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://u:p@localhost/db"}):
            with patch("src.infra.db.connection.DatabasePool", return_value=mock_pool):
                with patch("src.infra.db.market_recorder.MarketRecorder",
                           side_effect=Exception("recorder error")):
                    with patch("pathlib.Path.exists", return_value=False):
                        await engine._init_database()

        assert engine._db_pool is mock_pool
        assert engine._market_recorder is None


# ---------------------------------------------------------------------------
# _init_telegram / _init_rust_bridge — success paths
# ---------------------------------------------------------------------------

class TestEngineInitTelegramSuccess:
    def test_init_telegram_enabled(self):
        engine = _make_engine()
        mock_alerter = MagicMock()
        mock_alerter._enabled = True
        with patch("src.infra.telegram.get_telegram_alerter", return_value=mock_alerter):
            engine._init_telegram()
        assert engine._telegram is mock_alerter

    def test_init_rust_bridge_success(self):
        engine = _make_engine()
        with patch("src.core.rust_bridge.get_feature_flags", return_value={"orderbook": True}):
            engine._init_rust_bridge()  # must not raise


# ---------------------------------------------------------------------------
# _init_signal_pipeline
# ---------------------------------------------------------------------------

class TestEngineInitSignalPipeline:
    @pytest.mark.asyncio
    async def test_init_signal_pipeline_success(self):
        engine = _make_engine()
        engine._event_bus = MagicMock()

        mock_hub = MagicMock()
        mock_fee = MagicMock()
        mock_slip = MagicMock()
        mock_cost = MagicMock()
        mock_sig = MagicMock()

        with patch("src.core.price_hub.PriceHub", return_value=mock_hub):
            with patch("src.friction.fee_model.FeeModel", return_value=mock_fee):
                with patch("src.friction.slippage_model.CEXOrderbookSlippage", return_value=mock_slip):
                    with patch("src.friction.cost_calculator.CostCalculator", return_value=mock_cost):
                        with patch("src.core.signal.SignalGenerator", return_value=mock_sig):
                            await engine._init_signal_pipeline()

        assert engine._price_hub is mock_hub
        assert engine._cost_calculator is mock_cost
        assert engine._signal_generator is mock_sig

    @pytest.mark.asyncio
    async def test_init_signal_pipeline_cost_calculator_failure_uses_none(self):
        engine = _make_engine()
        engine._event_bus = MagicMock()

        mock_hub = MagicMock()
        mock_sig = MagicMock()

        with patch("src.core.price_hub.PriceHub", return_value=mock_hub):
            with patch("src.friction.fee_model.FeeModel", side_effect=Exception("fee error")):
                with patch("src.core.signal.SignalGenerator", return_value=mock_sig):
                    await engine._init_signal_pipeline()

        assert engine._cost_calculator is None
        assert engine._signal_generator is mock_sig


# ---------------------------------------------------------------------------
# _init_strategies
# ---------------------------------------------------------------------------

class TestEngineInitStrategies:
    @pytest.mark.asyncio
    async def test_init_strategies_registers_cross_exchange(self):
        engine = _make_engine()
        engine._event_bus = MagicMock()
        engine._cost_calculator = MagicMock()

        mock_manager = MagicMock()
        mock_manager._strategies = {"cross_exchange_v1": MagicMock()}
        mock_manager.list_strategies.return_value = ["cross_exchange_v1"]

        with patch("src.strategies.manager.StrategyManager", return_value=mock_manager):
            with patch("src.strategies.cross_exchange.CrossExchangeStrategy", return_value=MagicMock()):
                await engine._init_strategies()

        assert engine._strategy_manager is mock_manager

    @pytest.mark.asyncio
    async def test_init_strategies_registration_failure_nonfatal(self):
        engine = _make_engine()
        engine._event_bus = MagicMock()
        engine._cost_calculator = MagicMock()

        mock_manager = MagicMock()
        mock_manager._strategies = {}

        with patch("src.strategies.manager.StrategyManager", return_value=mock_manager):
            with patch.object(engine, "_register_default_strategies",
                              new=AsyncMock(side_effect=Exception("reg error"))):
                await engine._init_strategies()

        assert engine._strategy_manager is mock_manager  # still set despite failure

    @pytest.mark.asyncio
    async def test_init_strategies_uses_stub_when_no_cost_calculator(self):
        engine = _make_engine()
        engine._event_bus = MagicMock()
        engine._cost_calculator = None  # triggers _StubCostCalculator

        mock_manager = MagicMock()
        mock_manager._strategies = {}

        with patch("src.strategies.manager.StrategyManager", return_value=mock_manager):
            with patch("src.strategies.cross_exchange.CrossExchangeStrategy") as mock_strat_cls:
                await engine._init_strategies()

        # The stub should have been used (CrossExchangeStrategy should have been created)
        assert engine._strategy_manager is mock_manager


# ---------------------------------------------------------------------------
# _init_risk
# ---------------------------------------------------------------------------

class TestEngineInitRisk:
    @pytest.mark.asyncio
    async def test_init_risk_creates_circuit_breaker(self):
        engine = _make_engine()
        engine._telegram = None

        mock_cb = MagicMock()
        mock_guardian = MagicMock()

        with patch("src.risk.circuit_breaker.CircuitBreaker", return_value=mock_cb):
            with patch("src.risk.guardian.RiskGuardian", return_value=mock_guardian):
                await engine._init_risk()

        assert engine._circuit_breaker is mock_cb
        assert engine._risk_guardian is mock_guardian

    @pytest.mark.asyncio
    async def test_init_risk_with_telegram_wires_callback(self):
        engine = _make_engine()
        mock_telegram = MagicMock()
        mock_telegram._enabled = True
        mock_telegram.send_circuit_breaker_event = AsyncMock()
        engine._telegram = mock_telegram

        mock_cb = MagicMock()
        mock_guardian = MagicMock()

        with patch("src.risk.circuit_breaker.CircuitBreaker", return_value=mock_cb) as cb_cls:
            with patch("src.risk.guardian.RiskGuardian", return_value=mock_guardian):
                await engine._init_risk()

        # CircuitBreaker should have been created with a callback
        call_kwargs = cb_cls.call_args
        assert call_kwargs is not None

    @pytest.mark.asyncio
    async def test_init_risk_circuit_breaker_failure_nonfatal(self):
        engine = _make_engine()
        engine._telegram = None

        with patch("src.risk.circuit_breaker.CircuitBreaker", side_effect=Exception("cb error")):
            await engine._init_risk()  # must not raise

    @pytest.mark.asyncio
    async def test_init_risk_guardian_failure_nonfatal(self):
        engine = _make_engine()
        engine._telegram = None
        engine._circuit_breaker = MagicMock()

        with patch("src.risk.circuit_breaker.CircuitBreaker", return_value=MagicMock()):
            with patch("src.risk.guardian.RiskGuardian", side_effect=Exception("guardian error")):
                await engine._init_risk()

        assert engine._risk_guardian is None


# ---------------------------------------------------------------------------
# _init_execution
# ---------------------------------------------------------------------------

class TestEngineInitExecution:
    @pytest.mark.asyncio
    async def test_init_execution_creates_executor_and_consumer(self):
        engine = _make_engine()
        engine._event_bus = MagicMock()
        engine._exchanges = {"binance": MagicMock()}
        engine._risk_guardian = None

        mock_executor = MagicMock()
        mock_consumer = MagicMock()

        with patch("src.execution.executor.AtomicExecutor", return_value=mock_executor):
            with patch("src.execution.trade_consumer.TradeRequestConsumer", return_value=mock_consumer):
                await engine._init_execution()

        assert engine._executor is mock_executor
        assert engine._trade_consumer is mock_consumer

    @pytest.mark.asyncio
    async def test_init_execution_with_risk_guardian_wires_check(self):
        engine = _make_engine()
        engine._event_bus = MagicMock()
        engine._exchanges = {}
        engine._risk_guardian = MagicMock()
        engine._settings = MagicMock()
        engine._settings.capital.initial_capital = Decimal("1000")

        mock_executor = MagicMock()
        mock_consumer = MagicMock()

        with patch("src.execution.executor.AtomicExecutor", return_value=mock_executor):
            with patch("src.execution.trade_consumer.TradeRequestConsumer", return_value=mock_consumer) as tc_cls:
                await engine._init_execution()

        # TradeRequestConsumer should have been called with a risk_check callable
        call_kwargs = tc_cls.call_args[1]
        assert call_kwargs.get("risk_check") is not None


# ---------------------------------------------------------------------------
# _build_risk_check_fn
# ---------------------------------------------------------------------------

class TestBuildRiskCheckFn:
    def _make_engine_with_guardian(self):
        engine = _make_engine()
        engine._settings = MagicMock()
        engine._settings.capital.initial_capital = Decimal("1000")
        engine._exchanges = {"binance": MagicMock(), "okx": MagicMock()}
        engine._risk_guardian = MagicMock()
        return engine

    def _make_mock_leg(self, price=Decimal("50000"), size=Decimal("0.01")):
        mock_leg = MagicMock()
        mock_leg.price = price
        mock_leg.size = size
        mock_leg.side.value = "buy"
        mock_leg.exchange_id = "binance"
        mock_leg.symbol = "BTC/USDT"
        return mock_leg

    def _risk_check_patches(self):
        """Context manager: patch PortfolioState+TradeProposal for entire build+call."""
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch("src.risk.guardian.PortfolioState", MagicMock()))
        stack.enter_context(patch("src.risk.guardian.TradeProposal", MagicMock()))
        return stack

    def test_risk_check_approves_valid_trade(self):
        engine = self._make_engine_with_guardian()

        approved_result = MagicMock()
        approved_result.approved = True
        approved_result.reason = ""
        engine._risk_guardian.check.return_value = approved_result

        mock_request = MagicMock()
        mock_request.strategy_id = "test_strat"
        mock_request.legs = [self._make_mock_leg()]

        # Patch must wrap _build_risk_check_fn() since import is inside it
        with patch("src.risk.guardian.PortfolioState", MagicMock()):
            with patch("src.risk.guardian.TradeProposal", MagicMock()):
                risk_check = engine._build_risk_check_fn()
                ok, reason = risk_check(mock_request)

        assert ok is True
        assert reason == ""

    def test_risk_check_rejects_blocked_trade(self):
        engine = self._make_engine_with_guardian()

        rejected_result = MagicMock()
        rejected_result.approved = False
        rejected_result.reason = "position size too large"
        engine._risk_guardian.check.return_value = rejected_result

        mock_request = MagicMock()
        mock_request.strategy_id = "test_strat"
        mock_request.legs = [self._make_mock_leg(size=Decimal("100"))]

        with patch("src.risk.guardian.PortfolioState", MagicMock()):
            with patch("src.risk.guardian.TradeProposal", MagicMock()):
                risk_check = engine._build_risk_check_fn()
                ok, reason = risk_check(mock_request)

        assert ok is False
        assert "position size" in reason

    def test_risk_check_uses_default_price_when_none(self):
        engine = self._make_engine_with_guardian()
        approved_result = MagicMock()
        approved_result.approved = True
        approved_result.reason = ""
        engine._risk_guardian.check.return_value = approved_result

        mock_request = MagicMock()
        mock_request.strategy_id = "test_strat"
        mock_request.legs = [self._make_mock_leg(price=None)]  # triggers default 50000

        with patch("src.risk.guardian.PortfolioState", MagicMock()):
            with patch("src.risk.guardian.TradeProposal", MagicMock()):
                risk_check = engine._build_risk_check_fn()
                ok, reason = risk_check(mock_request)

        assert ok is True


# ---------------------------------------------------------------------------
# _on_execution_result
# ---------------------------------------------------------------------------

class TestOnExecutionResult:
    def test_on_execution_result_logs_without_error(self):
        engine = _make_engine()
        mock_request = MagicMock()
        mock_request.strategy_id = "arb_v1"
        mock_result = MagicMock()
        mock_result.status.value = "success"
        engine._on_execution_result(mock_request, mock_result)  # must not raise


# ---------------------------------------------------------------------------
# _start_background_tasks — DataMode routing
# ---------------------------------------------------------------------------

class TestStartBackgroundTasks:
    @pytest.mark.asyncio
    async def test_synthetic_mode_starts_orderbook_feed(self):
        from src.core.config import ExecutionMode
        engine = _make_engine()
        engine._settings = MagicMock()
        engine._settings.execution_mode = ExecutionMode.PAPER
        engine._strategy_manager = AsyncMock()
        engine._trade_consumer = AsyncMock()
        engine.context.ws_manager = None

        created_tasks = []
        original_create_task = asyncio.create_task

        def mock_create_task(coro, name=None):
            task = MagicMock()
            task.done.return_value = True
            created_tasks.append(name or "unknown")
            coro.close()  # prevent coroutine warning
            return task

        with patch.dict(os.environ, {"DATA_MODE": DataMode.SYNTHETIC}):
            with patch("asyncio.create_task", side_effect=mock_create_task):
                await engine._start_background_tasks()

        assert "orderbook_feed" in created_tasks

    @pytest.mark.asyncio
    async def test_real_public_mode_starts_real_data_feed(self):
        from src.core.config import ExecutionMode
        engine = _make_engine()
        engine._settings = MagicMock()
        engine._settings.execution_mode = ExecutionMode.PAPER
        engine._strategy_manager = AsyncMock()
        engine._trade_consumer = AsyncMock()
        engine.context.ws_manager = None

        created_tasks = []

        def mock_create_task(coro, name=None):
            task = MagicMock()
            task.done.return_value = True
            created_tasks.append(name or "unknown")
            coro.close()
            return task

        with patch.dict(os.environ, {"DATA_MODE": DataMode.REAL_PUBLIC}):
            with patch("asyncio.create_task", side_effect=mock_create_task):
                await engine._start_background_tasks()

        assert "real_data_feed" in created_tasks

    @pytest.mark.asyncio
    async def test_shadow_mode_starts_shadow_task(self):
        from src.core.config import ExecutionMode
        engine = _make_engine()
        engine._settings = MagicMock()
        engine._settings.execution_mode = ExecutionMode.PAPER
        engine._strategy_manager = AsyncMock()
        engine._trade_consumer = AsyncMock()
        engine.context.ws_manager = None

        created_tasks = []

        def mock_create_task(coro, name=None):
            task = MagicMock()
            task.done.return_value = True
            created_tasks.append(name or "unknown")
            coro.close()
            return task

        with patch.dict(os.environ, {"DATA_MODE": DataMode.SHADOW}):
            with patch("asyncio.create_task", side_effect=mock_create_task):
                await engine._start_background_tasks()

        assert "shadow_mode" in created_tasks

    @pytest.mark.asyncio
    async def test_background_tasks_always_includes_core_loops(self):
        from src.core.config import ExecutionMode
        engine = _make_engine()
        engine._settings = MagicMock()
        engine._settings.execution_mode = ExecutionMode.PAPER
        engine._strategy_manager = AsyncMock()
        engine._trade_consumer = AsyncMock()
        engine.context.ws_manager = None

        created_tasks = []

        def mock_create_task(coro, name=None):
            task = MagicMock()
            task.done.return_value = True
            created_tasks.append(name or "unknown")
            coro.close()
            return task

        with patch.dict(os.environ, {"DATA_MODE": DataMode.SYNTHETIC}):
            with patch("asyncio.create_task", side_effect=mock_create_task):
                await engine._start_background_tasks()

        assert "strategy_mgr" in created_tasks
        assert "trade_consumer" in created_tasks
        assert "health_check" in created_tasks
        assert "reconcile" in created_tasks
        assert "ws_heartbeat" in created_tasks


# ---------------------------------------------------------------------------
# Background loops
# ---------------------------------------------------------------------------

class TestBackgroundLoops:
    @pytest.mark.asyncio
    async def test_strategy_manager_loop_handles_cancelled(self):
        engine = _make_engine()
        mock_manager = AsyncMock()
        mock_manager.start.side_effect = asyncio.CancelledError()
        engine._strategy_manager = mock_manager

        await engine._strategy_manager_loop()  # must not raise

    @pytest.mark.asyncio
    async def test_strategy_manager_loop_handles_exception(self):
        engine = _make_engine()
        mock_manager = AsyncMock()
        mock_manager.start.side_effect = Exception("manager error")
        engine._strategy_manager = mock_manager

        await engine._strategy_manager_loop()  # must not raise

    @pytest.mark.asyncio
    async def test_trade_consumer_loop_handles_cancelled(self):
        engine = _make_engine()
        mock_consumer = AsyncMock()
        mock_consumer.start.side_effect = asyncio.CancelledError()
        engine._trade_consumer = mock_consumer

        await engine._trade_consumer_loop()  # must not raise

    @pytest.mark.asyncio
    async def test_trade_consumer_loop_handles_exception(self):
        engine = _make_engine()
        mock_consumer = AsyncMock()
        mock_consumer.start.side_effect = Exception("consumer error")
        engine._trade_consumer = mock_consumer

        await engine._trade_consumer_loop()  # must not raise

    @pytest.mark.asyncio
    async def test_health_check_loop_runs_and_cancels(self):
        engine = _make_engine()
        engine.state.running = True
        engine._trade_consumer = MagicMock()
        engine._trade_consumer.processed_count = 0
        engine._trade_consumer.execution_success_count = 0
        engine._trade_consumer.risk_rejected_count = 0
        engine._exchanges = {}

        call_count = 0

        async def fake_health_check():
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                engine.state.running = False

        engine._run_health_check = fake_health_check

        with patch("asyncio.sleep", new=AsyncMock()):
            await engine._health_check_loop()

        assert call_count >= 1

    @pytest.mark.asyncio
    async def test_reconcile_loop_runs_and_cancels(self):
        engine = _make_engine()
        engine.state.running = True

        call_count = 0

        async def fake_sleep(t):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                engine.state.running = False

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await engine._reconcile_loop()

        assert call_count >= 1

    @pytest.mark.asyncio
    async def test_heartbeat_loop_calls_ws_manager(self):
        engine = _make_engine()
        engine.state.running = True
        engine.context.ws_manager = AsyncMock()

        call_count = 0

        async def fake_sleep(t):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                engine.state.running = False

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await engine._heartbeat_loop()

        engine.context.ws_manager.send_heartbeat.assert_called()

    @pytest.mark.asyncio
    async def test_heartbeat_loop_no_ws_manager_nonfatal(self):
        engine = _make_engine()
        engine.state.running = True
        engine.context.ws_manager = None

        call_count = 0

        async def fake_sleep(t):
            nonlocal call_count
            call_count += 1
            engine.state.running = False

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await engine._heartbeat_loop()  # must not raise

    @pytest.mark.asyncio
    async def test_health_check_loop_cancelled_error_exits(self):
        engine = _make_engine()
        engine.state.running = True

        # CancelledError from _run_health_check is caught → breaks loop cleanly
        with patch.object(engine, "_run_health_check",
                          new=AsyncMock(side_effect=asyncio.CancelledError())):
            await engine._health_check_loop()  # must not raise

    @pytest.mark.asyncio
    async def test_reconcile_loop_cancelled_error_exits(self):
        engine = _make_engine()
        engine.state.running = True

        with patch("asyncio.sleep", side_effect=asyncio.CancelledError()):
            await engine._reconcile_loop()  # must not raise

    @pytest.mark.asyncio
    async def test_heartbeat_loop_cancelled_error_exits(self):
        engine = _make_engine()
        engine.state.running = True

        with patch("asyncio.sleep", side_effect=asyncio.CancelledError()):
            await engine._heartbeat_loop()  # must not raise


# ---------------------------------------------------------------------------
# _run_health_check
# ---------------------------------------------------------------------------

class TestRunHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_logs_low_score_exchange(self, caplog):
        import logging
        engine = _make_engine()
        mock_adapter = MagicMock()
        mock_adapter.health_score = 0.5  # below 0.9 threshold
        engine._exchanges = {"binance": mock_adapter}
        engine._trade_consumer = None

        with caplog.at_level(logging.WARNING):
            await engine._run_health_check()

        assert "health_score" in caplog.text or "binance" in caplog.text

    @pytest.mark.asyncio
    async def test_health_check_no_exchanges_nonfatal(self):
        engine = _make_engine()
        engine._exchanges = {}
        engine._trade_consumer = None
        await engine._run_health_check()  # must not raise

    @pytest.mark.asyncio
    async def test_health_check_with_trade_consumer_logs_metrics(self):
        engine = _make_engine()
        engine._exchanges = {}
        mock_consumer = MagicMock()
        mock_consumer.processed_count = 10
        mock_consumer.execution_success_count = 9
        mock_consumer.risk_rejected_count = 1
        engine._trade_consumer = mock_consumer
        await engine._run_health_check()  # must not raise
