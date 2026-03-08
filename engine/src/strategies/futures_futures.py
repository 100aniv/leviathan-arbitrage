"""Futures-Futures Cross strategy (CEX-CEX).

Price discrepancy between the same futures contract on two exchanges.
Similar to cross-exchange spot but operates on leveraged futures positions.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field

from src.core.models import OrderSide, OrderType, Signal, Trade
from src.strategies.base import BaseStrategy, CostCalculator, TradeLeg, TradeRequest


class FuturesFuturesConfig(BaseModel):
    """Configuration for FuturesFuturesStrategy."""

    min_spread_bps: Decimal = Field(default=Decimal("8"), ge=Decimal("0"))
    max_position_size: Decimal = Field(default=Decimal("1.0"), gt=Decimal("0"))
    max_leverage: int = Field(default=5, ge=1, le=20)
    margin_safety_pct: Decimal = Field(default=Decimal("0.20"), ge=Decimal("0"))


class FuturesFuturesStrategy(BaseStrategy):
    """
    Futures-Futures Cross-Exchange Arbitrage.

    Buys futures cheap on one exchange, sells expensive on another.
    Applies leverage cap and margin safety buffer checks.

    signal.metadata may contain:
      - 'margin_available': float  (USDT available as margin)
    """

    STRATEGY_TYPE = "futures_futures"

    def __init__(
        self,
        strategy_id: str,
        cost_calculator: CostCalculator,
        config: FuturesFuturesConfig | None = None,
    ) -> None:
        super().__init__(strategy_id, cost_calculator)
        self.config = config or FuturesFuturesConfig()

    async def on_signal(self, signal: Signal) -> Optional[TradeRequest]:
        self._metrics.signals_received += 1

        if not self._is_active:
            self._metrics.signals_filtered += 1
            return None

        min_spread = self.config.min_spread_bps / Decimal("10000")
        if signal.spread_pct < min_spread:
            self._metrics.signals_filtered += 1
            return None

        size = min(signal.volume, self.config.max_position_size)

        # Check margin safety: required margin must not exceed available * (1 - safety_pct)
        margin_available = Decimal(str(signal.metadata.get("margin_available", "0")))
        if margin_available > Decimal("0"):
            required_margin = (signal.buy_price * size) / Decimal(str(self.config.max_leverage))
            max_allowed_margin = margin_available * (Decimal("1") - self.config.margin_safety_pct)
            if required_margin > max_allowed_margin:
                self._metrics.signals_filtered += 1
                return None

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
                    metadata={"leverage": str(self.config.max_leverage), "leg_type": "futures"},
                ),
                TradeLeg(
                    exchange_id=signal.sell_exchange,
                    symbol=signal.symbol,
                    side=OrderSide.SELL,
                    size=size,
                    order_type=OrderType.MARKET,
                    metadata={"leverage": str(self.config.max_leverage), "leg_type": "futures"},
                ),
            ],
            expected_profit_usdt=net_profit,
            confidence=signal.confidence,
            metadata={
                "gross_profit": str(gross_profit),
                "total_cost": str(total_cost),
                "leverage": str(self.config.max_leverage),
            },
        )

    async def on_fill(self, trade: Trade) -> None:
        await super().on_fill(trade)
