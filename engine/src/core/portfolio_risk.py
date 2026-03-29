"""Portfolio-level risk management: correlation, VaR, volatility, MDD.

US-277: PortfolioRiskManager — correlation matrix, VaR, portfolio volatility.
US-278: MDD tracking per strategy and portfolio.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Strategy MDD threshold (3%) and portfolio MDD threshold (5%)
STRATEGY_MDD_THRESHOLD_PCT = 0.03
PORTFOLIO_MDD_THRESHOLD_PCT = 0.05
MIN_SAMPLES_FOR_STATS = 20


class PortfolioRiskManager:
    """Track strategy returns and compute portfolio-level risk metrics.

    Args:
        window_minutes: Rolling window for return history (default 30 min).
        enabled: If False, all methods return None/empty immediately.
    """

    def __init__(self, window_minutes: int = 30, enabled: bool = True) -> None:
        self._returns: dict[str, list[float]] = defaultdict(list)
        self._timestamps: dict[str, list[float]] = defaultdict(list)
        self._window = window_minutes * 60
        self._enabled = enabled
        # US-278: equity tracking
        self._peak_equity: dict[str, float] = {}     # strategy_id -> peak
        self._current_equity: dict[str, float] = {}  # strategy_id -> current
        self._portfolio_peak: float = 0.0
        self._portfolio_equity: float = 0.0

    # ------------------------------------------------------------------
    # US-277: Returns & Stats
    # ------------------------------------------------------------------

    def update_returns(
        self,
        strategy_id: str,
        pnl: float,
        timestamp: float | None = None,
    ) -> None:
        """Append a PnL sample and prune entries outside the rolling window."""
        if not self._enabled:
            return
        ts = timestamp if timestamp is not None else time.time()
        self._returns[strategy_id].append(pnl)
        self._timestamps[strategy_id].append(ts)
        # Prune old entries
        cutoff = ts - self._window
        while self._timestamps[strategy_id] and self._timestamps[strategy_id][0] < cutoff:
            self._timestamps[strategy_id].pop(0)
            self._returns[strategy_id].pop(0)

    def get_correlation_matrix(self) -> dict[str, dict[str, float]] | None:
        """Pearson correlation matrix across strategies.

        Returns None if any strategy has fewer than MIN_SAMPLES_FOR_STATS samples.
        """
        if not self._enabled:
            return None
        strategies = [s for s, r in self._returns.items() if len(r) >= MIN_SAMPLES_FOR_STATS]
        if len(strategies) < 2:
            return None

        # Align lengths by truncating to the shortest series
        min_len = min(len(self._returns[s]) for s in strategies)
        matrix: list[list[float]] = [self._returns[s][-min_len:] for s in strategies]
        arr = np.array(matrix, dtype=float)
        corr = np.corrcoef(arr)

        result: dict[str, dict[str, float]] = {}
        for i, si in enumerate(strategies):
            result[si] = {}
            for j, sj in enumerate(strategies):
                result[si][sj] = float(corr[i, j])
        return result

    def get_var(self, confidence: float = 0.95) -> float | None:
        """Historical VaR at given confidence level using combined return series.

        Returns None if fewer than MIN_SAMPLES_FOR_STATS total samples.
        """
        if not self._enabled:
            return None
        all_returns: list[float] = []
        for returns in self._returns.values():
            all_returns.extend(returns)
        if len(all_returns) < MIN_SAMPLES_FOR_STATS:
            return None
        arr = np.array(all_returns, dtype=float)
        var = float(np.percentile(arr, (1.0 - confidence) * 100))
        return var  # negative value = loss at this confidence level

    def get_portfolio_volatility(self) -> float | None:
        """Portfolio volatility: sqrt(w^T * Σ * w) with equal weights.

        Returns None if insufficient data.
        """
        if not self._enabled:
            return None
        strategies = [s for s, r in self._returns.items() if len(r) >= MIN_SAMPLES_FOR_STATS]
        if not strategies:
            return None
        min_len = min(len(self._returns[s]) for s in strategies)
        arr = np.array([self._returns[s][-min_len:] for s in strategies], dtype=float)
        cov = np.cov(arr)
        n = len(strategies)
        w = np.full(n, 1.0 / n)
        if n == 1:
            vol = float(np.std(arr[0]))
        else:
            vol = float(np.sqrt(w @ cov @ w))
        return vol

    def check_correlation_breach(
        self, threshold: float = 0.7
    ) -> list[tuple[str, str, float]]:
        """Return strategy pairs whose correlation exceeds threshold."""
        if not self._enabled:
            return []
        corr = self.get_correlation_matrix()
        if corr is None:
            return []
        breaches: list[tuple[str, str, float]] = []
        strategies = list(corr.keys())
        for i, si in enumerate(strategies):
            for sj in strategies[i + 1:]:
                val = corr[si][sj]
                if val > threshold:
                    breaches.append((si, sj, val))
        return breaches

    # ------------------------------------------------------------------
    # US-278: MDD Tracking
    # ------------------------------------------------------------------

    def update_equity(self, strategy_id: str, equity: float) -> None:
        """Update current equity for a strategy and portfolio total."""
        if not self._enabled:
            return
        self._current_equity[strategy_id] = equity
        if equity > self._peak_equity.get(strategy_id, equity):
            self._peak_equity[strategy_id] = equity
        elif strategy_id not in self._peak_equity:
            self._peak_equity[strategy_id] = equity

        # Portfolio total
        self._portfolio_equity = sum(self._current_equity.values())
        if self._portfolio_equity > self._portfolio_peak:
            self._portfolio_peak = self._portfolio_equity

    def check_mdd_breach(self) -> dict[str, Any]:
        """Return MDD breach status for strategies and portfolio.

        Returns:
            {
                "strategy_breaches": [{"strategy_id": str, "mdd_pct": float}],
                "portfolio_breach": bool,
                "portfolio_mdd_pct": float,
            }
        """
        if not self._enabled:
            return {
                "strategy_breaches": [],
                "portfolio_breach": False,
                "portfolio_mdd_pct": 0.0,
            }

        strategy_breaches: list[dict[str, Any]] = []
        for sid, peak in self._peak_equity.items():
            if peak <= 0:
                continue
            current = self._current_equity.get(sid, peak)
            mdd_pct = (peak - current) / peak
            if mdd_pct > STRATEGY_MDD_THRESHOLD_PCT:
                strategy_breaches.append({"strategy_id": sid, "mdd_pct": round(mdd_pct, 6)})

        portfolio_mdd_pct = 0.0
        if self._portfolio_peak > 0:
            portfolio_mdd_pct = (self._portfolio_peak - self._portfolio_equity) / self._portfolio_peak
        portfolio_breach = portfolio_mdd_pct > PORTFOLIO_MDD_THRESHOLD_PCT

        return {
            "strategy_breaches": strategy_breaches,
            "portfolio_breach": portfolio_breach,
            "portfolio_mdd_pct": round(portfolio_mdd_pct, 6),
        }
