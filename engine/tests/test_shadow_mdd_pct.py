"""Tests for US-148: ShadowStats.max_drawdown_pct — percentage-based MDD tracking.

Verifies:
- max_drawdown_pct calculated as (peak_pnl - total_pnl) / peak_pnl
- peak_pnl < 0.01 guard: pct MDD stays 0.0 to avoid division by tiny value
- dd_pct clamped to 1.0 (cannot exceed 100%)
- get_snapshot() includes max_drawdown_pct field

Run:
    cd engine && python -m pytest tests/test_shadow_mdd_pct.py -x --tb=short -v
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.modes.shadow import ShadowMode, ShadowStats


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_shadow_mode() -> ShadowMode:
    """Build a ShadowMode with mocked I/O dependencies (no real connections)."""
    mock_executor = MagicMock()
    mock_executor.slippage_model = MagicMock(spec=[])
    return ShadowMode(
        signal_generator=MagicMock(),
        paper_executor=mock_executor,
    )


# ===========================================================================
# _compute_drawdown() — percentage MDD calculation
# ===========================================================================


class TestComputeDrawdownPct:
    """Tests for _compute_drawdown() percentage-based MDD tracking."""

    def test_mdd_pct_calculated_correctly_from_peak_and_current_pnl(self):
        """max_drawdown_pct = (peak_pnl - total_pnl) / peak_pnl when in drawdown."""
        shadow = _make_shadow_mode()
        # Simulate: peak at 100, dropped to 50 → MDD = 50%
        shadow._stats.peak_pnl = 100.0
        shadow._stats.total_pnl = 50.0

        shadow._compute_drawdown()

        assert shadow._stats.max_drawdown_pct == pytest.approx(0.5)

    def test_mdd_pct_zero_when_no_drawdown(self):
        """max_drawdown_pct remains 0 when total_pnl >= peak_pnl (no drawdown)."""
        shadow = _make_shadow_mode()
        shadow._stats.peak_pnl = 100.0
        shadow._stats.total_pnl = 110.0  # New high — no drawdown

        shadow._compute_drawdown()

        assert shadow._stats.max_drawdown_pct == pytest.approx(0.0)

    def test_peak_pnl_guard_prevents_division_when_peak_tiny(self):
        """max_drawdown_pct stays 0.0 when peak_pnl <= 0.01 (division guard)."""
        shadow = _make_shadow_mode()
        shadow._stats.peak_pnl = 0.005  # < 0.01 guard
        shadow._stats.total_pnl = -1.0

        shadow._compute_drawdown()

        assert shadow._stats.max_drawdown_pct == pytest.approx(0.0)

    def test_mdd_pct_clamped_to_one_when_drawdown_exceeds_100pct(self):
        """max_drawdown_pct is clamped to 1.0 even if drawdown > peak."""
        shadow = _make_shadow_mode()
        shadow._stats.peak_pnl = 10.0
        shadow._stats.total_pnl = -50.0  # drawdown > peak → would be > 1.0

        shadow._compute_drawdown()

        assert shadow._stats.max_drawdown_pct <= 1.0
        assert shadow._stats.max_drawdown_pct == pytest.approx(1.0)

    def test_peak_pnl_updated_when_new_high_reached(self):
        """peak_pnl updated when total_pnl exceeds current peak."""
        shadow = _make_shadow_mode()
        shadow._stats.peak_pnl = 50.0
        shadow._stats.total_pnl = 75.0  # New peak

        shadow._compute_drawdown()

        assert shadow._stats.peak_pnl == pytest.approx(75.0)

    def test_mdd_pct_tracks_maximum_not_current_drawdown(self):
        """max_drawdown_pct retains the historical maximum, not just the current."""
        shadow = _make_shadow_mode()

        # First drawdown: 100 → 40 = 60% MDD
        shadow._stats.peak_pnl = 100.0
        shadow._stats.total_pnl = 40.0
        shadow._compute_drawdown()
        assert shadow._stats.max_drawdown_pct == pytest.approx(0.6)

        # Recovery: peak stays, pnl recovers
        shadow._stats.total_pnl = 80.0
        shadow._compute_drawdown()

        # MDD should still be 0.6 (historical max)
        assert shadow._stats.max_drawdown_pct == pytest.approx(0.6)


# ===========================================================================
# get_snapshot() includes max_drawdown_pct
# ===========================================================================


class TestGetSnapshotIncludesMddPct:
    """get_snapshot() exposes max_drawdown_pct in the result dict."""

    def test_get_snapshot_contains_max_drawdown_pct_key(self):
        """get_snapshot() dict includes max_drawdown_pct field."""
        shadow = _make_shadow_mode()
        snapshot = shadow.get_snapshot()

        assert "max_drawdown_pct" in snapshot

    def test_get_snapshot_max_drawdown_pct_reflects_computed_value(self):
        """get_snapshot() max_drawdown_pct matches the value from _compute_drawdown."""
        shadow = _make_shadow_mode()
        shadow._stats.peak_pnl = 200.0
        shadow._stats.total_pnl = 150.0  # 25% drawdown
        shadow._compute_drawdown()

        snapshot = shadow.get_snapshot()

        assert snapshot["max_drawdown_pct"] == pytest.approx(0.25, abs=1e-4)

    def test_get_snapshot_max_drawdown_pct_zero_initially(self):
        """get_snapshot() max_drawdown_pct is 0.0 on fresh ShadowMode (no trades yet)."""
        shadow = _make_shadow_mode()
        snapshot = shadow.get_snapshot()

        assert snapshot["max_drawdown_pct"] == pytest.approx(0.0)
