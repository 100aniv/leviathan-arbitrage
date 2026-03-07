"""Coverage tests for src/core/engine.py — LEVIATHANEngine.

Covers: __init__ defaults, start/stop lifecycle, kill switch, strategy management,
background loops (_health_check_loop, _reconcile_loop), uptime, run_until_shutdown.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.engine import EngineConfig, EngineStatus, LEVIATHANEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine(**kwargs) -> LEVIATHANEngine:
    return LEVIATHANEngine(**kwargs)


def _make_task(done: bool = True) -> MagicMock:
    t = MagicMock()
    t.done.return_value = done
    return t


def _mock_create_task(coro, **kwargs):
    """Close the coroutine to suppress warnings; return a mock done task."""
    try:
        coro.close()
    except Exception:
        pass
    return _make_task(done=True)


# ---------------------------------------------------------------------------
# __init__ defaults
# ---------------------------------------------------------------------------


class TestEngineInit:
    def test_default_status_is_stopped(self):
        engine = _make_engine()
        assert engine.status == EngineStatus.STOPPED

    def test_kill_switch_inactive_by_default(self):
        engine = _make_engine()
        assert engine.kill_switch_active is False
        assert engine.kill_switch_reason == ""

    def test_started_at_is_none_before_start(self):
        engine = _make_engine()
        assert engine._started_at is None

    def test_default_config_created_when_not_provided(self):
        engine = _make_engine()
        assert isinstance(engine.config, EngineConfig)

    def test_custom_config_is_used(self):
        cfg = EngineConfig(health_check_interval=30)
        engine = _make_engine(config=cfg)
        assert engine.config.health_check_interval == 30

    def test_all_dependencies_default_to_none(self):
        engine = _make_engine()
        assert engine.signal_processor is None
        assert engine.risk_checker is None
        assert engine.executor is None
        assert engine.strategy_manager is None

    def test_uptime_seconds_is_zero_before_start(self):
        engine = _make_engine()
        assert engine.uptime_seconds == 0.0

    def test_tasks_list_is_empty_initially(self):
        engine = _make_engine()
        assert engine._tasks == []


# ---------------------------------------------------------------------------
# start() / stop()
# ---------------------------------------------------------------------------


class TestEngineStartStop:
    @pytest.mark.asyncio
    async def test_start_sets_status_running(self):
        engine = _make_engine()
        with patch("asyncio.create_task", side_effect=_mock_create_task):
            await engine.start()
        assert engine.status == EngineStatus.RUNNING

    @pytest.mark.asyncio
    async def test_start_creates_two_background_tasks(self):
        engine = _make_engine()
        with patch("asyncio.create_task", side_effect=_mock_create_task) as mock_ct:
            await engine.start()
        assert mock_ct.call_count == 2

    @pytest.mark.asyncio
    async def test_start_records_started_at(self):
        engine = _make_engine()
        assert engine._started_at is None
        with patch("asyncio.create_task", side_effect=_mock_create_task):
            await engine.start()
        assert engine._started_at is not None

    @pytest.mark.asyncio
    async def test_start_is_idempotent_when_already_running(self):
        engine = _make_engine()
        engine.status = EngineStatus.RUNNING
        with patch("asyncio.create_task", side_effect=_mock_create_task) as mock_ct:
            await engine.start()
        mock_ct.assert_not_called()

    @pytest.mark.asyncio
    async def test_uptime_positive_after_start(self):
        engine = _make_engine()
        with patch("asyncio.create_task", side_effect=_mock_create_task):
            await engine.start()
        assert engine.uptime_seconds > 0

    @pytest.mark.asyncio
    async def test_stop_sets_status_stopped(self):
        engine = _make_engine()
        engine.status = EngineStatus.RUNNING
        with patch("asyncio.wait", new=AsyncMock()):
            await engine.stop()
        assert engine.status == EngineStatus.STOPPED

    @pytest.mark.asyncio
    async def test_stop_is_idempotent_when_already_stopped(self):
        engine = _make_engine()
        engine.status = EngineStatus.STOPPED
        await engine.stop()  # must not raise or change anything
        assert engine.status == EngineStatus.STOPPED

    @pytest.mark.asyncio
    async def test_stop_cancels_pending_tasks(self):
        engine = _make_engine()
        engine.status = EngineStatus.RUNNING
        pending = _make_task(done=False)
        engine._tasks = [pending]

        with patch("asyncio.wait", new=AsyncMock()):
            await engine.stop()

        pending.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_does_not_cancel_already_done_tasks(self):
        engine = _make_engine()
        engine.status = EngineStatus.RUNNING
        done = _make_task(done=True)
        engine._tasks = [done]

        with patch("asyncio.wait", new=AsyncMock()):
            await engine.stop()

        done.cancel.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_clears_task_list(self):
        engine = _make_engine()
        engine.status = EngineStatus.RUNNING
        engine._tasks = [_make_task()]

        with patch("asyncio.wait", new=AsyncMock()):
            await engine.stop()

        assert engine._tasks == []

    @pytest.mark.asyncio
    async def test_stop_sets_shutdown_event(self):
        engine = _make_engine()
        engine.status = EngineStatus.RUNNING

        with patch("asyncio.wait", new=AsyncMock()):
            await engine.stop()

        assert engine._shutdown.is_set()

    @pytest.mark.asyncio
    async def test_start_then_stop_full_cycle(self):
        engine = _make_engine()
        with patch("asyncio.create_task", side_effect=_mock_create_task):
            await engine.start()
        assert engine.status == EngineStatus.RUNNING

        with patch("asyncio.wait", new=AsyncMock()):
            await engine.stop()
        assert engine.status == EngineStatus.STOPPED


# ---------------------------------------------------------------------------
# run_until_shutdown
# ---------------------------------------------------------------------------


class TestRunUntilShutdown:
    @pytest.mark.asyncio
    async def test_run_until_shutdown_calls_start_and_stop(self):
        engine = _make_engine()
        start_mock = AsyncMock()
        stop_mock = AsyncMock()

        engine._shutdown.set()  # trigger immediate exit from await wait

        with patch.object(engine, "start", new=start_mock):
            with patch.object(engine, "stop", new=stop_mock):
                await engine.run_until_shutdown()

        start_mock.assert_called_once()
        stop_mock.assert_called_once()


# ---------------------------------------------------------------------------
# trigger_kill_switch
# ---------------------------------------------------------------------------


class TestKillSwitch:
    def test_trigger_sets_kill_switch_active(self):
        engine = _make_engine()
        with patch("src.core.engine.halt_local"):
            engine.trigger_kill_switch("max drawdown exceeded")
        assert engine.kill_switch_active is True

    def test_trigger_stores_reason(self):
        engine = _make_engine()
        with patch("src.core.engine.halt_local"):
            engine.trigger_kill_switch("max drawdown exceeded")
        assert engine.kill_switch_reason == "max drawdown exceeded"

    def test_trigger_sets_status_halted(self):
        engine = _make_engine()
        with patch("src.core.engine.halt_local"):
            engine.trigger_kill_switch("drawdown")
        assert engine.status == EngineStatus.HALTED

    def test_trigger_calls_halt_local(self):
        engine = _make_engine()
        with patch("src.core.engine.halt_local") as mock_halt:
            engine.trigger_kill_switch("test")
        mock_halt.assert_called_once()

    def test_trigger_is_idempotent_preserves_first_reason(self):
        engine = _make_engine()
        with patch("src.core.engine.halt_local") as mock_halt:
            engine.trigger_kill_switch("first reason")
            engine.trigger_kill_switch("second reason")

        mock_halt.assert_called_once()  # only on first trigger
        assert engine.kill_switch_reason == "first reason"

    def test_trigger_idempotent_does_not_change_status(self):
        engine = _make_engine()
        with patch("src.core.engine.halt_local"):
            engine.trigger_kill_switch("first")
            engine.status = EngineStatus.RUNNING  # simulate status change
            engine.trigger_kill_switch("second")
        # status should remain as set between calls (not overwritten)
        assert engine.status == EngineStatus.RUNNING


# ---------------------------------------------------------------------------
# list_strategies / toggle_strategy
# ---------------------------------------------------------------------------


class TestListStrategies:
    def test_returns_empty_list_when_no_strategy_manager(self):
        engine = _make_engine()
        assert engine.list_strategies() == []

    def test_delegates_to_strategy_manager(self):
        mock_mgr = MagicMock()
        mock_mgr.list_strategies.return_value = ["arb_v1", "funding_v2"]
        engine = _make_engine(strategy_manager=mock_mgr)

        result = engine.list_strategies()

        assert result == ["arb_v1", "funding_v2"]
        mock_mgr.list_strategies.assert_called_once()


class TestToggleStrategy:
    @pytest.mark.asyncio
    async def test_raises_key_error_when_no_strategy_manager(self):
        engine = _make_engine()
        with pytest.raises(KeyError, match="No strategy manager"):
            await engine.toggle_strategy("arb_v1")

    @pytest.mark.asyncio
    async def test_raises_key_error_when_strategy_not_registered(self):
        mock_mgr = MagicMock()
        mock_mgr.get_strategy.return_value = None
        engine = _make_engine(strategy_manager=mock_mgr)

        with pytest.raises(KeyError, match="not registered"):
            await engine.toggle_strategy("nonexistent")

    @pytest.mark.asyncio
    async def test_stops_active_strategy(self):
        mock_strategy = MagicMock()
        mock_strategy.is_active = True

        mock_mgr = MagicMock()
        mock_mgr.get_strategy.return_value = mock_strategy
        mock_mgr.stop_strategy = AsyncMock()

        engine = _make_engine(strategy_manager=mock_mgr)
        await engine.toggle_strategy("arb_v1")

        mock_mgr.stop_strategy.assert_called_once_with("arb_v1")
        mock_mgr.start_strategy.assert_not_called()

    @pytest.mark.asyncio
    async def test_starts_inactive_strategy(self):
        mock_strategy = MagicMock()
        mock_strategy.is_active = False

        mock_mgr = MagicMock()
        mock_mgr.get_strategy.return_value = mock_strategy
        mock_mgr.start_strategy = AsyncMock()

        engine = _make_engine(strategy_manager=mock_mgr)
        await engine.toggle_strategy("arb_v1")

        mock_mgr.start_strategy.assert_called_once_with("arb_v1")
        mock_mgr.stop_strategy.assert_not_called()


# ---------------------------------------------------------------------------
# _health_check_loop
# ---------------------------------------------------------------------------


class TestHealthCheckLoop:
    @pytest.mark.asyncio
    async def test_exits_immediately_when_shutdown_already_set(self):
        engine = _make_engine()
        engine._shutdown.set()

        with patch("asyncio.sleep", new=AsyncMock()) as mock_sleep:
            await engine._health_check_loop()

        mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_exits_on_cancelled_error(self):
        engine = _make_engine()
        with patch("asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError())):
            await engine._health_check_loop()  # must not raise

    @pytest.mark.asyncio
    async def test_exits_when_is_halted_returns_true(self):
        engine = _make_engine()
        call_count = 0

        async def fake_sleep(_):
            nonlocal call_count
            call_count += 1

        with patch("asyncio.sleep", side_effect=fake_sleep):
            with patch("src.core.engine.is_halted", return_value=True):
                await engine._health_check_loop()

        assert call_count == 1  # ran exactly once then broke

    @pytest.mark.asyncio
    async def test_handles_general_exception_and_continues(self):
        engine = _make_engine()
        call_count = 0

        async def fake_sleep(_):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                engine._shutdown.set()

        with patch("asyncio.sleep", side_effect=fake_sleep):
            with patch("src.core.engine.is_halted", side_effect=RuntimeError("check failed")):
                await engine._health_check_loop()  # must not raise

        assert call_count == 2  # continued after exception


# ---------------------------------------------------------------------------
# _reconcile_loop
# ---------------------------------------------------------------------------


class TestReconcileLoop:
    @pytest.mark.asyncio
    async def test_exits_immediately_when_shutdown_already_set(self):
        engine = _make_engine()
        engine._shutdown.set()

        with patch("asyncio.sleep", new=AsyncMock()) as mock_sleep:
            await engine._reconcile_loop()

        mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_exits_on_cancelled_error(self):
        engine = _make_engine()
        with patch("asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError())):
            await engine._reconcile_loop()  # must not raise

    @pytest.mark.asyncio
    async def test_handles_general_exception_and_continues(self):
        engine = _make_engine()
        call_count = 0

        async def fake_sleep(_):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                engine._shutdown.set()
            raise RuntimeError("reconcile boom")

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await engine._reconcile_loop()  # must not raise

        assert call_count == 2

    @pytest.mark.asyncio
    async def test_calls_sleep_with_reconcile_interval(self):
        engine = _make_engine()
        engine.config = EngineConfig(reconcile_interval=999)
        slept: list[float] = []

        async def fake_sleep(t):
            slept.append(t)
            engine._shutdown.set()

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await engine._reconcile_loop()

        assert slept[0] == 999
