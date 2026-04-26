"""EventBusPort — Phase 7 message bus abstraction (2026-04-26).

Gemini Priority 2 (architecture-audit-2026-04-26): Nautilus MessageBus / LEAN
EventBus 산업 표준 미러. Engine god-object 결합 해체 후 publish/subscribe는
이 Port 통해서만 — Redis / In-memory / future NATS / ZMQ swap 가능.

Codex BLOCKING 정합 완료 (2026-04-26 v2):
- create_consumer_group(stream, group, start_id="0") -> None — 실제 구현 정합
- subscribe(...) pull-based → list[dict] (raw=True 옵션 보존, trade_consumer.py:180에서 사용)
- handle_dead_letters(stream, group, consumer) -> list[dict] — 실제 구현 정합
- 시그니처는 src/infra/redis/event_bus.py + memory_bus.py와 1:1 매칭

구현체:
- engine/src/infra/redis/event_bus.py (RedisEventBus)
- engine/src/infra/redis/memory_bus.py (InMemoryEventBus)
"""
from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class EventBusPort(Protocol):
    """Hexagonal port for engine-internal pub/sub.

    LEVIATHAN engine 내부 통신 (signal → strategy → executor → result fanout).
    Phase 7 god-object 해체 시 runtime/* 모듈은 EventBus 대신 이 Port 의존.

    실제 구현은 두 가지 — 둘 다 동일 시그니처:
    - RedisEventBus (production, persistence + DLQ)
    - InMemoryEventBus (paper canary, test)

    pull-based 모델 (Redis XREADGROUP 미러). subscribe()가 list[dict] 반환,
    raw=True 시 message id + fields 보존 (XACK 위해).
    """

    async def publish(self, stream: str, event: dict[str, Any]) -> bytes:
        """Stream에 event 발행. message_id 반환."""
        ...

    async def create_consumer_group(
        self, stream: str, group: str, start_id: str = "0"
    ) -> None:
        """Consumer group 생성. 이미 존재 시 idempotent. start_id='$'은 신규만."""
        ...

    async def subscribe(
        self,
        stream: str,
        group: str,
        consumer: str,
        count: int = 10,
        block_ms: Optional[int] = None,
        raw: bool = False,
    ) -> list[dict]:
        """Group consumer pull. count개까지, block_ms 동안 대기.

        raw=True: {'id': msg_id, 'fields': fields} 보존 (XACK 위해).
        raw=False: deserialised event dicts.
        """
        ...

    async def ack_message(self, stream: str, group: str, msg_id: bytes | str) -> None:
        """Successful processing 확인 (XACK)."""
        ...

    async def handle_dead_letters(
        self, stream: str, group: str, consumer: str
    ) -> list[dict]:
        """Dead-letter queue 처리. Returns DLQ entries."""
        ...
