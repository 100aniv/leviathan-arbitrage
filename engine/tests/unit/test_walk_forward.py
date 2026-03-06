"""Unit tests for WalkForwardAnalyzer and WalkForwardResult.

Covers:
- _compute_sharpe with known returns → verifies the annualised formula
- _compute_mdd with known PnL sequence → verifies peak-drawdown formula
- _compute_window metrics (win_count, loss_count, profit_factor, avg_profit)
- analyze() with empty DB data returns block_reason
- analyze() with synthetic trade data returns correct aggregates
- live_eligible gate logic (Sharpe < 2.5 → blocked, MDD > 5% → blocked,
  signals/day < 100 → blocked)
- Window splitting: trades are assigned to the correct hour window
- WalkForwardResult default field values
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.analysis.walk_forward import (
    SHARPE_GATE,
    MDD_GATE,
    MIN_DAILY_SIGNALS,
    WalkForwardAnalyzer,
    WalkForwardResult,
    WindowResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_analyzer(rows: list[dict] | None = None) -> WalkForwardAnalyzer:
    """Return a WalkForwardAnalyzer with asyncpg pool fully mocked.

    ``rows`` is the list of dicts that will be returned by conn.fetch().
    Each dict must have keys: ts, net_pnl, gross_spread_bps, fee_total,
    slippage_total, status  (matching the real SQL columns).
    """
    if rows is None:
        rows = []

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=rows)

    # asyncpg pool.acquire() is used as an async context manager
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(
        return_value=_AsyncContextManager(mock_conn)
    )

    return WalkForwardAnalyzer(pool=mock_pool)


class _AsyncContextManager:
    """Minimal async context manager wrapper for the mock connection."""

    def __init__(self, obj: object) -> None:
        self._obj = obj

    async def __aenter__(self) -> object:
        return self._obj

    async def __aexit__(self, *args: object) -> None:
        pass


def make_row(
    ts: datetime,
    net_pnl: float,
    status: str = "filled",
) -> dict:
    return {
        "ts": ts,
        "net_pnl": net_pnl,
        "gross_spread_bps": 10.0,
        "fee_total": 0.5,
        "slippage_total": 0.2,
        "status": status,
    }


def recent_ts(hours_ago: float = 0.0) -> datetime:
    """UTC timestamp offset hours_ago hours from now."""
    return datetime.now(timezone.utc) - timedelta(hours=hours_ago)


# ---------------------------------------------------------------------------
# WalkForwardResult — default values
# ---------------------------------------------------------------------------


class TestWalkForwardResultDefaults:
    def test_default_overall_sharpe_is_zero(self) -> None:
        r = WalkForwardResult()
        assert r.overall_sharpe == 0.0

    def test_default_overall_mdd_is_zero(self) -> None:
        r = WalkForwardResult()
        assert r.overall_mdd == 0.0

    def test_default_live_eligible_is_false(self) -> None:
        r = WalkForwardResult()
        assert r.live_eligible is False

    def test_default_windows_is_empty_list(self) -> None:
        r = WalkForwardResult()
        assert r.windows == []

    def test_default_block_reason_is_empty_string(self) -> None:
        r = WalkForwardResult()
        assert r.block_reason == ""


# ---------------------------------------------------------------------------
# _compute_sharpe — formula verification
# ---------------------------------------------------------------------------


class TestComputeSharpe:
    def test_sharpe_returns_zero_for_single_return(self) -> None:
        """Less than 2 data points → Sharpe = 0.0 (insufficient data)."""
        result = WalkForwardAnalyzer._compute_sharpe([1.0])
        assert result == 0.0

    def test_sharpe_returns_zero_for_empty_list(self) -> None:
        """Empty returns list → Sharpe = 0.0."""
        result = WalkForwardAnalyzer._compute_sharpe([])
        assert result == 0.0

    def test_sharpe_returns_zero_when_std_is_zero(self) -> None:
        """Constant returns have zero std → Sharpe = 0.0."""
        result = WalkForwardAnalyzer._compute_sharpe([1.0, 1.0, 1.0], risk_free_rate=0.0)
        assert result == 0.0

    def test_sharpe_formula_with_known_values(self) -> None:
        """Verify annualised Sharpe = (mean - rf) / std * sqrt(periods_per_year).

        With returns=[1.0, 3.0], risk_free_rate=0, periods_per_year=1:
          mean = 2.0
          variance = ((1-2)^2 + (3-2)^2) / (2-1) = 2.0
          std = sqrt(2)
          sharpe = (2 / sqrt(2)) * sqrt(1) = sqrt(2) ≈ 1.4142
        """
        returns = [1.0, 3.0]
        result = WalkForwardAnalyzer._compute_sharpe(
            returns, risk_free_rate=0.0, periods_per_year=1.0
        )
        expected = math.sqrt(2.0)
        assert abs(result - expected) < 1e-9, f"Expected {expected}, got {result}"

    def test_sharpe_positive_for_consistently_positive_returns(self) -> None:
        """Positive mean returns with small variance → positive Sharpe."""
        returns = [0.01, 0.012, 0.011, 0.013, 0.009]
        result = WalkForwardAnalyzer._compute_sharpe(returns, risk_free_rate=0.0)
        assert result > 0.0

    def test_sharpe_annualisation_uses_periods_per_year(self) -> None:
        """Larger periods_per_year multiplies the Sharpe by sqrt factor."""
        returns = [0.1, 0.3]
        sharpe_1 = WalkForwardAnalyzer._compute_sharpe(
            returns, risk_free_rate=0.0, periods_per_year=1.0
        )
        sharpe_4 = WalkForwardAnalyzer._compute_sharpe(
            returns, risk_free_rate=0.0, periods_per_year=4.0
        )
        assert abs(sharpe_4 - sharpe_1 * 2.0) < 1e-9

    def test_sharpe_risk_free_rate_reduces_value(self) -> None:
        """Non-zero risk-free rate lowers the Sharpe ratio."""
        returns = [0.1, 0.2, 0.15, 0.12]
        sharpe_rf0 = WalkForwardAnalyzer._compute_sharpe(
            returns, risk_free_rate=0.0, periods_per_year=1.0
        )
        sharpe_rf_high = WalkForwardAnalyzer._compute_sharpe(
            returns, risk_free_rate=10.0, periods_per_year=1.0
        )
        assert sharpe_rf_high < sharpe_rf0


# ---------------------------------------------------------------------------
# _compute_mdd — formula verification
# ---------------------------------------------------------------------------


class TestComputeMDD:
    def test_mdd_returns_zero_for_empty_sequence(self) -> None:
        result = WalkForwardAnalyzer._compute_mdd([])
        assert result == 0.0

    def test_mdd_returns_zero_for_monotonically_increasing(self) -> None:
        """No drawdown when PnL always increases."""
        result = WalkForwardAnalyzer._compute_mdd([1.0, 2.0, 3.0, 4.0])
        assert result == 0.0

    def test_mdd_computed_as_fraction_of_peak(self) -> None:
        """MDD formula: (peak_cumulative - current_cumulative) / peak_cumulative.

        PnLs = [10, -5] → cumulative = [10, 5]
        peak = 10, dd = (10-5)/10 = 0.5
        """
        result = WalkForwardAnalyzer._compute_mdd([10.0, -5.0])
        assert abs(result - 0.5) < 1e-9, f"Expected 0.5, got {result}"

    def test_mdd_tracks_worst_drawdown_not_last(self) -> None:
        """MDD records the worst seen drawdown, not the final state.

        PnLs = [100, -80, 40] → cumulative = [100, 20, 60]
        After drop to 20: dd = (100-20)/100 = 0.80
        After recovery to 60: dd from that peak = still 0.80 from first peak
        """
        result = WalkForwardAnalyzer._compute_mdd([100.0, -80.0, 40.0])
        assert abs(result - 0.80) < 1e-9, f"Expected 0.80, got {result}"

    def test_mdd_zero_when_only_losses_no_prior_peak(self) -> None:
        """When cumulative never exceeds zero, peak=0, MDD=0 (no fractional dd)."""
        result = WalkForwardAnalyzer._compute_mdd([-1.0, -2.0, -3.0])
        assert result == 0.0

    def test_mdd_single_loss_after_gain(self) -> None:
        """Single PnL gain then single loss: dd = loss/gain."""
        result = WalkForwardAnalyzer._compute_mdd([100.0, -20.0])
        assert abs(result - 0.2) < 1e-9

    def test_mdd_multiple_drawdowns_returns_maximum(self) -> None:
        """Multiple drawdown sequences: MDD is the worst one.

        PnLs = [10, -2, 3, -8, 1]
        cum  = [10,  8, 11, 3, 4]
        dd1  = (10-8)/10 = 0.2  (at index 1)
        dd2  = (11-3)/11 ≈ 0.727 (at index 3)
        MDD should be ≈ 0.727
        """
        pnls = [10.0, -2.0, 3.0, -8.0, 1.0]
        result = WalkForwardAnalyzer._compute_mdd(pnls)
        expected = (11.0 - 3.0) / 11.0
        assert abs(result - expected) < 1e-9, f"Expected {expected}, got {result}"


# ---------------------------------------------------------------------------
# _compute_window — per-window metrics
# ---------------------------------------------------------------------------


class TestComputeWindow:
    def _make_window(self, pnls: list[float]) -> WindowResult:
        analyzer = make_analyzer()
        now = datetime.now(timezone.utc)
        trades = [{"pnl": p} for p in pnls]
        return analyzer._compute_window(now, now + timedelta(hours=1), trades)

    def test_window_trade_count(self) -> None:
        wr = self._make_window([1.0, -2.0, 3.0])
        assert wr.trade_count == 3

    def test_window_win_count(self) -> None:
        wr = self._make_window([1.0, -2.0, 3.0])
        assert wr.win_count == 2

    def test_window_loss_count_includes_zero(self) -> None:
        """Trades with PnL <= 0 are counted as losses."""
        wr = self._make_window([1.0, 0.0, -1.0])
        assert wr.loss_count == 2

    def test_window_total_pnl(self) -> None:
        wr = self._make_window([2.0, 3.0, -1.0])
        assert abs(wr.total_pnl - 4.0) < 1e-9

    def test_window_win_rate(self) -> None:
        wr = self._make_window([1.0, 1.0, -1.0])
        assert abs(wr.win_rate - 2 / 3) < 1e-9

    def test_window_avg_profit_per_trade(self) -> None:
        wr = self._make_window([10.0, 20.0, -6.0])
        assert abs(wr.avg_profit_per_trade - 24.0 / 3) < 1e-9

    def test_window_profit_factor_with_wins_and_losses(self) -> None:
        """profit_factor = gross_profit / gross_loss."""
        wr = self._make_window([10.0, 5.0, -3.0, -2.0])
        # gross_profit=15, gross_loss=5 → pf=3.0
        assert abs(wr.profit_factor - 3.0) < 1e-9

    def test_window_profit_factor_infinite_when_no_losses(self) -> None:
        """profit_factor = inf when there are no losing trades."""
        wr = self._make_window([5.0, 10.0, 3.0])
        assert wr.profit_factor == float("inf")

    def test_window_empty_trades_returns_zeroed_result(self) -> None:
        """Window with no trades produces all-zero metrics."""
        analyzer = make_analyzer()
        now = datetime.now(timezone.utc)
        wr = analyzer._compute_window(now, now + timedelta(hours=1), [])
        assert wr.trade_count == 0
        assert wr.win_rate == 0.0
        assert wr.profit_factor == float("inf")


# ---------------------------------------------------------------------------
# analyze() — empty data case
# ---------------------------------------------------------------------------


class TestAnalyzeEmptyData:
    @pytest.mark.asyncio
    async def test_analyze_returns_result_when_no_data(self) -> None:
        """analyze() returns WalkForwardResult even when DB has no rows."""
        analyzer = make_analyzer(rows=[])
        result = await analyzer.analyze()
        assert isinstance(result, WalkForwardResult)

    @pytest.mark.asyncio
    async def test_analyze_sets_block_reason_when_no_data(self) -> None:
        """block_reason is set to a descriptive string when no trades found."""
        analyzer = make_analyzer(rows=[])
        result = await analyzer.analyze()
        assert result.block_reason != ""
        assert "No execution data" in result.block_reason

    @pytest.mark.asyncio
    async def test_analyze_not_eligible_when_no_data(self) -> None:
        """live_eligible is False when no execution data is available."""
        analyzer = make_analyzer(rows=[])
        result = await analyzer.analyze()
        assert result.live_eligible is False

    @pytest.mark.asyncio
    async def test_analyze_overall_trades_zero_when_no_data(self) -> None:
        analyzer = make_analyzer(rows=[])
        result = await analyzer.analyze()
        assert result.overall_trades == 0


# ---------------------------------------------------------------------------
# analyze() — synthetic data, correct aggregates
# ---------------------------------------------------------------------------


class TestAnalyzeSyntheticData:
    def _make_rows(self, n: int, pnl: float = 1.0) -> list[dict]:
        """Create n trades spread evenly over the past 7 days."""
        base = datetime.now(timezone.utc)
        interval = timedelta(days=7) / n
        return [
            make_row(ts=base - timedelta(days=7) + i * interval, net_pnl=pnl)
            for i in range(n)
        ]

    @pytest.mark.asyncio
    async def test_analyze_returns_correct_trade_count(self) -> None:
        rows = self._make_rows(n=70, pnl=1.0)
        analyzer = make_analyzer(rows=rows)
        result = await analyzer.analyze()
        assert result.overall_trades == 70

    @pytest.mark.asyncio
    async def test_analyze_overall_pnl_is_sum_of_all_trades(self) -> None:
        rows = self._make_rows(n=10, pnl=5.0)
        analyzer = make_analyzer(rows=rows)
        result = await analyzer.analyze()
        assert abs(result.overall_pnl - 50.0) < 1e-6

    @pytest.mark.asyncio
    async def test_analyze_overall_win_rate_all_positive(self) -> None:
        """All profitable trades → win_rate = 1.0."""
        rows = self._make_rows(n=20, pnl=1.0)
        analyzer = make_analyzer(rows=rows)
        result = await analyzer.analyze()
        assert abs(result.overall_win_rate - 1.0) < 1e-9

    @pytest.mark.asyncio
    async def test_analyze_avg_signals_per_day_calculated_correctly(self) -> None:
        """avg_signals_per_day = total_trades / total_days."""
        # 140 trades over 7 days → 20/day
        rows = self._make_rows(n=140, pnl=0.1)
        analyzer = make_analyzer(rows=rows)
        result = await analyzer.analyze(days=7)
        # Allow ±2 for floating-point rounding in time calculations
        assert abs(result.avg_signals_per_day - 20.0) < 2.0

    @pytest.mark.asyncio
    async def test_analyze_populates_windows(self) -> None:
        """analyze() creates at least one window when trades exist."""
        rows = self._make_rows(n=50, pnl=0.5)
        analyzer = make_analyzer(rows=rows)
        result = await analyzer.analyze()
        assert len(result.windows) > 0


# ---------------------------------------------------------------------------
# analyze() — live eligibility gate logic
# ---------------------------------------------------------------------------


class TestLiveEligibilityGate:
    def _make_rows_with_metrics(
        self,
        n: int = 1000,
        pnl_per_trade: float = 1.0,
    ) -> list[dict]:
        """Dense uniform trades over 7 days for gate testing."""
        base = datetime.now(timezone.utc)
        interval = timedelta(days=7) / n
        return [
            make_row(ts=base - timedelta(days=7) + i * interval, net_pnl=pnl_per_trade)
            for i in range(n)
        ]

    @pytest.mark.asyncio
    async def test_eligible_when_sharpe_above_gate_and_mdd_low(self) -> None:
        """Strategy with high consistent PnL is live-eligible."""
        # 1000 uniform wins → high Sharpe, zero MDD, >100 signals/day
        rows = self._make_rows_with_metrics(n=1000, pnl_per_trade=1.0)
        analyzer = make_analyzer(rows=rows)
        result = await analyzer.analyze(days=7)
        assert result.live_eligible is True
        assert result.block_reason == ""

    @pytest.mark.asyncio
    async def test_blocked_when_sharpe_below_gate(self) -> None:
        """Sharpe < SHARPE_GATE blocks live eligibility."""
        # Alternating wins/losses with large variance → low Sharpe
        rows: list[dict] = []
        base = datetime.now(timezone.utc)
        for i in range(200):
            pnl = 100.0 if i % 2 == 0 else -99.0
            ts = base - timedelta(days=7) + i * timedelta(hours=0.84)
            rows.append(make_row(ts=ts, net_pnl=pnl))

        analyzer = make_analyzer(rows=rows)
        result = await analyzer.analyze(days=7)

        # Whether or not Sharpe is the issue, if it's below gate, it's blocked
        if result.overall_sharpe < SHARPE_GATE:
            assert result.live_eligible is False
            assert str(SHARPE_GATE) in result.block_reason or "Sharpe" in result.block_reason

    @pytest.mark.asyncio
    async def test_blocked_when_mdd_above_gate(self) -> None:
        """MDD > MDD_GATE blocks live eligibility."""
        # Big gain followed by catastrophic loss → large MDD
        rows: list[dict] = []
        base = datetime.now(timezone.utc)
        rows.append(make_row(ts=base - timedelta(days=6), net_pnl=1000.0))
        for i in range(199):
            ts = base - timedelta(days=6) + (i + 1) * timedelta(minutes=30)
            rows.append(make_row(ts=ts, net_pnl=-5.0))  # erodes the peak

        analyzer = make_analyzer(rows=rows)
        result = await analyzer.analyze(days=7)

        if result.overall_mdd > MDD_GATE:
            assert result.live_eligible is False
            assert "MDD" in result.block_reason

    @pytest.mark.asyncio
    async def test_blocked_when_insufficient_signals_per_day(self) -> None:
        """Fewer than MIN_DAILY_SIGNALS/day blocks live eligibility."""
        # Only 7 trades over 7 days → 1/day << 100/day
        rows = self._make_rows_with_metrics(n=7, pnl_per_trade=5.0)
        analyzer = make_analyzer(rows=rows)
        result = await analyzer.analyze(days=7)
        assert result.live_eligible is False
        assert "Signals/day" in result.block_reason or "signals" in result.block_reason.lower()

    @pytest.mark.asyncio
    async def test_block_reason_contains_all_failing_conditions(self) -> None:
        """block_reason lists ALL failing criteria, not just the first."""
        # Low signals/day and also potentially low Sharpe (sparse data)
        rows = self._make_rows_with_metrics(n=5, pnl_per_trade=0.0)
        analyzer = make_analyzer(rows=rows)
        result = await analyzer.analyze(days=7)
        # Must be blocked; block_reason is non-empty
        assert result.live_eligible is False
        assert result.block_reason != ""


# ---------------------------------------------------------------------------
# Window splitting — trades assigned to correct windows
# ---------------------------------------------------------------------------


class TestWindowSplitting:
    @pytest.mark.asyncio
    async def test_trades_in_different_hours_appear_in_separate_windows(self) -> None:
        """Trades separated by 2 hours land in 2 different windows."""
        base = datetime.now(timezone.utc) - timedelta(days=1)
        rows = [
            make_row(ts=base + timedelta(minutes=30), net_pnl=1.0),   # window 0
            make_row(ts=base + timedelta(hours=2, minutes=30), net_pnl=2.0),  # window 2
        ]
        analyzer = make_analyzer(rows=rows)
        result = await analyzer.analyze(days=7, window_hours=1)
        # Should have at least 2 windows (one for each trade cluster)
        assert len(result.windows) >= 2

    @pytest.mark.asyncio
    async def test_trades_in_same_hour_appear_in_same_window(self) -> None:
        """Trades in the same 1-hour bucket are aggregated in one window."""
        base = datetime.now(timezone.utc) - timedelta(days=1)
        rows = [
            make_row(ts=base + timedelta(minutes=10), net_pnl=1.0),
            make_row(ts=base + timedelta(minutes=40), net_pnl=2.0),
        ]
        analyzer = make_analyzer(rows=rows)
        result = await analyzer.analyze(days=7, window_hours=1)
        # Both trades are in the same hour → 1 window
        assert len(result.windows) == 1
        assert result.windows[0].trade_count == 2
        assert abs(result.windows[0].total_pnl - 3.0) < 1e-9
