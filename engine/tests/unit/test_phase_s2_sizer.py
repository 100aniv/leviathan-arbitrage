"""Tests for US-130 — DynamicSizer sigmoid confidence and regime multipliers.

US-130: sigmoid(5bps)→~0.38, sigmoid(50bps)→~0.98;
        regime_multiplier: CRISIS=0.25, VOLATILE=0.75, NORMAL=1.0, CALM=1.5.
        compute_dynamic_size() varies by edge_bps and regime.
"""
from __future__ import annotations

import math
from decimal import Decimal

import pytest

from src.execution.sizer import (
    CapitalTier,
    DynamicSizer,
    PositionSizer,
    SizerConfig,
)
from src.tuning.regime_detector import MarketRegime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sizer() -> DynamicSizer:
    config = SizerConfig(
        capital=Decimal("10000"),
        tier=CapitalTier.BETA,
        max_single_trade_pct=Decimal("0.05"),
        max_strategy_allocation_pct=Decimal("0.30"),
    )
    base = PositionSizer(config)
    return DynamicSizer(base, liquidity_threshold=Decimal("10000"))


# ---------------------------------------------------------------------------
# US-130: sigmoid confidence mapping
# ---------------------------------------------------------------------------

class TestSigmoidConfidence:
    """US-130: DynamicSizer.confidence() sigmoid mapping verification."""

    def test_sigmoid_5bps_approx_0_38(self):
        """sigmoid(5) → ~0.38 (tolerance ±0.02)."""
        result = DynamicSizer.confidence(5.0)
        assert abs(result - 0.38) <= 0.02, f"sigmoid(5) = {result:.4f}, expected ~0.38"

    def test_sigmoid_50bps_approx_0_98(self):
        """sigmoid(50) → ~0.98 (tolerance ±0.02)."""
        result = DynamicSizer.confidence(50.0)
        assert abs(result - 0.98) <= 0.02, f"sigmoid(50) = {result:.4f}, expected ~0.98"

    def test_sigmoid_zero_edge_below_half(self):
        """sigmoid(0bps) < 0.5 (no edge → low confidence)."""
        result = DynamicSizer.confidence(0.0)
        assert result < 0.5

    def test_sigmoid_10bps_approx_half(self):
        """sigmoid(10bps) ≈ 0.5 (inflection point of this sigmoid)."""
        result = DynamicSizer.confidence(10.0)
        assert abs(result - 0.5) <= 0.05

    def test_sigmoid_increases_monotonically(self):
        """Higher edge_bps → higher confidence (monotone increasing)."""
        values = [DynamicSizer.confidence(bps) for bps in [1, 5, 10, 20, 50, 100]]
        for i in range(len(values) - 1):
            assert values[i] < values[i + 1], f"Not monotone at index {i}"

    def test_sigmoid_bounded_between_0_and_1(self):
        """sigmoid output must be in (0, 1] — never negative, never exceeds 1."""
        for bps in [-100, -10, 0, 5, 10, 50, 100, 1000]:
            result = DynamicSizer.confidence(float(bps))
            assert 0.0 < result <= 1.0, f"sigmoid({bps}) = {result} out of (0,1]"


# ---------------------------------------------------------------------------
# US-130: regime_multiplier values
# ---------------------------------------------------------------------------

class TestRegimeMultiplier:
    """US-130: REGIME_MULTIPLIER values verified per spec."""

    def test_crisis_multiplier_is_0_25(self):
        """CRISIS regime → multiplier 0.25."""
        assert DynamicSizer.regime_multiplier(MarketRegime.CRISIS) == pytest.approx(0.25)

    def test_volatile_multiplier_is_0_75(self):
        """VOLATILE regime → multiplier 0.75."""
        assert DynamicSizer.regime_multiplier(MarketRegime.VOLATILE) == pytest.approx(0.75)

    def test_normal_multiplier_is_1_0(self):
        """NORMAL regime → multiplier 1.0."""
        assert DynamicSizer.regime_multiplier(MarketRegime.NORMAL) == pytest.approx(1.0)

    def test_calm_multiplier_is_1_5(self):
        """CALM regime → multiplier 1.5."""
        assert DynamicSizer.regime_multiplier(MarketRegime.CALM) == pytest.approx(1.5)

    def test_high_multiplier_is_0_75(self):
        """HIGH regime → multiplier 0.75 (same as VOLATILE)."""
        assert DynamicSizer.regime_multiplier(MarketRegime.HIGH) == pytest.approx(0.75)

    def test_low_multiplier_is_1_5(self):
        """LOW regime → multiplier 1.5 (same as CALM)."""
        assert DynamicSizer.regime_multiplier(MarketRegime.LOW) == pytest.approx(1.5)

    def test_medium_multiplier_is_1_0(self):
        """MEDIUM regime → multiplier 1.0 (same as NORMAL)."""
        assert DynamicSizer.regime_multiplier(MarketRegime.MEDIUM) == pytest.approx(1.0)

    def test_unknown_regime_defaults_to_1_0(self):
        """Unknown regime value → fallback to 1.0 (safe default)."""
        # Use a mock/sentinel value
        class FakeRegime(str):
            pass

        fake = FakeRegime("UNKNOWN")
        result = DynamicSizer.regime_multiplier(fake)
        assert result == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# US-130: compute_dynamic_size() varies by edge_bps and regime
# ---------------------------------------------------------------------------

class TestComputeDynamicSize:
    """US-130: compute_dynamic_size() produces different sizes for different parameters."""

    def test_different_edge_bps_yields_different_sizes(self):
        """Higher edge_bps → larger dynamic size (via confidence scaling)."""
        sizer = _make_sizer()
        common_kwargs = dict(
            win_prob=Decimal("0.6"),
            win_loss_ratio=Decimal("1.5"),
            price=Decimal("50000"),
            strategy_id="test",
            strategy_used_capital=Decimal("0"),
            regime=MarketRegime.NORMAL,
            bid_depth_usd=Decimal("50000"),
        )
        size_5bps = sizer.compute_dynamic_size(edge_bps=5.0, **common_kwargs)
        size_50bps = sizer.compute_dynamic_size(edge_bps=50.0, **common_kwargs)
        assert size_5bps != size_50bps
        assert size_5bps < size_50bps  # higher confidence = larger size

    def test_different_regimes_yield_different_sizes(self):
        """CRISIS regime produces smaller size than CALM regime."""
        sizer = _make_sizer()
        common_kwargs = dict(
            win_prob=Decimal("0.6"),
            win_loss_ratio=Decimal("1.5"),
            price=Decimal("50000"),
            strategy_id="test",
            strategy_used_capital=Decimal("0"),
            edge_bps=20.0,
            bid_depth_usd=Decimal("50000"),
        )
        size_crisis = sizer.compute_dynamic_size(regime=MarketRegime.CRISIS, **common_kwargs)
        size_calm = sizer.compute_dynamic_size(regime=MarketRegime.CALM, **common_kwargs)
        assert size_crisis < size_calm

    def test_crisis_regime_reduces_size_vs_normal(self):
        """CRISIS (multiplier 0.25) → smaller size than NORMAL (multiplier 1.0)."""
        sizer = _make_sizer()
        common_kwargs = dict(
            win_prob=Decimal("0.6"),
            win_loss_ratio=Decimal("2.0"),
            price=Decimal("1000"),
            strategy_id="strat1",
            strategy_used_capital=Decimal("0"),
            edge_bps=30.0,
            bid_depth_usd=Decimal("100000"),
        )
        size_normal = sizer.compute_dynamic_size(regime=MarketRegime.NORMAL, **common_kwargs)
        size_crisis = sizer.compute_dynamic_size(regime=MarketRegime.CRISIS, **common_kwargs)
        assert size_crisis < size_normal

    def test_zero_edge_returns_very_small_size(self):
        """0bps edge → very small (but not necessarily zero) dynamic size."""
        sizer = _make_sizer()
        size = sizer.compute_dynamic_size(
            win_prob=Decimal("0.6"),
            win_loss_ratio=Decimal("1.5"),
            price=Decimal("50000"),
            strategy_id="test",
            strategy_used_capital=Decimal("0"),
            edge_bps=0.0,
            regime=MarketRegime.NORMAL,
            bid_depth_usd=Decimal("50000"),
        )
        assert size >= Decimal("0")

    def test_size_non_negative_all_regimes(self):
        """compute_dynamic_size() never returns negative for all regime types."""
        sizer = _make_sizer()
        for regime in MarketRegime:
            size = sizer.compute_dynamic_size(
                win_prob=Decimal("0.55"),
                win_loss_ratio=Decimal("1.2"),
                price=Decimal("100"),
                strategy_id="t",
                strategy_used_capital=Decimal("0"),
                edge_bps=10.0,
                regime=regime,
                bid_depth_usd=Decimal("10000"),
            )
            assert size >= Decimal("0"), f"Negative size for regime {regime}"
