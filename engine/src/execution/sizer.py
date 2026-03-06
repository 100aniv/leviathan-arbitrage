"""Position sizing — Kelly criterion, capital-tier aware, per-strategy limits."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum


class CapitalTier(StrEnum):
    ALPHA = "alpha"          # $70
    BETA = "beta"            # $750
    PRODUCTION = "production"


@dataclass
class SizerConfig:
    capital: Decimal
    tier: CapitalTier
    max_single_trade_pct: Decimal = Decimal("0.02")
    max_strategy_allocation_pct: Decimal = Decimal("0.30")


class PositionSizer:
    """
    Kelly criterion-based position sizer with capital-tier constraints.

    Kelly formula: f* = (b*p - q) / b
      where b = win/loss ratio, p = win probability, q = 1 - p
    """

    def __init__(self, config: SizerConfig) -> None:
        self._config = config

    def kelly_fraction(
        self,
        win_prob: Decimal,
        win_loss_ratio: Decimal,
        kelly_fraction: float = 1.0,
    ) -> float:
        """
        Compute Kelly fraction f*.

        Returns float in [0, 1]. Clamped to 0 if negative (no edge).
        kelly_fraction: multiplier to scale down (e.g. 0.5 for half-Kelly).
        """
        b = float(win_loss_ratio)
        p = float(win_prob)
        q = 1.0 - p
        if b <= 0:
            return 0.0
        f = (b * p - q) / b
        f = max(0.0, f)  # clamp negative to 0
        return f * kelly_fraction

    def compute_size(
        self,
        win_prob: Decimal,
        win_loss_ratio: Decimal,
        price: Decimal,
        strategy_id: str,
        strategy_used_capital: Decimal,
        kelly_multiplier: float = 0.5,  # half-Kelly by default for safety
    ) -> Decimal:
        """
        Compute position size in base currency units.

        Applies:
          1. Kelly fraction (half-Kelly default)
          2. max_single_trade_pct cap
          3. Per-strategy allocation limit
        """
        capital = self._config.capital

        # 1. Check strategy allocation remaining
        max_strategy_value = capital * self._config.max_strategy_allocation_pct
        remaining_strategy = max_strategy_value - strategy_used_capital
        if remaining_strategy <= Decimal("0"):
            return Decimal("0")

        # 2. Kelly-based position value
        f = self.kelly_fraction(win_prob, win_loss_ratio, kelly_multiplier)
        if f <= 0.0:
            return Decimal("0")
        kelly_value = capital * Decimal(str(f))

        # 3. Cap at max_single_trade_pct
        max_trade_value = capital * self._config.max_single_trade_pct
        position_value = min(kelly_value, max_trade_value)

        # 4. Cap at remaining strategy allocation
        position_value = min(position_value, remaining_strategy)

        if price <= Decimal("0"):
            return Decimal("0")

        return position_value / price

    def compute_margin(
        self,
        position_value: Decimal,
        leverage: int,
        cross_margin: bool,
    ) -> Decimal:
        """
        Compute required margin.

        Both cross-margin and isolated use position_value / leverage.
        The distinction matters for liquidation risk, not initial margin calculation.
        """
        if leverage <= 0:
            return position_value
        return position_value / Decimal(leverage)

    def check_post_leg1_margin(
        self,
        available_capital: Decimal,
        leg2_position_value: Decimal,
        leg2_leverage: int,
        cross_margin: bool,
    ) -> bool:
        """
        Amendment 5: Post-leg-1 margin check before submitting leg 2.

        Returns True if available_capital >= required margin for leg 2.
        """
        required = self.compute_margin(leg2_position_value, leg2_leverage, cross_margin)
        return available_capital >= required
