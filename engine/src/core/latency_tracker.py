"""Per-exchange latency tracker with sliding window and EMA smoothing."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional


@dataclass
class ExchangeLatencyInfo:
    """Latency statistics for a single exchange."""

    exchange_id: str
    ema_ms: float
    window_avg_ms: float
    sample_count: int


class LatencyTracker:
    """
    Tracks per-exchange message latency with sliding window + EMA smoothing.

    Args:
        window_size: Number of samples kept in the sliding window.
        ema_alpha:   EMA smoothing factor (higher = faster response to changes).
    """

    DEFAULT_EMA_ALPHA = 0.2

    def __init__(
        self,
        window_size: int = 50,
        ema_alpha: float = DEFAULT_EMA_ALPHA,
    ) -> None:
        self._window_size = window_size
        self._alpha = ema_alpha
        self._windows: dict[str, deque[float]] = {}
        self._emas: dict[str, float] = {}
        self._counts: dict[str, int] = {}

    def record_latency(self, exchange_id: str, latency_ms: float) -> None:
        """Record a new latency observation for exchange_id."""
        if exchange_id not in self._windows:
            self._windows[exchange_id] = deque(maxlen=self._window_size)
            self._emas[exchange_id] = latency_ms
            self._counts[exchange_id] = 0
        else:
            prev = self._emas[exchange_id]
            self._emas[exchange_id] = self._alpha * latency_ms + (1.0 - self._alpha) * prev

        self._windows[exchange_id].append(latency_ms)
        self._counts[exchange_id] += 1

    def get_latency_info(self, exchange_id: str) -> Optional[ExchangeLatencyInfo]:
        """Return latency statistics for exchange_id, or None if unknown."""
        if exchange_id not in self._windows:
            return None
        window = self._windows[exchange_id]
        avg = sum(window) / len(window) if window else 0.0
        return ExchangeLatencyInfo(
            exchange_id=exchange_id,
            ema_ms=self._emas[exchange_id],
            window_avg_ms=avg,
            sample_count=self._counts[exchange_id],
        )

    def ranked_exchanges(self) -> list[str]:
        """Return all known exchanges sorted by EMA latency ascending (fastest first)."""
        return sorted(self._emas.keys(), key=lambda ex: self._emas[ex])

    def lead_lag_pairs(self, threshold_ms: float = 5.0) -> list[tuple[str, str]]:
        """
        Return (fast, slow) exchange pairs where EMA latency difference >= threshold_ms.

        The fast exchange is the 'leader' — its prices move before the slow one.
        """
        exchanges = list(self._emas.keys())
        pairs: list[tuple[str, str]] = []
        for i, ex_a in enumerate(exchanges):
            for ex_b in exchanges[i + 1:]:
                diff = self._emas[ex_b] - self._emas[ex_a]
                if diff >= threshold_ms:
                    pairs.append((ex_a, ex_b))
                elif -diff >= threshold_ms:
                    pairs.append((ex_b, ex_a))
        return pairs
