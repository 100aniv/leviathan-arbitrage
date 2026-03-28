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
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field

from src.core.models import OrderSide, OrderType, Signal, Trade
from src.core.ou_process import OUProcess
from src.strategies.base import BaseStrategy, CostCalculator, TradeLeg, TradeRequest

logger = logging.getLogger(__name__)


class FundingRateConfig(BaseModel):
    """Configuration for FundingRateStrategy."""

    min_funding_diff_bps: Decimal = Field(
        default=Decimal(os.environ.get("MIN_FUNDING_DIFF_BPS", "10")), ge=Decimal("0")
    )  # Must exceed round-trip friction; env override for tuning
    max_position_size: Decimal = Field(default=Decimal("1.0"), gt=Decimal("0"))
    max_holding_periods: int = Field(default=12, ge=1)  # SIT-3: 3→12 (4일 carry, 업계 표준)
    hedge_ratio: Decimal = Field(default=Decimal("1.0"), gt=Decimal("0"))
    # US-239: Settlement timing — only enter within this many minutes before settlement
    # Default 0 = disabled (backward compatible); set to 30 in production config
    settlement_window_minutes: float = Field(default=0.0, ge=0.0)
    # US-239: Settlement hours (UTC)
    settlement_hours: list[int] = Field(default_factory=lambda: [0, 8, 16])
    # US-268: OU Process filter
    enable_ou_filter: bool = Field(default=bool(os.environ.get("ENABLE_OU_FILTER", "true").lower() != "false"))
    ou_min_halflife_s: float = Field(default=float(os.environ.get("FUNDING_OU_MIN_HALFLIFE_S", "300.0")))
    ou_window: int = Field(default=360)


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
        # US-268: OU Process for mean-reversion analysis
        self._ou = OUProcess(window=self.config.ou_window)

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


        # US-254: Regime check — SKIP for funding_rate (delta-neutral carry trade)
        # Funding rate arb is hedged (long+short), so CRISIS regime doesn't apply.
        # Other strategies (cross_exchange, spot_futures) still respect regime gate.

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

        # US-268: OU Process mean-reversion filter
        self._ou.update(float(funding_diff_bps), time.monotonic())
        if self.config.enable_ou_filter and self._ou.is_mean_reverting:
            if self._ou.half_life < self.config.ou_min_halflife_s:
                self._metrics.signals_filtered += 1
                logger.info(
                    "OU filter: half_life=%.1fs < min=%.1fs, skipping signal",
                    self._ou.half_life,
                    self.config.ou_min_halflife_s,
                )
                return None

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

        # SIT-3: 저가 코인 USD 사이징 보정. carry trade는 충분한 포지션이 있어야 수익.
        # 기존 base-unit sizing 유지하되, USD 가치가 $100 미만이면 $1000 USD까지 확대.
        avg_price = (signal.buy_price + signal.sell_price) / Decimal("2")
        base_size = min(signal.volume, self.config.max_position_size)
        _position_usd = base_size * avg_price if avg_price > 0 else Decimal("0")
        if _position_usd < Decimal("100") and avg_price > 0:
            # 저가 코인: $1000 USD 기준으로 사이징 확대
            size = min(signal.volume, Decimal("1000") / avg_price)
        else:
            size = base_size
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

        # Expected income: funding rate arb is a carry trade — income accrues over
        # multiple settlement periods. Use max_holding_periods as expected hold.
        # 3 periods = 24h (8h each), typical for funding rate arb
        expected_funding_income = (
            funding_diff * avg_price * size * Decimal(str(self.config.max_holding_periods))
        )
        net_profit = expected_funding_income - total_cost

        if net_profit <= Decimal("0"):
            self._metrics.signals_filtered += 1
            logger.info(
                "funding_rate.cost_rejected sym=%s diff_bps=%.1f income=%.4f cost=%.4f net=%.4f periods=%d",
                signal.symbol, float(funding_diff_bps), float(expected_funding_income),
                float(total_cost), float(net_profit), self.config.max_holding_periods,
            )
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
