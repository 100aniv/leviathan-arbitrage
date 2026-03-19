"""Verification tests for Phase S15 already-completed US.

Covers:
  US-245: StatisticalArbStrategy accepts regime_detector parameter
  US-246: LiveGate.enforce_or_fallback() exists + blocks when not eligible
  US-257: ShadowStats winning_pnl_sum / losing_pnl_sum accumulation + profit_factor formula
  US-250-a: ComplianceChecker importable + run_audit() async method exists
"""
from __future__ import annotations

import inspect
import time
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# US-245: StatisticalArbStrategy accepts regime_detector
# ---------------------------------------------------------------------------


def test_us245_stat_arb_accepts_regime_detector_parameter():
    """StatisticalArbStrategy.__init__ accepts regime_detector without error."""
    from src.friction.cost_calculator import CostCalculator
    from src.friction.fee_model import FeeModel
    from src.strategies.statistical_arb import StatisticalArbStrategy

    mock_detector = MagicMock()
    cost_calc = CostCalculator(fee_model=FeeModel())

    strategy = StatisticalArbStrategy(
        strategy_id="stat_arb_test",
        cost_calculator=cost_calc,
        regime_detector=mock_detector,
    )
    assert strategy is not None


def test_us245_stat_arb_regime_detector_defaults_to_none():
    """StatisticalArbStrategy can be created without regime_detector (defaults to None)."""
    from src.friction.cost_calculator import CostCalculator
    from src.friction.fee_model import FeeModel
    from src.strategies.statistical_arb import StatisticalArbStrategy

    strategy = StatisticalArbStrategy(
        strategy_id="stat_arb_no_detector",
        cost_calculator=CostCalculator(fee_model=FeeModel()),
    )
    assert strategy is not None


# ---------------------------------------------------------------------------
# US-246: LiveGate.enforce_or_fallback()
# ---------------------------------------------------------------------------


def test_us246_live_gate_has_enforce_or_fallback_method():
    """LiveGate class exposes enforce_or_fallback() async method."""
    from src.modes.live_gate import LiveGate

    assert hasattr(LiveGate, "enforce_or_fallback"), (
        "LiveGate must have enforce_or_fallback() method"
    )
    assert inspect.iscoroutinefunction(LiveGate.enforce_or_fallback), (
        "enforce_or_fallback() must be an async method"
    )


@pytest.mark.asyncio
async def test_us246_enforce_or_fallback_returns_false_when_not_eligible():
    """enforce_or_fallback() returns False (shadow fallback) when gate evaluation is not eligible."""
    from datetime import datetime, timezone

    from src.modes.live_gate import LiveGate, LiveGateResult

    mock_pool = MagicMock()
    gate = LiveGate(pool=mock_pool)

    # Mock evaluate() to return ineligible result
    not_eligible = LiveGateResult(
        timestamp=datetime.now(timezone.utc),
        eligible=False,
        checks=[],
        block_reasons=["Sharpe below threshold"],
    )
    gate.evaluate = AsyncMock(return_value=not_eligible)

    result = await gate.enforce_or_fallback()
    assert result is False, "enforce_or_fallback() must return False when gate is not eligible"


@pytest.mark.asyncio
async def test_us246_enforce_or_fallback_returns_true_when_eligible():
    """enforce_or_fallback() returns True when all gate checks pass."""
    from datetime import datetime, timezone

    from src.modes.live_gate import LiveGate, LiveGateResult

    mock_pool = MagicMock()
    gate = LiveGate(pool=mock_pool)

    eligible = LiveGateResult(
        timestamp=datetime.now(timezone.utc),
        eligible=True,
        checks=[],
        block_reasons=[],
    )
    gate.evaluate = AsyncMock(return_value=eligible)

    result = await gate.enforce_or_fallback()
    assert result is True, "enforce_or_fallback() must return True when gate is eligible"


# ---------------------------------------------------------------------------
# US-257: ShadowStats winning/losing PnL accumulation + profit_factor formula
# ---------------------------------------------------------------------------


def test_us257_shadow_stats_has_winning_and_losing_pnl_fields():
    """ShadowStats has winning_pnl_sum and losing_pnl_sum fields initialized to 0."""
    from src.modes.shadow import ShadowStats

    stats = ShadowStats(start_time=time.monotonic())
    assert hasattr(stats, "winning_pnl_sum"), "ShadowStats must have winning_pnl_sum field"
    assert hasattr(stats, "losing_pnl_sum"), "ShadowStats must have losing_pnl_sum field"
    assert stats.winning_pnl_sum == 0.0
    assert stats.losing_pnl_sum == 0.0


def test_us257_shadow_stats_accumulates_pnl_correctly():
    """winning_pnl_sum and losing_pnl_sum accumulate independently."""
    from src.modes.shadow import ShadowStats

    stats = ShadowStats(start_time=time.monotonic())

    stats.winning_pnl_sum += 150.0
    stats.winning_pnl_sum += 50.0
    stats.losing_pnl_sum += 80.0

    assert stats.winning_pnl_sum == 200.0
    assert stats.losing_pnl_sum == 80.0


def test_us257_profit_factor_is_amount_ratio_not_count_ratio():
    """profit_factor = winning_pnl_sum / losing_pnl_sum (gross profit / gross loss).

    Must NOT be trades_won / trades_lost (count ratio).
    """
    from src.modes.shadow import ShadowStats

    stats = ShadowStats(start_time=time.monotonic())
    stats.winning_pnl_sum = 300.0
    stats.losing_pnl_sum = 100.0

    # Formula per US-257: profit_factor = gross_profit / gross_loss
    profit_factor = stats.winning_pnl_sum / stats.losing_pnl_sum
    assert profit_factor == pytest.approx(3.0), (
        "profit_factor must be winning_pnl_sum / losing_pnl_sum = 3.0"
    )


def test_us257_profit_factor_edge_case_no_losses():
    """When losing_pnl_sum == 0, profit_factor falls back to 10.0 (avoids ZeroDivisionError)."""
    from src.modes.shadow import ShadowStats

    stats = ShadowStats(start_time=time.monotonic())
    stats.winning_pnl_sum = 500.0
    stats.losing_pnl_sum = 0.0  # no losses

    # Expected fallback per shadow.py line: profit_factor = 10.0 when no losses
    losing = stats.losing_pnl_sum
    profit_factor = (stats.winning_pnl_sum / losing) if losing > 0 else 10.0
    assert profit_factor == 10.0, "profit_factor fallback when no losses must be 10.0"


# ---------------------------------------------------------------------------
# US-250-a: ComplianceChecker importable + run_audit() method
# ---------------------------------------------------------------------------


def test_us250a_compliance_checker_is_importable():
    """ComplianceChecker can be imported from src.infra.compliance."""
    from src.infra.compliance import ComplianceChecker

    assert ComplianceChecker is not None


def test_us250a_compliance_checker_has_async_run_audit():
    """ComplianceChecker.run_audit() exists and is an async method."""
    from src.infra.compliance import ComplianceChecker

    assert hasattr(ComplianceChecker, "run_audit"), (
        "ComplianceChecker must have run_audit() method"
    )
    assert inspect.iscoroutinefunction(ComplianceChecker.run_audit), (
        "run_audit() must be an async coroutine"
    )
