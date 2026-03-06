"""Unit tests for position sizer (Kelly criterion, capital-tier, per-strategy limits)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.execution.sizer import CapitalTier, PositionSizer, SizerConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def alpha_config() -> SizerConfig:
    return SizerConfig(
        capital=Decimal("70"),
        tier=CapitalTier.ALPHA,
        max_single_trade_pct=Decimal("0.02"),
        max_strategy_allocation_pct=Decimal("0.30"),
    )


@pytest.fixture
def beta_config() -> SizerConfig:
    return SizerConfig(
        capital=Decimal("750"),
        tier=CapitalTier.BETA,
        max_single_trade_pct=Decimal("0.02"),
        max_strategy_allocation_pct=Decimal("0.30"),
    )


@pytest.fixture
def prod_config() -> SizerConfig:
    return SizerConfig(
        capital=Decimal("10000"),
        tier=CapitalTier.PRODUCTION,
        max_single_trade_pct=Decimal("0.02"),
        max_strategy_allocation_pct=Decimal("0.30"),
    )


@pytest.fixture
def alpha_sizer(alpha_config: SizerConfig) -> PositionSizer:
    return PositionSizer(alpha_config)


@pytest.fixture
def beta_sizer(beta_config: SizerConfig) -> PositionSizer:
    return PositionSizer(beta_config)


@pytest.fixture
def prod_sizer(prod_config: SizerConfig) -> PositionSizer:
    return PositionSizer(prod_config)


# ---------------------------------------------------------------------------
# CapitalTier tests
# ---------------------------------------------------------------------------


def test_capital_tiers_exist() -> None:
    assert CapitalTier.ALPHA
    assert CapitalTier.BETA
    assert CapitalTier.PRODUCTION


# ---------------------------------------------------------------------------
# Kelly criterion tests
# ---------------------------------------------------------------------------


def test_kelly_positive_edge(prod_sizer: PositionSizer) -> None:
    """f* = (bp - q) / b with positive edge."""
    # win_prob=0.6, win_ratio=2.0 (b=2), lose_prob=0.4
    # f* = (2*0.6 - 0.4) / 2 = (1.2 - 0.4) / 2 = 0.4
    fraction = prod_sizer.kelly_fraction(
        win_prob=Decimal("0.6"), win_loss_ratio=Decimal("2.0")
    )
    assert fraction == pytest.approx(float(Decimal("0.4")), rel=1e-4)


def test_kelly_zero_edge(prod_sizer: PositionSizer) -> None:
    """f* = 0 when expected value is zero."""
    # win_prob=0.5, win_ratio=1.0 → f* = (1*0.5 - 0.5) / 1 = 0
    fraction = prod_sizer.kelly_fraction(
        win_prob=Decimal("0.5"), win_loss_ratio=Decimal("1.0")
    )
    assert fraction == pytest.approx(0.0, abs=1e-9)


def test_kelly_negative_edge_clamps_to_zero(prod_sizer: PositionSizer) -> None:
    """Negative Kelly fraction is clamped to 0 (no bet)."""
    fraction = prod_sizer.kelly_fraction(
        win_prob=Decimal("0.3"), win_loss_ratio=Decimal("1.0")
    )
    assert fraction == 0.0


def test_kelly_half_kelly(prod_sizer: PositionSizer) -> None:
    """Half-Kelly reduces fraction by 50%."""
    full = prod_sizer.kelly_fraction(
        win_prob=Decimal("0.6"), win_loss_ratio=Decimal("2.0")
    )
    half = prod_sizer.kelly_fraction(
        win_prob=Decimal("0.6"), win_loss_ratio=Decimal("2.0"), kelly_fraction=0.5
    )
    assert half == pytest.approx(full * 0.5, rel=1e-4)


# ---------------------------------------------------------------------------
# compute_size tests
# ---------------------------------------------------------------------------


def test_compute_size_alpha_tier(alpha_sizer: PositionSizer) -> None:
    """Alpha tier ($70): max single trade = 2% = $1.40."""
    size = alpha_sizer.compute_size(
        win_prob=Decimal("0.6"),
        win_loss_ratio=Decimal("2.0"),
        price=Decimal("50000"),
        strategy_id="strat_1",
        strategy_used_capital=Decimal("0"),
    )
    max_allowed = Decimal("70") * Decimal("0.02")  # $1.40
    assert size * Decimal("50000") <= max_allowed


def test_compute_size_beta_tier(beta_sizer: PositionSizer) -> None:
    """Beta tier ($750): max single trade = 2% = $15."""
    size = beta_sizer.compute_size(
        win_prob=Decimal("0.6"),
        win_loss_ratio=Decimal("2.0"),
        price=Decimal("50000"),
        strategy_id="strat_1",
        strategy_used_capital=Decimal("0"),
    )
    max_allowed = Decimal("750") * Decimal("0.02")  # $15
    assert size * Decimal("50000") <= max_allowed


def test_compute_size_respects_max_single_trade(prod_sizer: PositionSizer) -> None:
    """High Kelly fraction is capped at max_single_trade_pct."""
    size = prod_sizer.compute_size(
        win_prob=Decimal("0.99"),
        win_loss_ratio=Decimal("100.0"),
        price=Decimal("100"),
        strategy_id="strat_1",
        strategy_used_capital=Decimal("0"),
    )
    max_trade_value = Decimal("10000") * Decimal("0.02")  # $200
    assert size * Decimal("100") <= max_trade_value + Decimal("0.01")  # tiny rounding


def test_compute_size_respects_strategy_allocation(prod_sizer: PositionSizer) -> None:
    """Strategy allocation limit: max 30% of capital per strategy."""
    # Already used 29% of capital for this strategy
    strategy_used = Decimal("10000") * Decimal("0.29")  # $2900
    size = prod_sizer.compute_size(
        win_prob=Decimal("0.6"),
        win_loss_ratio=Decimal("2.0"),
        price=Decimal("100"),
        strategy_id="strat_1",
        strategy_used_capital=strategy_used,
    )
    max_strategy = Decimal("10000") * Decimal("0.30")  # $3000
    remaining = max_strategy - strategy_used  # $100
    assert size * Decimal("100") <= remaining + Decimal("0.01")


def test_compute_size_zero_when_strategy_at_limit(prod_sizer: PositionSizer) -> None:
    """Returns 0 when strategy is at allocation limit."""
    strategy_used = Decimal("10000") * Decimal("0.30")  # exactly at limit
    size = prod_sizer.compute_size(
        win_prob=Decimal("0.6"),
        win_loss_ratio=Decimal("2.0"),
        price=Decimal("100"),
        strategy_id="strat_1",
        strategy_used_capital=strategy_used,
    )
    assert size == Decimal("0")


def test_compute_size_zero_when_negative_kelly(prod_sizer: PositionSizer) -> None:
    """Returns 0 when Kelly fraction is 0 (no edge)."""
    size = prod_sizer.compute_size(
        win_prob=Decimal("0.3"),
        win_loss_ratio=Decimal("1.0"),
        price=Decimal("100"),
        strategy_id="strat_1",
        strategy_used_capital=Decimal("0"),
    )
    assert size == Decimal("0")


# ---------------------------------------------------------------------------
# Margin calculation tests
# ---------------------------------------------------------------------------


def test_margin_cross(prod_sizer: PositionSizer) -> None:
    """Cross-margin uses full position value / leverage."""
    margin = prod_sizer.compute_margin(
        position_value=Decimal("1000"),
        leverage=10,
        cross_margin=True,
    )
    assert margin == Decimal("100")  # 1000 / 10


def test_margin_isolated(prod_sizer: PositionSizer) -> None:
    """Isolated margin: same formula but flagged differently."""
    margin = prod_sizer.compute_margin(
        position_value=Decimal("1000"),
        leverage=5,
        cross_margin=False,
    )
    assert margin == Decimal("200")  # 1000 / 5


def test_margin_no_leverage(prod_sizer: PositionSizer) -> None:
    """Leverage 1 = full capital required."""
    margin = prod_sizer.compute_margin(
        position_value=Decimal("500"),
        leverage=1,
        cross_margin=True,
    )
    assert margin == Decimal("500")


# ---------------------------------------------------------------------------
# Post-leg-1 margin check (Amendment 5)
# ---------------------------------------------------------------------------


def test_post_leg1_margin_check_passes(prod_sizer: PositionSizer) -> None:
    """Passes when available capital covers required margin for leg 2."""
    result = prod_sizer.check_post_leg1_margin(
        available_capital=Decimal("500"),
        leg2_position_value=Decimal("1000"),
        leg2_leverage=10,
        cross_margin=True,
    )
    assert result is True  # need $100, have $500


def test_post_leg1_margin_check_fails(prod_sizer: PositionSizer) -> None:
    """Fails when available capital is insufficient for leg 2 margin."""
    result = prod_sizer.check_post_leg1_margin(
        available_capital=Decimal("50"),
        leg2_position_value=Decimal("1000"),
        leg2_leverage=5,
        cross_margin=True,
    )
    assert result is False  # need $200, have $50
