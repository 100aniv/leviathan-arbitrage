"""NoOpMetricsAdapter — MetricsPort 무동작 구현 (2026-04-27).

목적:
- Test fixture (Prometheus 의존성 없이 listener/runtime 테스트 가능)
- Bootstrap fallback (Prometheus 미설치 환경)

Production은 PrometheusMetricsAdapter 사용 예정 (Phase 8 후속).
"""
from __future__ import annotations

from typing import Mapping


class NoOpMetricsAdapter:
    """MetricsPort no-op impl — 모든 emit 호출이 silent skip."""

    def increment_counter(
        self,
        name: str,
        value: float = 1.0,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        pass

    def set_gauge(
        self,
        name: str,
        value: float,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        pass

    def observe_histogram(
        self,
        name: str,
        value: float,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        pass
