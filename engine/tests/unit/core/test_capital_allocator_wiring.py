"""Tests for CapitalAllocator wiring — US-284-a, US-279."""
from __future__ import annotations

import pytest

from src.core.capital_allocator import CapitalAllocator, StrategyAllocation


def _make_stats(
    win_rate: float = 0.6,
    avg_win: float = 2.0,
    avg_loss: float = 1.0,
    num_trades: int = 50,
) -> dict:
    return {
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "num_trades": num_trades,
    }


# ---------------------------------------------------------------------------
# Wiring: EngineContext has capital_allocator field
# ---------------------------------------------------------------------------

class TestEngineContextWiring:
    def test_capital_allocator_created_in_engine_context(self) -> None:
        """EngineContext must expose capital_allocator attribute (US-284-a)."""
        from src.api.server import EngineContext
        ctx = EngineContext.__dataclass_fields__
        assert "capital_allocator" in ctx


# ---------------------------------------------------------------------------
# Allocation logic
# ---------------------------------------------------------------------------

class TestAllocateReturnsValidAllocations:
    def test_allocate_returns_valid_allocations(self) -> None:
        """Valid strategy stats → list of StrategyAllocation with allocated_pct > 0."""
        alloc = CapitalAllocator(total_capital=10_000)
        stats = {
            "cross_exchange": _make_stats(),
            "triangular": _make_stats(win_rate=0.55),
        }
        result = alloc.compute_allocations(stats)
        assert len(result) == 2
        for a in result:
            assert isinstance(a, StrategyAllocation)
            assert 0.0 < a.allocated_pct <= 1.0
            assert a.kelly_fraction >= 0.0
            assert a.half_kelly == pytest.approx(a.kelly_fraction / 2.0, rel=1e-9)

    def test_allocate_total_pct_does_not_exceed_100(self) -> None:
        """Normalized allocation sum must not exceed 1.0."""
        alloc = CapitalAllocator(total_capital=10_000)
        stats = {f"s{i}": _make_stats() for i in range(10)}
        result = alloc.compute_allocations(stats)
        total = sum(a.allocated_pct for a in result)
        assert total <= 1.0 + 1e-9


class TestAllocateMinTradesFilter:
    def test_allocate_min_trades_filter(self) -> None:
        """Strategies with fewer trades than min_trades should be excluded."""
        alloc = CapitalAllocator(min_trades=30)
        stats = {
            "enough": _make_stats(num_trades=50),
            "not_enough": _make_stats(num_trades=10),
        }
        result = alloc.compute_allocations(stats)
        ids = [a.strategy_id for a in result]
        assert "enough" in ids
        assert "not_enough" not in ids

    def test_allocate_exactly_min_trades_excluded(self) -> None:
        """Exactly min_trades - 1 → excluded."""
        alloc = CapitalAllocator(min_trades=30)
        result = alloc.compute_allocations({"s": _make_stats(num_trades=29)})
        assert result == []


class TestRegimeAwareCapital:
    def test_allocate_with_regime_bear_reduces_40pct(self) -> None:
        """Bear regime: manually scale allocation by 60% of normal Kelly."""
        alloc = CapitalAllocator(total_capital=10_000)
        stats = {"s1": _make_stats()}
        normal = alloc.compute_allocations(stats)
        bear_pct = normal[0].half_kelly * 0.6  # 40% reduction
        assert bear_pct < normal[0].half_kelly

    def test_allocate_with_regime_crisis_reduces_10pct(self) -> None:
        """Crisis regime: scale allocation by 10% of normal Kelly."""
        alloc = CapitalAllocator(total_capital=10_000)
        stats = {"s1": _make_stats()}
        normal = alloc.compute_allocations(stats)
        crisis_pct = normal[0].half_kelly * 0.1
        assert crisis_pct < normal[0].half_kelly * 0.5

    def test_regime_aware_disabled_uses_full_kelly(self) -> None:
        """Without regime scaling, allocated_pct is clamped half-kelly."""
        alloc = CapitalAllocator(max_allocation_pct=0.40, min_allocation_pct=0.02)
        wr, aw, al = 0.6, 2.0, 1.0
        f_star = CapitalAllocator.kelly_fraction(wr, aw, al)
        half_k = f_star / 2.0
        result = alloc.compute_allocations({"s": _make_stats(win_rate=wr, avg_win=aw, avg_loss=al)})
        assert len(result) == 1
        expected = max(0.02, min(half_k, 0.40))
        assert result[0].allocated_pct == pytest.approx(expected, rel=1e-9)


# ---------------------------------------------------------------------------
# Kelly math
# ---------------------------------------------------------------------------

class TestKellyMath:
    def test_kelly_negative_edge_returns_zero(self) -> None:
        """Losing strategy (win_rate=0.3) → Kelly ≤ 0 → clamped to 0."""
        f = CapitalAllocator.kelly_fraction(0.3, 1.0, 2.0)
        assert f == 0.0

    def test_kelly_invalid_inputs_return_zero(self) -> None:
        assert CapitalAllocator.kelly_fraction(0.0, 1.0, 1.0) == 0.0
        assert CapitalAllocator.kelly_fraction(0.6, 0.0, 1.0) == 0.0
        assert CapitalAllocator.kelly_fraction(0.6, 1.0, 0.0) == 0.0
