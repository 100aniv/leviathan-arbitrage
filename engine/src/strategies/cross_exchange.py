"""Cross-Exchange Spot Arbitrage strategy (CEX-CEX).

Detects price discrepancy for the same asset across two exchanges.
Pre-funded accounts on both sides — no transfer needed.
Entry: buy cheap on exchange A, sell expensive on exchange B simultaneously.

latency_boost mode (US-194): when enabled, additionally requires a measurable
latency advantage (slow_exchange_ema > fast_exchange_ema) before trading.
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
    latency_boost: bool = Field(default=False)
    min_latency_advantage_ms: float = Field(default=5.0, ge=0.0)


class CrossExchangeStrategy(BaseStrategy):
    """
    Cross-Exchange Spot Arbitrage.

    Buys on the cheaper exchange and sells on the more expensive one
    simultaneously. Net profit must exceed all friction costs (fees + slippage).

    When latency_boost=True, an additional latency-advantage gate is applied:
    signals are only accepted when the slow exchange's EMA latency exceeds the
    fast exchange's EMA latency by at least min_latency_advantage_ms.
    """

    STRATEGY_TYPE = "cross_exchange_spot"

    def __init__(
        self,
        strategy_id: str,
        cost_calculator: CostCalculator,
        config: CrossExchangeConfig | None = None,
        latency_tracker=None,
    ) -> None:
        super().__init__(strategy_id, cost_calculator)
        self.config = config or CrossExchangeConfig()
        self._latency_tracker = latency_tracker

    def _latency_advantage_ms(self, fast_exchange: str, slow_exchange: str) -> float:
        """Return EMA latency difference (slow - fast) in ms, or 0 if data missing."""
        if self._latency_tracker is None:
            return 0.0
        fast_info = self._latency_tracker.get_latency_info(fast_exchange)
        slow_info = self._latency_tracker.get_latency_info(slow_exchange)
        if fast_info is None or slow_info is None:
            return 0.0
        return max(0.0, slow_info.ema_ms - fast_info.ema_ms)

    async def on_signal(self, signal: Signal) -> Optional[TradeRequest]:
        self._metrics.signals_received += 1

        if not self._is_active:
            self._metrics.signals_filtered += 1
            return None

        # US-198: Korean exchange filter for latency_boost mode
        # Korean exchanges have stale orderbook data, making latency-based decisions unreliable
        if self.config.latency_boost:
            _KOREAN = frozenset({"upbit", "bithumb", "coinone"})
            if signal.buy_exchange in _KOREAN or signal.sell_exchange in _KOREAN:
                self._metrics.signals_filtered += 1
                return None

        # Check spread threshold
        min_spread = self.config.min_spread_bps / Decimal("10000")
        if signal.spread_pct < min_spread:
            self._metrics.signals_filtered += 1
            return None

        # Latency-boost gate
        if self.config.latency_boost:
            advantage_ms = self._latency_advantage_ms(signal.buy_exchange, signal.sell_exchange)
            if advantage_ms < self.config.min_latency_advantage_ms:
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

        metadata: dict = {"gross_profit": str(gross_profit), "total_cost": str(total_cost)}
        if self.config.latency_boost:
            metadata["mode"] = "latency_boost"
            metadata["latency_advantage_ms"] = str(
                self._latency_advantage_ms(signal.buy_exchange, signal.sell_exchange)
            )

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
            metadata=metadata,
        )

    async def on_fill(self, trade: Trade) -> None:
        await super().on_fill(trade)
