"""Triangular Arbitrage strategy — A→B→C→A on a single exchange.

All three legs execute on the same exchange.
Net profit = gross_profit - (3 × taker_fee).
Target execution window: 200 ms for all three legs.

Signal metadata schema:
    path:      list[str]  — 3 currencies, e.g. ["USDT", "BTC", "ETH"]
    pairs:     list[str]  — trading pair per leg, e.g. ["BTC/USDT", "ETH/BTC", "ETH/USDT"]
    sides:     list[str]  — "buy" or "sell" per leg
    prices:    list[str]  — execution price per leg (as string Decimal)
    exchange_id: str      — exchange where all legs execute
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field

from src.core.models import OrderSide, OrderType, Signal, Trade
from src.strategies.base import BaseStrategy, CostCalculator, TradeLeg, TradeRequest

logger = logging.getLogger(__name__)


class TriangularConfig(BaseModel):
    """Configuration for TriangularStrategy."""

    # US-241: Reduced from 10 to 8 bps (3x Coinone fee = 6bps, 8 > 6)
    min_profit_bps: Decimal = Field(default=Decimal("8"), ge=Decimal("0"))
    max_position_usdt: Decimal = Field(default=Decimal("1000"), gt=Decimal("0"))


class TriangularStrategy(BaseStrategy):
    """
    Triangular Arbitrage on a single exchange.

    Receives pre-computed signals whose metadata contains the triangle path.
    Applies 3× taker fee to net profit calculation.
    Generates a 3-leg TradeRequest where every leg shares the same exchange_id.
    """

    STRATEGY_TYPE = "triangular"

    def __init__(
        self,
        strategy_id: str,
        cost_calculator: CostCalculator,
        config: TriangularConfig | None = None,
    ) -> None:
        super().__init__(strategy_id, cost_calculator)
        self.config = config or TriangularConfig()

    async def on_signal(self, signal: Signal) -> Optional[TradeRequest]:
        self._metrics.signals_received += 1

        if not self._is_active:
            self._metrics.signals_filtered += 1
            return None

        # Validate triangle metadata
        meta = signal.metadata
        path: list[str] | None = meta.get("path")
        pairs: list[str] | None = meta.get("pairs")
        sides: list[str] | None = meta.get("sides")
        prices: list[str] | None = meta.get("prices")
        exchange_id: str = meta.get("exchange_id") or signal.buy_exchange

        if not path or not pairs or not sides or not prices:
            self._metrics.signals_filtered += 1
            return None

        # Apply minimum profit threshold
        min_profit = self.config.min_profit_bps / Decimal("10000")
        if signal.spread_pct < min_profit:
            self._metrics.signals_filtered += 1
            return None

        # Convert max_position_usdt to base units using first leg price
        first_price = Decimal(str(prices[0]))
        max_base_size = (
            self.config.max_position_usdt / first_price
            if first_price > 0
            else signal.volume
        )
        # US-241: Use bottleneck volume from scanner if available
        bottleneck_usdt = meta.get("max_volume_usdt")
        if bottleneck_usdt is not None:
            try:
                bottleneck_base = Decimal(str(bottleneck_usdt)) / first_price if first_price > 0 else signal.volume
                size = min(signal.volume, max_base_size, bottleneck_base)
            except Exception:
                size = min(signal.volume, max_base_size)
        else:
            size = min(signal.volume, max_base_size)

        # Calculate 3× taker fees (one per leg)
        total_cost = Decimal("0")
        for pair, side, price_str in zip(pairs, sides, prices):
            order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
            leg_cost = self._cost_calculator.estimate_cost(
                exchange_id=exchange_id,
                symbol=pair,
                side=order_side,
                size=size,
                price=Decimal(str(price_str)),
            )
            total_cost += leg_cost

        # gross_profit = spread_pct * notional (USDT), not spread_pct * base_size
        notional = size * first_price
        gross_profit = signal.spread_pct * notional
        net_profit = gross_profit - total_cost

        if net_profit <= Decimal("0"):
            self._metrics.signals_filtered += 1
            return None

        # US-241: Sanity check — reject obviously fake spreads (stale cross-pair data)
        # Max realistic triangular profit is ~50bps; >500bps is certainly fake
        if signal.spread_pct > Decimal("0.05"):  # 5%
            logger.warning(
                "triangular.fake_spread_rejected spread_pct=%.4f path=%s",
                float(signal.spread_pct),
                path,
            )
            self._metrics.signals_filtered += 1
            return None

        # Build 3 trade legs
        legs: list[TradeLeg] = []
        for pair, side, price_str in zip(pairs, sides, prices):
            order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
            legs.append(
                TradeLeg(
                    exchange_id=exchange_id,
                    symbol=pair,
                    side=order_side,
                    size=size,
                    order_type=OrderType.MARKET,
                    price=Decimal(str(price_str)),
                )
            )

        self._metrics.trade_requests_generated += 1
        return TradeRequest(
            strategy_id=self.strategy_id,
            legs=legs,
            expected_profit_usdt=net_profit,
            confidence=signal.confidence,
            metadata={
                "path": path,
                "exchange_id": exchange_id,
                "gross_profit": str(gross_profit),
                "total_cost": str(total_cost),
            },
        )

    async def on_fill(self, trade: Trade) -> None:
        await super().on_fill(trade)
