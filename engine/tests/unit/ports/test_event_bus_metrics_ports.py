"""Phase 7 EventBusPort + MetricsPort runtime_checkable 검증.

Gemini Priority 2 (architecture-audit-2026-04-26):
- EventBusPort: Nautilus MessageBus 산업 표준 미러
- MetricsPort: LEAN telemetry / OpenTelemetry 호환 추상화
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping

import pytest

from src.ports import EventBusPort, MetricsPort


class _MockEventBus:
    async def publish(self, stream: str, event: dict[str, Any]) -> bytes:
        return b"id-1"

    async def create_consumer_group(
        self, stream: str, group: str, *, last_id: str = "0", mkstream: bool = True
    ) -> bool:
        return True

    async def subscribe(
        self,
        stream: str,
        group: str,
        consumer: str,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        block_ms: int = 1000,
        count: int = 100,
    ) -> None:
        pass

    async def ack_message(self, stream: str, group: str, msg_id: bytes | str) -> None:
        pass

    async def handle_dead_letters(
        self, stream: str, group: str, *, max_retry: int = 3
    ) -> int:
        return 0


class _MockMetrics:
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


class TestEventBusPort:
    def test_mock_implements_port(self) -> None:
        assert isinstance(_MockEventBus(), EventBusPort)

    def test_in_memory_event_bus_implements_port(self) -> None:
        from src.infra.redis.memory_bus import InMemoryEventBus
        bus = InMemoryEventBus(maxsize=10)
        assert isinstance(bus, EventBusPort)


class TestMetricsPort:
    def test_mock_implements_port(self) -> None:
        assert isinstance(_MockMetrics(), MetricsPort)


class TestPortsExports:
    def test_all_9_ports_exported(self) -> None:
        from src.ports import __all__
        expected = {
            "DataFeedPort", "EventBusPort", "ExchangeAdapterPort",
            "ExecutorPort", "JournalPort", "KillSwitchPort",
            "LedgerPort", "MetricsPort", "RiskPort",
        }
        assert set(__all__) == expected
