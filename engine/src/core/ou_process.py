"""Ornstein-Uhlenbeck process estimator for mean-reversion analysis.

[MUST FIX #1] Uses time-weighted OLS for event-driven (non-uniform) data.
dX[i] = a*dt[i] + b*X[i-1]*dt[i] weighted regression.
"""
from __future__ import annotations
import math
import time
from collections import deque
from typing import Optional

class OUProcess:
    def __init__(self, window: int = 360):
        self._history: deque[tuple[float, float]] = deque(maxlen=window)  # (timestamp_s, value)
        self._theta: float = 0.0
        self._mu: float = 0.0
        self._sigma: float = 0.0
        self._fitted: bool = False
        self._min_samples: int = 30

    def update(self, value: float, timestamp_s: float | None = None) -> None:
        if timestamp_s is None:
            timestamp_s = time.monotonic()
        self._history.append((timestamp_s, value))
        if len(self._history) >= self._min_samples:
            self._fit()

    def _fit(self) -> None:
        """Time-weighted OLS: dX = a*dt + b*X*dt"""
        import numpy as np
        n = len(self._history)
        if n < self._min_samples:
            return
        times = [h[0] for h in self._history]
        values = [h[1] for h in self._history]
        dt = [max(times[i] - times[i-1], 0.1) for i in range(1, n)]  # min_dt=0.1s
        dX = [values[i] - values[i-1] for i in range(1, n)]
        X_prev = values[:-1]
        # dX = a*dt + b*X*dt => dX/dt = a + b*X
        # Weighted: y = dX[i], A = [[dt[i], X[i-1]*dt[i]]]
        A = np.array([[dt[i], X_prev[i] * dt[i]] for i in range(len(dt))])
        y = np.array(dX)
        try:
            result, _, _, _ = np.linalg.lstsq(A, y, rcond=None)
            a, b = result
            if b < 0:  # mean-reverting
                self._theta = -b
                self._mu = a / self._theta if self._theta > 1e-10 else 0.0
                residuals = y - A @ result
                self._sigma = float(np.std(residuals))
                self._fitted = True
            else:
                self._fitted = False
                self._theta = 0.0
        except Exception:
            self._fitted = False

    @property
    def half_life(self) -> float:
        if not self._fitted or self._theta <= 1e-10:
            return float('inf')
        return math.log(2) / self._theta

    @property
    def mu(self) -> float:
        return self._mu

    @property
    def theta(self) -> float:
        return self._theta

    @property
    def is_mean_reverting(self) -> bool:
        return self._fitted and self._theta > 1e-10

    def predict(self, horizon_s: float) -> float:
        if not self._fitted or not self._history:
            return self._history[-1][1] if self._history else 0.0
        current = self._history[-1][1]
        return self._mu + (current - self._mu) * math.exp(-self._theta * horizon_s)
