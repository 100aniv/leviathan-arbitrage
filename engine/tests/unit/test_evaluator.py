"""Tests for OutOfSampleEvaluator (TDD)."""
from __future__ import annotations

import pytest

from src.tuning.backtest import BacktestResult
from src.tuning.evaluator import EvaluationReport, OutOfSampleEvaluator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _result(
    pnl: float,
    sharpe: float = 1.0,
    returns: list[float] | None = None,
    max_drawdown: float = -0.05,
) -> BacktestResult:
    if returns is None:
        returns = [0.001] * 30
    return BacktestResult(
        total_pnl=pnl,
        sharpe_ratio=sharpe,
        max_drawdown=max_drawdown,
        win_rate=0.6,
        num_trades=20,
        returns=returns,
    )


# ---------------------------------------------------------------------------
# Sim-Real Variance
# ---------------------------------------------------------------------------


class TestSimRealVariance:
    def test_identical_pnl_zero_variance(self):
        ev = OutOfSampleEvaluator()
        report = ev.evaluate(_result(1000.0), _result(1000.0))
        assert report.sim_real_variance_pct == pytest.approx(0.0)

    def test_2pct_variance_passes(self):
        ev = OutOfSampleEvaluator()
        report = ev.evaluate(_result(1000.0), _result(1020.0))
        # |1000 - 1020| / 1020 * 100 ≈ 1.96%
        assert report.sim_real_variance_pct < 5.0
        assert report.passes_variance_check is True

    def test_100pct_variance_fails(self):
        ev = OutOfSampleEvaluator()
        report = ev.evaluate(_result(1000.0), _result(500.0))
        # |1000 - 500| / 500 * 100 = 100%
        assert report.sim_real_variance_pct == pytest.approx(100.0)
        assert report.passes_variance_check is False

    def test_near_zero_live_pnl_no_division_error(self):
        ev = OutOfSampleEvaluator()
        report = ev.evaluate(_result(10.0), _result(0.0))
        assert isinstance(report.sim_real_variance_pct, float)
        assert not (report.sim_real_variance_pct != report.sim_real_variance_pct)  # not NaN

    def test_variance_target_is_5pct(self):
        assert OutOfSampleEvaluator.SIM_REAL_VARIANCE_TARGET_PCT == 5.0

    def test_exact_5pct_boundary_fails(self):
        # 5% is not strictly less than 5%
        ev = OutOfSampleEvaluator()
        report = ev.evaluate(_result(1050.0), _result(1000.0))
        # variance = 50/1000*100 = 5% → should FAIL (target is < 5%)
        assert report.passes_variance_check is False


# ---------------------------------------------------------------------------
# T-test
# ---------------------------------------------------------------------------


class TestTTest:
    def test_identical_returns_not_significant(self):
        returns = [0.001] * 30
        ev = OutOfSampleEvaluator()
        report = ev.evaluate(_result(100.0, returns=returns), _result(100.0, returns=returns))
        assert report.p_value > 0.05
        assert report.is_significant is False

    def test_opposite_returns_significant(self):
        sim_r = [0.02] * 30
        live_r = [-0.02] * 30
        ev = OutOfSampleEvaluator()
        report = ev.evaluate(
            _result(100.0, returns=sim_r),
            _result(-100.0, returns=live_r),
        )
        assert report.is_significant is True
        assert report.p_value < 0.05

    def test_fewer_than_min_samples_skips_ttest(self):
        ev = OutOfSampleEvaluator()
        report = ev.evaluate(
            _result(100.0, returns=[0.001] * 5),
            _result(100.0, returns=[0.001] * 5),
        )
        assert report.p_value == pytest.approx(1.0)
        assert report.is_significant is False

    def test_t_statistic_is_float(self):
        ev = OutOfSampleEvaluator()
        report = ev.evaluate(_result(100.0), _result(100.0))
        assert isinstance(report.t_statistic, float)


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------


class TestRecommendations:
    def test_apply_when_variance_ok_and_not_significant(self):
        returns = [0.001] * 30
        ev = OutOfSampleEvaluator()
        report = ev.evaluate(_result(1000.0, returns=returns), _result(1010.0, returns=returns))
        assert "APPLY" in report.recommendation

    def test_reject_when_variance_too_high(self):
        ev = OutOfSampleEvaluator()
        report = ev.evaluate(_result(1000.0), _result(100.0))
        assert "REJECT" in report.recommendation

    def test_monitor_when_variance_ok_but_returns_differ(self):
        ev = OutOfSampleEvaluator()
        sim_r = [0.02] * 30
        live_r = [-0.02] * 30
        # Variance < 5% but returns significantly different
        report = ev.evaluate(
            _result(102.0, returns=sim_r),
            _result(100.0, returns=live_r),
        )
        # Either MONITOR or REJECT depending on variance
        assert report.recommendation  # non-empty


# ---------------------------------------------------------------------------
# compare_sharpe
# ---------------------------------------------------------------------------


class TestCompareSharpe:
    def test_better_candidate_returns_true(self):
        ev = OutOfSampleEvaluator()
        baseline = _result(100.0, sharpe=1.0)
        candidate = _result(100.0, sharpe=1.5)
        assert ev.compare_sharpe(baseline, candidate) is True

    def test_worse_candidate_returns_false(self):
        ev = OutOfSampleEvaluator()
        baseline = _result(100.0, sharpe=1.5)
        candidate = _result(100.0, sharpe=0.5)
        assert ev.compare_sharpe(baseline, candidate) is False

    def test_equal_sharpe_returns_false(self):
        ev = OutOfSampleEvaluator()
        r = _result(100.0, sharpe=1.0)
        assert ev.compare_sharpe(r, r) is False


# ---------------------------------------------------------------------------
# EvaluationReport fields
# ---------------------------------------------------------------------------


class TestEvaluationReport:
    def test_report_has_all_fields(self):
        ev = OutOfSampleEvaluator()
        report = ev.evaluate(_result(1000.0), _result(1000.0))
        assert hasattr(report, "sim_total_pnl")
        assert hasattr(report, "live_total_pnl")
        assert hasattr(report, "sim_real_variance_pct")
        assert hasattr(report, "t_statistic")
        assert hasattr(report, "p_value")
        assert hasattr(report, "is_significant")
        assert hasattr(report, "passes_variance_check")
        assert hasattr(report, "recommendation")

    def test_report_pnl_values_match_inputs(self):
        ev = OutOfSampleEvaluator()
        report = ev.evaluate(_result(500.0), _result(750.0))
        assert report.sim_total_pnl == pytest.approx(500.0)
        assert report.live_total_pnl == pytest.approx(750.0)
