"""WS-A4 seed: TCA observation layer feeding into pre-trade cost model.

Collects TCA slippage observations per (strategy, exchange) and exposes p95
via Prometheus gauge. **No threshold adjustment yet** — WS-B will consume the
exposed p95 to recompute `dynamic min_spread`.

Rationale: observation must be decoupled from threshold adjustment so the
pre-trade cost model can be tuned independently (see PHOENIX_PLAN §5 /
hidden-cuddling-pascal.md WS-B).
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

logger = logging.getLogger(__name__)


class TCAAdaptiveFeedback:
    """Rolling TCA slippage observations per (strategy, exchange).

    Usage:
        fb = TCAAdaptiveFeedback(window=100)
        fb.record_observation("funding_rate_arb", 12.5, exchange="binance")
        p95 = fb.p95_bps("funding_rate_arb", "binance")

    WS-A4 scope: observation + p95 gauge export only.
    WS-B will wire p95 into `dynamic min_spread` computation.
    """

    def __init__(self, window: int = 100) -> None:
        self.window = window
        # (strategy, exchange) -> deque[slippage_bps]
        self._observations: Dict[Tuple[str, str], Deque[float]] = defaultdict(
            lambda: deque(maxlen=window)
        )

    def record_observation(
        self,
        strategy: str,
        slippage_bps: float,
        exchange: str = "unknown",
    ) -> None:
        """Append one TCA slippage observation and refresh p95 gauge.

        `slippage_bps` may be negative (favorable fill) or positive (leakage).
        Negative values are clipped to 0.0 for p95 computation — only adverse
        slippage influences the cost model (matches live.py adverse-only IS).
        """
        if not strategy:
            strategy = "unknown"
        if not exchange:
            exchange = "unknown"
        key = (strategy, exchange)
        # Clip favorable slippage to 0 — only adverse slippage widens min_spread.
        bps = max(0.0, float(slippage_bps))
        self._observations[key].append(bps)
        # Refresh gauge after each observation (cheap, p95 is O(n log n) on ≤100 items).
        try:
            from src.infra.metrics import OBSERVED_SLIPPAGE_P95_BPS

            p95 = self.p95_bps(strategy, exchange)
            OBSERVED_SLIPPAGE_P95_BPS.labels(
                strategy=strategy, exchange=exchange,
            ).set(p95)
            logger.debug(
                "tca_feedback_recorded strategy=%s exchange=%s slippage_bps=%.2f p95=%.2f n=%d",
                strategy, exchange, bps, p95, len(self._observations[key]),
            )
        except Exception as exc:
            # Prometheus not available / labels error — non-fatal for observation.
            logger.debug("tca_feedback_metric_export_failed error=%s", exc)

    def p95_bps(self, strategy: str, exchange: str) -> float:
        """Return observed slippage p95 (bps) for (strategy, exchange). 0.0 if no data."""
        key = (strategy, exchange)
        data = list(self._observations.get(key, ()))
        n = len(data)
        if n == 0:
            return 0.0
        if n == 1:
            return data[0]
        data_sorted = sorted(data)
        k = 0.95 * (n - 1)
        f = math.floor(k)
        c = min(f + 1, n - 1)
        if f == c:
            return data_sorted[f]
        return data_sorted[f] + (k - f) * (data_sorted[c] - data_sorted[f])

    def sample_count(self, strategy: str, exchange: str) -> int:
        """Number of observations currently held for (strategy, exchange)."""
        return len(self._observations.get((strategy, exchange), ()))
