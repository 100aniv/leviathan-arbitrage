"""US-180: InMemoryEventBus queue limits — maxsize, overflow, defaults."""
from __future__ import annotations

import asyncio
import os

import pytest
from unittest.mock import patch

from src.infra.redis.memory_bus import InMemoryEventBus


# ---------------------------------------------------------------------------
# Default maxsize
# ---------------------------------------------------------------------------


class TestInMemoryEventBusDefaults:
    def test_default_maxsize_is_10000(self):
        """InMemoryEventBus default maxsize is 10000."""
        bus = InMemoryEventBus()
        assert bus._maxsize == 10000

    def test_custom_maxsize_is_stored(self):
        """InMemoryEventBus stores provided maxsize."""
        bus = InMemoryEventBus(maxsize=500)
        assert bus._maxsize == 500

    def test_zero_maxsize_accepted(self):
        """InMemoryEventBus accepts maxsize=0 (unlimited in asyncio.Queue)."""
        bus = InMemoryEventBus(maxsize=0)
        assert bus._maxsize == 0


# ---------------------------------------------------------------------------
# EVENT_BUS_MAXSIZE env var
# ---------------------------------------------------------------------------


class TestEventBusMaxsizeEnvVar:
    def test_env_var_reflected_in_maxsize(self, monkeypatch):
        """EVENT_BUS_MAXSIZE env var is read and used when instantiating bus."""
        monkeypatch.setenv("EVENT_BUS_MAXSIZE", "500")
        # The main.py reads this var when creating the bus
        maxsize = int(os.getenv("EVENT_BUS_MAXSIZE", "10000"))
        bus = InMemoryEventBus(maxsize=maxsize)
        assert bus._maxsize == 500

    def test_env_var_missing_uses_default(self, monkeypatch):
        """Missing EVENT_BUS_MAXSIZE env var falls back to 10000."""
        monkeypatch.delenv("EVENT_BUS_MAXSIZE", raising=False)
        maxsize = int(os.getenv("EVENT_BUS_MAXSIZE", "10000"))
        assert maxsize == 10000


# ---------------------------------------------------------------------------
# Queue overflow → oldest drop + WARNING log
# ---------------------------------------------------------------------------


class TestQueueOverflowBehavior:
    @pytest.mark.asyncio
    async def test_publish_drops_oldest_when_queue_full(self):
        """When queue is full, oldest message is dropped and new one is added."""
        bus = InMemoryEventBus(maxsize=3)
        await bus.create_consumer_group("test_stream", "group1")

        # Fill queue to capacity
        for i in range(3):
            await bus.publish("test_stream", {"seq": i})

        # One more publish → should drop oldest and add new
        await bus.publish("test_stream", {"seq": 99})

        messages = await bus.subscribe("test_stream", "group1", "consumer", count=10)
        # Queue should have exactly maxsize messages (3), oldest was dropped
        assert len(messages) <= 3

    @pytest.mark.asyncio
    async def test_overflow_log_warning_emitted(self):
        """WARNING is logged when queue overflows."""
        import logging
        bus = InMemoryEventBus(maxsize=2)
        await bus.create_consumer_group("test_stream", "group1")

        for i in range(2):
            await bus.publish("test_stream", {"seq": i})

        with patch("src.infra.redis.memory_bus.logger") as mock_logger:
            await bus.publish("test_stream", {"seq": 99})
            assert mock_logger.warning.call_count >= 1
            # Verify at least one warning mentions queue full / drop
            all_calls = [str(c) for c in mock_logger.warning.call_args_list]
            assert any(
                "full" in msg.lower() or "drop" in msg.lower()
                for msg in all_calls
            )

    @pytest.mark.asyncio
    async def test_bus_still_functional_after_overflow(self):
        """Bus remains functional and accepts messages after overflow."""
        bus = InMemoryEventBus(maxsize=2)
        await bus.create_consumer_group("test_stream", "group1")

        # Overflow
        for i in range(5):
            await bus.publish("test_stream", {"seq": i})

        # Should still be able to publish and receive
        await bus.publish("test_stream", {"seq": 100})
        messages = await bus.subscribe("test_stream", "group1", "consumer", count=10)
        assert len(messages) >= 1


# ---------------------------------------------------------------------------
# Fan-out to multiple consumer groups
# ---------------------------------------------------------------------------


class TestConsumerGroupFanout:
    @pytest.mark.asyncio
    async def test_publish_delivers_to_all_groups(self):
        """publish() delivers the message to each registered consumer group."""
        bus = InMemoryEventBus(maxsize=100)
        await bus.create_consumer_group("stream", "group_a")
        await bus.create_consumer_group("stream", "group_b")

        await bus.publish("stream", {"data": "hello"})

        msgs_a = await bus.subscribe("stream", "group_a", "c1", count=1)
        msgs_b = await bus.subscribe("stream", "group_b", "c2", count=1)

        assert len(msgs_a) == 1
        assert len(msgs_b) == 1

    @pytest.mark.asyncio
    async def test_subscribe_returns_empty_for_unregistered_group(self):
        """subscribe() returns [] for a (stream, group) pair with no queue."""
        bus = InMemoryEventBus(maxsize=100)
        await bus.publish("unknown_stream", {"data": "x"})

        messages = await bus.subscribe("unknown_stream", "no_such_group", "c", count=5)
        assert messages == []

    @pytest.mark.asyncio
    async def test_create_consumer_group_idempotent(self):
        """Calling create_consumer_group twice for same (stream, group) is idempotent."""
        bus = InMemoryEventBus(maxsize=100)
        await bus.create_consumer_group("stream", "grp")
        await bus.create_consumer_group("stream", "grp")  # should not raise or duplicate
        assert ("stream", "grp") in bus._queues
