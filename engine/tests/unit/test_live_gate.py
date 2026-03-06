"""Unit tests for LiveGate, LiveGateCheck, and LiveGateResult.

Covers:
- evaluate() runs all 6 checks and aggregates correctly
- Sharpe check passes/fails at threshold
- MDD check passes/fails at threshold
- Signals/day check passes/fails
- Kill switch check: halted vs clear
- Circuit breaker check: CLOSED vs OPEN
- Exchange health check: healthy vs degraded
- eligible = True only when ALL checks pass
- Auto-evaluation loop start/stop
- is_live_eligible() returns False before first evaluation
- Telegram notification sent on evaluation
- Settings override of thresholds works
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.analysis.walk_forward import WalkForwardResult
from src.modes.live_gate import LiveGate, LiveGateCheck, LiveGateResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_wf_result(
    sharpe: float = 3.0,
    mdd: float = 0.02,
    signals_per_day: float = 150.0,
    live_eligible: bool = True,
) -> WalkForwardResult:
    """Build a WalkForwardResult with controllable metrics."""
    return WalkForwardResult(
        overall_sharpe=sharpe,
        overall_mdd=mdd,
        avg_signals_per_day=signals_per_day,
        live_eligible=live_eligible,
        overall_win_rate=0.6,
        overall_trades=700,
        overall_pnl=1500.0,
    )


def make_live_gate(
    wf_result: WalkForwardResult | None = None,
    telegram: object | None = None,
    kill_switch: object | None = None,
    circuit_breaker: object | None = None,
    exchange_health_fn: object | None = None,
) -> LiveGate:
    """Create a LiveGate with asyncpg pool mocked and analyzer patched."""
    mock_pool = MagicMock()
    gate = LiveGate(
        pool=mock_pool,
        telegram=telegram,
        kill_switch=kill_switch,
        circuit_breaker=circuit_breaker,
        exchange_health_fn=exchange_health_fn,
    )
    # Patch the analyzer so no real DB is needed
    if wf_result is not None:
        gate._analyzer.analyze = AsyncMock(return_value=wf_result)
    else:
        gate._analyzer.analyze = AsyncMock(return_value=make_wf_result())
    return gate


# ---------------------------------------------------------------------------
# LiveGateCheck and LiveGateResult dataclasses
# ---------------------------------------------------------------------------


class TestLiveGateDataclasses:
    def test_live_gate_check_fields(self) -> None:
        """LiveGateCheck stores name, passed, value, threshold, detail."""
        check = LiveGateCheck(
            name="Sharpe Ratio",
            passed=True,
            value="3.10",
            threshold=">= 2.5",
            detail="",
        )
        assert check.name == "Sharpe Ratio"
        assert check.passed is True
        assert check.value == "3.10"
        assert check.threshold == ">= 2.5"

    def test_live_gate_result_eligible_defaults_false(self) -> None:
        """LiveGateResult initializes with eligible flag and empty lists."""
        result = LiveGateResult(
            timestamp=datetime.now(timezone.utc),
            eligible=False,
            checks=[],
        )
        assert result.eligible is False
        assert result.block_reasons == []
        assert result.walk_forward is None


# ---------------------------------------------------------------------------
# evaluate() — all 6 checks
# ---------------------------------------------------------------------------


class TestLiveGateEvaluate:
    @pytest.mark.asyncio
    async def test_evaluate_returns_live_gate_result(self) -> None:
        """evaluate() returns a LiveGateResult instance."""
        gate = make_live_gate()
        result = await gate.evaluate()
        assert isinstance(result, LiveGateResult)

    @pytest.mark.asyncio
    async def test_evaluate_produces_six_checks(self) -> None:
        """evaluate() produces exactly 6 individual checks."""
        gate = make_live_gate()
        result = await gate.evaluate()
        assert len(result.checks) == 6

    @pytest.mark.asyncio
    async def test_evaluate_eligible_when_all_checks_pass(self) -> None:
        """eligible=True when all 6 checks pass."""
        gate = make_live_gate(wf_result=make_wf_result(sharpe=3.0, mdd=0.02, signals_per_day=200))
        result = await gate.evaluate()
        assert result.eligible is True
        assert result.block_reasons == []

    @pytest.mark.asyncio
    async def test_evaluate_not_eligible_when_any_check_fails(self) -> None:
        """eligible=False when at least one check fails."""
        # Low sharpe will fail
        gate = make_live_gate(wf_result=make_wf_result(sharpe=1.0, mdd=0.02))
        result = await gate.evaluate()
        assert result.eligible is False
        assert len(result.block_reasons) >= 1


# ---------------------------------------------------------------------------
# Check 1: Sharpe ratio
# ---------------------------------------------------------------------------


class TestSharpeCheck:
    @pytest.mark.asyncio
    async def test_sharpe_check_passes_at_threshold(self) -> None:
        """Sharpe exactly at threshold (2.5) passes."""
        gate = make_live_gate(wf_result=make_wf_result(sharpe=2.5, mdd=0.02, signals_per_day=150))
        result = await gate.evaluate()
        sharpe_check = next(c for c in result.checks if c.name == "Sharpe Ratio")
        assert sharpe_check.passed is True

    @pytest.mark.asyncio
    async def test_sharpe_check_fails_below_threshold(self) -> None:
        """Sharpe below 2.5 blocks the gate."""
        gate = make_live_gate(wf_result=make_wf_result(sharpe=1.8, mdd=0.02, signals_per_day=150))
        result = await gate.evaluate()
        sharpe_check = next(c for c in result.checks if c.name == "Sharpe Ratio")
        assert sharpe_check.passed is False
        assert result.eligible is False

    @pytest.mark.asyncio
    async def test_sharpe_block_reason_mentions_value(self) -> None:
        """Block reason for failed Sharpe includes the actual value."""
        gate = make_live_gate(wf_result=make_wf_result(sharpe=1.23))
        result = await gate.evaluate()
        assert any("1.23" in r for r in result.block_reasons)


# ---------------------------------------------------------------------------
# Check 2: Maximum drawdown
# ---------------------------------------------------------------------------


class TestMDDCheck:
    @pytest.mark.asyncio
    async def test_mdd_check_passes_below_threshold(self) -> None:
        """MDD below 5% passes."""
        gate = make_live_gate(wf_result=make_wf_result(sharpe=3.0, mdd=0.03, signals_per_day=150))
        result = await gate.evaluate()
        mdd_check = next(c for c in result.checks if c.name == "Max Drawdown")
        assert mdd_check.passed is True

    @pytest.mark.asyncio
    async def test_mdd_check_fails_at_or_above_threshold(self) -> None:
        """MDD >= 5% blocks the gate."""
        gate = make_live_gate(wf_result=make_wf_result(sharpe=3.0, mdd=0.06, signals_per_day=150))
        result = await gate.evaluate()
        mdd_check = next(c for c in result.checks if c.name == "Max Drawdown")
        assert mdd_check.passed is False
        assert result.eligible is False

    @pytest.mark.asyncio
    async def test_mdd_block_reason_present_on_failure(self) -> None:
        """Block reason for failed MDD check is added to block_reasons."""
        gate = make_live_gate(wf_result=make_wf_result(sharpe=3.0, mdd=0.10))
        result = await gate.evaluate()
        assert any("MDD" in r for r in result.block_reasons)


# ---------------------------------------------------------------------------
# Check 3: Signals per day
# ---------------------------------------------------------------------------


class TestSignalsPerDayCheck:
    @pytest.mark.asyncio
    async def test_signals_check_passes_at_or_above_minimum(self) -> None:
        """Signals/day >= 100 passes."""
        gate = make_live_gate(
            wf_result=make_wf_result(sharpe=3.0, mdd=0.02, signals_per_day=100)
        )
        result = await gate.evaluate()
        signals_check = next(c for c in result.checks if c.name == "Signals/Day")
        assert signals_check.passed is True

    @pytest.mark.asyncio
    async def test_signals_check_fails_below_minimum(self) -> None:
        """Signals/day < 100 blocks the gate."""
        gate = make_live_gate(
            wf_result=make_wf_result(sharpe=3.0, mdd=0.02, signals_per_day=50)
        )
        result = await gate.evaluate()
        signals_check = next(c for c in result.checks if c.name == "Signals/Day")
        assert signals_check.passed is False
        assert result.eligible is False


# ---------------------------------------------------------------------------
# Check 4: Kill switch
# ---------------------------------------------------------------------------


class TestKillSwitchCheck:
    @pytest.mark.asyncio
    async def test_kill_switch_check_passes_when_not_halted(self) -> None:
        """Kill switch check passes when is_halted() returns False."""
        ks = MagicMock()
        ks.is_halted = MagicMock(return_value=False)
        gate = make_live_gate(kill_switch=ks)
        result = await gate.evaluate()
        ks_check = next(c for c in result.checks if c.name == "Kill Switch")
        assert ks_check.passed is True
        assert ks_check.value == "Clear"

    @pytest.mark.asyncio
    async def test_kill_switch_check_fails_when_halted(self) -> None:
        """Kill switch check fails when is_halted() returns True."""
        ks = MagicMock()
        ks.is_halted = MagicMock(return_value=True)
        gate = make_live_gate(kill_switch=ks)
        result = await gate.evaluate()
        ks_check = next(c for c in result.checks if c.name == "Kill Switch")
        assert ks_check.passed is False
        assert ks_check.value == "HALTED"
        assert result.eligible is False


# ---------------------------------------------------------------------------
# Check 5: Circuit breaker
# ---------------------------------------------------------------------------


class TestCircuitBreakerCheck:
    @pytest.mark.asyncio
    async def test_circuit_breaker_check_passes_when_closed(self) -> None:
        """Circuit breaker check passes when state is CLOSED."""
        cb = MagicMock()
        cb.state = "CLOSED"
        gate = make_live_gate(circuit_breaker=cb)
        result = await gate.evaluate()
        cb_check = next(c for c in result.checks if c.name == "Circuit Breaker")
        assert cb_check.passed is True
        assert cb_check.value == "CLOSED"

    @pytest.mark.asyncio
    async def test_circuit_breaker_check_fails_when_open(self) -> None:
        """Circuit breaker check fails when state is OPEN."""
        cb = MagicMock()
        cb.state = "OPEN"
        gate = make_live_gate(circuit_breaker=cb)
        result = await gate.evaluate()
        cb_check = next(c for c in result.checks if c.name == "Circuit Breaker")
        assert cb_check.passed is False
        assert result.eligible is False

    @pytest.mark.asyncio
    async def test_circuit_breaker_check_fails_when_half_open(self) -> None:
        """Circuit breaker check fails when state is HALF_OPEN."""
        cb = MagicMock()
        cb.state = "HALF_OPEN"
        gate = make_live_gate(circuit_breaker=cb)
        result = await gate.evaluate()
        cb_check = next(c for c in result.checks if c.name == "Circuit Breaker")
        assert cb_check.passed is False


# ---------------------------------------------------------------------------
# Check 6: Exchange health
# ---------------------------------------------------------------------------


class TestExchangeHealthCheck:
    @pytest.mark.asyncio
    async def test_exchange_health_passes_when_all_above_threshold(self) -> None:
        """Exchange health check passes when all scores >= 0.95."""
        health_fn = MagicMock(return_value={"binance": 0.99, "okx": 0.97})
        gate = make_live_gate(exchange_health_fn=health_fn)
        result = await gate.evaluate()
        health_check = next(c for c in result.checks if c.name == "Exchange Health")
        assert health_check.passed is True
        assert health_check.value == "OK"

    @pytest.mark.asyncio
    async def test_exchange_health_fails_when_one_below_threshold(self) -> None:
        """Exchange health check fails when any score < 0.95."""
        health_fn = MagicMock(return_value={"binance": 0.99, "okx": 0.80})
        gate = make_live_gate(exchange_health_fn=health_fn)
        result = await gate.evaluate()
        health_check = next(c for c in result.checks if c.name == "Exchange Health")
        assert health_check.passed is False
        assert result.eligible is False

    @pytest.mark.asyncio
    async def test_exchange_health_passes_when_no_provider_configured(self) -> None:
        """Exchange health check passes by default when no health_fn is provided."""
        gate = make_live_gate(exchange_health_fn=None)
        result = await gate.evaluate()
        health_check = next(c for c in result.checks if c.name == "Exchange Health")
        assert health_check.passed is True


# ---------------------------------------------------------------------------
# is_live_eligible() and latest_result
# ---------------------------------------------------------------------------


class TestIsLiveEligible:
    def test_is_live_eligible_returns_false_before_evaluation(self) -> None:
        """is_live_eligible() returns False when no evaluation has been run."""
        gate = make_live_gate()
        assert gate.is_live_eligible() is False

    @pytest.mark.asyncio
    async def test_is_live_eligible_returns_true_after_passing_evaluation(self) -> None:
        """is_live_eligible() returns True after a fully-passing evaluation."""
        gate = make_live_gate(wf_result=make_wf_result(sharpe=3.0, mdd=0.02, signals_per_day=200))
        await gate.evaluate()
        assert gate.is_live_eligible() is True

    @pytest.mark.asyncio
    async def test_is_live_eligible_returns_false_after_failing_evaluation(self) -> None:
        """is_live_eligible() returns False after a blocked evaluation."""
        gate = make_live_gate(wf_result=make_wf_result(sharpe=1.0))
        await gate.evaluate()
        assert gate.is_live_eligible() is False

    def test_latest_result_is_none_before_evaluation(self) -> None:
        """latest_result property returns None before any evaluation."""
        gate = make_live_gate()
        assert gate.latest_result is None

    @pytest.mark.asyncio
    async def test_latest_result_is_stored_after_evaluation(self) -> None:
        """latest_result is set to the most recent LiveGateResult."""
        gate = make_live_gate()
        result = await gate.evaluate()
        assert gate.latest_result is result


# ---------------------------------------------------------------------------
# Telegram notifications
# ---------------------------------------------------------------------------


class TestTelegramNotification:
    @pytest.mark.asyncio
    async def test_telegram_notified_on_passing_evaluation(self) -> None:
        """Telegram.send_alert is called after a passing evaluation."""
        telegram = MagicMock()
        telegram.send_alert = AsyncMock()
        gate = make_live_gate(
            wf_result=make_wf_result(sharpe=3.0, mdd=0.02, signals_per_day=200),
            telegram=telegram,
        )
        await gate.evaluate()
        telegram.send_alert.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_telegram_notified_with_warning_level_on_failure(self) -> None:
        """Telegram.send_alert is called with level='WARNING' when gate blocks."""
        telegram = MagicMock()
        telegram.send_alert = AsyncMock()
        gate = make_live_gate(
            wf_result=make_wf_result(sharpe=1.0),
            telegram=telegram,
        )
        await gate.evaluate()
        _, kwargs = telegram.send_alert.call_args
        assert kwargs.get("level") == "WARNING"

    @pytest.mark.asyncio
    async def test_telegram_not_required(self) -> None:
        """evaluate() completes normally even without a Telegram alerter."""
        gate = make_live_gate(telegram=None)
        result = await gate.evaluate()
        assert isinstance(result, LiveGateResult)


# ---------------------------------------------------------------------------
# Auto-evaluation loop
# ---------------------------------------------------------------------------


class TestAutoEvaluationLoop:
    @pytest.mark.asyncio
    async def test_start_auto_evaluation_creates_task(self) -> None:
        """start_auto_evaluation() creates a background asyncio Task."""
        gate = make_live_gate()
        # Patch the loop to avoid 24h sleep
        gate._auto_evaluation_loop = AsyncMock()
        await gate.start_auto_evaluation()
        assert gate._auto_task is not None
        await gate.stop_auto_evaluation()

    @pytest.mark.asyncio
    async def test_stop_auto_evaluation_cancels_task(self) -> None:
        """stop_auto_evaluation() cancels and awaits the background task."""
        gate = make_live_gate()

        async def infinite_loop(strategy_id: str) -> None:
            while True:
                await asyncio.sleep(1000)

        gate._auto_evaluation_loop = infinite_loop
        await gate.start_auto_evaluation()
        assert gate._auto_task is not None
        await gate.stop_auto_evaluation()
        assert gate._auto_task is None

    @pytest.mark.asyncio
    async def test_double_start_auto_evaluation_is_idempotent(self) -> None:
        """Calling start_auto_evaluation twice does not create a second task."""
        gate = make_live_gate()

        async def infinite_loop(strategy_id: str) -> None:
            while True:
                await asyncio.sleep(1000)

        gate._auto_evaluation_loop = infinite_loop
        await gate.start_auto_evaluation()
        first_task = gate._auto_task
        await gate.start_auto_evaluation()  # second call should be no-op
        assert gate._auto_task is first_task
        await gate.stop_auto_evaluation()


# ---------------------------------------------------------------------------
# Settings override
# ---------------------------------------------------------------------------


class TestSettingsOverride:
    def test_settings_override_sharpe_threshold(self) -> None:
        """LiveGateSettings.live_gate.sharpe_threshold overrides the class default."""
        mock_settings = MagicMock()
        mock_settings.live_gate.sharpe_threshold = 3.5
        mock_settings.live_gate.mdd_threshold = 0.03
        mock_settings.live_gate.min_signals_per_day = 200
        mock_settings.live_gate.min_exchange_health = 0.98
        mock_settings.live_gate.evaluation_days = 14
        mock_settings.live_gate.reevaluation_interval_hours = 12

        gate = make_live_gate()
        mock_pool = MagicMock()
        gate_with_settings = LiveGate(pool=mock_pool, settings=mock_settings)

        assert gate_with_settings.SHARPE_THRESHOLD == 3.5
        assert gate_with_settings.MDD_THRESHOLD == 0.03
        assert gate_with_settings.MIN_SIGNALS_PER_DAY == 200
        assert gate_with_settings.MIN_EXCHANGE_HEALTH == 0.98
        assert gate_with_settings.EVALUATION_DAYS == 14
        assert gate_with_settings.REEVALUATION_INTERVAL_HOURS == 12
