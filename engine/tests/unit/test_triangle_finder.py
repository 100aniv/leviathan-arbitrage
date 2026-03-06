"""Tests for TriangleFinder — graph construction and cycle detection."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.core.triangle_finder import PairRate, TriangleFinder, TrianglePath


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def pair(base: str, quote: str, price: str) -> PairRate:
    p = Decimal(price)
    return PairRate(
        base=base,
        quote=quote,
        bid=p,
        ask=p,
    )


def skewed_pair(base: str, quote: str, bid: str, ask: str) -> PairRate:
    return PairRate(base=base, quote=quote, bid=Decimal(bid), ask=Decimal(ask))


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


class TestTriangleFinderGraphConstruction:
    def test_currencies_populated_from_pairs(self):
        finder = TriangleFinder()
        finder.update([
            pair("BTC", "USDT", "50000"),
            pair("ETH", "USDT", "3000"),
            pair("ETH", "BTC", "0.06"),
        ])
        assert {"BTC", "USDT", "ETH"} <= finder.currencies

    def test_empty_rates_yields_no_currencies(self):
        finder = TriangleFinder()
        finder.update([])
        assert len(finder.currencies) == 0

    def test_update_replaces_previous_graph(self):
        finder = TriangleFinder()
        finder.update([pair("BTC", "USDT", "50000")])
        assert "BTC" in finder.currencies
        finder.update([pair("ETH", "USDT", "3000")])
        assert "ETH" in finder.currencies
        assert "BTC" not in finder.currencies  # replaced


# ---------------------------------------------------------------------------
# Cycle detection — no opportunity
# ---------------------------------------------------------------------------


class TestTriangleFinderNoOpportunity:
    def test_fair_market_no_profit(self):
        """Consistent rates: product of rates == 1 → no profit."""
        # BTC→USDT→ETH→BTC: 50000 * (1/3000) * 0.06 = 1.0 exactly
        finder = TriangleFinder(min_profit_pct=Decimal("0.0001"))
        rates = [
            pair("BTC", "USDT", "50000"),
            pair("ETH", "USDT", "3000"),
            pair("ETH", "BTC", "0.06"),
        ]
        paths = finder.find_triangles(rates)
        assert paths == []

    def test_below_min_profit_filtered(self):
        finder = TriangleFinder(min_profit_pct=Decimal("0.05"))
        # Create a slightly profitable triangle (< 5%)
        rates = [
            pair("BTC", "USDT", "50000"),
            pair("ETH", "BTC", "0.059"),
            pair("ETH", "USDT", "3000"),
        ]
        # Profit < 5% — filtered by min_profit_pct threshold
        # (1/0.059 * 3000 / 50000) - 1 ≈ 1.7% → below 5%
        paths = finder.find_triangles(rates)
        assert paths == []

    def test_insufficient_pairs_no_triangle(self):
        finder = TriangleFinder(min_profit_pct=Decimal("0.0"))
        # Only 2 currencies — no triangle possible
        rates = [pair("BTC", "USDT", "50000")]
        paths = finder.find_triangles(rates)
        assert paths == []


# ---------------------------------------------------------------------------
# Cycle detection — profitable opportunity
# ---------------------------------------------------------------------------


class TestTriangleFinderProfitableOpportunity:
    def test_misaligned_rates_detects_triangle(self):
        """ETH/BTC underpriced → BTC→ETH→USDT→BTC is profitable."""
        finder = TriangleFinder(min_profit_pct=Decimal("0.0"))
        rates = [
            pair("BTC", "USDT", "50000"),
            pair("ETH", "BTC", "0.059"),   # ETH cheap in BTC terms
            pair("ETH", "USDT", "3050"),   # ETH expensive in USDT terms
        ]
        # Rate USDT→BTC: 1/50000; BTC→ETH: 1/0.059; ETH→USDT: 3050
        # Product: (1/50000) * (1/0.059) * 3050 ≈ 1.0339 → +3.39%
        paths = finder.find_triangles(rates)
        assert len(paths) >= 1

    def test_profitable_path_has_three_currencies(self):
        finder = TriangleFinder(min_profit_pct=Decimal("0.0"))
        rates = [
            pair("BTC", "USDT", "50000"),
            pair("ETH", "BTC", "0.059"),
            pair("ETH", "USDT", "3050"),
        ]
        paths = finder.find_triangles(rates)
        assert len(paths) >= 1
        p = paths[0]
        assert len(p.currencies) == 3
        assert len(p.pairs) == 3
        assert len(p.sides) == 3
        assert len(p.rates) == 3

    def test_gross_profit_is_positive(self):
        finder = TriangleFinder(min_profit_pct=Decimal("0.0"))
        rates = [
            pair("BTC", "USDT", "50000"),
            pair("ETH", "BTC", "0.059"),
            pair("ETH", "USDT", "3050"),
        ]
        paths = finder.find_triangles(rates)
        assert paths[0].gross_profit_pct > Decimal("0")

    def test_gross_profit_magnitude_correct(self):
        """Verify profit calculation: (1/50000)*(1/0.059)*3050 - 1 ≈ 0.0339."""
        finder = TriangleFinder(min_profit_pct=Decimal("0.0"))
        rates = [
            pair("USDT", "BTC", str(Decimal("1") / Decimal("50000"))),
            # Easier: directly set rates as currency edges
        ]
        # Simpler: use find_triangles with rates that have known product
        rates2 = [
            pair("BTC", "USDT", "50000"),
            pair("ETH", "BTC", "0.059"),
            pair("ETH", "USDT", "3050"),
        ]
        paths = finder.find_triangles(rates2)
        assert len(paths) >= 1
        # Allow some tolerance: profit should be ~3.4%
        assert paths[0].gross_profit_pct > Decimal("0.03")
        assert paths[0].gross_profit_pct < Decimal("0.10")

    def test_pass_rates_directly_to_find_triangles(self):
        """find_triangles accepts rates list directly (no prior update)."""
        finder = TriangleFinder(min_profit_pct=Decimal("0.0"))
        rates = [
            pair("BTC", "USDT", "50000"),
            pair("ETH", "BTC", "0.059"),
            pair("ETH", "USDT", "3050"),
        ]
        paths = finder.find_triangles(rates)
        assert isinstance(paths, list)
        assert len(paths) >= 1

    def test_no_duplicate_cycles(self):
        """Same triangle should not appear twice (A→B→C same as A→C→B reversed)."""
        finder = TriangleFinder(min_profit_pct=Decimal("0.0"))
        rates = [
            pair("BTC", "USDT", "50000"),
            pair("ETH", "BTC", "0.059"),
            pair("ETH", "USDT", "3050"),
        ]
        paths = finder.find_triangles(rates)
        # Currency sets should be unique
        seen: set[frozenset] = set()
        for p in paths:
            key = frozenset(p.currencies)
            assert key not in seen
            seen.add(key)


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------


class TestTriangleFinderSorting:
    def test_paths_sorted_by_profit_descending(self):
        finder = TriangleFinder(min_profit_pct=Decimal("0.0"))
        rates = [
            pair("BTC", "USDT", "50000"),
            pair("ETH", "BTC", "0.059"),
            pair("ETH", "USDT", "3050"),
            pair("LTC", "USDT", "100"),
            pair("LTC", "BTC", "0.0018"),  # 1/0.0018 * 100 / 50000 ≈ 1.11 → +11%
        ]
        paths = finder.find_triangles(rates)
        for i in range(len(paths) - 1):
            assert paths[i].gross_profit_pct >= paths[i + 1].gross_profit_pct


# ---------------------------------------------------------------------------
# TrianglePath data integrity
# ---------------------------------------------------------------------------


class TestTrianglePath:
    def test_triangle_path_pairs_correspond_to_currencies(self):
        finder = TriangleFinder(min_profit_pct=Decimal("0.0"))
        rates = [
            pair("BTC", "USDT", "50000"),
            pair("ETH", "BTC", "0.059"),
            pair("ETH", "USDT", "3050"),
        ]
        paths = finder.find_triangles(rates)
        assert len(paths) >= 1
        p = paths[0]
        # All currencies in path should appear in at least one pair symbol
        for ccy in p.currencies:
            assert any(ccy in pair_str for pair_str in p.pairs)

    def test_triangle_path_sides_are_buy_or_sell(self):
        finder = TriangleFinder(min_profit_pct=Decimal("0.0"))
        rates = [
            pair("BTC", "USDT", "50000"),
            pair("ETH", "BTC", "0.059"),
            pair("ETH", "USDT", "3050"),
        ]
        paths = finder.find_triangles(rates)
        assert len(paths) >= 1
        for side in paths[0].sides:
            assert side in ("buy", "sell")
