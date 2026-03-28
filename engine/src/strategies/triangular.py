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
import os
import time
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field

from src.core.models import OrderSide, OrderType, Signal, Trade
from src.strategies.base import BaseStrategy, CostCalculator, TradeLeg, TradeRequest

logger = logging.getLogger(__name__)


from src.core.config_loader import get_config as _get_config
_ENABLE_LATENCY_BUDGET = _get_config("strategy_filters.enable_latency_budget", default=False)
_TRIANGULAR_MAX_LATENCY_MS = float(_get_config("strategy_filters.triangular_max_latency_ms", default=500))


class TriangularConfig(BaseModel):
    """Configuration for TriangularStrategy."""

    # US-241: Reduced from 10 to 8 bps (3x Coinone fee = 6bps, 8 > 6)
    min_profit_bps: Decimal = Field(default=Decimal("8"), ge=Decimal("0"))
    max_position_usdt: Decimal = Field(default=Decimal("1000"), gt=Decimal("0"))
    max_latency_ms: float = Field(default=_TRIANGULAR_MAX_LATENCY_MS, gt=0)


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
        regime_detector: Any = None,
    ) -> None:
        super().__init__(strategy_id, cost_calculator)
        self._regime_detector = regime_detector
        self.config = config or TriangularConfig()

    async def on_signal(self, signal: Signal) -> Optional[TradeRequest]:
        self._metrics.signals_received += 1

        if not self._is_active:
            self._metrics.signals_filtered += 1
            return None

        # US-254: Regime check — block new entries in CRISIS mode
        if self._regime_detector is not None:
            try:
                if self._regime_detector.current_regime == "CRISIS":
                    self._metrics.signals_filtered += 1
                    return None
            except Exception:
                pass  # graceful fallback

        # US-267: Latency budget — reject stale signals
        if _ENABLE_LATENCY_BUDGET:
            meta_pre = signal.metadata
            signal_ts_ms = meta_pre.get("signal_timestamp_ms")
            if signal_ts_ms is not None:
                now_ms = time.time() * 1000
                elapsed_ms = now_ms - float(signal_ts_ms)
                if elapsed_ms > self.config.max_latency_ms:
                    logger.debug(
                        "triangular.latency_budget_exceeded elapsed_ms=%.1f max_ms=%.1f",
                        elapsed_ms,
                        self.config.max_latency_ms,
                    )
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

        # US-249: Compute per-leg sizes — each leg uses its own base asset unit
        # Start from USDT-equivalent capital, propagate through the triangle
        leg_sizes: list[Decimal] = []
        try:
            current_amount = size * first_price  # convert BTC→USDT for propagation
            for _pair, _side, price_str in zip(pairs, sides, prices):
                price = Decimal(str(price_str))
                if _side == "buy":
                    if price == 0:
                        raise ZeroDivisionError("zero price in buy leg")
                    leg_size = current_amount / price  # quote asset → base asset
                    current_amount = leg_size
                else:
                    leg_size = current_amount  # sell base asset we hold
                    current_amount = leg_size * price  # → quote asset output
                leg_sizes.append(leg_size)
        except ZeroDivisionError:
            # Fallback to uniform size when prices are invalid (e.g. first_price=0)
            leg_sizes = [size] * len(pairs)

        # Calculate 3× taker fees (one per leg) using per-leg sizes
        total_cost = Decimal("0")
        for leg_sz, pair, side, price_str in zip(leg_sizes, pairs, sides, prices):
            order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
            _extra = {"dest_exchange_id": exchange_id} if self._calc_supports_dest_exchange else {}
            leg_cost = self._cost_calculator.estimate_cost(
                exchange_id=exchange_id,
                symbol=pair,
                side=order_side,
                size=leg_sz,
                price=Decimal(str(price_str)),
                **_extra,  # US-247: intra-exchange, skip network_cost when supported
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

        # Build 3 trade legs with per-leg sizes
        legs: list[TradeLeg] = []
        for leg_sz, pair, side, price_str in zip(leg_sizes, pairs, sides, prices):
            order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
            legs.append(
                TradeLeg(
                    exchange_id=exchange_id,
                    symbol=pair,
                    side=order_side,
                    size=leg_sz,
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
