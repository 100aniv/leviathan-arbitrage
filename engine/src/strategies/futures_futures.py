"""Futures-Futures Cross strategy (CEX-CEX).

Price discrepancy between the same futures contract on two exchanges.
Similar to cross-exchange spot but operates on leveraged futures positions.

US-233: Tighter parameters — min_spread_bps=15, min_book_depth_usd=500, max_notional_usd=200.
"""
from __future__ import annotations

import logging
import os
from decimal import Decimal
from typing import Any, Optional

logger = logging.getLogger(__name__)

from pydantic import BaseModel, Field

from src.core.models import OrderSide, OrderType, Signal, Trade
from src.strategies.base import BaseStrategy, CostCalculator, TradeLeg, TradeRequest


class FuturesFuturesConfig(BaseModel):
    """Configuration for FuturesFuturesStrategy."""

    min_spread_bps: Decimal = Field(default=Decimal("15"), ge=Decimal("0"))
    max_position_size: Decimal = Field(default=Decimal("1.0"), gt=Decimal("0"))
    max_leverage: int = Field(default=5, ge=1, le=20)
    margin_safety_pct: Decimal = Field(default=Decimal("0.20"), ge=Decimal("0"))
    max_notional_usd: Decimal | None = Field(default=Decimal("200"))  # US-233: hard notional cap
    min_book_depth_usd: Decimal = Field(default=Decimal("500"), ge=Decimal("0"))  # US-233
    # US-272: Funding convergence combined signal
    funding_convergence_weight: Decimal = Field(default=Decimal("0.3"), ge=Decimal("0"), le=Decimal("1"))
    enable_funding_convergence: bool = Field(default=True)
    # US-273: Stale guard
    max_book_age_seconds: float = Field(default=5.0, gt=0)
    enable_stale_guard: bool = Field(default=False)


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
        regime_detector: Any = None,
    ) -> None:
        super().__init__(strategy_id, cost_calculator)
        self._regime_detector = regime_detector
        if config is None:
            config = FuturesFuturesConfig(
                min_spread_bps=Decimal(os.environ.get("FUTURES_MIN_SPREAD_BPS", "15")),
                min_book_depth_usd=Decimal(os.environ.get("FUTURES_MIN_BOOK_DEPTH_USD", "500")),
                max_notional_usd=Decimal(os.environ.get("FUTURES_MAX_NOTIONAL_USD", "200")),
                funding_convergence_weight=Decimal(os.environ.get("FUNDING_CONVERGENCE_WEIGHT", "0.3")),
                enable_funding_convergence=os.environ.get("ENABLE_FUNDING_CONVERGENCE", "true").lower() == "true",
                max_book_age_seconds=float(os.environ.get("FUTURES_MAX_BOOK_AGE_S", "5.0")),
                enable_stale_guard=os.environ.get("ENABLE_STALE_GUARD", "false").lower() == "true",
            )
        self.config = config

        # US-260: Adaptive threshold — rolling percentile + volatility weight
        try:
            from src.core.adaptive_threshold import AdaptiveThreshold
            self._adaptive_threshold = AdaptiveThreshold(
                window=1440,
                entry_percentile=95.0,
                exit_percentile=50.0,
                static_entry=float(config.min_spread_bps),
                static_exit=float(config.min_spread_bps) * 0.5,
            )
        except ImportError:
            self._adaptive_threshold = None

    async def on_signal(self, signal: Signal) -> Optional[TradeRequest]:
        self._metrics.signals_received += 1

        if not self._is_active:
            self._metrics.signals_filtered += 1
            return None

        # US-273: Stale Guard — fail closed if book_age_ms missing or stale
        if self.config.enable_stale_guard:
            raw_book_age = signal.metadata.get("book_age_ms")
            if raw_book_age is None:
                logger.warning("missing book_age_ms, filtering signal")
                self._metrics.signals_filtered += 1
                return None
            book_age_ms = float(raw_book_age)
            if book_age_ms / 1000 > self.config.max_book_age_seconds:
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

        # US-260: Feed spread to adaptive threshold tracker
        _spread_bps = float(signal.spread_pct) * 10000
        if self._adaptive_threshold is not None:
            self._adaptive_threshold.update(_spread_bps)

        # US-260: dynamic threshold when ready, static fallback (in bps)
        if self._adaptive_threshold is not None and self._adaptive_threshold.is_ready:
            _entry_bps, _ = self._adaptive_threshold.thresholds
            min_spread_bps_effective = Decimal(str(_entry_bps))
        else:
            min_spread_bps_effective = self.config.min_spread_bps

        # US-272: Funding convergence combined score
        try:
            funding_diff_bps = Decimal(str(signal.metadata.get("funding_diff_bps", 0)))
        except Exception:
            funding_diff_bps = Decimal("0")
        # Clamp to ±500 bps — anything beyond is anomalous
        funding_diff_bps = max(Decimal("-500"), min(funding_diff_bps, Decimal("500")))
        if self.config.enable_funding_convergence:
            combined_score = Decimal(str(_spread_bps)) + self.config.funding_convergence_weight * funding_diff_bps
        else:
            combined_score = Decimal(str(_spread_bps))

        if combined_score < min_spread_bps_effective:
            self._metrics.signals_filtered += 1
            logger.info(
                "strategy.rejected strategy=futures_futures reason=min_spread symbol=%s "
                "score_bps=%.2f threshold_bps=%.2f",
                signal.symbol, float(combined_score), float(min_spread_bps_effective),
            )
            return None

        # US-233: minimum book depth filter
        if self.config.min_book_depth_usd > Decimal("0"):
            book_depth_usd = signal.volume * signal.buy_price
            if book_depth_usd < self.config.min_book_depth_usd:
                self._metrics.signals_filtered += 1
                logger.info(
                    "strategy.rejected strategy=futures_futures reason=depth_insufficient symbol=%s "
                    "depth_usd=%.2f min_depth_usd=%.2f",
                    signal.symbol, float(book_depth_usd), float(self.config.min_book_depth_usd),
                )
                return None

        size = min(signal.volume, self.config.max_position_size)

        # S10: Optional per-trade notional cap
        if self.config.max_notional_usd is not None:
            notional = signal.buy_price * size
            if notional > self.config.max_notional_usd:
                size = self.config.max_notional_usd / signal.buy_price
                if size <= Decimal("0"):
                    self._metrics.signals_filtered += 1
                    return None

        # Check margin safety: required margin must not exceed available * (1 - safety_pct)
        margin_available = Decimal(str(signal.metadata.get("margin_available", "0")))
        if margin_available > Decimal("0"):
            required_margin = (signal.buy_price * size) / Decimal(str(self.config.max_leverage))
            max_allowed_margin = margin_available * (Decimal("1") - self.config.margin_safety_pct)
            if required_margin > max_allowed_margin:
                self._metrics.signals_filtered += 1
                logger.info(
                    "strategy.rejected strategy=futures_futures reason=margin_insufficient symbol=%s "
                    "required=%.2f max_allowed=%.2f",
                    signal.symbol, float(required_margin), float(max_allowed_margin),
                )
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
            logger.info(
                "strategy.rejected strategy=futures_futures reason=net_profit_negative symbol=%s "
                "net_profit=%.6f gross=%.6f cost=%.6f",
                signal.symbol, float(net_profit), float(gross_profit), float(total_cost),
            )
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
                    metadata={"leverage": str(self.config.max_leverage), "leg_type": "futures"},
                ),
                TradeLeg(
                    exchange_id=signal.sell_exchange,
                    symbol=signal.symbol,
                    side=OrderSide.SELL,
                    size=size,
                    order_type=OrderType.MARKET,
                    price=signal.sell_price,
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
