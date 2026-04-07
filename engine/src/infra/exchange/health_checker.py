"""Exchange health scoring — produces a 0.0-1.0 score from multiple metrics."""
from __future__ import annotations

import statistics
import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class HealthMetrics:
    """Rolling metrics for a single exchange."""

    api_latencies: deque = field(default_factory=lambda: deque(maxlen=100))
    ws_disconnect_times: deque = field(default_factory=lambda: deque(maxlen=100))
    order_fill_rates: deque = field(default_factory=lambda: deque(maxlen=50))
    error_count: int = 0
    last_heartbeat: float = field(default_factory=time.monotonic)
    is_connected: bool = True  # PHOENIX: assume connected until WS disconnect recorded


class HealthChecker:
    """
    Exchange health scoring.

    Score is a weighted composite:
      - Connection state & staleness: 40%
      - API latency:                  30%
      - WebSocket stability:          20%
      - Order fill rate:              10%
    """

    def __init__(
        self,
        exchange_id: str,
        stale_threshold_seconds: float = 120.0,  # PHOENIX: 5→120s (REST adapters poll every ~30s)
        max_acceptable_latency_ms: float = 500.0,
    ) -> None:
        self.exchange_id = exchange_id
        self.stale_threshold = stale_threshold_seconds
        self.max_latency_ms = max_acceptable_latency_ms
        self._metrics = HealthMetrics()

    def record_api_latency(self, latency_ms: float) -> None:
        self._metrics.api_latencies.append(latency_ms)
        self._metrics.last_heartbeat = time.monotonic()  # REST calls count as heartbeat

    def record_ws_disconnect(self) -> None:
        self._metrics.ws_disconnect_times.append(time.monotonic())
        self._metrics.is_connected = False

    def record_ws_connect(self) -> None:
        self._metrics.is_connected = True
        self._metrics.last_heartbeat = time.monotonic()

    def record_heartbeat(self) -> None:
        self._metrics.last_heartbeat = time.monotonic()

    def record_order_fill(self, filled: bool) -> None:
        self._metrics.order_fill_rates.append(1.0 if filled else 0.0)

    def record_error(self) -> None:
        self._metrics.error_count += 1

    @property
    def health_score(self) -> float:
        # --- Connection score (40%) ---
        if self._metrics.is_connected:
            staleness = time.monotonic() - self._metrics.last_heartbeat
            if staleness <= self.stale_threshold:
                connection_score = 1.0
            else:
                # Decay to 0 over 30 seconds past stale threshold
                connection_score = max(0.0, 1.0 - (staleness - self.stale_threshold) / 30.0)
        else:
            connection_score = 0.0

        # --- Latency score (30%) ---
        if self._metrics.api_latencies:
            avg_latency = statistics.mean(self._metrics.api_latencies)
            latency_score = max(0.0, 1.0 - avg_latency / self.max_latency_ms)
        else:
            latency_score = 0.5  # neutral — no data yet

        # --- WebSocket stability score (20%) ---
        now = time.monotonic()
        recent_disconnects = sum(
            1 for t in self._metrics.ws_disconnect_times if now - t < 300  # last 5 min
        )
        ws_score = max(0.0, 1.0 - recent_disconnects * 0.2)

        # --- Order fill rate score (10%) ---
        if self._metrics.order_fill_rates:
            fill_score = statistics.mean(self._metrics.order_fill_rates)
        else:
            fill_score = 1.0  # optimistic when no orders placed

        total = (
            connection_score * 0.4
            + latency_score * 0.3
            + ws_score * 0.2
            + fill_score * 0.1
        )
        return min(1.0, max(0.0, total))

    def reset(self) -> None:
        self._metrics = HealthMetrics()
