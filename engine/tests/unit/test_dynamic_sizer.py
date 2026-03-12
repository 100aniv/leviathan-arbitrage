"""Tests for DynamicSizer (US-114) — confidence × regime × liquidity sizing."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.execution.sizer import (
    CapitalTier,
    DynamicSizer,
    MarketRegime,
    PositionSizer,
    SizerConfig,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def base_sizer() -> PositionSizer:
    config = SizerConfig(capital=Decimal("100000"), tier=CapitalTier.PRODUCTION)
    return PositionSizer(config)


@pytest.fixture
def dynamic(base_sizer: PositionSizer) -> DynamicSizer:
    return DynamicSizer(base_sizer=base_sizer, liquidity_threshold=Decimal("10000"))


# ---------------------------------------------------------------------------
# Confidence sigmoid
# ---------------------------------------------------------------------------

class TestConfidence:
    def test_5bps_below_half(self) -> None:
        assert DynamicSizer.confidence(5.0) < 0.5

    def test_50bps_near_one(self) -> None:
        assert DynamicSizer.confidence(50.0) > 0.95

    def test_monotonic(self) -> None:
        c5 = DynamicSizer.confidence(5.0)
        c20 = DynamicSizer.confidence(20.0)
        c50 = DynamicSizer.confidence(50.0)
        assert c5 < c20 < c50

    def test_bounded_zero_to_one(self) -> None:
        for bps in [0, 1, 5, 10, 50, 100, 500]:
            c = DynamicSizer.confidence(float(bps))
            assert 0.0 <= c <= 1.0


# ---------------------------------------------------------------------------
# Regime multiplier
# ---------------------------------------------------------------------------

class TestRegimeMultiplier:
    def test_crisis_025(self) -> None:
        assert DynamicSizer.regime_multiplier(MarketRegime.CRISIS) == pytest.approx(0.25)

    def test_high_075(self) -> None:
        assert DynamicSizer.regime_multiplier(MarketRegime.HIGH) == pytest.approx(0.75)

    def test_normal_100(self) -> None:
        assert DynamicSizer.regime_multiplier(MarketRegime.NORMAL) == pytest.approx(1.0)

    def test_low_vol_150(self) -> None:
        assert DynamicSizer.regime_multiplier(MarketRegime.LOW) == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# Liquidity factor
# ---------------------------------------------------------------------------

class TestLiquidityFactor:
    def test_zero_depth_zero(self) -> None:
        assert DynamicSizer.liquidity_factor(Decimal("0"), Decimal("10000")) == pytest.approx(0.0)

    def test_half_threshold(self) -> None:
        assert DynamicSizer.liquidity_factor(Decimal("5000"), Decimal("10000")) == pytest.approx(0.5)

    def test_above_threshold_capped(self) -> None:
        assert DynamicSizer.liquidity_factor(Decimal("15000"), Decimal("10000")) == pytest.approx(1.0)

    def test_exactly_at_threshold(self) -> None:
        assert DynamicSizer.liquidity_factor(Decimal("10000"), Decimal("10000")) == pytest.approx(1.0)

    def test_zero_threshold_returns_one(self) -> None:
        assert DynamicSizer.liquidity_factor(Decimal("5000"), Decimal("0")) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# compute_dynamic_size integration
# ---------------------------------------------------------------------------

class TestComputeDynamicSize:
    def test_crisis_quarter_of_normal(self, dynamic: DynamicSizer) -> None:
        params = dict(
            win_prob=Decimal("0.7"), win_loss_ratio=Decimal("1.5"),
            price=Decimal("50000"), strategy_id="cross_exchange",
            strategy_used_capital=Decimal("0"), edge_bps=50.0,
            bid_depth_usd=Decimal("10000"),
        )
        size_normal = dynamic.compute_dynamic_size(regime=MarketRegime.NORMAL, **params)
        size_crisis = dynamic.compute_dynamic_size(regime=MarketRegime.CRISIS, **params)
        if size_normal > 0:
            ratio = float(size_crisis / size_normal)
            assert ratio == pytest.approx(0.25, rel=0.05)

    def test_zero_liquidity_returns_zero(self, dynamic: DynamicSizer) -> None:
        size = dynamic.compute_dynamic_size(
            win_prob=Decimal("0.7"), win_loss_ratio=Decimal("1.5"),
            price=Decimal("50000"), strategy_id="cross_exchange",
            strategy_used_capital=Decimal("0"), edge_bps=50.0,
            regime=MarketRegime.NORMAL, bid_depth_usd=Decimal("0"),
        )
        assert size == Decimal("0")

    def test_size_always_non_negative(self, dynamic: DynamicSizer) -> None:
        for regime in MarketRegime:
            size = dynamic.compute_dynamic_size(
                win_prob=Decimal("0.7"), win_loss_ratio=Decimal("1.5"),
                price=Decimal("50000"), strategy_id="test",
                strategy_used_capital=Decimal("0"), edge_bps=5.0,
                regime=regime, bid_depth_usd=Decimal("3000"),
            )
            assert size >= Decimal("0"), f"Negative for {regime}"
