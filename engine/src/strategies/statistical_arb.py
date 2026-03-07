"""Statistical Arbitrage strategy.

Cointegration-based pair selection with z-score entry/exit signals.
Uses a Kalman filter for dynamic hedge ratio estimation and the
Engle-Granger test to validate pair cointegration.

Flow per signal:
    1. Update Kalman hedge ratio estimate.
    2. Compute log-price spread: log(sell_price) - beta * log(buy_price).
    3. Accumulate spread history.
    4. After min_history samples: compute z-score vs. historical distribution.
    5. Entry: |z-score| > zscore_entry AND pair passes cointegration test.
    6. Exit:  |z-score| < zscore_exit (handled on next signal in open state).
    7. Size, friction, and net-profit gate before emitting TradeRequest.
"""
from __future__ import annotations

import math
from collections import deque
from decimal import Decimal
from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, Field

from src.core.models import OrderSide, OrderType, Signal, Trade
from src.strategies.base import BaseStrategy, CostCalculator, TradeLeg, TradeRequest


class StatArbState(StrEnum):
    FLAT = "flat"
    LONG = "long"   # long spread: buy at buy_exchange, sell at sell_exchange (spread rising)
    SHORT = "short"  # short spread: spread falling back to mean


class StatArbConfig(BaseModel):
    """Configuration for StatisticalArbStrategy."""

    min_history: int = Field(default=60, ge=10)
    zscore_entry: float = Field(default=2.0, ge=0.0)
    zscore_exit: float = Field(default=0.5, ge=0.0)
    max_position_size: Decimal = Field(default=Decimal("1.0"), gt=Decimal("0"))
    cointegration_pvalue: float = Field(default=0.05, ge=0.0, le=1.0)
    kalman_process_noise: float = Field(default=1e-4, ge=0.0)
    kalman_observation_noise: float = Field(default=1e-2, ge=0.0)


class _KalmanHedgeRatio:
    """
    Scalar Kalman filter estimating the dynamic hedge ratio beta where:
        log(sell_price) ≈ beta * log(buy_price)

    State: beta (scalar).  Observation model: log_sell = beta * log_buy + noise.
    """

    def __init__(self, process_noise: float = 1e-4, observation_noise: float = 1e-2) -> None:
        self._beta = 1.0
        self._P = 1.0       # estimate error covariance
        self._Q = process_noise
        self._R = observation_noise

    def update(self, log_buy: float, log_sell: float) -> float:
        """Update filter with new observation; return current beta estimate."""
        # Predict step
        self._P = self._P + self._Q

        # Kalman gain
        H = log_buy
        S = H * self._P * H + self._R
        K = (self._P * H / S) if S != 0.0 else 0.0

        # Update step
        innovation = log_sell - self._beta * H
        self._beta = self._beta + K * innovation
        self._P = (1.0 - K * H) * self._P

        return self._beta

    @property
    def hedge_ratio(self) -> float:
        return self._beta


def _zscore(history: list[float], current: float) -> float:
    """
    Compute z-score of current vs. history.

    Returns a capped value (±10) when history standard deviation is near zero
    but current deviates from the mean, to allow threshold comparisons.
    """
    if len(history) < 2:
        return 0.0
    n = len(history)
    mean = sum(history) / n
    variance = sum((x - mean) ** 2 for x in history) / n
    std = math.sqrt(variance)

    if std < 1e-10:
        deviation = current - mean
        if abs(deviation) < 1e-10:
            return 0.0
        return math.copysign(10.0, deviation)  # large but finite, above any reasonable entry threshold

    return (current - mean) / std


class StatisticalArbStrategy(BaseStrategy):
    """
    Statistical Arbitrage Strategy.

    Maintains a rolling history of Kalman-filtered log-price spreads between
    buy_exchange and sell_exchange. Enters when the z-score of the current
    spread versus historical distribution exceeds zscore_entry, indicating
    likely mean reversion. Validates pair cointegration via Engle-Granger test.
    """

    STRATEGY_TYPE = "statistical_arb"

    def __init__(
        self,
        strategy_id: str,
        cost_calculator: CostCalculator,
        config: StatArbConfig | None = None,
    ) -> None:
        super().__init__(strategy_id, cost_calculator)
        self.config = config or StatArbConfig()
        _max_len = self.config.min_history * 4
        self._buy_prices: deque[float] = deque(maxlen=_max_len)
        self._sell_prices: deque[float] = deque(maxlen=_max_len)
        self._spreads: deque[float] = deque(maxlen=_max_len)
        self._kalman = _KalmanHedgeRatio(
            process_noise=self.config.kalman_process_noise,
            observation_noise=self.config.kalman_observation_noise,
        )
        self._state = StatArbState.FLAT

    @property
    def state(self) -> StatArbState:
        return self._state

    def _is_cointegrated(self) -> bool:
        """
        Run Engle-Granger cointegration test on accumulated price history.
        Returns True if the pair is cointegrated at config.cointegration_pvalue.
        Falls back to True when statsmodels is unavailable (fail-open).
        """
        if len(self._buy_prices) < self.config.min_history:
            return False
        try:
            import numpy as np
            from statsmodels.tsa.stattools import coint

            buy_arr = np.array(list(self._buy_prices))
            sell_arr = np.array(list(self._sell_prices))
            _, pvalue, _ = coint(buy_arr, sell_arr)
            return float(pvalue) < self.config.cointegration_pvalue
        except Exception:
            return True  # fail-open: allow trading if test unavailable

    async def on_signal(self, signal: Signal) -> Optional[TradeRequest]:
        self._metrics.signals_received += 1

        if not self._is_active:
            self._metrics.signals_filtered += 1
            return None

        buy_f = float(signal.buy_price)
        sell_f = float(signal.sell_price)

        log_buy = math.log(buy_f) if buy_f > 0 else 0.0
        log_sell = math.log(sell_f) if sell_f > 0 else 0.0
        hedge_ratio = self._kalman.update(log_buy, log_sell)

        spread = log_sell - hedge_ratio * log_buy

        self._buy_prices.append(buy_f)
        self._sell_prices.append(sell_f)
        self._spreads.append(spread)

        # Require warmup
        if len(self._spreads) < self.config.min_history:
            self._metrics.signals_filtered += 1
            return None

        # Compute z-score using all history except the current sample
        history = list(self._spreads)[:-1]
        zscore = _zscore(history, spread)

        # --- Exit logic (close open position when z reverts) ---
        if self._state == StatArbState.SHORT and zscore < self.config.zscore_exit:
            # Unwind SHORT spread: we were long buy_exchange, short sell_exchange
            # Close by reversing: sell on buy_exchange, buy on sell_exchange
            size = min(signal.volume, self.config.max_position_size)
            self._state = StatArbState.FLAT
            self._metrics.trade_requests_generated += 1
            return TradeRequest(
                strategy_id=self.strategy_id,
                legs=[
                    TradeLeg(
                        exchange_id=signal.buy_exchange,
                        symbol=signal.symbol,
                        side=OrderSide.SELL,
                        size=size,
                        order_type=OrderType.MARKET,
                    ),
                    TradeLeg(
                        exchange_id=signal.sell_exchange,
                        symbol=signal.symbol,
                        side=OrderSide.BUY,
                        size=size,
                        order_type=OrderType.MARKET,
                    ),
                ],
                expected_profit_usdt=Decimal("0"),
                confidence=signal.confidence,
                metadata={"action": "exit", "prev_state": "short", "zscore": str(zscore)},
            )
        if self._state == StatArbState.LONG and zscore > -self.config.zscore_exit:
            # Unwind LONG spread: we were long sell_exchange, short buy_exchange
            # Close by reversing: buy on buy_exchange, sell on sell_exchange
            size = min(signal.volume, self.config.max_position_size)
            self._state = StatArbState.FLAT
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
                    ),
                    TradeLeg(
                        exchange_id=signal.buy_exchange,
                        symbol=signal.symbol,
                        side=OrderSide.BUY,
                        size=size,
                        order_type=OrderType.MARKET,
                    ),
                ],
                expected_profit_usdt=Decimal("0"),
                confidence=signal.confidence,
                metadata={"action": "exit", "prev_state": "long", "zscore": str(zscore)},
            )

        # Only open new positions when flat
        if self._state != StatArbState.FLAT:
            self._metrics.signals_filtered += 1
            return None

        # Entry threshold
        if abs(zscore) < self.config.zscore_entry:
            self._metrics.signals_filtered += 1
            return None

        # Cointegration gate
        if not self._is_cointegrated():
            self._metrics.signals_filtered += 1
            return None

        size = min(signal.volume, self.config.max_position_size)

        # zscore > 0 → spread above mean → expect reversion down → SHORT spread
        # SHORT spread: buy at buy_exchange (cheap), sell at sell_exchange (expensive)
        # zscore < 0 → spread below mean → expect reversion up → LONG spread
        # LONG spread: same direction — signal already has buy_exchange < sell_exchange
        if zscore > 0:
            buy_exchange = signal.buy_exchange
            sell_exchange = signal.sell_exchange
            buy_price = signal.buy_price
            sell_price = signal.sell_price
            new_state = StatArbState.SHORT
        else:
            # Reverse legs: buy at sell_exchange (now the cheaper one relative to mean)
            buy_exchange = signal.sell_exchange
            sell_exchange = signal.buy_exchange
            buy_price = signal.sell_price
            sell_price = signal.buy_price
            new_state = StatArbState.LONG

        buy_cost = self._cost_calculator.estimate_cost(
            exchange_id=buy_exchange,
            symbol=signal.symbol,
            side=OrderSide.BUY,
            size=size,
            price=buy_price,
        )
        sell_cost = self._cost_calculator.estimate_cost(
            exchange_id=sell_exchange,
            symbol=signal.symbol,
            side=OrderSide.SELL,
            size=size,
            price=sell_price,
        )
        total_cost = buy_cost + sell_cost
        gross_profit = (sell_price - buy_price) * size
        net_profit = gross_profit - total_cost

        if net_profit <= Decimal("0"):
            self._metrics.signals_filtered += 1
            return None

        self._state = new_state
        self._metrics.trade_requests_generated += 1

        abs_z = abs(zscore)
        confidence = signal.confidence * min(abs_z / 5.0, 1.0)

        return TradeRequest(
            strategy_id=self.strategy_id,
            legs=[
                TradeLeg(
                    exchange_id=buy_exchange,
                    symbol=signal.symbol,
                    side=OrderSide.BUY,
                    size=size,
                    order_type=OrderType.MARKET,
                ),
                TradeLeg(
                    exchange_id=sell_exchange,
                    symbol=signal.symbol,
                    side=OrderSide.SELL,
                    size=size,
                    order_type=OrderType.MARKET,
                ),
            ],
            expected_profit_usdt=net_profit,
            confidence=confidence,
            metadata={
                "zscore": str(zscore),
                "hedge_ratio": str(hedge_ratio),
                "gross_profit": str(gross_profit),
                "total_cost": str(total_cost),
                "state": str(new_state),
            },
        )

    async def on_fill(self, trade: Trade) -> None:
        await super().on_fill(trade)
