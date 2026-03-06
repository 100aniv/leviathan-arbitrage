"""In-memory event bus — drop-in replacement for Redis Streams EventBus.

Uses asyncio.Queue internally per stream, with semantic consumer group support.
No Redis dependency required. Suitable for paper trading and testing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from typing import Any, Optional

logger = logging.getLogger(__name__)


class InMemoryEventBus:
    """
    In-memory event bus matching the EventBus interface.

    Each (stream, group) pair gets its own asyncio.Queue so that consumer
    groups receive independent copies of published messages (fan-out).
    Within a single group only one consumer processes each message
    (single-consumer-per-group semantics).
    """

    def __init__(self) -> None:
        # stream -> list of group names
        self._groups: dict[str, list[str]] = defaultdict(list)
        # (stream, group) -> asyncio.Queue of (msg_id, event_dict)
        self._queues: dict[tuple[str, str], asyncio.Queue[tuple[bytes, dict[str, Any]]]] = {}
        # Monotonic message ID counter per stream
        self._counters: dict[str, int] = defaultdict(int)

    async def publish(self, stream: str, event: dict[str, Any]) -> bytes:
        """
        Publish event to all consumer groups registered on this stream.

        Returns a synthetic message ID (bytes) in Redis-compatible format.
        """
        self._counters[stream] += 1
        seq = self._counters[stream]
        ts_ms = int(time.time() * 1000)
        msg_id = f"{ts_ms}-{seq}".encode()

        # Fan out to every registered consumer group
        for group in self._groups.get(stream, []):
            key = (stream, group)
            queue = self._queues.get(key)
            if queue is not None:
                await queue.put((msg_id, event))

        logger.debug("Published to %s: %s", stream, msg_id)
        return msg_id

    async def create_consumer_group(
        self, stream: str, group: str, start_id: str = "0"
    ) -> None:
        """
        Register a consumer group on the given stream (no-op if already exists).

        Creates the internal queue for the (stream, group) pair.
        """
        key = (stream, group)
        if key not in self._queues:
            self._queues[key] = asyncio.Queue()
            self._groups[stream].append(group)
            logger.info("Created consumer group '%s' on stream '%s'", group, stream)

    async def subscribe(
        self,
        stream: str,
        group: str,
        consumer: str,
        count: int = 10,
        block_ms: Optional[int] = None,
        raw: bool = False,
    ) -> list[dict]:
        """
        Read pending messages from the in-memory queue.

        Args:
            stream:    Stream key.
            group:     Consumer group name.
            consumer:  Consumer identity (ignored — single consumer per group).
            count:     Max messages to fetch.
            block_ms:  Block until messages arrive (ms). None = non-blocking.
            raw:       If True, return {"id": msg_id, "fields": fields} dicts
                       instead of deserialised event dicts.

        Returns list of event dicts (or raw dicts if raw=True).
        """
        key = (stream, group)
        queue = self._queues.get(key)
        if queue is None:
            return []

        messages: list[dict] = []

        # Try to get up to `count` messages
        for _ in range(count):
            try:
                if block_ms is not None and queue.empty():
                    # Block for up to block_ms milliseconds for the first message
                    try:
                        msg_id, event = await asyncio.wait_for(
                            queue.get(), timeout=block_ms / 1000.0
                        )
                    except asyncio.TimeoutError:
                        break
                else:
                    msg_id, event = queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            if raw:
                fields = {b"data": json.dumps(event, default=str).encode()}
                messages.append({"id": msg_id, "fields": fields})
            else:
                messages.append(event)

        return messages

    async def ack_message(
        self, stream: str, group: str, msg_id: bytes | str
    ) -> None:
        """Acknowledge a message (no-op for in-memory bus)."""

    async def handle_dead_letters(
        self, stream: str, group: str, consumer: str
    ) -> list[dict]:
        """No dead-letter handling needed for in-memory bus."""
        return []
