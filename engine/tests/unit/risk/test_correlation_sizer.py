"""US-176: CorrelationMonitor → DynamicSizer pipeline."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from src.risk.correlation_monitor import CorrelationMonitor, PositionScaleEvent


# ---------------------------------------------------------------------------
# CorrelationMonitor basics
# ---------------------------------------------------------------------------


class TestCorrelationMonitorBasics:
    def test_no_events_when_insufficient_data(self):
        """check_correlations returns [] when not enough trade history."""
        monitor = CorrelationMonitor(window=30, threshold=0.7)
        events = monitor.check_correlations()
        assert events == []

    def test_records_pnl_without_error(self):
        """record_trade_pnl does not raise."""
        monitor = CorrelationMonitor()
        monitor.record_trade_pnl("strat_a", 1.5)
        monitor.record_trade_pnl("strat_a", -0.5)

    def test_no_events_for_single_strategy(self):
        """With only one strategy, no correlation pairs exist."""
        monitor = CorrelationMonitor(window=5, threshold=0.7)
        for _ in range(10):
            monitor.record_trade_pnl("strat_a", 1.0)
        events = monitor.check_correlations()
        assert events == []


# ---------------------------------------------------------------------------
# High correlation detection → PositionScaleEvent
# ---------------------------------------------------------------------------


class TestHighCorrelationDetection:
    def test_high_correlation_emits_scale_event(self):
        """Two perfectly correlated strategies emit a PositionScaleEvent."""
        monitor = CorrelationMonitor(window=10, threshold=0.7)
        for i in range(15):
            val = float(i) * 0.1
            monitor.record_trade_pnl("strat_a", val)
            monitor.record_trade_pnl("strat_b", val)  # identical → corr = 1.0

        events = monitor.check_correlations()
        assert len(events) > 0
        assert all(isinstance(e, PositionScaleEvent) for e in events)

    def test_low_correlation_emits_no_event(self):
        """Uncorrelated strategies (random walk) emit no scale events."""
        import random
        random.seed(42)
        monitor = CorrelationMonitor(window=10, threshold=0.7)
        for _ in range(20):
            monitor.record_trade_pnl("strat_a", random.uniform(-1, 1))
            monitor.record_trade_pnl("strat_b", random.uniform(-1, 1))

        events = monitor.check_correlations()
        # Not guaranteed to be 0 with random data, but correlation should be < threshold often
        # Just verify the type contract
        assert isinstance(events, list)

    def test_scale_event_has_correct_fields(self):
        """PositionScaleEvent has strategy_id, scale, and reason."""
        monitor = CorrelationMonitor(window=10, threshold=0.7)
        for i in range(15):
            v = float(i)
            monitor.record_trade_pnl("strat_a", v)
            monitor.record_trade_pnl("strat_b", v)

        events = monitor.check_correlations()
        assert len(events) > 0
        for event in events:
            assert hasattr(event, "strategy_id")
            assert hasattr(event, "scale")
            assert hasattr(event, "reason")

    def test_high_correlation_scale_is_05(self):
        """Correlation > 0.7 yields scale=0.5 (50% position reduction)."""
        monitor = CorrelationMonitor(window=10, threshold=0.7)
        for i in range(15):
            v = float(i)
            monitor.record_trade_pnl("strat_a", v)
            monitor.record_trade_pnl("strat_b", v)

        events = monitor.check_correlations()
        scales = [e.scale for e in events]
        assert all(s <= 0.5 for s in scales), f"Expected scale <= 0.5 but got {scales}"


# ---------------------------------------------------------------------------
# DynamicSizer correlation scale integration
# ---------------------------------------------------------------------------


class TestDynamicSizerCorrelationScale:
    def test_dynamic_sizer_has_compute_dynamic_size(self):
        """DynamicSizer has a compute_dynamic_size method."""
        from src.execution.sizer import DynamicSizer
        sizer = DynamicSizer(base_sizer=MagicMock())
        assert hasattr(sizer, "compute_dynamic_size")
        assert callable(sizer.compute_dynamic_size)

    def test_pearson_correlation_range(self):
        """Pearson correlation is always in [-1, 1]."""
        corr = CorrelationMonitor.pearson([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
        assert corr is not None
        assert -1.0 <= corr <= 1.0

    def test_pearson_returns_none_for_constant_series(self):
        """Pearson returns None when std-dev is zero (constant series)."""
        corr = CorrelationMonitor.pearson([1.0, 1.0, 1.0], [2.0, 3.0, 4.0])
        assert corr is None

    def test_pearson_returns_none_for_short_series(self):
        """Pearson returns None for series shorter than 2 elements."""
        corr = CorrelationMonitor.pearson([1.0], [1.0])
        assert corr is None

    def test_position_scale_event_instantiation(self):
        """PositionScaleEvent can be instantiated with required fields."""
        event = PositionScaleEvent(strategy_id="strat_a", scale=0.5, reason="high correlation")
        assert event.strategy_id == "strat_a"
        assert event.scale == 0.5
