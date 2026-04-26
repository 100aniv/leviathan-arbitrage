"""Phase 7 EventBusPort + MetricsPort runtime_checkable 검증.

Gemini Priority 2 (architecture-audit-2026-04-26):
- EventBusPort: Nautilus MessageBus 산업 표준 미러
- MetricsPort: LEAN telemetry / OpenTelemetry 호환 추상화 (write-side)

Codex BLOCKING (2026-04-26 v2) 정합 완료:
- EventBusPort 시그니처 = src/infra/redis/{event_bus,memory_bus}.py 1:1 매칭
- create_consumer_group(stream, group, start_id="0") -> None
- subscribe(stream, group, consumer, count=10, block_ms=None, raw=False) -> list[dict]
- handle_dead_letters(stream, group, consumer) -> list[dict]
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

import pytest

from src.ports import EventBusPort, MetricsPort


class _MockEventBus:
    async def publish(self, stream: str, event: dict[str, Any]) -> bytes:
        return b"id-1"

    async def create_consumer_group(
        self, stream: str, group: str, start_id: str = "0"
    ) -> None:
        pass

    async def subscribe(
        self,
        stream: str,
        group: str,
        consumer: str,
        count: int = 10,
        block_ms: Optional[int] = None,
        raw: bool = False,
    ) -> list[dict]:
        return []

    async def ack_message(self, stream: str, group: str, msg_id: bytes | str) -> None:
        pass

    async def handle_dead_letters(
        self, stream: str, group: str, consumer: str
    ) -> list[dict]:
        return []


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

    @pytest.mark.asyncio
    async def test_in_memory_event_bus_real_pub_sub(self) -> None:
        """실제 InMemoryEventBus pub/sub round-trip — Codex BLOCKING parameter
        정합 검증 (단순 isinstance 외 실제 호출 검증)."""
        from src.infra.redis.memory_bus import InMemoryEventBus
        bus = InMemoryEventBus(maxsize=10)
        await bus.create_consumer_group("test_stream", "test_group", start_id="0")
        msg_id = await bus.publish("test_stream", {"data": 42})
        assert isinstance(msg_id, bytes)
        msgs = await bus.subscribe("test_stream", "test_group", "consumer1",
                                   count=10, block_ms=None, raw=False)
        assert len(msgs) == 1
        assert msgs[0]["data"] == 42

        # raw=True returns id+fields
        await bus.publish("test_stream", {"data": 99})
        raw_msgs = await bus.subscribe("test_stream", "test_group", "consumer1",
                                        count=10, raw=True)
        assert len(raw_msgs) == 1
        assert "id" in raw_msgs[0] and "fields" in raw_msgs[0]


class TestMetricsPort:
    def test_mock_implements_port(self) -> None:
        assert isinstance(_MockMetrics(), MetricsPort)


class TestPortsExports:
    def test_required_ports_exported(self) -> None:
        """Codex NIT (2026-04-26 v2): exact-count 대신 required exports만 검증.
        앞으로 ConfigPort/AlertPort/IncomeFetcherPort 추가 시 churn 없음."""
        from src.ports import __all__
        required = {
            "DataFeedPort", "EventBusPort", "ExchangeAdapterPort",
            "ExecutorPort", "JournalPort", "KillSwitchPort",
            "LedgerPort", "MetricsPort", "RiskPort",
        }
        assert required.issubset(set(__all__)), \
            f"missing required ports: {required - set(__all__)}"
