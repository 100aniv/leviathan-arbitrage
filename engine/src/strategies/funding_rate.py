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

import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field

from src.core.models import OrderSide, OrderType, Signal, Trade
from src.strategies.base import BaseStrategy, CostCalculator, TradeLeg, TradeRequest

logger = logging.getLogger(__name__)


class FundingRateConfig(BaseModel):
    """Configuration for FundingRateStrategy."""

    min_funding_diff_bps: Decimal = Field(default=Decimal("10"), ge=Decimal("0"))  # Must exceed round-trip friction
    max_position_size: Decimal = Field(default=Decimal("1.0"), gt=Decimal("0"))
    max_holding_periods: int = Field(default=3, ge=1)
    hedge_ratio: Decimal = Field(default=Decimal("1.0"), gt=Decimal("0"))
    # US-239: Settlement timing — only enter within this many minutes before settlement
    # Default 0 = disabled (backward compatible); set to 30 in production config
    settlement_window_minutes: float = Field(default=0.0, ge=0.0)
    # US-239: Settlement hours (UTC)
    settlement_hours: list[int] = Field(default_factory=lambda: [0, 8, 16])


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
        regime_detector: Any = None,
    ) -> None:
        super().__init__(strategy_id, cost_calculator)
        self._regime_detector = regime_detector
        self.config = config or FundingRateConfig()
        # US-239: Track open positions per symbol to prevent duplicate entries
        self._open_positions: dict[str, str] = {}  # symbol → direction
        # US-239: Last settlement hour seen (for auto-release after settlement)
        self._last_settlement_hour: int = -1
        # US-262: Rolling funding rate history for z-score dynamic threshold
        from collections import deque
        self._funding_diff_history: deque[float] = deque(maxlen=360)  # ~8H at 80s intervals

    def _minutes_to_next_settlement(self, now_utc: datetime | None = None) -> float:
        """Return minutes until next funding settlement (UTC 00/08/16).

        Used by on_signal to restrict entries to the settlement window.
        """
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)
        hours_since_midnight = now_utc.hour + now_utc.minute / 60.0 + now_utc.second / 3600.0
        min_hours_before = min(
            ((sh - hours_since_midnight) % 24) for sh in self.config.settlement_hours
        )
        return min_hours_before * 60.0

    def _check_settlement_release(self) -> None:
        """Auto-release all positions after a settlement hour passes."""
        now_utc = datetime.now(timezone.utc)
        current_hour = now_utc.hour
        if current_hour in self.config.settlement_hours and current_hour != self._last_settlement_hour:
            self._last_settlement_hour = current_hour
            if self._open_positions:
                self._open_positions.clear()

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

        # US-239: Auto-release positions after settlement
        self._check_settlement_release()

        # US-239: Settlement timing filter — only enter within window before settlement
        # Disabled when settlement_window_minutes == 0 (e.g., test mode)
        if self.config.settlement_window_minutes > 0:
            minutes_to_settlement = self._minutes_to_next_settlement()
            if minutes_to_settlement > self.config.settlement_window_minutes:
                self._metrics.signals_filtered += 1
                return None

        # US-239: Duplicate position guard — skip if already have position on this symbol
        if signal.symbol in self._open_positions:
            self._metrics.signals_filtered += 1
            return None

        # Extract funding rates from metadata
        funding_rate_sell = Decimal(str(signal.metadata.get("funding_rate_sell", "0")))
        funding_rate_buy = Decimal(str(signal.metadata.get("funding_rate_buy", "0")))
        funding_diff = funding_rate_sell - funding_rate_buy
        funding_diff_bps = funding_diff * Decimal("10000")

        # US-262: Z-score dynamic threshold for funding rate
        self._funding_diff_history.append(float(funding_diff_bps))
        if len(self._funding_diff_history) >= 30:
            import math
            _hist = list(self._funding_diff_history)
            _mean = sum(_hist) / len(_hist)
            _var = sum((x - _mean) ** 2 for x in _hist) / (len(_hist) - 1)
            _std = math.sqrt(_var) if _var > 0 else 0.0
            if _std > 0:
                _z_score = (float(funding_diff_bps) - _mean) / _std
                # Only enter when z-score > 1.5 (significant deviation)
                _z_threshold = float(os.environ.get("FUNDING_ZSCORE_THRESHOLD", "1.5"))
                if _z_score < _z_threshold:
                    self._metrics.signals_filtered += 1
                    logger.debug(
                        "funding_rate.zscore_filter z=%.2f threshold=%.1f diff_bps=%.1f",
                        _z_score, _z_threshold, float(funding_diff_bps),
                    )
                    return None

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

        # NOTE: Slippage is already accounted for upstream by SignalGenerator
        # (CEXOrderbookSlippage pre-filter). Adding phantom slippage here
        # would double-count and reject profitable funding rate trades.
        avg_price = (signal.buy_price + signal.sell_price) / Decimal("2")

        # Expected income: conservatively assume 1 funding period (8h) collected
        # max_holding_periods is the CEILING (force-exit), not the expected hold time
        expected_funding_income = (
            funding_diff * avg_price * size * Decimal("1")
        )
        net_profit = expected_funding_income - total_cost

        if net_profit <= Decimal("0"):
            self._metrics.signals_filtered += 1
            return None

        # US-239: Record open position to prevent duplicate entries
        self._open_positions[signal.symbol] = "short_high_long_low"

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
