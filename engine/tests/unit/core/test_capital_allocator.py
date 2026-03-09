"""Tests for CapitalAllocator (US-049).

Behavioral contracts:
  - kelly_fraction: f* = (b*p - q) / b, returns 0 for negative edge
  - Half-Kelly: f* / 2
  - Allocations clamped to [min_allocation_pct, max_allocation_pct]
  - Normalized so total <= 100%
  - Strategies with < min_trades are skipped
  - save_config writes JSON to config path
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.capital_allocator import CapitalAllocator, StrategyAllocation


# ---------------------------------------------------------------------------
# Kelly Criterion formula
# ---------------------------------------------------------------------------


class TestKellyFraction:
    def test_positive_edge_returns_positive_fraction(self):
        """Profitable strategy → f* > 0."""
        f = CapitalAllocator.kelly_fraction(win_rate=0.60, avg_win=1.5, avg_loss=1.0)
        assert f > 0

    def test_known_kelly_value(self):
        """f* = (b*p - q) / b with b=2, p=0.6, q=0.4 → (1.2-0.4)/2 = 0.4."""
        f = CapitalAllocator.kelly_fraction(win_rate=0.6, avg_win=2.0, avg_loss=1.0)
        assert abs(f - 0.4) < 1e-10

    def test_negative_edge_returns_zero(self):
        """Losing strategy → f* = 0."""
        f = CapitalAllocator.kelly_fraction(win_rate=0.30, avg_win=1.0, avg_loss=2.0)
        assert f == 0.0

    def test_zero_avg_loss_returns_zero(self):
        """Division by zero guard: avg_loss=0 → 0."""
        f = CapitalAllocator.kelly_fraction(win_rate=0.5, avg_win=1.0, avg_loss=0.0)
        assert f == 0.0

    def test_zero_avg_win_returns_zero(self):
        """avg_win=0 → 0 (no positive returns)."""
        f = CapitalAllocator.kelly_fraction(win_rate=0.5, avg_win=0.0, avg_loss=1.0)
        assert f == 0.0

    def test_invalid_win_rate_returns_zero(self):
        """win_rate outside (0,1) → 0."""
        assert CapitalAllocator.kelly_fraction(0.0, 1.0, 1.0) == 0.0
        assert CapitalAllocator.kelly_fraction(1.0, 1.0, 1.0) == 0.0
        assert CapitalAllocator.kelly_fraction(-0.1, 1.0, 1.0) == 0.0

    def test_coin_flip_fair_odds_returns_zero(self):
        """50/50 with equal payoff → f* = 0 (no edge)."""
        f = CapitalAllocator.kelly_fraction(win_rate=0.5, avg_win=1.0, avg_loss=1.0)
        assert f == 0.0


# ---------------------------------------------------------------------------
# compute_allocations
# ---------------------------------------------------------------------------


class TestComputeAllocations:
    def _stats(self, wr=0.65, avg_w=1.5, avg_l=1.0, trades=100):
        return {"win_rate": wr, "avg_win": avg_w, "avg_loss": avg_l, "num_trades": trades}

    def test_single_strategy_allocation(self):
        """Single strategy gets Half-Kelly allocation."""
        ca = CapitalAllocator(total_capital=10000)
        allocs = ca.compute_allocations({"cross_exchange": self._stats()})
        assert len(allocs) == 1
        a = allocs[0]
        assert a.strategy_id == "cross_exchange"
        assert a.half_kelly == a.kelly_fraction / 2.0
        assert a.allocated_pct > 0

    def test_half_kelly_is_half_of_full(self):
        """Half-Kelly = f* / 2."""
        ca = CapitalAllocator()
        allocs = ca.compute_allocations({"s1": self._stats()})
        a = allocs[0]
        assert abs(a.half_kelly - a.kelly_fraction / 2.0) < 1e-10

    def test_allocation_clamped_to_max(self):
        """Allocation does not exceed max_allocation_pct."""
        ca = CapitalAllocator(max_allocation_pct=0.20)
        # High edge → large Kelly → should be clamped
        allocs = ca.compute_allocations({"s1": self._stats(wr=0.90, avg_w=5.0, avg_l=0.5)})
        assert allocs[0].allocated_pct <= 0.20

    def test_allocation_clamped_to_min(self):
        """Allocation does not go below min_allocation_pct."""
        ca = CapitalAllocator(min_allocation_pct=0.05)
        # Low edge → tiny Kelly → should be raised to min
        allocs = ca.compute_allocations({"s1": self._stats(wr=0.52, avg_w=1.01, avg_l=1.0)})
        if allocs:
            assert allocs[0].allocated_pct >= 0.05

    def test_skip_strategy_below_min_trades(self):
        """Strategies with insufficient trades are excluded."""
        ca = CapitalAllocator(min_trades=50)
        allocs = ca.compute_allocations({"s1": self._stats(trades=10)})
        assert len(allocs) == 0

    def test_multiple_strategies_normalized(self):
        """Multiple strategies: total allocation <= 100%."""
        ca = CapitalAllocator(max_allocation_pct=0.60)
        stats = {
            "s1": self._stats(wr=0.80, avg_w=3.0, avg_l=1.0),
            "s2": self._stats(wr=0.75, avg_w=2.5, avg_l=1.0),
            "s3": self._stats(wr=0.70, avg_w=2.0, avg_l=1.0),
        }
        allocs = ca.compute_allocations(stats)
        total = sum(a.allocated_pct for a in allocs)
        assert total <= 1.0 + 1e-10

    def test_independent_strategy_allocations(self):
        """Different strategies get different allocations based on their stats."""
        ca = CapitalAllocator()
        stats = {
            "high_edge": self._stats(wr=0.80, avg_w=3.0, avg_l=1.0),
            "low_edge": self._stats(wr=0.55, avg_w=1.1, avg_l=1.0),
        }
        allocs = ca.compute_allocations(stats)
        by_id = {a.strategy_id: a for a in allocs}
        assert by_id["high_edge"].kelly_fraction > by_id["low_edge"].kelly_fraction

    def test_negative_edge_strategy_gets_minimum(self):
        """Strategy with no edge gets min allocation (Kelly=0)."""
        ca = CapitalAllocator(min_allocation_pct=0.02)
        allocs = ca.compute_allocations({
            "loser": self._stats(wr=0.30, avg_w=1.0, avg_l=2.0),
        })
        if allocs:
            assert allocs[0].allocated_pct == 0.02


# ---------------------------------------------------------------------------
# save_config
# ---------------------------------------------------------------------------


class TestSaveConfig:
    def test_save_creates_json_file(self, tmp_path: Path):
        """save_config writes capital_allocation.json with strategy data."""
        config_file = tmp_path / "capital_allocation.json"
        ca = CapitalAllocator(total_capital=10000, config_path=str(config_file))

        allocs = [
            StrategyAllocation(
                strategy_id="cross_exchange_v1",
                kelly_fraction=0.30,
                half_kelly=0.15,
                allocated_pct=0.15,
                win_rate=0.65,
                avg_win=1.5,
                avg_loss=1.0,
            )
        ]
        ca.save_config(allocs)

        assert config_file.exists()
        data = json.loads(config_file.read_text())
        assert "cross_exchange_v1" in data
        assert data["cross_exchange_v1"]["allocated_usd"] == 1500.0

    def test_save_overwrites_existing(self, tmp_path: Path):
        """save_config overwrites existing file with fresh data."""
        config_file = tmp_path / "capital_allocation.json"
        config_file.write_text('{"old": {}}')

        ca = CapitalAllocator(config_path=str(config_file))
        ca.save_config([
            StrategyAllocation("new_strat", 0.2, 0.1, 0.1, 0.6, 1.5, 1.0)
        ])

        data = json.loads(config_file.read_text())
        assert "new_strat" in data
        assert "old" not in data


# ---------------------------------------------------------------------------
# get_allocation_usd
# ---------------------------------------------------------------------------


class TestGetAllocationUsd:
    def test_returns_correct_usd_amount(self):
        """get_allocation_usd returns allocated_pct * total_capital."""
        ca = CapitalAllocator(total_capital=50000)
        allocs = [StrategyAllocation("s1", 0.3, 0.15, 0.15, 0.6, 1.5, 1.0)]
        assert ca.get_allocation_usd(allocs, "s1") == 7500.0

    def test_unknown_strategy_returns_zero(self):
        """Unknown strategy_id → 0.0 USD."""
        ca = CapitalAllocator(total_capital=10000)
        assert ca.get_allocation_usd([], "nonexistent") == 0.0
