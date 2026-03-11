"""Strategy PnL correlation monitor — rolling window Pearson correlation.

US-118: Detect highly correlated strategy pairs and emit position scale-down events.
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass

import structlog

from src.infra.metrics import STRATEGY_CORRELATION

logger = structlog.get_logger(__name__)


@dataclass
class PositionScaleEvent:
    strategy_id: str
    scale: float
    reason: str


class CorrelationMonitor:
    """Rolling-window Pearson correlation monitor across strategy PnL streams.

    When correlation between two strategies exceeds threshold, emits a
    PositionScaleEvent to scale down the lower-performing strategy.
    """

    def __init__(self, window: int = 30, threshold: float = 0.7) -> None:
        self._window = window
        self._threshold = threshold
        self._pnl_history: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=window)
        )

    def record_trade_pnl(self, strategy_id: str, pnl: float) -> None:
        """Append a realized PnL observation for the given strategy."""
        self._pnl_history[strategy_id].append(pnl)

    @staticmethod
    def pearson(x: list[float], y: list[float]) -> float | None:
        """Compute sample Pearson correlation coefficient.

        Returns None when std-dev is zero (constant series) or n < 2.

        NOTE: Pearson captures linear co-movement only. Nonlinear tail-dependence
        is not detected. Consider Spearman or regime-conditional correlation in Phase K.
        """
        n = len(x)
        if n < 2:
            return None
        mx = sum(x) / n
        my = sum(y) / n
        sx = math.sqrt(sum((xi - mx) ** 2 for xi in x) / (n - 1))
        sy = math.sqrt(sum((yi - my) ** 2 for yi in y) / (n - 1))
        if sx == 0 or sy == 0:
            return None
        cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / (n - 1)
        return cov / (sx * sy)

    def check_correlations(self) -> list[PositionScaleEvent]:
        """Evaluate all strategy pairs; return scale-down events for high correlations."""
        events: list[PositionScaleEvent] = []
        strategies = [
            s for s, h in self._pnl_history.items() if len(h) >= self._window
        ]
        for i, s1 in enumerate(strategies):
            for s2 in strategies[i + 1 :]:
                x = list(self._pnl_history[s1])[-self._window :]
                y = list(self._pnl_history[s2])[-self._window :]
                corr = self.pearson(x, y)
                if corr is None:
                    continue

                STRATEGY_CORRELATION.labels(strategy_a=s1, strategy_b=s2).set(corr)

                if corr > self._threshold:
                    total1 = sum(self._pnl_history[s1])
                    total2 = sum(self._pnl_history[s2])
                    smaller = s1 if total1 <= total2 else s2
                    reason = f"corr({s1},{s2})={corr:.3f}>{self._threshold}"
                    logger.warning(
                        "strategy_correlation_high",
                        s1=s1,
                        s2=s2,
                        corr=corr,
                        scale_down=smaller,
                    )
                    events.append(
                        PositionScaleEvent(
                            strategy_id=smaller,
                            scale=0.5,
                            reason=reason,
                        )
                    )
        return events
