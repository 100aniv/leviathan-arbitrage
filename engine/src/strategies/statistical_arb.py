"""Statistical Arbitrage strategy.

Cross-asset cointegration-based pair trading on the SAME exchange.
Default pairs: BTC/USDT–ETH/USDT, ETH/USDT–SOL/USDT, BTC/USDT–SOL/USDT.

Flow per on_orderbook_update(exchange, symbolA, mid_price):
    1. Store mid_price in _all_books[symbolA][exchange].
    2. For each configured pair containing symbolA, look up symbolB on same exchange.
    3. Update Kalman hedge ratio: spread = log(midA) – beta * log(midB).
    4. Accumulate spread history.
    5. After min_history samples: compute z-score vs. historical distribution.
    6. Entry: |z-score| > zscore_entry AND pair passes cointegration test.
    7. Exit:  |z-score| < zscore_exit (or max_holding_bars exceeded).
    8. Friction + net-profit gate before emitting TradeRequest.

Legacy on_signal() interface retained for backward compatibility with
cross-exchange (same symbol, two exchange) mode.
"""
from __future__ import annotations

import math
import os
import time
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any, Optional

from pydantic import BaseModel, Field

from src.core.models import OrderSide, OrderType, Signal, Trade
from src.strategies.base import BaseStrategy, CostCalculator, TradeLeg, TradeRequest

import structlog

logger = structlog.get_logger(__name__)


class StatArbState(StrEnum):
    FLAT = "flat"
    LONG = "long"   # long symbolA, short symbolB (spread rising)
    SHORT = "short"  # short symbolA, long symbolB (spread falling to mean)


class StatArbConfig(BaseModel):
    """Configuration for StatisticalArbStrategy."""

    min_history: int = Field(default=120, ge=10)
    zscore_entry: float = Field(default=2.5, ge=0.0)
    zscore_exit: float = Field(default=0.5)
    max_position_size: Decimal = Field(default=Decimal("1.0"), gt=Decimal("0"))
    cointegration_pvalue: float = Field(default=0.05, ge=0.0, le=1.0)
    kalman_process_noise: float = Field(default=1e-4, ge=0.0)
    kalman_observation_noise: float = Field(default=5e-3, ge=0.0)
    # Adaptive z-score: tighten entry when vol_ratio = current_vol / hist_vol is high
    adaptive_threshold: bool = Field(default=True)
    vol_lookback: int = Field(default=20, ge=5)
    # Stationarity filter: spread must cross zero at least this many times in last N obs
    min_zero_crossings: int = Field(default=3, ge=0)
    zero_crossing_lookback: int = Field(default=60, ge=10)
    # Maximum holding period: force exit after this many bars even if z hasn't reverted
    max_holding_bars: int = Field(default=60, ge=1)
    # Set False to skip cointegration test (useful for low-sample / constant-price tests)
    enable_cointegration: bool = Field(default=True)
    # US-274: Cost gate — block entry when round-trip cost > expected spread profit
    enable_cost_gate: bool = Field(
        default_factory=lambda: os.environ.get("ENABLE_COST_GATE", "true").lower() != "false"
    )
    # US-231: z-score hardstop (force-exit if |z| exceeds this while in position)
    zscore_hardstop: float = Field(default=3.5, ge=0.0)
    # US-231: Kalman stale guard — skip z-score if last update was > this many seconds ago
    kalman_stale_threshold_s: float = Field(default=60.0, ge=0.0)
    # US-240: OU half-life maximum (days) — pairs with slower mean-reversion are skipped
    max_half_life_days: float = Field(default=15.0, ge=0.0)
    # Cross-asset pairs: (symbolA, symbolB) traded on the SAME exchange
    pairs: list[tuple[str, str]] = Field(default_factory=lambda: [
        ("BTC/USDT", "ETH/USDT"),
        ("ETH/USDT", "SOL/USDT"),
        ("BTC/USDT", "SOL/USDT"),
    ])


class _KalmanHedgeRatio:
    """
    Scalar Kalman filter estimating the dynamic hedge ratio beta where:
        log(priceA) ≈ beta * log(priceB)

    State: beta (scalar).  Observation model: log_a = beta * log_b + noise.
    """

    def __init__(self, process_noise: float = 1e-4, observation_noise: float = 1e-2) -> None:
        self._beta = 1.0
        self._P = 1.0       # estimate error covariance
        self._Q = process_noise
        self._R = observation_noise

    def update(self, log_b: float, log_a: float) -> float:
        """Update filter with new observation; return current beta estimate."""
        # Predict step
        self._P = self._P + self._Q

        # Kalman gain
        H = log_b
        S = H * self._P * H + self._R
        K = (self._P * H / S) if S != 0.0 else 0.0

        # Update step
        innovation = log_a - self._beta * H
        self._beta = self._beta + K * innovation
        self._P = (1.0 - K * H) * self._P

        return self._beta

    @property
    def hedge_ratio(self) -> float:
        return self._beta


@dataclass
class _PairState:
    """Per-pair runtime state for cross-asset statistical arbitrage."""

    kalman: _KalmanHedgeRatio
    prices_a: deque  # symbolA mid prices
    prices_b: deque  # symbolB mid prices
    spreads: deque   # log spread history: log(midA) - beta*log(midB)
    state: StatArbState = field(default=StatArbState.FLAT)
    bars_in_position: int = 0
    # US-231: monotonic time of previous Kalman update (for stale guard)
    kalman_last_update: float = 0.0


def _zscore_std(history: list[float]) -> float:
    """Return std of spread history (for PnL calculation)."""
    if len(history) < 2:
        return 1e-10
    n = len(history)
    mean = sum(history) / n
    variance = sum((x - mean) ** 2 for x in history) / n
    return math.sqrt(variance) if variance > 0 else 1e-10


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
        return math.copysign(10.0, deviation)

    return (current - mean) / std


class StatisticalArbStrategy(BaseStrategy):
    """
    Statistical Arbitrage Strategy — cross-asset, same-exchange mode.

    Maintains per-pair rolling spread history (log(midA) - beta*log(midB))
    using a Kalman filter for dynamic hedge ratio estimation. Enters when the
    z-score exceeds zscore_entry and the pair passes cointegration validation.
    """

    STRATEGY_TYPE = "statistical_arb"

    def __init__(
        self,
        strategy_id: str,
        cost_calculator: CostCalculator,
        config: StatArbConfig | None = None,
        regime_detector: Any = None,
    ) -> None:
        super().__init__(strategy_id, cost_calculator)
        self.config = config or StatArbConfig()
        self._regime_detector = regime_detector
        _max_len = max(self.config.min_history * 4, self.config.zero_crossing_lookback * 2)

        # Cross-asset pair state (new architecture)
        self._pair_states: dict[tuple[str, str], _PairState] = {
            pair: _PairState(
                kalman=_KalmanHedgeRatio(
                    process_noise=self.config.kalman_process_noise,
                    observation_noise=self.config.kalman_observation_noise,
                ),
                prices_a=deque(maxlen=_max_len),
                prices_b=deque(maxlen=_max_len),
                spreads=deque(maxlen=_max_len),
            )
            for pair in self.config.pairs
        }
        # Latest mid prices per symbol per exchange: {symbol: {exchange: mid}}
        self._all_books: dict[str, dict[str, float]] = {}

        # US-240: Per-pair trade cooldown (prevent over-trading)
        self._pair_last_trade: dict[tuple[str, str], float] = {}
        self._trade_cooldown_s: float = float(os.environ.get("STAT_ARB_COOLDOWN_S", "300"))

        # US-240: Entry spread tracking for exit PnL calculation
        self._pair_entry_spread: dict[tuple[str, str], float] = {}
        self._pair_entry_notional: dict[tuple[str, str], float] = {}

        # Legacy cross-exchange single-pair state (for on_signal backward compat)
        self._buy_prices: deque[float] = deque(maxlen=_max_len)
        self._sell_prices: deque[float] = deque(maxlen=_max_len)
        self._spreads: deque[float] = deque(maxlen=_max_len)
        self._kalman = _KalmanHedgeRatio(
            process_noise=self.config.kalman_process_noise,
            observation_noise=self.config.kalman_observation_noise,
        )
        self._state = StatArbState.FLAT
        self._bars_in_position: int = 0

        # US-258-b: warm-up tracking — strategy not ready until min_history samples received
        self._warmup_complete: bool = False
        self._total_samples_received: int = 0

    @property
    def state(self) -> StatArbState:
        return self._state

    @property
    def bars_in_position(self) -> int:
        return self._bars_in_position

    def is_warmed_up(self) -> bool:
        """US-258-b: Return True once min_history samples have been received."""
        return self._warmup_complete

    # ------------------------------------------------------------------
    # Cross-asset mode: on_orderbook_update + _evaluate_statistical_arb
    # ------------------------------------------------------------------

    async def on_orderbook_update(
        self,
        exchange: str,
        symbol: str,
        mid_price: float,
    ) -> list[TradeRequest]:
        """
        Update internal book cache and evaluate all pairs containing symbol.

        Called by the signal producer whenever a new orderbook update arrives
        for (exchange, symbol). Returns a list of TradeRequests (may be empty).
        """
        if not self._is_active:
            return []

        self._all_books.setdefault(symbol, {})[exchange] = mid_price
        self._metrics.signals_received += 1

        # US-258-b: track total samples for warm-up detection
        self._total_samples_received += 1
        if not self._warmup_complete and self._total_samples_received >= self.config.min_history:
            self._warmup_complete = True
            logger.info("stat_arb.warmup_complete samples=%d", self._total_samples_received)

        results: list[TradeRequest] = []
        for sym_a, sym_b in self.config.pairs:
            if symbol in (sym_a, sym_b):
                result = await self._evaluate_statistical_arb(exchange, sym_a, sym_b)
                if result is not None:
                    results.append(result)
        return results

    async def _evaluate_statistical_arb(
        self,
        exchange: str,
        symbol_a: str,
        symbol_b: str,
    ) -> Optional[TradeRequest]:
        """
        Core cross-asset spread evaluation for (symbolA, symbolB) on exchange.

        spread = log(midA) - beta * log(midB)
        TradeRequest.metadata includes "symbol2" = symbolB.
        """
        books = self._all_books
        if symbol_a not in books or exchange not in books[symbol_a]:
            return None
        if symbol_b not in books or exchange not in books[symbol_b]:
            return None

        mid_a = books[symbol_a][exchange]
        mid_b = books[symbol_b][exchange]
        if mid_a <= 0 or mid_b <= 0:
            return None

        pair_key = (symbol_a, symbol_b)
        ps = self._pair_states[pair_key]

        # US-231: Kalman stale guard — check gap BEFORE updating
        now_mono = time.monotonic()
        prev_update = ps.kalman_last_update
        if prev_update > 0 and (now_mono - prev_update) > self.config.kalman_stale_threshold_s:
            # Data gap too long — z-score would be unreliable; force flat if in position
            if ps.state != StatArbState.FLAT:
                ps.state = StatArbState.FLAT
                ps.bars_in_position = 0
                logger.warning(
                    "stat_arb.kalman_stale: forced flat for %s/%s on %s (gap=%.1fs)",
                    symbol_a, symbol_b, exchange, now_mono - prev_update,
                )
            return None
        ps.kalman_last_update = now_mono

        log_a = math.log(mid_a)
        log_b = math.log(mid_b)
        beta = ps.kalman.update(log_b, log_a)
        spread = log_a - beta * log_b

        ps.prices_a.append(mid_a)
        ps.prices_b.append(mid_b)
        ps.spreads.append(spread)

        if ps.state != StatArbState.FLAT:
            ps.bars_in_position += 1

        if len(ps.spreads) < self.config.min_history:
            self._metrics.signals_filtered += 1
            return None

        history = list(ps.spreads)[:-1]
        zscore = _zscore(history, spread)

        force_exit = (
            ps.state != StatArbState.FLAT
            and ps.bars_in_position >= self.config.max_holding_bars
        )

        # --- Exit logic ---
        if ps.state == StatArbState.SHORT and (
            zscore < self.config.zscore_exit or force_exit
        ):
            ps.state = StatArbState.FLAT
            ps.bars_in_position = 0
            self._metrics.trade_requests_generated += 1
            exit_reason = "timeout" if force_exit else "zscore"
            # US-240: hedge-ratio adjusted exit sizes (mirror entry)
            _exit_notional = float(self.config.max_position_size) * mid_b
            _exit_size_a = Decimal(str(_exit_notional / mid_a)) if mid_a > 0 else self.config.max_position_size
            # US-240: Spread-based PnL — SHORT entry profited if spread decreased
            _pair_key_exit = (symbol_a, symbol_b)
            _entry_spread = self._pair_entry_spread.get(_pair_key_exit, spread)
            _entry_notional = self._pair_entry_notional.get(_pair_key_exit, _exit_notional)
            _std = _zscore_std(list(ps.spreads))
            _spread_pnl = (_entry_spread - spread) * _entry_notional / _std if _std > 0 else 0.0
            self._pair_last_trade[_pair_key_exit] = time.monotonic()
            return TradeRequest(
                strategy_id=self.strategy_id,
                legs=[
                    TradeLeg(
                        exchange_id=exchange,
                        symbol=symbol_a,
                        side=OrderSide.BUY,
                        size=_exit_size_a,
                        order_type=OrderType.MARKET,
                        price=Decimal(str(mid_a)),
                    ),
                    TradeLeg(
                        exchange_id=exchange,
                        symbol=symbol_b,
                        side=OrderSide.SELL,
                        size=self.config.max_position_size,
                        order_type=OrderType.MARKET,
                        price=Decimal(str(mid_b)),
                    ),
                ],
                expected_profit_usdt=Decimal(str(round(_spread_pnl, 6))),
                confidence=0.5,
                metadata={
                    "action": "exit",
                    "prev_state": "short",
                    "cross_asset": "true",
                    "zscore": str(zscore),
                    "exit_reason": exit_reason,
                    "spread_pnl": str(round(_spread_pnl, 4)),
                    "symbol2": symbol_b,
                },
            )

        if ps.state == StatArbState.LONG and (
            zscore > -self.config.zscore_exit or force_exit
        ):
            ps.state = StatArbState.FLAT
            ps.bars_in_position = 0
            self._metrics.trade_requests_generated += 1
            exit_reason = "timeout" if force_exit else "zscore"
            # US-240: hedge-ratio adjusted exit sizes
            _exit_notional_l = float(self.config.max_position_size) * mid_b
            _exit_size_a_l = Decimal(str(_exit_notional_l / mid_a)) if mid_a > 0 else self.config.max_position_size
            # US-240: Spread-based PnL — LONG entry profited if spread increased
            _pair_key_exit_l = (symbol_a, symbol_b)
            _entry_spread_l = self._pair_entry_spread.get(_pair_key_exit_l, spread)
            _entry_notional_l = self._pair_entry_notional.get(_pair_key_exit_l, _exit_notional_l)
            _std_l = _zscore_std(list(ps.spreads))
            _spread_pnl_l = (spread - _entry_spread_l) * _entry_notional_l / _std_l if _std_l > 0 else 0.0
            self._pair_last_trade[_pair_key_exit_l] = time.monotonic()
            return TradeRequest(
                strategy_id=self.strategy_id,
                legs=[
                    TradeLeg(
                        exchange_id=exchange,
                        symbol=symbol_a,
                        side=OrderSide.SELL,
                        size=_exit_size_a_l,
                        order_type=OrderType.MARKET,
                        price=Decimal(str(mid_a)),
                    ),
                    TradeLeg(
                        exchange_id=exchange,
                        symbol=symbol_b,
                        side=OrderSide.BUY,
                        size=self.config.max_position_size,
                        order_type=OrderType.MARKET,
                        price=Decimal(str(mid_b)),
                    ),
                ],
                expected_profit_usdt=Decimal(str(round(_spread_pnl_l, 6))),
                confidence=0.5,
                metadata={
                    "action": "exit",
                    "prev_state": "long",
                    "spread_pnl": str(round(_spread_pnl_l, 4)),
                    "zscore": str(zscore),
                    "exit_reason": exit_reason,
                    "cross_asset": "true",
                    "symbol2": symbol_b,
                },
            )

        # US-231: z-score hardstop — force-exit if |z| exceeds hardstop while in position
        if (
            ps.state != StatArbState.FLAT
            and abs(zscore) > self.config.zscore_hardstop
        ):
            logger.warning(
                "stat_arb.hardstop: |z|=%.2f > %.2f for %s/%s on %s, forcing flat",
                abs(zscore), self.config.zscore_hardstop, symbol_a, symbol_b, exchange,
            )
            ps.state = StatArbState.FLAT
            ps.bars_in_position = 0
            self._metrics.signals_filtered += 1
            return None

        if ps.state != StatArbState.FLAT:
            self._metrics.signals_filtered += 1
            return None

        # US-231: Regime gate — block new entries in CRISIS regime
        if self._regime_detector is not None:
            try:
                current_regime = getattr(self._regime_detector, "current_regime", None)
                if current_regime is not None and str(current_regime) == "CRISIS":
                    self._metrics.signals_filtered += 1
                    return None
            except Exception:
                pass

        # US-240: OU half-life filter — skip pairs with slow mean-reversion
        if self.config.max_half_life_days > 0:
            half_life = self._compute_half_life(list(ps.spreads))
            if half_life > self.config.max_half_life_days:
                self._metrics.signals_filtered += 1
                return None

        # US-240: Per-pair cooldown — prevent over-trading
        pair_key = (symbol_a, symbol_b)
        now_mono = time.monotonic()
        last_trade_time = self._pair_last_trade.get(pair_key, 0.0)
        if ps.state == StatArbState.FLAT and (now_mono - last_trade_time) < self._trade_cooldown_s:
            return None

        # Adaptive entry threshold
        effective_entry = self._adaptive_entry_threshold_for_pair(ps)
        if abs(zscore) < effective_entry:
            self._metrics.signals_filtered += 1
            return None

        # Stationarity gate
        if not self._has_sufficient_zero_crossings_for_pair(ps):
            self._metrics.signals_filtered += 1
            return None

        # Cointegration gate
        if not self._is_cointegrated_for_pair(ps):
            self._metrics.signals_filtered += 1
            return None

        # US-274: Expected spread profit estimate + cost gate
        # SIT-3 P1 Fix: position_size is in asset units (e.g., 1.0 BTC).
        # Profit = fractional spread convergence × USD position value.
        # Use mean-reversion distance (|spread - mean|) as fractional return.
        _mean_spread = sum(ps.spreads) / len(ps.spreads) if ps.spreads else 0.0
        _spread_convergence = abs(spread - _mean_spread)  # fractional return
        _position_usd = float(self.config.max_position_size) * mid_b
        # Cap position USD — conservative for Shadow (spread 수렴 보장 아님)
        _position_usd = min(_position_usd, 1000.0)  # SIT-3: 5000→1000
        expected_spread_profit = (
            Decimal(str(_spread_convergence * _position_usd)) if _spread_convergence > 0 else Decimal("0")
        )
        if self.config.enable_cost_gate and self._cost_calculator is not None:
            _sa = Decimal(str(_position_usd / mid_a)) if mid_a > 0 else self.config.max_position_size
            _sb = self.config.max_position_size
            _pa, _pb = Decimal(str(mid_a)), Decimal(str(mid_b))
            round_trip_cost = (
                self._cost_calculator.estimate_cost(exchange, symbol_a, OrderSide.BUY, _sa, _pa)
                + self._cost_calculator.estimate_cost(exchange, symbol_a, OrderSide.SELL, _sa, _pa)
                + self._cost_calculator.estimate_cost(exchange, symbol_b, OrderSide.BUY, _sb, _pb)
                + self._cost_calculator.estimate_cost(exchange, symbol_b, OrderSide.SELL, _sb, _pb)
            )
            if round_trip_cost > expected_spread_profit:
                self._metrics.signals_filtered += 1
                return None

        # US-240: Hedge-ratio adjusted sizes for dollar-neutral cross-asset position.
        # Without adjustment, BTC($90K) vs ETH($3.5K) creates 25x notional imbalance.
        # size_a * mid_a ≈ size_b * mid_b (dollar-neutral)
        notional_usd = float(self.config.max_position_size) * mid_b  # base notional from symbolB
        size_a = Decimal(str(notional_usd / mid_a)) if mid_a > 0 else self.config.max_position_size
        size_b = self.config.max_position_size

        # zscore > 0: symbolA overpriced vs symbolB → SHORT symbolA, LONG symbolB
        # zscore < 0: symbolA underpriced vs symbolB → LONG symbolA, SHORT symbolB
        if zscore > 0:
            legs = [
                TradeLeg(
                    exchange_id=exchange,
                    symbol=symbol_a,
                    side=OrderSide.SELL,
                    size=size_a,
                    order_type=OrderType.MARKET,
                    price=Decimal(str(mid_a)),
                ),
                TradeLeg(
                    exchange_id=exchange,
                    symbol=symbol_b,
                    side=OrderSide.BUY,
                    size=size_b,
                    order_type=OrderType.MARKET,
                    price=Decimal(str(mid_b)),
                ),
            ]
            ps.state = StatArbState.SHORT
        else:
            legs = [
                TradeLeg(
                    exchange_id=exchange,
                    symbol=symbol_a,
                    side=OrderSide.BUY,
                    size=size_a,
                    order_type=OrderType.MARKET,
                    price=Decimal(str(mid_a)),
                ),
                TradeLeg(
                    exchange_id=exchange,
                    symbol=symbol_b,
                    side=OrderSide.SELL,
                    size=size_b,
                    order_type=OrderType.MARKET,
                    price=Decimal(str(mid_b)),
                ),
            ]
            ps.state = StatArbState.LONG

        ps.bars_in_position = 0
        self._metrics.trade_requests_generated += 1
        confidence = min(abs(zscore) / 5.0, 1.0)

        # US-240: Track entry for cooldown + spread-based PnL
        self._pair_last_trade[pair_key] = now_mono
        self._pair_entry_spread[pair_key] = spread
        _entry_notional = float(size_b) * mid_b
        self._pair_entry_notional[pair_key] = _entry_notional

        return TradeRequest(
            strategy_id=self.strategy_id,
            legs=legs,
            expected_profit_usdt=expected_spread_profit,
            confidence=confidence,
            metadata={
                "zscore": str(zscore),
                "hedge_ratio": str(beta),
                "cross_asset": "true",
                "state": str(ps.state),
                "symbol2": symbol_b,
                "effective_entry_threshold": str(effective_entry),
            },
        )

    # ------------------------------------------------------------------
    # Per-pair helper methods
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_half_life(spreads: list[float]) -> float:
        """Ornstein-Uhlenbeck half-life from spread autocorrelation.

        Fits a linear regression: delta_spread = beta * lag_spread + noise.
        Half-life = -ln(2) / beta.  Returns inf if not mean-reverting.
        """
        if len(spreads) < 22:  # need at least 20 lag pairs + 2
            return float('inf')
        try:
            import numpy as np
            spreads_arr = np.array(spreads)
            lag_spread = spreads_arr[:-1]
            delta_spread = np.diff(spreads_arr)
            if len(lag_spread) < 20:
                return float('inf')
            beta = np.polyfit(lag_spread, delta_spread, 1)[0]
            if beta >= 0:
                return float('inf')
            return -math.log(2) / beta
        except Exception:
            return float('inf')

    def _adaptive_entry_threshold_for_pair(self, ps: _PairState) -> float:
        """Return zscore_entry scaled up when current spread volatility is elevated."""
        if not self.config.adaptive_threshold:
            return self.config.zscore_entry

        spreads = list(ps.spreads)
        n = len(spreads)
        lookback = self.config.vol_lookback

        if n < lookback * 2:
            return self.config.zscore_entry

        recent = spreads[-lookback:]
        hist = spreads[:-lookback]

        def _std(xs: list[float]) -> float:
            if len(xs) < 2:
                return 0.0
            m = sum(xs) / len(xs)
            v = sum((x - m) ** 2 for x in xs) / len(xs)
            return math.sqrt(v)

        recent_vol = _std(recent)
        hist_vol = _std(hist)

        if hist_vol < 1e-12:
            return self.config.zscore_entry

        vol_ratio = max(0.0, recent_vol / hist_vol - 1.0)
        return self.config.zscore_entry * (1.0 + vol_ratio)

    def _has_sufficient_zero_crossings_for_pair(self, ps: _PairState) -> bool:
        """Return True if spread crossed zero at least min_zero_crossings times."""
        min_crossings = self.config.min_zero_crossings
        if min_crossings <= 0:
            return True

        spreads = list(ps.spreads)
        lookback = self.config.zero_crossing_lookback
        window = spreads[-lookback:] if len(spreads) >= lookback else spreads

        if len(window) < 2:
            return min_crossings == 0

        crossings = sum(
            1 for i in range(1, len(window))
            if (window[i - 1] >= 0) != (window[i] >= 0)
        )
        return crossings >= min_crossings

    def _is_cointegrated_for_pair(self, ps: _PairState) -> bool:
        """
        Run Engle-Granger cointegration test on per-pair price history.
        Fail-closed: returns False when statsmodels/numpy unavailable or test errors.
        """
        if not self.config.enable_cointegration:
            return True
        if len(ps.prices_a) < self.config.min_history:
            return False
        try:
            import numpy as np
            from statsmodels.tsa.stattools import coint
        except ImportError:
            return False  # fail-closed: no statsmodels → skip trade
        try:
            from numpy.linalg import LinAlgError
            a_arr = np.array(list(ps.prices_a))
            b_arr = np.array(list(ps.prices_b))
            _, pvalue, _ = coint(a_arr, b_arr)
            return float(pvalue) < self.config.cointegration_pvalue
        except (ValueError, LinAlgError):
            return False  # fail-closed: numerical failure → skip trade

    # ------------------------------------------------------------------
    # Legacy cross-exchange mode helpers (on_signal backward compat)
    # ------------------------------------------------------------------

    def _adaptive_entry_threshold(self) -> float:
        """Return zscore_entry scaled up when current spread volatility is elevated."""
        if not self.config.adaptive_threshold:
            return self.config.zscore_entry

        spreads = list(self._spreads)
        n = len(spreads)
        lookback = self.config.vol_lookback

        if n < lookback * 2:
            return self.config.zscore_entry

        recent = spreads[-lookback:]
        hist = spreads[:-lookback]

        def _std(xs: list[float]) -> float:
            if len(xs) < 2:
                return 0.0
            m = sum(xs) / len(xs)
            v = sum((x - m) ** 2 for x in xs) / len(xs)
            return math.sqrt(v)

        recent_vol = _std(recent)
        hist_vol = _std(hist)

        if hist_vol < 1e-12:
            return self.config.zscore_entry

        vol_ratio = max(0.0, recent_vol / hist_vol - 1.0)
        return self.config.zscore_entry * (1.0 + vol_ratio)

    def _has_sufficient_zero_crossings(self) -> bool:
        """Return True if the spread crossed zero at least min_zero_crossings times."""
        min_crossings = self.config.min_zero_crossings
        if min_crossings <= 0:
            return True

        spreads = list(self._spreads)
        lookback = self.config.zero_crossing_lookback
        window = spreads[-lookback:] if len(spreads) >= lookback else spreads

        if len(window) < 2:
            return min_crossings == 0

        crossings = sum(
            1 for i in range(1, len(window))
            if (window[i - 1] >= 0) != (window[i] >= 0)
        )
        return crossings >= min_crossings

    def _is_cointegrated(self) -> bool:
        """
        Run Engle-Granger cointegration test on legacy buy/sell price history.
        Fail-closed: returns False when statsmodels/numpy unavailable or test errors.
        """
        if not self.config.enable_cointegration:
            return True
        if len(self._buy_prices) < self.config.min_history:
            return False
        try:
            import numpy as np
            from statsmodels.tsa.stattools import coint
        except ImportError:
            return False  # fail-closed
        try:
            from numpy.linalg import LinAlgError
            buy_arr = np.array(list(self._buy_prices))
            sell_arr = np.array(list(self._sell_prices))
            _, pvalue, _ = coint(buy_arr, sell_arr)
            return float(pvalue) < self.config.cointegration_pvalue
        except (ValueError, LinAlgError):
            return False  # fail-closed

    # ------------------------------------------------------------------
    # Legacy on_signal (cross-exchange, same symbol)
    # ------------------------------------------------------------------

    async def on_signal(self, signal: Signal) -> Optional[TradeRequest]:
        self._metrics.signals_received += 1

        buy_f = float(signal.buy_price)
        sell_f = float(signal.sell_price)

        log_buy = math.log(buy_f) if buy_f > 0 else 0.0
        log_sell = math.log(sell_f) if sell_f > 0 else 0.0
        hedge_ratio = self._kalman.update(log_buy, log_sell)

        spread = log_sell - hedge_ratio * log_buy

        # Accumulate spread history regardless of active state (warmup progresses in background)
        self._buy_prices.append(buy_f)
        self._sell_prices.append(sell_f)
        self._spreads.append(spread)

        if not self._is_active:
            self._metrics.signals_filtered += 1
            return None

        if self._state != StatArbState.FLAT:
            self._bars_in_position += 1

        if len(self._spreads) < self.config.min_history:
            self._metrics.signals_filtered += 1
            return None

        history = list(self._spreads)[:-1]
        zscore = _zscore(history, spread)

        force_exit = (
            self._state != StatArbState.FLAT
            and self._bars_in_position >= self.config.max_holding_bars
        )

        if self._state == StatArbState.SHORT and (
            zscore < self.config.zscore_exit or force_exit
        ):
            size = min(signal.volume, self.config.max_position_size)
            self._state = StatArbState.FLAT
            self._bars_in_position = 0
            self._metrics.trade_requests_generated += 1
            exit_reason = "timeout" if force_exit else "zscore"
            return TradeRequest(
                strategy_id=self.strategy_id,
                legs=[
                    TradeLeg(
                        exchange_id=signal.buy_exchange,
                        symbol=signal.symbol,
                        side=OrderSide.SELL,
                        size=size,
                        order_type=OrderType.MARKET,
                        price=signal.buy_price,
                    ),
                    TradeLeg(
                        exchange_id=signal.sell_exchange,
                        symbol=signal.symbol,
                        side=OrderSide.BUY,
                        size=size,
                        order_type=OrderType.MARKET,
                        price=signal.sell_price,
                    ),
                ],
                expected_profit_usdt=Decimal("0"),
                confidence=signal.confidence,
                metadata={
                    "action": "exit",
                    "prev_state": "short",
                    "zscore": str(zscore),
                    "exit_reason": exit_reason,
                },
            )
        if self._state == StatArbState.LONG and (
            zscore > -self.config.zscore_exit or force_exit
        ):
            size = min(signal.volume, self.config.max_position_size)
            self._state = StatArbState.FLAT
            self._bars_in_position = 0
            self._metrics.trade_requests_generated += 1
            exit_reason = "timeout" if force_exit else "zscore"
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
                    ),
                    TradeLeg(
                        exchange_id=signal.buy_exchange,
                        symbol=signal.symbol,
                        side=OrderSide.BUY,
                        size=size,
                        order_type=OrderType.MARKET,
                        price=signal.buy_price,
                    ),
                ],
                expected_profit_usdt=Decimal("0"),
                confidence=signal.confidence,
                metadata={
                    "action": "exit",
                    "prev_state": "long",
                    "zscore": str(zscore),
                    "exit_reason": exit_reason,
                },
            )

        if self._state != StatArbState.FLAT:
            self._metrics.signals_filtered += 1
            return None

        effective_entry = self._adaptive_entry_threshold()

        if abs(zscore) < effective_entry:
            self._metrics.signals_filtered += 1
            return None

        if not self._has_sufficient_zero_crossings():
            self._metrics.signals_filtered += 1
            return None

        if not self._is_cointegrated():
            self._metrics.signals_filtered += 1
            return None

        size = min(signal.volume, self.config.max_position_size)

        if zscore > 0:
            buy_exchange = signal.buy_exchange
            sell_exchange = signal.sell_exchange
            buy_price = signal.buy_price
            sell_price = signal.sell_price
            new_state = StatArbState.SHORT
        else:
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
        self._bars_in_position = 0
        self._metrics.trade_requests_generated += 1

        abs_z = abs(zscore)
        confidence = signal.confidence * min(abs_z / 5.0, 1.0)
        effective_entry = self._adaptive_entry_threshold()

        return TradeRequest(
            strategy_id=self.strategy_id,
            legs=[
                TradeLeg(
                    exchange_id=buy_exchange,
                    symbol=signal.symbol,
                    side=OrderSide.BUY,
                    size=size,
                    order_type=OrderType.MARKET,
                    price=buy_price,
                ),
                TradeLeg(
                    exchange_id=sell_exchange,
                    symbol=signal.symbol,
                    side=OrderSide.SELL,
                    size=size,
                    order_type=OrderType.MARKET,
                    price=sell_price,
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
                "effective_entry_threshold": str(effective_entry),
            },
        )

    async def on_fill(self, trade: Trade) -> None:
        await super().on_fill(trade)
