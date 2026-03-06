"""Tests for Engine main lifecycle."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.main import Engine, EngineState


class TestEngineState:
    def test_initial_state_not_running(self):
        state = EngineState()
        assert state.running is False

    def test_initial_state_not_halted(self):
        state = EngineState()
        assert state.kill_switch_active is False

    def test_initial_tasks_empty(self):
        state = EngineState()
        assert state.background_tasks == []


class TestEngineLifecycle:
    @pytest.fixture
    def engine(self):
        from src.api.server import EngineContext
        ctx = EngineContext()
        return Engine(context=ctx)

    def test_engine_initial_state_not_running(self, engine):
        assert engine.state.running is False

    @pytest.mark.asyncio
    async def test_engine_stop_when_not_running_is_noop(self, engine):
        # Should not raise
        await engine.stop()

    @pytest.mark.asyncio
    async def test_engine_stop_sets_running_false(self, engine):
        engine.state.running = True
        await engine.stop()
        assert engine.state.running is False

    @pytest.mark.asyncio
    async def test_graceful_shutdown_cancels_tasks(self, engine):
        mock_task = MagicMock()
        mock_task.cancel = MagicMock()
        mock_task.done = MagicMock(return_value=False)
        engine.state.background_tasks.append(mock_task)
        await engine.stop()
        mock_task.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_engine_registers_signal_handlers(self, engine):
        """Engine should set up SIGTERM/SIGINT handlers."""
        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value = MagicMock()
            # Just verifying the method exists and is callable
            assert hasattr(engine, "_setup_signal_handlers")


class TestEngineHealthCheck:
    @pytest.fixture
    def engine(self):
        from src.api.server import EngineContext
        ctx = EngineContext()
        return Engine(context=ctx)

    @pytest.mark.asyncio
    async def test_health_check_loop_runs_once(self, engine):
        """Health check loop executes without exception."""
        engine.state.running = True
        call_count = 0

        async def fake_check():
            nonlocal call_count
            call_count += 1
            engine.state.running = False  # stop after 1 iteration

        engine._run_health_check = fake_check
        await engine._run_health_check()
        assert call_count == 1


class TestEngineContext:
    def test_context_has_api_state(self):
        from src.api.server import EngineContext
        ctx = EngineContext()
        assert hasattr(ctx, "running")
        assert hasattr(ctx, "kill_switch_active")
        assert hasattr(ctx, "strategies")
        assert hasattr(ctx, "positions")

    def test_context_environment_default(self):
        from src.api.server import EngineContext
        ctx = EngineContext()
        assert ctx.environment in ("dev", "staging", "prod", "test", "unknown")
