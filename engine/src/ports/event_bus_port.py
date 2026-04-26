"""EventBusPort — Phase 7 message bus abstraction (2026-04-26).

Gemini Priority 2 (architecture-audit-2026-04-26): Nautilus MessageBus / LEAN
EventBus 산업 표준 미러. Engine god-object 결합 해체 후 publish/subscribe는
이 Port 통해서만 — Redis / In-memory / future NATS / ZMQ swap 가능.

구현체:
- engine/src/infra/redis/event_bus.py (RedisEventBus)
- engine/src/infra/redis/memory_bus.py (InMemoryEventBus)
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol, runtime_checkable


@runtime_checkable
class EventBusPort(Protocol):
    """Hexagonal port for engine-internal pub/sub.

    LEVIATHAN engine 내부 통신 (signal → strategy → executor → result fanout).
    Phase 7 god-object 해체 시 runtime/* 모듈은 EventBus 대신 이 Port 의존.

    실제 구현은 두 가지 — 둘 다 동일 시그니처:
    - RedisEventBus (production, persistence + DLQ)
    - InMemoryEventBus (paper canary, test)
    """

    async def publish(self, stream: str, event: dict[str, Any]) -> bytes:
        """Stream에 event 발행. message_id 반환."""
        ...

    async def create_consumer_group(
        self,
        stream: str,
        group: str,
        *,
        last_id: str = "0",
        mkstream: bool = True,
    ) -> bool:
        """Consumer group 생성. 이미 존재 시 idempotent."""
        ...

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
        """Group consumer로 subscribe. callback await."""
        ...

    async def ack_message(self, stream: str, group: str, msg_id: bytes | str) -> None:
        """Successful processing 확인 (XACK)."""
        ...

    async def handle_dead_letters(
        self,
        stream: str,
        group: str,
        *,
        max_retry: int = 3,
    ) -> int:
        """Dead-letter queue 처리. Returns processed count."""
        ...
