"""Tests for LEVIATHANEngine — main async loop with Protocol-based DI."""
import asyncio
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.engine import (
    LEVIATHANEngine,
    EngineConfig,
    EngineStatus,
    ISignalProcessor,
    IRiskChecker,
    IExecutor,
    IStrategyManager,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_signal_processor():
    sp = MagicMock(spec=ISignalProcessor)
    sp.process = AsyncMock(return_value=None)
    return sp


@pytest.fixture
def mock_risk_checker():
    rc = MagicMock(spec=IRiskChecker)
    result = MagicMock()
    result.approved = True
    rc.check = AsyncMock(return_value=result)
    return rc


@pytest.fixture
def mock_executor():
    ex = MagicMock(spec=IExecutor)
    result = MagicMock()
    result.status = "success"
    ex.execute = AsyncMock(return_value=result)
    return ex


@pytest.fixture
def mock_strategy_manager():
    sm = MagicMock(spec=IStrategyManager)
    sm.list_strategies = MagicMock(return_value=["arb_v1"])
    sm.start_strategy = AsyncMock()
    sm.stop_strategy = AsyncMock()
    sm.get_strategy = MagicMock(return_value=MagicMock(is_active=True))
    return sm


@pytest.fixture
def engine(mock_signal_processor, mock_risk_checker, mock_executor, mock_strategy_manager):
    config = EngineConfig(reconcile_interval=999, health_check_interval=999)
    return LEVIATHANEngine(
        signal_processor=mock_signal_processor,
        risk_checker=mock_risk_checker,
        executor=mock_executor,
        strategy_manager=mock_strategy_manager,
        config=config,
    )


# ---------------------------------------------------------------------------
# EngineConfig
# ---------------------------------------------------------------------------

class TestEngineConfig:
    def test_defaults(self):
        cfg = EngineConfig()
        assert cfg.reconcile_interval > 0
        assert cfg.health_check_interval > 0

    def test_custom_values(self):
        cfg = EngineConfig(reconcile_interval=30, health_check_interval=5)
        assert cfg.reconcile_interval == 30
        assert cfg.health_check_interval == 5


# ---------------------------------------------------------------------------
# EngineStatus
# ---------------------------------------------------------------------------

class TestEngineStatus:
    def test_initial_status_stopped(self, engine):
        assert engine.status == EngineStatus.STOPPED

    def test_status_after_kill_switch(self, engine):
        engine.trigger_kill_switch("test")
        assert engine.status == EngineStatus.HALTED

    def test_kill_switch_sets_flag(self, engine):
        assert engine.kill_switch_active is False
        engine.trigger_kill_switch("test")
        assert engine.kill_switch_active is True

    def test_kill_switch_reason_stored(self, engine):
        engine.trigger_kill_switch("emergency stop")
        assert engine.kill_switch_reason == "emergency stop"


# ---------------------------------------------------------------------------
# Strategy management
# ---------------------------------------------------------------------------

class TestStrategyManagement:
    def test_list_strategies(self, engine, mock_strategy_manager):
        result = engine.list_strategies()
        mock_strategy_manager.list_strategies.assert_called_once()
        assert result == ["arb_v1"]

    @pytest.mark.asyncio
    async def test_toggle_strategy_start(self, engine, mock_strategy_manager):
        # Strategy is currently active → toggle stops it
        strategy_mock = MagicMock()
        strategy_mock.is_active = True
        mock_strategy_manager.get_strategy.return_value = strategy_mock
        await engine.toggle_strategy("arb_v1")
        mock_strategy_manager.stop_strategy.assert_called_once_with("arb_v1")

    @pytest.mark.asyncio
    async def test_toggle_strategy_stop(self, engine, mock_strategy_manager):
        # Strategy is currently inactive → toggle starts it
        strategy_mock = MagicMock()
        strategy_mock.is_active = False
        mock_strategy_manager.get_strategy.return_value = strategy_mock
        await engine.toggle_strategy("arb_v1")
        mock_strategy_manager.start_strategy.assert_called_once_with("arb_v1")

    @pytest.mark.asyncio
    async def test_toggle_nonexistent_raises(self, engine, mock_strategy_manager):
        mock_strategy_manager.get_strategy.return_value = None
        with pytest.raises(KeyError):
            await engine.toggle_strategy("nonexistent")


# ---------------------------------------------------------------------------
# Uptime tracking
# ---------------------------------------------------------------------------

class TestUptime:
    def test_uptime_zero_before_start(self, engine):
        assert engine.uptime_seconds == 0.0

    @pytest.mark.asyncio
    async def test_uptime_nonzero_after_start(self, engine):
        engine._started_at = asyncio.get_event_loop().time() - 5.0
        assert engine.uptime_seconds >= 5.0


# ---------------------------------------------------------------------------
# Kill switch integration
# ---------------------------------------------------------------------------

class TestKillSwitchIntegration:
    def test_kill_switch_calls_halt_local(self, engine):
        with patch("src.core.engine.halt_local") as mock_halt:
            engine.trigger_kill_switch("api_trigger")
            mock_halt.assert_called_once()

    def test_double_trigger_is_idempotent(self, engine):
        engine.trigger_kill_switch("first")
        engine.trigger_kill_switch("second")
        assert engine.kill_switch_active is True
        assert engine.kill_switch_reason == "first"  # original reason preserved


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class TestEngineLifecycle:
    @pytest.mark.asyncio
    async def test_stop_before_start_is_safe(self, engine):
        await engine.stop()  # should not raise
        assert engine.status == EngineStatus.STOPPED

    @pytest.mark.asyncio
    async def test_stop_sets_status(self, engine):
        engine.status = EngineStatus.RUNNING
        engine._shutdown = asyncio.Event()
        await engine.stop()
        assert engine.status == EngineStatus.STOPPED
