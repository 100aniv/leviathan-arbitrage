"""US-174: AdaptiveThreshold — instantiation and 1-hour adjust cycle."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.tuning.adaptive_threshold import AdaptiveThreshold


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------


class TestAdaptiveThresholdInstantiation:
    def test_instantiates_without_arguments(self):
        """AdaptiveThreshold instantiates with no args."""
        at = AdaptiveThreshold()
        assert at is not None

    def test_has_current_edge_bps(self):
        """AdaptiveThreshold has current_edge_bps attribute > 0."""
        at = AdaptiveThreshold()
        assert at.current_edge_bps > 0

    def test_has_history_list(self):
        """AdaptiveThreshold has empty history on init."""
        at = AdaptiveThreshold()
        assert isinstance(at.history, list)
        assert len(at.history) == 0

    def test_custom_initial_edge_stored(self):
        """Custom initial_edge_bps is stored correctly."""
        at = AdaptiveThreshold(initial_edge_bps=15.0)
        assert at.current_edge_bps == 15.0


# ---------------------------------------------------------------------------
# adjust() — core rules
# ---------------------------------------------------------------------------


class TestAdjustBehavior:
    def test_low_win_rate_increases_edge(self):
        """WR < 50% with enough trades raises edge threshold."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        before = at.current_edge_bps
        at.adjust(win_rate=0.35, total_trades=20)
        assert at.current_edge_bps > before

    def test_high_win_rate_decreases_edge(self):
        """WR > 90% with enough trades lowers edge threshold."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        before = at.current_edge_bps
        at.adjust(win_rate=0.95, total_trades=20)
        assert at.current_edge_bps < before

    def test_insufficient_trades_skips_adjustment(self):
        """adjust() with trades < 10 makes no change."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        before = at.current_edge_bps
        at.adjust(win_rate=0.20, total_trades=5)
        assert at.current_edge_bps == before

    def test_exactly_9_trades_skips_adjustment(self):
        """adjust() with exactly 9 trades (< 10) makes no change."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        before = at.current_edge_bps
        at.adjust(win_rate=0.20, total_trades=9)
        assert at.current_edge_bps == before

    def test_exactly_10_trades_triggers_adjustment(self):
        """adjust() with exactly 10 trades (>= 10) makes adjustment."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        before = at.current_edge_bps
        at.adjust(win_rate=0.20, total_trades=10)
        assert at.current_edge_bps != before

    def test_edge_never_exceeds_max(self):
        """Edge is clamped to max_edge after increase."""
        at = AdaptiveThreshold(initial_edge_bps=49.5, min_edge=2.0, max_edge=50.0, step_bps=5.0)
        at.adjust(win_rate=0.10, total_trades=50)
        assert at.current_edge_bps <= at.max_edge

    def test_edge_never_falls_below_min(self):
        """Edge is clamped to min_edge after decrease."""
        at = AdaptiveThreshold(initial_edge_bps=2.2, min_edge=2.0, max_edge=50.0, step_bps=5.0)
        at.adjust(win_rate=0.99, total_trades=50)
        assert at.current_edge_bps >= at.min_edge


# ---------------------------------------------------------------------------
# Hourly cycle simulation
# ---------------------------------------------------------------------------


class TestHourlyCycle:
    def test_multiple_adjust_calls_accumulate(self):
        """Multiple adjust() calls compound edge changes correctly."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        for _ in range(5):
            at.adjust(win_rate=0.40, total_trades=20)
        # After 5 low-WR adjustments, edge must be higher than initial
        assert at.current_edge_bps > 10.0

    def test_history_records_each_change(self):
        """Each successful adjust() that changes edge appends a history entry."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        at.adjust(win_rate=0.40, total_trades=20)
        at.adjust(win_rate=0.40, total_trades=20)
        assert len(at.history) >= 1  # at least one change recorded
