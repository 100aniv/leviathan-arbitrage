"""Tests for src/risk/correlation_monitor.py — US-118 Strategy Correlation Monitor.

Covers: Pearson correlation calculation, threshold-based PositionScaleEvent emission,
window size guard, PnL-based strategy selection for scaling.
"""
from __future__ import annotations

import pytest

from src.risk.correlation_monitor import CorrelationMonitor, PositionScaleEvent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def monitor() -> CorrelationMonitor:
    return CorrelationMonitor(window=30, threshold=0.7)


# ---------------------------------------------------------------------------
# Pearson static method tests
# ---------------------------------------------------------------------------


class TestPearsonCorrelation:
    def test_pearson_perfect_positive_correlation(self) -> None:
        """Identical series → Pearson r = 1.0."""
        series = [1.0, 2.0, 3.0, 4.0, 5.0]
        r = CorrelationMonitor.pearson(series, series)
        assert r == pytest.approx(1.0, abs=1e-9)

    def test_pearson_perfect_negative_correlation(self) -> None:
        """Reversed series → Pearson r = -1.0."""
        a = [1.0, 2.0, 3.0]
        b = [3.0, 2.0, 1.0]
        r = CorrelationMonitor.pearson(a, b)
        assert r == pytest.approx(-1.0, abs=1e-9)

    def test_pearson_independent_series_near_zero(self) -> None:
        """Orthogonal series → |r| is small."""
        a = [1.0, -1.0, 1.0, -1.0, 1.0, -1.0]
        b = [1.0, 1.0, -1.0, -1.0, 1.0, 1.0]
        r = CorrelationMonitor.pearson(a, b)
        assert abs(r) < 0.5

    def test_pearson_insufficient_data_returns_none(self) -> None:
        """Single-element series → cannot compute correlation, returns None."""
        r = CorrelationMonitor.pearson([1.0], [1.0])
        assert r is None

    def test_pearson_empty_series_returns_none(self) -> None:
        """Empty series → returns None."""
        r = CorrelationMonitor.pearson([], [])
        assert r is None

    def test_pearson_zero_variance_series_returns_none(self) -> None:
        """Constant series (zero std-dev) → returns None (undefined correlation)."""
        a = [5.0, 5.0, 5.0]
        b = [1.0, 2.0, 3.0]
        r = CorrelationMonitor.pearson(a, b)
        assert r is None

    def test_pearson_two_element_series_computable(self) -> None:
        """Two-element series → correlation computable (n >= 2)."""
        r = CorrelationMonitor.pearson([1.0, 2.0], [1.0, 2.0])
        assert r == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# record_trade_pnl + check_correlations tests
# ---------------------------------------------------------------------------


class TestPositionScaleEventEmission:
    def _fill_both(
        self,
        monitor: CorrelationMonitor,
        pnls_a: list[float],
        pnls_b: list[float],
    ) -> None:
        for p in pnls_a:
            monitor.record_trade_pnl("strat_a", p)
        for p in pnls_b:
            monitor.record_trade_pnl("strat_b", p)

    def test_high_correlation_emits_scale_event(
        self, monitor: CorrelationMonitor
    ) -> None:
        """Correlation > 0.7 → emits PositionScaleEvent with scale=0.5."""
        pnls = [float(i) for i in range(1, 31)]
        self._fill_both(monitor, pnls, pnls)
        events = monitor.check_correlations()
        assert len(events) == 1
        assert isinstance(events[0], PositionScaleEvent)
        assert events[0].scale == pytest.approx(0.5)

    def test_low_correlation_emits_no_events(
        self, monitor: CorrelationMonitor
    ) -> None:
        """Correlation < 0.7 → no events emitted."""
        a = [1.0, -1.0, 1.0, -1.0] * 8  # 32 alternating
        b = [1.0, 1.0, -1.0, -1.0] * 8
        self._fill_both(monitor, a[:30], b[:30])
        events = monitor.check_correlations()
        assert events == []

    def test_window_under_30_skips_correlation_check(
        self, monitor: CorrelationMonitor
    ) -> None:
        """< 30 trades → strategy excluded from check, no events."""
        pnls = [1.0] * 29  # one short of window
        self._fill_both(monitor, pnls, pnls)
        events = monitor.check_correlations()
        assert events == []

    def test_exactly_30_trades_triggers_check(
        self, monitor: CorrelationMonitor
    ) -> None:
        """Exactly 30 trades → correlation check IS performed."""
        pnls = [float(i) for i in range(1, 31)]
        self._fill_both(monitor, pnls, pnls)
        events = monitor.check_correlations()
        # Perfect correlation → should emit event
        assert len(events) > 0


# ---------------------------------------------------------------------------
# Strategy selection — scale smaller PnL strategy
# ---------------------------------------------------------------------------


class TestStrategyScaleSelection:
    def test_smaller_total_pnl_strategy_is_scaled(
        self, monitor: CorrelationMonitor
    ) -> None:
        """When correlation is high, strategy with lower total PnL is scaled."""
        # strat_a: ascending [1..30] (total=465)
        # strat_b: ascending [10..300] step 10 (total=4650, perfect corr with strat_a)
        for i in range(1, 31):
            monitor.record_trade_pnl("strat_a", float(i))
            monitor.record_trade_pnl("strat_b", float(i * 10))
        events = monitor.check_correlations()
        assert len(events) == 1
        assert events[0].strategy_id == "strat_a"

    def test_scale_event_has_05_scale_factor(
        self, monitor: CorrelationMonitor
    ) -> None:
        """PositionScaleEvent.scale == 0.5 (50% reduction)."""
        pnls = [float(i) for i in range(1, 31)]
        for p in pnls:
            monitor.record_trade_pnl("strat_a", p)
            monitor.record_trade_pnl("strat_b", p)
        events = monitor.check_correlations()
        assert events[0].scale == pytest.approx(0.5)

    def test_scale_event_has_reason_string(
        self, monitor: CorrelationMonitor
    ) -> None:
        """PositionScaleEvent includes a non-empty reason string."""
        pnls = [float(i) for i in range(1, 31)]
        for p in pnls:
            monitor.record_trade_pnl("strat_a", p)
            monitor.record_trade_pnl("strat_b", p)
        events = monitor.check_correlations()
        assert events[0].reason is not None
        assert len(events[0].reason) > 0
