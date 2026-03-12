"""Tests for DEXCostCalculator — US-087.

Covers:
  - LP_FEE_TIERS constant (4 tiers)
  - DEXCostCalculator: init, calculate (lp_fee, gas, bridge, mev, total)
  - DEXCost dataclass fields
"""
from decimal import Decimal

import pytest

from src.friction.dex_cost import (
    BRIDGE_COST_USD,
    DEXCost,
    DEXCostCalculator,
    LP_FEE_TIERS,
    MEV_ESTIMATE_BPS,
)


# ---------------------------------------------------------------------------
# 1. LP_FEE_TIERS constant
# ---------------------------------------------------------------------------


class TestLPFeeTiers:
    def test_four_tiers_exist(self):
        """LP_FEE_TIERS contains exactly 4 fee tiers."""
        assert len(LP_FEE_TIERS) == 4

    def test_tier_keys_are_100_500_3000_10000(self):
        """LP_FEE_TIERS keys are 100, 500, 3000, 10000."""
        assert set(LP_FEE_TIERS.keys()) == {100, 500, 3000, 10000}

    def test_exotic_tier_10000_is_100bps(self):
        """Fee tier 10000 maps to 1% (100 bps)."""
        assert LP_FEE_TIERS[10000] == Decimal("0.01")

    def test_standard_tier_3000_is_30bps(self):
        """Fee tier 3000 maps to 0.30%."""
        assert LP_FEE_TIERS[3000] == Decimal("0.003")


# ---------------------------------------------------------------------------
# 2-3. DEXCostCalculator init
# ---------------------------------------------------------------------------


class TestDEXCostCalculatorInit:
    def test_default_construction(self):
        """DEXCostCalculator can be instantiated with no arguments."""
        calc = DEXCostCalculator()
        assert calc is not None

    def test_custom_mev_bps_stored(self):
        """Custom mev_estimate_bps is stored on the instance."""
        calc = DEXCostCalculator(mev_estimate_bps=Decimal("5"))
        assert calc._mev_bps == Decimal("5")


# ---------------------------------------------------------------------------
# 4-10. DEXCostCalculator.calculate
# ---------------------------------------------------------------------------


class TestDEXCostCalculate:
    def test_lp_fee_tier_3000_notional_10000(self):
        """fee_tier=3000 on $10,000 notional → lp_fee=$30 (0.30%)."""
        calc = DEXCostCalculator()
        result = calc.calculate(Decimal("10000"), fee_tier=3000, gas_cost_usd=Decimal("10"))
        assert result.lp_fee == Decimal("30")

    def test_gas_cost_directly_specified(self):
        """Directly-specified gas_cost_usd is stored as-is."""
        calc = DEXCostCalculator()
        result = calc.calculate(Decimal("10000"), gas_cost_usd=Decimal("5"))
        assert result.gas_cost_usd == Decimal("5")

    def test_cross_chain_bridge_cost_positive(self):
        """Cross-chain swap (ethereum→polygon) incurs positive bridge cost."""
        calc = DEXCostCalculator()
        result = calc.calculate(
            Decimal("10000"),
            gas_cost_usd=Decimal("10"),
            source_chain="ethereum",
            dest_chain="polygon",
        )
        assert result.bridge_cost_usd > Decimal("0")

    def test_same_chain_bridge_cost_zero(self):
        """Same-chain swap has zero bridge cost."""
        calc = DEXCostCalculator()
        result = calc.calculate(
            Decimal("10000"),
            gas_cost_usd=Decimal("10"),
            source_chain="ethereum",
            dest_chain="ethereum",
        )
        assert result.bridge_cost_usd == Decimal("0")

    def test_total_cost_equals_sum_of_components(self):
        """total_cost_usd == lp_fee + gas + mev + bridge."""
        calc = DEXCostCalculator()
        result = calc.calculate(
            Decimal("10000"),
            fee_tier=3000,
            gas_cost_usd=Decimal("10"),
            source_chain="ethereum",
            dest_chain="ethereum",
        )
        expected = (
            result.lp_fee
            + result.gas_cost_usd
            + Decimal("10000") * MEV_ESTIMATE_BPS / Decimal("10000")
            + result.bridge_cost_usd
        )
        assert result.total_cost_usd == expected

    def test_total_cost_bps_positive(self):
        """total_cost_bps is positive for non-zero notional."""
        calc = DEXCostCalculator()
        result = calc.calculate(Decimal("10000"), fee_tier=3000, gas_cost_usd=Decimal("10"))
        assert result.total_cost_bps > Decimal("0")

    def test_stable_tier_100_lower_lp_fee_than_standard(self):
        """Stable tier 100 (1 bps) produces smaller lp_fee than standard 3000 (30 bps)."""
        calc = DEXCostCalculator()
        r_stable = calc.calculate(Decimal("10000"), fee_tier=100, gas_cost_usd=Decimal("10"))
        r_standard = calc.calculate(Decimal("10000"), fee_tier=3000, gas_cost_usd=Decimal("10"))
        assert r_stable.lp_fee < r_standard.lp_fee

    def test_lp_fee_tier_100_is_1bps_of_notional(self):
        """fee_tier=100 on $10,000 → lp_fee=$1 (1 bps)."""
        calc = DEXCostCalculator()
        result = calc.calculate(Decimal("10000"), fee_tier=100, gas_cost_usd=Decimal("5"))
        assert result.lp_fee == Decimal("1")

    def test_dex_cost_has_all_required_fields(self):
        """DEXCost dataclass exposes all 6 required fields."""
        calc = DEXCostCalculator()
        result = calc.calculate(Decimal("10000"), fee_tier=3000, gas_cost_usd=Decimal("10"))
        assert isinstance(result, DEXCost)
        assert hasattr(result, "lp_fee")
        assert hasattr(result, "gas_cost_usd")
        assert hasattr(result, "mev_cost_bps")
        assert hasattr(result, "bridge_cost_usd")
        assert hasattr(result, "total_cost_usd")
        assert hasattr(result, "total_cost_bps")

    def test_mev_cost_bps_matches_default_estimate(self):
        """mev_cost_bps in result equals the default MEV_ESTIMATE_BPS."""
        calc = DEXCostCalculator()
        result = calc.calculate(Decimal("10000"), fee_tier=3000, gas_cost_usd=Decimal("10"))
        assert result.mev_cost_bps == MEV_ESTIMATE_BPS
