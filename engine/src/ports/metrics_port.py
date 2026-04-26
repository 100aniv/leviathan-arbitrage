"""MetricsPort — Phase 7 telemetry abstraction (2026-04-26).

Gemini Priority 2 (architecture-audit-2026-04-26): LEAN telemetry / Hummingbot
metrics 산업 표준 미러. Listeners/runtime은 Prometheus 직접 의존 대신 이 Port
의존 — 향후 OpenTelemetry / StatsD / Datadog swap 가능.

구현체 (Phase 7 후속):
- PrometheusMetricsAdapter (engine/src/infra/metrics.py wrap)
- NoOpMetricsAdapter (test)
"""
from __future__ import annotations

from typing import Mapping, Protocol, runtime_checkable


@runtime_checkable
class MetricsPort(Protocol):
    """Hexagonal port for engine telemetry.

    3-method 최소 인터페이스 (Counter/Gauge/Histogram). Prometheus와 정합하되
    OpenTelemetry/StatsD adapter 추가 가능. labels는 dict 또는 비어있는 경우 None.
    """

    def increment_counter(
        self,
        name: str,
        value: float = 1.0,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        """Counter 메트릭 증가 (단조 증가만 허용).

        예: increment_counter("trade_request_executed", 1, {"strategy": "spot_futures"})
        """
        ...

    def set_gauge(
        self,
        name: str,
        value: float,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        """Gauge 메트릭 절대값 설정.

        예: set_gauge("position_count", 3, {"exchange": "binance"})
        """
        ...

    def observe_histogram(
        self,
        name: str,
        value: float,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        """Histogram 메트릭 분포 관측.

        예: observe_histogram("trade_latency_ms", 47.2, {"adapter": "binance"})
        """
        ...
