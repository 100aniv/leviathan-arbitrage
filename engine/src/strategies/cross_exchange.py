"""Cross-Exchange Spot Arbitrage strategy (CEX-CEX).

Detects price discrepancy for the same asset across two exchanges.
Pre-funded accounts on both sides — no transfer needed.
Entry: buy cheap on exchange A, sell expensive on exchange B simultaneously.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field

from src.core.models import OrderSide, OrderType, Signal, Trade
from src.strategies.base import BaseStrategy, CostCalculator, TradeLeg, TradeRequest


class CrossExchangeConfig(BaseModel):
    """Configuration for CrossExchangeStrategy."""

    min_spread_bps: Decimal = Field(default=Decimal("10"), ge=Decimal("0"))
    max_position_size: Decimal = Field(default=Decimal("1.0"), gt=Decimal("0"))
    rebalance_threshold: Decimal = Field(default=Decimal("0.05"), ge=Decimal("0"))


class CrossExchangeStrategy(BaseStrategy):
    """
    Cross-Exchange Spot Arbitrage.

    Buys on the cheaper exchange and sells on the more expensive one
    simultaneously. Net profit must exceed all friction costs (fees + slippage).
    """

    STRATEGY_TYPE = "cross_exchange_spot"

    def __init__(
        self,
        strategy_id: str,
        cost_calculator: CostCalculator,
        config: CrossExchangeConfig | None = None,
    ) -> None:
        super().__init__(strategy_id, cost_calculator)
        self.config = config or CrossExchangeConfig()

    async def on_signal(self, signal: Signal) -> Optional[TradeRequest]:
        self._metrics.signals_received += 1

        if not self._is_active:
            self._metrics.signals_filtered += 1
            return None

        # Check spread threshold
        min_spread = self.config.min_spread_bps / Decimal("10000")
        if signal.spread_pct < min_spread:
            self._metrics.signals_filtered += 1
            return None

        size = min(signal.volume, self.config.max_position_size)

        # Calculate friction costs for both legs
        buy_cost = self._cost_calculator.estimate_cost(
            exchange_id=signal.buy_exchange,
            symbol=signal.symbol,
            side=OrderSide.BUY,
            size=size,
            price=signal.buy_price,
        )
        sell_cost = self._cost_calculator.estimate_cost(
            exchange_id=signal.sell_exchange,
            symbol=signal.symbol,
            side=OrderSide.SELL,
            size=size,
            price=signal.sell_price,
        )
        total_cost = buy_cost + sell_cost

        gross_profit = (signal.sell_price - signal.buy_price) * size
        net_profit = gross_profit - total_cost

        if net_profit <= Decimal("0"):
            self._metrics.signals_filtered += 1
            return None

        self._metrics.trade_requests_generated += 1
        return TradeRequest(
            strategy_id=self.strategy_id,
            legs=[
                TradeLeg(
                    exchange_id=signal.buy_exchange,
                    symbol=signal.symbol,
                    side=OrderSide.BUY,
                    size=size,
                    order_type=OrderType.MARKET,
                    price=signal.buy_price,
                ),
                TradeLeg(
                    exchange_id=signal.sell_exchange,
                    symbol=signal.symbol,
                    side=OrderSide.SELL,
                    size=size,
                    order_type=OrderType.MARKET,
                    price=signal.sell_price,
                ),
            ],
            expected_profit_usdt=net_profit,
            confidence=signal.confidence,
            metadata={"gross_profit": str(gross_profit), "total_cost": str(total_cost)},
        )

    async def on_fill(self, trade: Trade) -> None:
        await super().on_fill(trade)
