"""Spot-Futures Basis Trade strategy (same exchange).

Exploits the basis premium/discount between spot and perpetual futures
on the SAME exchange. Both legs execute atomically on one exchange.

- Contango (basis > 0): futures > spot -> sell futures, buy spot
- Backwardation (basis < 0): futures < spot -> buy futures, sell spot
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field

from src.core.models import OrderSide, OrderType, Signal, Trade
from src.core.ou_process import OUProcess
from src.strategies.base import BaseStrategy, CostCalculator, TradeLeg, TradeRequest

logger = logging.getLogger(__name__)


@dataclass
class OpenPosition:
    symbol: str
    entry_time: float
    entry_price: Decimal
    size: Decimal
    side: str  # "contango" or "backwardation"
    exchange_id: str
    futures_symbol: str = ""
    futures_exchange: str = ""


class SpotFuturesConfig(BaseModel):
    """Configuration for SpotFuturesStrategy."""

    min_basis_bps: Decimal = Field(default=Decimal("15"), ge=Decimal("0"))
    max_position_size: Decimal = Field(default=Decimal("1.0"), gt=Decimal("0"))
    max_holding_hours: float = Field(default=8.0, gt=0.0)
    funding_rate_threshold: Decimal = Field(default=Decimal("0.001"), ge=Decimal("0"))
    enable_basis_ou_filter: bool = Field(default=True)
    max_basis_halflife_h: float = Field(default=24.0)


class SpotFuturesStrategy(BaseStrategy):
    """
    Spot-Futures Basis Trade.

    Uses signal.buy_exchange == signal.sell_exchange (same exchange).
    signal.metadata must contain:
      - 'basis_bps': float  (futures_price - spot_price) / spot_price * 10000
      - 'spot_symbol': str  e.g. 'BTC/USDT'
      - 'futures_symbol': str  e.g. 'BTC/USDT:USDT'
      - 'funding_rate': float  (current funding rate, skip if too adverse)
    """

    STRATEGY_TYPE = "spot_futures_basis"

    def __init__(
        self,
        strategy_id: str,
        cost_calculator: CostCalculator,
        config: SpotFuturesConfig | None = None,
        regime_detector: Any = None,
    ) -> None:
        super().__init__(strategy_id, cost_calculator)
        self._regime_detector = regime_detector
        self.config = config or SpotFuturesConfig()

        # US-261: Adaptive threshold for basis — 95th entry, 50th exit
        try:
            from src.core.adaptive_threshold import AdaptiveThreshold
            self._adaptive_threshold = AdaptiveThreshold(
                window=1440,
                entry_percentile=95.0,
                exit_percentile=50.0,
                static_entry=float(self.config.min_basis_bps),
                static_exit=float(self.config.min_basis_bps) * 0.5,
            )
        except ImportError:
            self._adaptive_threshold = None

        # US-270: OU basis modeling
        self._ou_basis = OUProcess(window=1440)

        # US-271: Open position tracking for holding timeout
        self._open_positions: dict[str, OpenPosition] = {}
        from src.core.config_loader import get_config
        self._holding_timeout_enabled = get_config("strategy_filters.enable_holding_timeout", default=False)

    async def on_signal(self, signal: Signal) -> Optional[TradeRequest]:
        self._metrics.signals_received += 1

        if not self._is_active:
            self._metrics.signals_filtered += 1
            return None

        # US-271: Expire stale positions by max_holding_hours — close BOTH legs
        if self._holding_timeout_enabled:
            now = time.monotonic()
            max_hold_s = self.config.max_holding_hours * 3600.0
            expired = [
                sym for sym, pos in self._open_positions.items()
                if now - pos.entry_time >= max_hold_s
            ]
            for sym in expired:
                pos = self._open_positions.pop(sym)
                # Contango: we bought spot + sold futures → close by selling spot + buying futures
                # Backwardation: we sold spot + bought futures → close by buying spot + selling futures
                spot_close_side = OrderSide.SELL if pos.side == "contango" else OrderSide.BUY
                futures_close_side = OrderSide.BUY if pos.side == "contango" else OrderSide.SELL
                futures_sym = pos.futures_symbol or sym
                futures_ex = pos.futures_exchange or pos.exchange_id
                self._metrics.trade_requests_generated += 1
                # Emit closing request with BOTH legs (spot + futures)
                return TradeRequest(
                    strategy_id=self.strategy_id,
                    legs=[
                        TradeLeg(
                            exchange_id=pos.exchange_id,
                            symbol=sym,
                            side=spot_close_side,
                            size=pos.size,
                            order_type=OrderType.MARKET,
                            price=pos.entry_price,
                            metadata={"leg_type": "timeout_close_spot"},
                        ),
                        TradeLeg(
                            exchange_id=futures_ex,
                            symbol=futures_sym,
                            side=futures_close_side,
                            size=pos.size,
                            order_type=OrderType.MARKET,
                            price=pos.entry_price,
                            metadata={"leg_type": "timeout_close_futures"},
                        ),
                    ],
                    expected_profit_usdt=Decimal("0"),
                    confidence=0.0,
                    metadata={"reason": "holding_timeout"},
                )

        # US-254: Regime check — block new entries in CRISIS mode
        if self._regime_detector is not None:
            try:
                if self._regime_detector.current_regime == "CRISIS":
                    self._metrics.signals_filtered += 1
                    return None
            except Exception:
                pass  # graceful fallback

        # Both legs must be on the same exchange
        if signal.buy_exchange != signal.sell_exchange:
            self._metrics.signals_filtered += 1
            return None

        exchange_id = signal.buy_exchange
        basis_bps = Decimal(str(signal.metadata.get("basis_bps", "0")))
        abs_basis_bps = abs(basis_bps)

        # US-261: Feed basis to adaptive threshold tracker
        if self._adaptive_threshold is not None:
            self._adaptive_threshold.update(float(abs_basis_bps))

        # US-261: Dynamic basis threshold when ready, static fallback
        if self._adaptive_threshold is not None and self._adaptive_threshold.is_ready:
            _entry_bps, _ = self._adaptive_threshold.thresholds
            _min_basis = Decimal(str(_entry_bps))
        else:
            _min_basis = self.config.min_basis_bps

        if abs_basis_bps < _min_basis:
            self._metrics.signals_filtered += 1
            logger.info(
                "strategy.rejected strategy=spot_futures reason=min_basis symbol=%s "
                "basis_bps=%.2f threshold_bps=%.2f",
                signal.symbol, float(abs_basis_bps), float(_min_basis),
            )
            return None

        # US-270: OU basis modeling — update with raw (signed) basis_bps, no abs()
        self._ou_basis.update(float(basis_bps), time.monotonic())

        # US-270: Filter if basis is not mean-reverting within max_basis_halflife_h
        if self.config.enable_basis_ou_filter and self._ou_basis.is_mean_reverting:
            if self._ou_basis.half_life > self.config.max_basis_halflife_h * 3600:
                self._metrics.signals_filtered += 1
                return None

        # Skip if funding rate direction is ADVERSE to our position.
        # Contango (basis > 0): we sell futures (short) → positive funding = good (shorts receive).
        #   Only reject if funding is negative (longs receive, shorts pay).
        # Backwardation (basis < 0): we buy futures (long) → negative funding = good (longs receive).
        #   Only reject if funding is positive (shorts receive, longs pay).
        funding_rate = Decimal(str(signal.metadata.get("funding_rate", "0")))
        if basis_bps > 0 and funding_rate < -self.config.funding_rate_threshold:
            # Contango but negative funding → shorts pay → adverse
            self._metrics.signals_filtered += 1
            return None
        if basis_bps < 0 and funding_rate > self.config.funding_rate_threshold:
            # Backwardation but positive funding → longs pay → adverse
            self._metrics.signals_filtered += 1
            return None

        spot_symbol = str(signal.metadata.get("spot_symbol", signal.symbol))
        futures_symbol = str(signal.metadata.get("futures_symbol", signal.symbol))
        size = min(signal.volume, self.config.max_position_size)

        # Contango: sell futures (expensive), buy spot (cheap)
        # Backwardation: buy futures (cheap), sell spot (expensive)
        if basis_bps > 0:
            # Contango: futures expensive -> sell futures, buy spot
            spot_side = OrderSide.BUY
            futures_side = OrderSide.SELL
            spot_price = signal.buy_price
            futures_price = signal.sell_price
        else:
            # Backwardation: futures cheap -> buy futures, sell spot
            spot_side = OrderSide.SELL
            futures_side = OrderSide.BUY
            spot_price = signal.sell_price
            futures_price = signal.buy_price

        _intra = {"dest_exchange_id": exchange_id} if self._calc_supports_dest_exchange else {}
        spot_cost = self._cost_calculator.estimate_cost(
            exchange_id=exchange_id,
            symbol=spot_symbol,
            side=spot_side,
            size=size,
            price=spot_price,
            **_intra,  # US-249: intra-exchange spot↔futures, skip network_cost when supported
        )
        futures_cost = self._cost_calculator.estimate_cost(
            exchange_id=exchange_id,
            symbol=futures_symbol,
            side=futures_side,
            size=size,
            price=futures_price,
            **_intra,  # US-249: intra-exchange spot↔futures, skip network_cost when supported
        )
        total_cost = spot_cost + futures_cost
        gross_profit = abs(signal.sell_price - signal.buy_price) * size
        net_profit = gross_profit - total_cost

        if net_profit <= Decimal("0"):
            self._metrics.signals_filtered += 1
            return None

        # US-270: Confidence boost via OU predict — closer to mu → higher confidence
        ou_confidence = signal.confidence
        if self._ou_basis.is_mean_reverting:
            predicted = self._ou_basis.predict(horizon_s=3600.0)
            if abs(float(basis_bps)) > 0:
                reversion_ratio = min(1.0, abs(float(basis_bps) - predicted) / abs(float(basis_bps)))
                ou_confidence = min(1.0, signal.confidence * (1.0 + 0.2 * reversion_ratio))

        # US-271: Track new open position
        pos_side = "contango" if basis_bps > 0 else "backwardation"
        if self._holding_timeout_enabled:
            self._open_positions[spot_symbol] = OpenPosition(
                symbol=spot_symbol,
                entry_time=time.monotonic(),
                entry_price=spot_price,
                size=size,
                side=pos_side,
                exchange_id=exchange_id,
                futures_symbol=futures_symbol,
                futures_exchange=exchange_id,
            )

        self._metrics.trade_requests_generated += 1
        return TradeRequest(
            strategy_id=self.strategy_id,
            legs=[
                TradeLeg(
                    exchange_id=exchange_id,
                    symbol=spot_symbol,
                    side=spot_side,
                    size=size,
                    order_type=OrderType.MARKET,
                    price=spot_price,
                    metadata={"leg_type": "spot"},
                ),
                TradeLeg(
                    exchange_id=exchange_id,
                    symbol=futures_symbol,
                    side=futures_side,
                    size=size,
                    order_type=OrderType.MARKET,
                    price=futures_price,
                    metadata={"leg_type": "futures"},
                ),
            ],
            expected_profit_usdt=net_profit,
            confidence=ou_confidence,
            metadata={
                "basis_bps": str(basis_bps),
                "gross_profit": str(gross_profit),
                "total_cost": str(total_cost),
            },
        )

    def _resolve_spot_symbol(self, symbol: str) -> str | None:
        """US-322: Resolve futures symbol to spot symbol for position tracking.

        If the symbol is already a spot key in _open_positions, return as-is.
        Otherwise, search for a position whose futures_symbol matches.
        """
        if symbol in self._open_positions:
            return symbol
        for spot_sym, pos in self._open_positions.items():
            if getattr(pos, "futures_symbol", None) == symbol:
                return spot_sym
        return None

    async def on_fill(self, trade: Trade) -> None:
        await super().on_fill(trade)
        # US-271/US-322: Remove closed position on exit fill
        # Handles both spot and futures leg symbols via reverse lookup
        if self._holding_timeout_enabled:
            meta = getattr(trade, "metadata", {}) or {}
            if meta.get("leg_type", "").startswith("timeout_close"):
                resolved = self._resolve_spot_symbol(trade.symbol)
                if resolved:
                    self._open_positions.pop(resolved, None)
