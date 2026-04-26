"""MetricsPort — Phase 7 telemetry write-side abstraction (2026-04-26).

Gemini Priority 2 (architecture-audit-2026-04-26): LEAN telemetry / Hummingbot
metrics 산업 표준 미러. Listeners/runtime은 Prometheus 직접 의존 대신 이 Port
의존 — 향후 OpenTelemetry / StatsD / Datadog swap 가능.

스코프 (Codex BLOCKING 정합, 2026-04-26 v2):
- **WRITE-SIDE only** — Counter/Gauge/Histogram emit
- Read-side (api/routes/pnl_attributed.py:60 collectors introspection)는 별도 책임,
  이 Port는 다루지 않음. 이는 Phase 8+ 후속 작업 (`MetricsQueryPort` 분리 가능).

구현체 (Phase 7 후속, 미구현 — 의도된 추상화):
- PrometheusMetricsAdapter — engine/src/infra/metrics.py wrap (TODO)
- NoOpMetricsAdapter (test, 신규)

현재 정확한 상태: 추상화 declared, adapter 미구현 → call-site 마이그레이션 시 함께 추가.
"""
from __future__ import annotations

from typing import Mapping, Protocol, runtime_checkable


@runtime_checkable
class MetricsPort(Protocol):
    """Hexagonal port for engine telemetry write-side emission.

    3-method 최소 인터페이스 (Counter/Gauge/Histogram). Prometheus와 정합하되
    OpenTelemetry/StatsD adapter 추가 가능. labels는 Mapping (None 가능).

    NOTE: read-side 메트릭 조회 (collectors.labels(...)._value)는 본 Port 범위 외.
    """

    def increment_counter(
        self,
        name: str,
        value: float = 1.0,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        """Counter 메트릭 증가 (단조 증가만 허용).

        예: increment_counter("trade_request_executed", 1, {"strategy": "spot_futures"})
        Prometheus equivalent: counter.labels(**labels).inc(value)
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
        Prometheus equivalent: gauge.labels(**labels).set(value)
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
        Prometheus equivalent: histogram.labels(**labels).observe(value)
        """
        ...
