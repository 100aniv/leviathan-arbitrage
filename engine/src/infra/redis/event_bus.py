"""Redis Streams event bus with consumer groups and dead-letter handling."""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from .client import RedisClient

logger = logging.getLogger(__name__)


class EventBus:
    """
    Redis Streams-based event bus.

    Publishes structured events via XADD and consumes them via XREADGROUP
    with consumer groups for reliable delivery and at-least-once semantics.

    Dead-letter handling: messages idle longer than CLAIM_MIN_IDLE_MS are
    claimed and moved to a dead-letter stream.
    """

    DEAD_LETTER_STREAM = "leviathan:dead_letter"
    MAX_DELIVERY_COUNT = 3
    CLAIM_MIN_IDLE_MS = 30_000  # 30 seconds

    def __init__(self, client: RedisClient) -> None:
        self._client = client

    async def publish(self, stream: str, event: dict[str, Any]) -> bytes:
        """
        Publish event to Redis stream via XADD.

        The event dict is JSON-serialised and stored under the 'data' field.
        Returns the Redis message ID (e.g. b"1700000000000-0").
        """
        payload = {"data": json.dumps(event, default=str)}
        msg_id = await self._client.xadd(stream, payload, maxlen=10000, approximate=True)
        logger.debug("Published to %s: %s", stream, msg_id)
        return msg_id

    async def create_consumer_group(
        self, stream: str, group: str, start_id: str = "0"
    ) -> None:
        """
        Create a consumer group on the given stream.

        Idempotent: silently ignores BUSYGROUP errors if the group already exists.
        Uses mkstream=True so the stream is created if it doesn't yet exist.
        """
        try:
            await self._client.xgroup_create(stream, group, id=start_id, mkstream=True)
            logger.info("Created consumer group '%s' on stream '%s'", group, stream)
        except Exception as exc:
            if "BUSYGROUP" in str(exc):
                pass  # Already exists — idempotent
            else:
                raise

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
        Read pending messages from stream via XREADGROUP.

        Args:
            stream:    Stream key.
            group:     Consumer group name.
            consumer:  Consumer identity within the group.
            count:     Max messages to fetch.
            block_ms:  Block until messages arrive (ms). None = non-blocking.
            raw:       If True, return {"id": msg_id, "fields": fields} dicts
                       instead of deserialised event dicts.

        Returns list of event dicts (or raw dicts if raw=True).
        Messages are NOT automatically ACK'd — call ack_message() after processing.
        """
        result = await self._client.xreadgroup(
            group, consumer, {stream: ">"}, count=count, block=block_ms
        )
        if not result:
            return []

        messages: list[dict] = []
        for _stream_name, stream_messages in result:
            for msg_id, fields in stream_messages:
                if raw:
                    messages.append({"id": msg_id, "fields": fields})
                else:
                    data = fields.get(b"data") or fields.get("data")
                    if data:
                        if isinstance(data, bytes):
                            data = data.decode()
                        try:
                            messages.append(json.loads(data))
                        except (json.JSONDecodeError, ValueError):
                            pass  # skip malformed messages — do not crash consumer loop
        return messages

    async def ack_message(self, stream: str, group: str, msg_id: bytes | str) -> None:
        """
        Acknowledge a message, removing it from the Pending Entries List (PEL).
        Must be called after successfully processing a message.
        """
        await self._client.xack(stream, group, msg_id)

    async def handle_dead_letters(
        self, stream: str, group: str, consumer: str
    ) -> list[dict]:
        """
        Claim messages idle longer than CLAIM_MIN_IDLE_MS and move them to
        the dead-letter stream. Returns list of dead-lettered events.

        Call periodically to prevent the PEL from growing unbounded.
        """
        pending_info = await self._client.xpending(stream, group)
        if not pending_info or pending_info.get("pending", 0) == 0:
            return []

        claimed = await self._client.xclaim(
            stream, group, consumer,
            min_idle_time=self.CLAIM_MIN_IDLE_MS,
            message_ids=[],
        )

        dead: list[dict] = []
        for msg_id, fields in claimed:
            data = fields.get(b"data") or fields.get("data", b"{}")
            if isinstance(data, bytes):
                data = data.decode()
            try:
                event = json.loads(data)
            except (json.JSONDecodeError, ValueError):
                event = {}  # skip malformed dead-letter — log but continue

            await self._client.xadd(self.DEAD_LETTER_STREAM, {
                "original_stream": stream,
                "original_id": str(msg_id),
                "data": json.dumps(event, default=str),
            })
            await self._client.xack(stream, group, msg_id)
            dead.append(event)
            logger.warning("Dead-lettered message %s from stream %s", msg_id, stream)

        return dead
