"""Tests for ContinuousLiveGateMonitor — US-280."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.live_gate_continuous import ContinuousLiveGateMonitor


def _make_gate(eligible: bool = True):
    result = MagicMock()
    result.eligible = eligible
    gate = MagicMock()
    gate.evaluate = AsyncMock(return_value=result)
    return gate, result


# ---------------------------------------------------------------------------
# Enabled / disabled
# ---------------------------------------------------------------------------

class TestEnabledFlag:
    def test_continuous_monitor_disabled_by_env(self, monkeypatch) -> None:
        monkeypatch.setenv("LIVE_GATE_CONTINUOUS", "0")
        gate, _ = _make_gate()
        monitor = ContinuousLiveGateMonitor(gate, interval_seconds=999)
        assert monitor.enabled is False

    def test_enabled_by_default(self, monkeypatch) -> None:
        monkeypatch.delenv("LIVE_GATE_CONTINUOUS", raising=False)
        gate, _ = _make_gate()
        monitor = ContinuousLiveGateMonitor(gate)
        assert monitor.enabled is True

    def test_false_string_disables(self, monkeypatch) -> None:
        monkeypatch.setenv("LIVE_GATE_CONTINUOUS", "false")
        gate, _ = _make_gate()
        assert ContinuousLiveGateMonitor(gate).enabled is False


# ---------------------------------------------------------------------------
# Start: disabled env skips task creation
# ---------------------------------------------------------------------------

class TestStart:
    @pytest.mark.asyncio
    async def test_continuous_monitor_disabled_by_env_no_task(self, monkeypatch) -> None:
        monkeypatch.setenv("LIVE_GATE_CONTINUOUS", "0")
        gate, _ = _make_gate()
        monitor = ContinuousLiveGateMonitor(gate)
        await monitor.start()
        assert monitor._task is None

    @pytest.mark.asyncio
    async def test_start_creates_task_when_enabled(self, monkeypatch) -> None:
        monkeypatch.delenv("LIVE_GATE_CONTINUOUS", raising=False)
        gate, _ = _make_gate()
        monitor = ContinuousLiveGateMonitor(gate, interval_seconds=9999)
        await monitor.start()
        assert monitor._task is not None
        await monitor.stop()


# ---------------------------------------------------------------------------
# Evaluate
# ---------------------------------------------------------------------------

class TestEvaluate:
    @pytest.mark.asyncio
    async def test_continuous_monitor_evaluates_periodically(self) -> None:
        """Calling _evaluate directly appends to results."""
        gate, _ = _make_gate(eligible=True)
        monitor = ContinuousLiveGateMonitor(gate)
        await monitor._evaluate()
        assert len(monitor.results) == 1
        gate.evaluate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_continuous_monitor_logs_results(self) -> None:
        gate, result = _make_gate(eligible=True)
        monitor = ContinuousLiveGateMonitor(gate)
        await monitor._evaluate()
        assert monitor.results[0] is result

    @pytest.mark.asyncio
    async def test_continuous_monitor_triggers_risk_on_fail(self) -> None:
        """Failed gate → risk_guardian.trigger_halt('live_gate_failed') is called."""
        gate, _ = _make_gate(eligible=False)
        risk_guardian = MagicMock()
        monitor = ContinuousLiveGateMonitor(gate, risk_guardian=risk_guardian)
        await monitor._evaluate()
        risk_guardian.trigger_halt.assert_called_once_with("live_gate_failed")

    @pytest.mark.asyncio
    async def test_evaluate_no_halt_when_eligible(self) -> None:
        gate, _ = _make_gate(eligible=True)
        risk_guardian = MagicMock()
        monitor = ContinuousLiveGateMonitor(gate, risk_guardian=risk_guardian)
        await monitor._evaluate()
        risk_guardian.trigger_halt.assert_not_called()

    @pytest.mark.asyncio
    async def test_evaluate_handles_exception_gracefully(self) -> None:
        gate = MagicMock()
        gate.evaluate = AsyncMock(side_effect=RuntimeError("db down"))
        monitor = ContinuousLiveGateMonitor(gate)
        # Must not raise
        await monitor._evaluate()
        assert monitor.results == []
