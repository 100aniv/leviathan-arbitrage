"""Unit tests for ShadowMode.get_snapshot()."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.modes.shadow import ShadowMode, ShadowStats, StrategyStats


def make_shadow_mode() -> ShadowMode:
    """Create a minimal ShadowMode with a mocked signal generator."""
    sg = MagicMock()
    shadow = ShadowMode(signal_generator=sg)
    return shadow


class TestGetSnapshotDefaultStats:
    def test_get_snapshot_default_stats(self):
        """Initial snapshot has trades=0 and win_rate=0.0."""
        shadow = make_shadow_mode()
        snap = shadow.get_snapshot()
        assert snap["trades_executed"] == 0
        assert snap["win_rate"] == 0.0

    def test_get_snapshot_active_false_by_default(self):
        """active field is False when shadow is not running."""
        shadow = make_shadow_mode()
        shadow._running = False
        assert shadow.get_snapshot()["active"] is False

    def test_get_snapshot_active_true_when_running(self):
        """active field is True when shadow._running is set to True."""
        shadow = make_shadow_mode()
        shadow._running = True
        assert shadow.get_snapshot()["active"] is True


class TestGetSnapshotWithTrades:
    def test_get_snapshot_with_trades(self):
        """After trades, snapshot reflects trades/pnl/win_rate."""
        shadow = make_shadow_mode()
        shadow._stats.trades_executed = 10
        shadow._stats.trades_won = 7
        shadow._stats.trades_lost = 3
        shadow._stats.total_pnl = 42.5
        snap = shadow.get_snapshot()
        assert snap["trades_executed"] == 10
        assert snap["trades_won"] == 7
        assert snap["trades_lost"] == 3
        assert snap["win_rate"] == pytest.approx(0.7, abs=1e-4)
        assert snap["total_pnl"] == pytest.approx(42.5, abs=1e-4)

    def test_get_snapshot_pnl_rounded_to_6_places(self):
        """total_pnl is rounded to 6 decimal places."""
        shadow = make_shadow_mode()
        shadow._stats.trades_executed = 1
        shadow._stats.trades_won = 1
        shadow._stats.total_pnl = 1.123456789
        snap = shadow.get_snapshot()
        assert snap["total_pnl"] == round(1.123456789, 6)


class TestGetSnapshotByStrategy:
    def test_get_snapshot_by_strategy(self):
        """by_strategy list contains per-strategy data."""
        shadow = make_shadow_mode()
        shadow._stats.by_strategy["cross_exchange"] = StrategyStats(
            trades=5, wins=3, losses=2, pnl=10.0
        )
        snap = shadow.get_snapshot()
        assert isinstance(snap["by_strategy"], list)
        assert len(snap["by_strategy"]) == 1
        entry = snap["by_strategy"][0]
        assert entry["strategy_id"] == "cross_exchange"
        assert entry["trades"] == 5
        assert entry["wins"] == 3
        assert entry["win_rate"] == pytest.approx(0.6, abs=1e-4)
        assert entry["pnl"] == pytest.approx(10.0, abs=1e-4)

    def test_get_snapshot_by_strategy_sorted(self):
        """by_strategy entries are sorted alphabetically by strategy_id."""
        shadow = make_shadow_mode()
        shadow._stats.by_strategy["z_strategy"] = StrategyStats(trades=1, wins=1)
        shadow._stats.by_strategy["a_strategy"] = StrategyStats(trades=2, wins=2)
        snap = shadow.get_snapshot()
        ids = [e["strategy_id"] for e in snap["by_strategy"]]
        assert ids == sorted(ids)


class TestGetSnapshotWinRateZeroDivision:
    def test_get_snapshot_win_rate_zero_division(self):
        """win_rate returns 0.0 when trades_executed=0 — no ZeroDivisionError."""
        shadow = make_shadow_mode()
        shadow._stats.trades_executed = 0
        snap = shadow.get_snapshot()
        assert snap["win_rate"] == 0.0

    def test_get_snapshot_by_strategy_win_rate_zero_division(self):
        """Per-strategy win_rate returns 0.0 when strategy trades=0."""
        shadow = make_shadow_mode()
        shadow._stats.by_strategy["arb"] = StrategyStats(trades=0, wins=0)
        snap = shadow.get_snapshot()
        assert snap["by_strategy"][0]["win_rate"] == 0.0
