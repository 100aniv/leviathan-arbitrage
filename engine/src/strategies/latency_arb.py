"""Latency Arbitrage strategy.

Detects when a price move on the fast (leader) exchange has not yet propagated
to the slow (follower) exchange. Trades on the slow exchange before the price
catches up. Auto-disables when the measured latency advantage drops below
config.min_latency_advantage_ms.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field

from src.core.latency_tracker import LatencyTracker
from src.core.models import OrderSide, OrderType, Signal, Trade
from src.strategies.base import BaseStrategy, CostCalculator, TradeLeg, TradeRequest


class LatencyArbConfig(BaseModel):
    """Configuration for LatencyArbStrategy."""

    min_latency_advantage_ms: float = Field(default=5.0, ge=0.0)
    max_position_size: Decimal = Field(default=Decimal("1.0"), gt=Decimal("0"))
    min_net_profit_usdt: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))


class LatencyArbStrategy(BaseStrategy):
    """
    Latency Arbitrage Strategy.

    When buy_exchange is the 'fast' (leader) exchange and sell_exchange is the
    'slow' (follower) exchange, this strategy exploits the propagation lag by
    trading on the slow exchange before its price catches up to the fast one.

    Auto-disables: signals are filtered when the observed latency advantage
    (slow_ema - fast_ema) falls below config.min_latency_advantage_ms.
    """

    STRATEGY_TYPE = "latency_arb"

    def __init__(
        self,
        strategy_id: str,
        cost_calculator: CostCalculator,
        latency_tracker: LatencyTracker,
        config: LatencyArbConfig | None = None,
    ) -> None:
        super().__init__(strategy_id, cost_calculator)
        self._tracker = latency_tracker
        self.config = config or LatencyArbConfig()

    def _latency_advantage_ms(self, fast_exchange: str, slow_exchange: str) -> float:
        """Return EMA latency difference (slow - fast) in ms, or 0 if data missing."""
        fast_info = self._tracker.get_latency_info(fast_exchange)
        slow_info = self._tracker.get_latency_info(slow_exchange)
        if fast_info is None or slow_info is None:
            return 0.0
        return max(0.0, slow_info.ema_ms - fast_info.ema_ms)

    async def on_signal(self, signal: Signal) -> Optional[TradeRequest]:
        self._metrics.signals_received += 1

        if not self._is_active:
            self._metrics.signals_filtered += 1
            return None

        advantage_ms = self._latency_advantage_ms(signal.buy_exchange, signal.sell_exchange)
        if advantage_ms < self.config.min_latency_advantage_ms:
            self._metrics.signals_filtered += 1
            return None

        size = min(signal.volume, self.config.max_position_size)

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

        if net_profit <= self.config.min_net_profit_usdt:
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
                ),
                TradeLeg(
                    exchange_id=signal.sell_exchange,
                    symbol=signal.symbol,
                    side=OrderSide.SELL,
                    size=size,
                    order_type=OrderType.MARKET,
                ),
            ],
            expected_profit_usdt=net_profit,
            confidence=signal.confidence,
            metadata={
                "latency_advantage_ms": str(advantage_ms),
                "gross_profit": str(gross_profit),
                "total_cost": str(total_cost),
            },
        )

    async def on_fill(self, trade: Trade) -> None:
        await super().on_fill(trade)
