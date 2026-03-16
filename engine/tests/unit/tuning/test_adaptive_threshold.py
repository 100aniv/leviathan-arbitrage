"""Tests for AdaptiveThreshold (TDD - US-047, US-201).

Behavioral contracts:
  - WR < 50%           → current_edge_bps 상향 (+step)
  - WR > 90%           → current_edge_bps 하향 (-step)
  - 50% <= WR <= 90%  → 유지
  - total_trades < 30 → 스킵 (조정 없음)
  - edge 항상 [min_edge, max_edge] 범위 내 유지
  - 변경 시 history 기록, 변경 없으면 history 미기록
  - save_history → asyncpg conn.executemany 호출
  - US-201: 복합 지표 (expected_edge_bps + profit_factor) 기반 조정
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
# adjust — core WR-fallback behavior
# ---------------------------------------------------------------------------


class TestAdaptiveThresholdAdjust:
    def test_wr_below_50_increases_edge(self):
        """adjust raises current_edge_bps when WR < 50%."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        before = at.current_edge_bps
        at.adjust(win_rate=0.45, total_trades=30)
        assert at.current_edge_bps > before

    def test_wr_above_90_decreases_edge(self):
        """adjust lowers current_edge_bps when WR > 90%."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        before = at.current_edge_bps
        at.adjust(win_rate=0.95, total_trades=30)
        assert at.current_edge_bps < before

    def test_wr_between_50_and_90_keeps_edge_unchanged(self):
        """adjust leaves current_edge_bps unchanged when 50% <= WR <= 90%."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        before = at.current_edge_bps
        at.adjust(win_rate=0.70, total_trades=30)
        assert at.current_edge_bps == before

    def test_fewer_than_30_trades_skips_adjustment(self):
        """adjust performs no change when total_trades < 30."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        before = at.current_edge_bps
        at.adjust(win_rate=0.20, total_trades=5)
        assert at.current_edge_bps == before

    def test_clamp_prevents_exceeding_max_edge(self):
        """adjust clamps current_edge_bps to max_edge when increase would overshoot."""
        at = AdaptiveThreshold(initial_edge_bps=49.5, min_edge=2.0, max_edge=50.0, step_bps=5.0)
        at.adjust(win_rate=0.10, total_trades=30)
        assert at.current_edge_bps <= at.max_edge

    def test_clamp_prevents_going_below_min_edge(self):
        """adjust clamps current_edge_bps to min_edge when decrease would undershoot."""
        at = AdaptiveThreshold(initial_edge_bps=2.5, min_edge=2.0, max_edge=50.0, step_bps=5.0)
        at.adjust(win_rate=0.99, total_trades=100)
        assert at.current_edge_bps >= at.min_edge

    def test_records_history_entry_when_edge_changes(self):
        """adjust appends one entry to history when current_edge_bps changes."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        at.adjust(win_rate=0.45, total_trades=30)
        assert len(at.history) == 1

    def test_no_history_entry_when_edge_unchanged(self):
        """adjust does not append to history when current_edge_bps is unchanged."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        at.adjust(win_rate=0.70, total_trades=30)
        assert len(at.history) == 0

    def test_wr_exactly_50_keeps_edge_unchanged(self):
        """adjust leaves edge unchanged at exact WR=50% boundary (strict < 50%)."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        before = at.current_edge_bps
        at.adjust(win_rate=0.50, total_trades=30)
        assert at.current_edge_bps == before

    def test_wr_exactly_90_keeps_edge_unchanged(self):
        """adjust leaves edge unchanged at exact WR=90% boundary (strict > 90%)."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        before = at.current_edge_bps
        at.adjust(win_rate=0.90, total_trades=30)
        assert at.current_edge_bps == before


# ---------------------------------------------------------------------------
# save_history
# ---------------------------------------------------------------------------


class TestAdaptiveThresholdSaveHistory:
    @pytest.mark.asyncio
    async def test_save_history_calls_conn_executemany_with_history(self):
        """save_history persists history entries via asyncpg conn.executemany."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        at.adjust(win_rate=0.45, total_trades=30)  # create one history entry

        mock_conn = MagicMock()
        mock_conn.executemany = AsyncMock()

        await at.save_history(mock_conn)

        mock_conn.executemany.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_save_history_clears_history_after_success(self):
        """save_history clears self.history after successful write to prevent re-send."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        at.adjust(win_rate=0.45, total_trades=30)
        assert len(at.history) == 1

        mock_conn = MagicMock()
        mock_conn.executemany = AsyncMock()

        await at.save_history(mock_conn)
        assert len(at.history) == 0


# ---------------------------------------------------------------------------
# US-201: composite indicator logic
# ---------------------------------------------------------------------------


class TestAdaptiveThresholdComposite:
    def test_edge_negative_raises_threshold(self):
        """edge < 0 → threshold 공격적 상향 (max(2, current*0.5))."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        before = at.current_edge_bps
        at.adjust(win_rate=0.5, total_trades=30, expected_edge_bps=-2.0, profit_factor=1.2)
        assert at.current_edge_bps > before

    def test_pf_below_one_raises_threshold(self):
        """profit_factor < 1.0 → threshold 상향 (+2 bps)."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        before = at.current_edge_bps
        # edge in neutral zone (1.0 <= edge <= 5.0), PF bad
        at.adjust(win_rate=0.5, total_trades=30, expected_edge_bps=3.0, profit_factor=0.5)
        assert at.current_edge_bps > before

    def test_edge_high_pf_high_lowers_threshold(self):
        """edge > 5.0 AND profit_factor > 1.5 → threshold 소극적 하향 (-0.5 bps)."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        before = at.current_edge_bps
        at.adjust(win_rate=0.5, total_trades=30, expected_edge_bps=6.0, profit_factor=2.0)
        assert at.current_edge_bps < before
        assert at.current_edge_bps == pytest.approx(before - 0.5)

    def test_backward_compatible_wr_only(self):
        """edge=None, pf=None → 기존 WR 기반 로직 그대로 동작."""
        at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
        before = at.current_edge_bps
        # WR > 90% without composite params → should decrease
        at.adjust(win_rate=0.95, total_trades=30, expected_edge_bps=None, profit_factor=None)
        assert at.current_edge_bps < before

    def test_min_max_bounds_composite(self):
        """복합 지표 사용 시 threshold가 [min_edge, max_edge] 범위를 벗어나지 않음."""
        # Test lower bound: start near min, edge < 0 should not go below 2.0
        at_low = AdaptiveThreshold(initial_edge_bps=2.1, min_edge=2.0, max_edge=50.0)
        at_low.adjust(win_rate=0.5, total_trades=30, expected_edge_bps=-5.0, profit_factor=0.3)
        assert at_low.current_edge_bps >= 2.0

        # Test upper bound: start near max, edge < 0 should not exceed 50.0
        at_high = AdaptiveThreshold(initial_edge_bps=48.0, min_edge=2.0, max_edge=50.0)
        at_high.adjust(win_rate=0.5, total_trades=30, expected_edge_bps=-5.0, profit_factor=0.3)
        assert at_high.current_edge_bps <= 50.0
