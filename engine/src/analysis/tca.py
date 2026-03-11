"""Transaction Cost Analysis (TCA) — Implementation Shortfall + Latency + Fill Rate.

US-116: Measures execution quality via rolling percentile windows.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class ExecutionRecord:
    """Single execution measurement."""
    expected_price: float
    fill_price: float
    latency_ms: float
    filled_ratio: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    strategy_id: str = ""


class PercentileTracker:
    """Rolling window percentile calculator (no numpy dependency)."""

    def __init__(self, window_size: int = 1000) -> None:
        self._data: deque[float] = deque(maxlen=window_size)

    def add(self, value: float) -> None:
        self._data.append(value)

    def percentile(self, pct: float) -> float:
        if not self._data:
            return 0.0
        sorted_data = sorted(self._data)
        idx = (pct / 100.0) * (len(sorted_data) - 1)
        lower = int(idx)
        upper = min(lower + 1, len(sorted_data) - 1)
        frac = idx - lower
        return sorted_data[lower] * (1 - frac) + sorted_data[upper] * frac

    @property
    def count(self) -> int:
        return len(self._data)


class TCAAnalyzer:
    """Transaction Cost Analysis — tracks IS, latency, and fill rate."""

    def __init__(self, window_size: int = 1000) -> None:
        self._is_tracker = PercentileTracker(window_size)
        self._latency_tracker = PercentileTracker(window_size)
        self._fill_rates: deque[float] = deque(maxlen=window_size)
        self._records: deque[ExecutionRecord] = deque(maxlen=window_size)

    def record_execution(
        self,
        expected_price: float,
        fill_price: float,
        latency_ms: float,
        filled_ratio: float,
        strategy_id: str = "",
    ) -> None:
        if expected_price <= 0:
            return
        is_bps = abs(fill_price - expected_price) / expected_price * 10_000
        self._is_tracker.add(is_bps)
        self._latency_tracker.add(max(0.0, latency_ms))
        self._fill_rates.append(max(0.0, min(1.0, filled_ratio)))
        self._records.append(ExecutionRecord(
            expected_price=expected_price,
            fill_price=fill_price,
            latency_ms=latency_ms,
            filled_ratio=filled_ratio,
            strategy_id=strategy_id,
        ))

    def get_summary(self) -> dict:
        fill_rate = (
            sum(self._fill_rates) / len(self._fill_rates) * 100
            if self._fill_rates else 0.0
        )
        return {
            "is_p50_bps": round(self._is_tracker.percentile(50), 2),
            "is_p95_bps": round(self._is_tracker.percentile(95), 2),
            "latency_p50_ms": round(self._latency_tracker.percentile(50), 1),
            "latency_p95_ms": round(self._latency_tracker.percentile(95), 1),
            "latency_p99_ms": round(self._latency_tracker.percentile(99), 1),
            "fill_rate_pct": round(fill_rate, 1),
            "sample_count": self._is_tracker.count,
        }
