"""Funding Rate Arbitrage strategy.

Exploits divergent funding rates across exchanges.
Go short where funding rate is high (shorts receive funding payments).
Go long where funding rate is low or negative (longs receive funding payments).

signal.metadata must contain:
  - 'funding_rate_sell': float  (funding rate on sell_exchange, high = shorts receive)
  - 'funding_rate_buy': float   (funding rate on buy_exchange, low = longs receive)
  - 'funding_diff_bps': float   (abs difference in basis points)
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field

from src.core.models import OrderSide, OrderType, Signal, Trade
from src.strategies.base import BaseStrategy, CostCalculator, TradeLeg, TradeRequest


class FundingRateConfig(BaseModel):
    """Configuration for FundingRateStrategy."""

    min_funding_diff_bps: Decimal = Field(default=Decimal("5"), ge=Decimal("0"))
    max_position_size: Decimal = Field(default=Decimal("1.0"), gt=Decimal("0"))
    max_holding_periods: int = Field(default=3, ge=1)
    hedge_ratio: Decimal = Field(default=Decimal("1.0"), gt=Decimal("0"))


class FundingRateStrategy(BaseStrategy):
    """
    Funding Rate Arbitrage.

    Simultaneously:
      - SHORT on sell_exchange where funding_rate_sell is positive (shorts receive)
      - LONG on buy_exchange where funding_rate_buy is low/negative (longs receive)

    Net income per period ≈ (funding_rate_sell - funding_rate_buy) * position_size.
    Exits after max_holding_periods or when the differential collapses.
    """

    STRATEGY_TYPE = "funding_rate_arb"

    def __init__(
        self,
        strategy_id: str,
        cost_calculator: CostCalculator,
        config: FundingRateConfig | None = None,
    ) -> None:
        super().__init__(strategy_id, cost_calculator)
        self.config = config or FundingRateConfig()

    async def on_signal(self, signal: Signal) -> Optional[TradeRequest]:
        self._metrics.signals_received += 1

        if not self._is_active:
            self._metrics.signals_filtered += 1
            return None

        # Extract funding rates from metadata
        funding_rate_sell = Decimal(str(signal.metadata.get("funding_rate_sell", "0")))
        funding_rate_buy = Decimal(str(signal.metadata.get("funding_rate_buy", "0")))
        funding_diff = funding_rate_sell - funding_rate_buy
        funding_diff_bps = funding_diff * Decimal("10000")

        if funding_diff_bps < self.config.min_funding_diff_bps:
            self._metrics.signals_filtered += 1
            return None

        size = min(signal.volume, self.config.max_position_size)
        # Apply hedge ratio to the long leg size
        long_size = (size * self.config.hedge_ratio).quantize(Decimal("0.00000001"))

        # Friction costs for both legs
        short_cost = self._cost_calculator.estimate_cost(
            exchange_id=signal.sell_exchange,
            symbol=signal.symbol,
            side=OrderSide.SELL,
            size=size,
            price=signal.sell_price,
        )
        long_cost = self._cost_calculator.estimate_cost(
            exchange_id=signal.buy_exchange,
            symbol=signal.symbol,
            side=OrderSide.BUY,
            size=long_size,
            price=signal.buy_price,
        )
        total_cost = short_cost + long_cost

        # Expected income: conservatively assume 1 funding period (8h) collected
        # max_holding_periods is the CEILING (force-exit), not the expected hold time
        avg_price = (signal.buy_price + signal.sell_price) / Decimal("2")
        expected_funding_income = (
            funding_diff * avg_price * size * Decimal("1")
        )
        net_profit = expected_funding_income - total_cost

        if net_profit <= Decimal("0"):
            self._metrics.signals_filtered += 1
            return None

        self._metrics.trade_requests_generated += 1
        return TradeRequest(
            strategy_id=self.strategy_id,
            legs=[
                TradeLeg(
                    exchange_id=signal.sell_exchange,
                    symbol=signal.symbol,
                    side=OrderSide.SELL,
                    size=size,
                    order_type=OrderType.MARKET,
                    price=signal.sell_price,
                    metadata={
                        "leg_type": "short",
                        "funding_rate": str(funding_rate_sell),
                    },
                ),
                TradeLeg(
                    exchange_id=signal.buy_exchange,
                    symbol=signal.symbol,
                    side=OrderSide.BUY,
                    size=long_size,
                    order_type=OrderType.MARKET,
                    price=signal.buy_price,
                    metadata={
                        "leg_type": "long",
                        "funding_rate": str(funding_rate_buy),
                    },
                ),
            ],
            expected_profit_usdt=net_profit,
            confidence=signal.confidence,
            metadata={
                "funding_diff_bps": str(funding_diff_bps),
                "max_holding_periods": str(self.config.max_holding_periods),
                "expected_funding_income": str(expected_funding_income),
                "total_cost": str(total_cost),
            },
        )

    async def on_fill(self, trade: Trade) -> None:
        await super().on_fill(trade)
