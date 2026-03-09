"""Tests for AdaptiveThreshold (TDD - US-047).

Behavioral contracts:
  - WR < 50%           → current_edge_bps 상향 (+step)
  - WR > 90%           → current_edge_bps 하향 (-step)
  - 50% <= WR <= 90%  → 유지
  - total_trades < 10 → 스킵 (조정 없음)
  - edge 항상 [min_edge, max_edge] 범위 내 유지
  - 변경 시 history 기록, 변경 없으면 history 미기록
  - save_history → asyncpg conn.executemany 호출
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.tuning.adaptive_threshold import AdaptiveThreshold


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestAdaptiveThresholdInit:
    def test_default_values_are_sensible(self):
        """AdaptiveThreshold initializes with positive bps values and empty history."""
        at = AdaptiveThreshold()
        assert at.current_edge_bps > 0
        assert at.min_edge > 0
        assert at.max_edge > at.min_edge
        assert at.history == []

    def test_custom_values_are_stored(self):
        """AdaptiveThreshold stores custom initial_edge_bps, min_edge, max_edge."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        assert at.current_edge_bps == 10.0
        assert at.min_edge == 2.0
        assert at.max_edge == 50.0


# ---------------------------------------------------------------------------
# adjust — core behavior
# ---------------------------------------------------------------------------


class TestAdaptiveThresholdAdjust:
    def test_wr_below_50_increases_edge(self):
        """adjust raises current_edge_bps when WR < 50%."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        before = at.current_edge_bps
        at.adjust(win_rate=0.45, total_trades=20)
        assert at.current_edge_bps > before

    def test_wr_above_90_decreases_edge(self):
        """adjust lowers current_edge_bps when WR > 90%."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        before = at.current_edge_bps
        at.adjust(win_rate=0.95, total_trades=20)
        assert at.current_edge_bps < before

    def test_wr_between_50_and_90_keeps_edge_unchanged(self):
        """adjust leaves current_edge_bps unchanged when 50% <= WR <= 90%."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        before = at.current_edge_bps
        at.adjust(win_rate=0.70, total_trades=20)
        assert at.current_edge_bps == before

    def test_fewer_than_10_trades_skips_adjustment(self):
        """adjust performs no change when total_trades < 10."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        before = at.current_edge_bps
        at.adjust(win_rate=0.20, total_trades=5)
        assert at.current_edge_bps == before

    def test_clamp_prevents_exceeding_max_edge(self):
        """adjust clamps current_edge_bps to max_edge when increase would overshoot."""
        at = AdaptiveThreshold(initial_edge_bps=49.5, min_edge=2.0, max_edge=50.0, step_bps=5.0)
        at.adjust(win_rate=0.10, total_trades=20)
        assert at.current_edge_bps <= at.max_edge

    def test_clamp_prevents_going_below_min_edge(self):
        """adjust clamps current_edge_bps to min_edge when decrease would undershoot."""
        at = AdaptiveThreshold(initial_edge_bps=2.5, min_edge=2.0, max_edge=50.0, step_bps=5.0)
        at.adjust(win_rate=0.99, total_trades=100)
        assert at.current_edge_bps >= at.min_edge

    def test_records_history_entry_when_edge_changes(self):
        """adjust appends one entry to history when current_edge_bps changes."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        at.adjust(win_rate=0.45, total_trades=20)
        assert len(at.history) == 1

    def test_no_history_entry_when_edge_unchanged(self):
        """adjust does not append to history when current_edge_bps is unchanged."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        at.adjust(win_rate=0.70, total_trades=20)
        assert len(at.history) == 0

    def test_wr_exactly_50_keeps_edge_unchanged(self):
        """adjust leaves edge unchanged at exact WR=50% boundary (strict < 50%)."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        before = at.current_edge_bps
        at.adjust(win_rate=0.50, total_trades=20)
        assert at.current_edge_bps == before

    def test_wr_exactly_90_keeps_edge_unchanged(self):
        """adjust leaves edge unchanged at exact WR=90% boundary (strict > 90%)."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        before = at.current_edge_bps
        at.adjust(win_rate=0.90, total_trades=20)
        assert at.current_edge_bps == before


# ---------------------------------------------------------------------------
# save_history
# ---------------------------------------------------------------------------


class TestAdaptiveThresholdSaveHistory:
    @pytest.mark.asyncio
    async def test_save_history_calls_conn_executemany_with_history(self):
        """save_history persists history entries via asyncpg conn.executemany."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        at.adjust(win_rate=0.45, total_trades=20)  # create one history entry

        mock_conn = MagicMock()
        mock_conn.executemany = AsyncMock()

        await at.save_history(mock_conn)

        mock_conn.executemany.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_save_history_clears_history_after_success(self):
        """save_history clears self.history after successful write to prevent re-send."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        at.adjust(win_rate=0.45, total_trades=20)
        assert len(at.history) == 1

        mock_conn = MagicMock()
        mock_conn.executemany = AsyncMock()

        await at.save_history(mock_conn)
        assert len(at.history) == 0
