"""US-260/261: Rolling percentile + volatility-weighted adaptive thresholds.

Maintains a rolling window of spread/basis observations and computes
dynamic entry/exit thresholds based on the empirical distribution.
Volatility multiplier widens thresholds during high-vol regimes.

Usage:
    at = AdaptiveThreshold(window=1440, entry_pctile=95, exit_pctile=50)
    at.update(spread_bps=12.5)
    entry, exit_ = at.thresholds  # dynamic values

A/B logging: every call to `thresholds` logs static_vs_dynamic comparison
so shadow-tester can measure false-positive reduction.
"""
from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class AdaptiveThreshold:
    """Rolling percentile threshold with volatility weighting."""

    # Window size (number of observations, ~24H at 1 obs/min = 1440)
    window: int = 1440
    # Percentiles for entry (high = conservative) and exit (low = quick exit)
    entry_percentile: float = 95.0
    exit_percentile: float = 50.0
    # Static fallback thresholds (used until enough data collected)
    static_entry: float = 10.0   # bps
    static_exit: float = 5.0     # bps
    # Minimum observations before using dynamic thresholds
    min_samples: int = 60
    # Cap: dynamic entry must not exceed static_entry * max_entry_multiplier
    # Prevents stale/anomaly spreads from inflating threshold beyond usable range
    max_entry_multiplier: float = 2.0  # e.g. static=5bps → max dynamic=10bps (SignalGenerator already ensures net profitability)
    # Volatility multiplier parameters
    vol_lookback: int = 60       # observations for vol calculation
    vol_baseline: float = 0.0    # set on first vol calc
    vol_multiplier_cap: float = 2.0  # max multiplier

    _observations: deque = field(default_factory=lambda: deque(maxlen=1440), repr=False)
    _vol_baseline_set: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        self._observations = deque(maxlen=self.window)

    def update(self, value_bps: float) -> None:
        """Record a new spread/basis observation (in bps).

        Filters outliers: values > static_entry * max_entry_multiplier are
        likely stale/anomaly data and would inflate dynamic thresholds.
        """
        max_allowed = self.static_entry * self.max_entry_multiplier
        if value_bps > max_allowed:
            return  # skip stale/anomaly outlier
        if value_bps < 0:
            return  # skip negative spreads (data error)
        self._observations.append(value_bps)

    def _percentile(self, pct: float) -> float:
        """Compute percentile from observations using linear interpolation."""
        data = sorted(self._observations)
        n = len(data)
        if n == 0:
            return 0.0
        if n == 1:
            return data[0]
        k = (pct / 100.0) * (n - 1)
        f = math.floor(k)
        c = min(f + 1, n - 1)
        if f == c:
            return data[f]
        return data[f] + (k - f) * (data[c] - data[f])

    def _volatility_multiplier(self) -> float:
        """Compute volatility multiplier (>1 during high-vol, 1 during normal)."""
        if len(self._observations) < self.vol_lookback:
            return 1.0
        recent = list(self._observations)[-self.vol_lookback:]
        if len(recent) < 2:
            return 1.0
        mean = sum(recent) / len(recent)
        var = sum((x - mean) ** 2 for x in recent) / (len(recent) - 1)
        vol = math.sqrt(var) if var > 0 else 0.0

        if not self._vol_baseline_set and len(self._observations) >= self.min_samples:
            self.vol_baseline = vol
            self._vol_baseline_set = True

        if self.vol_baseline <= 0:
            return 1.0

        ratio = vol / self.vol_baseline
        return min(max(ratio, 1.0), self.vol_multiplier_cap)

    @property
    def is_ready(self) -> bool:
        """Whether enough samples have been collected for dynamic thresholds."""
        return len(self._observations) >= self.min_samples

    @property
    def thresholds(self) -> tuple[float, float]:
        """Return (entry_threshold_bps, exit_threshold_bps).

        Uses dynamic percentile-based values when ready, static fallback otherwise.
        Applies volatility multiplier to widen thresholds during high-vol periods.
        """
        if not self.is_ready:
            return (self.static_entry, self.static_exit)

        vol_mult = self._volatility_multiplier()
        dynamic_entry = self._percentile(self.entry_percentile) * vol_mult
        dynamic_exit = self._percentile(self.exit_percentile)

        # A/B comparison logging for shadow analysis
        logger.debug(
            "adaptive_threshold.ab_compare static_entry=%.2f dynamic_entry=%.2f "
            "static_exit=%.2f dynamic_exit=%.2f vol_mult=%.3f samples=%d",
            self.static_entry, dynamic_entry,
            self.static_exit, dynamic_exit,
            vol_mult, len(self._observations),
        )

        # Cap: prevent stale/anomaly data from inflating threshold
        max_entry = self.static_entry * self.max_entry_multiplier
        if dynamic_entry > max_entry:
            logger.info(
                "adaptive_threshold.capped dynamic=%.2f max=%.2f static=%.2f",
                dynamic_entry, max_entry, self.static_entry,
            )
            dynamic_entry = max_entry

        # Ensure entry >= exit (sanity)
        if dynamic_entry < dynamic_exit:
            dynamic_entry = dynamic_exit * 1.5

        return (dynamic_entry, dynamic_exit)

    @property
    def sample_count(self) -> int:
        return len(self._observations)
