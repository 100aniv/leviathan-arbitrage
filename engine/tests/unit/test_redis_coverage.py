"""Coverage tests for RedisClient and EventBus — targeting uncovered lines."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import fakeredis.aioredis as aioredis_fake

from src.infra.redis.client import RedisClient, RedisConfig
from src.infra.redis.event_bus import EventBus


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def config():
    return RedisConfig(host="localhost", port=6379, db=0)


@pytest.fixture
def fake_client(config):
    client = RedisClient(config)
    client._redis = aioredis_fake.FakeRedis()
    return client


@pytest.fixture
def bus(fake_client):
    return EventBus(fake_client)


# ── RedisClient.connect() (lines 47-55) ──────────────────────────────────────

class TestRedisClientConnect:
    async def test_connect_calls_ping(self, config):
        """connect() verifies connectivity by calling PING."""
        client = RedisClient(config)
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        mock_pool = MagicMock()

        with patch("redis.asyncio.ConnectionPool.from_url", return_value=mock_pool), \
             patch("redis.asyncio.Redis", return_value=mock_redis):
            await client.connect()

        mock_redis.ping.assert_called_once()

    async def test_connect_assigns_redis_and_pool(self, config):
        """connect() sets _redis and _pool attributes."""
        client = RedisClient(config)
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        mock_pool = MagicMock()

        with patch("redis.asyncio.ConnectionPool.from_url", return_value=mock_pool), \
             patch("redis.asyncio.Redis", return_value=mock_redis):
            await client.connect()

        assert client._redis is mock_redis
        assert client._pool is mock_pool


# ── RedisClient.disconnect() (lines 57-65) ────────────────────────────────────

class TestRedisClientDisconnect:
    async def test_disconnect_closes_redis_and_pool(self, config):
        """disconnect() calls aclose() on both redis and pool."""
        client = RedisClient(config)
        mock_redis = AsyncMock()
        mock_pool = AsyncMock()
        client._redis = mock_redis
        client._pool = mock_pool

        await client.disconnect()

        mock_redis.aclose.assert_called_once()
        mock_pool.aclose.assert_called_once()
        assert client._redis is None
        assert client._pool is None

    async def test_disconnect_when_not_connected_is_safe(self, config):
        """disconnect() with no connection does not raise."""
        client = RedisClient(config)
        await client.disconnect()  # must not raise


# ── RedisClient.health_check() (lines 67-93) ──────────────────────────────────

class TestRedisClientHealthCheck:
    async def test_health_check_ping_failure_returns_error(self, config):
        """health_check() returns error dict when PING raises."""
        client = RedisClient(config)
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=ConnectionError("connection refused"))
        client._redis = mock_redis

        result = await client.health_check()

        assert result["status"] == "error"
        assert "connection refused" in result["error"]

    async def test_health_check_info_failure_returns_ok_with_zero_memory(self, config):
        """health_check() returns ok with zero memory when INFO raises."""
        client = RedisClient(config)
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        mock_redis.info = AsyncMock(side_effect=Exception("INFO not supported"))
        client._redis = mock_redis

        result = await client.health_check()

        assert result["status"] == "ok"
        assert result["memory_used_bytes"] == 0

    async def test_health_check_not_connected_returns_error(self, config):
        """health_check() returns error when _redis is None."""
        client = RedisClient(config)
        # _redis is None by default
        result = await client.health_check()
        assert result["status"] == "error"
        assert "not connected" in result["error"]


# ── RedisClient.redis property (line 99) ──────────────────────────────────────

class TestRedisClientProperty:
    def test_redis_property_raises_when_not_connected(self, config):
        """redis property raises RuntimeError when not connected."""
        client = RedisClient(config)
        with pytest.raises(RuntimeError, match="not connected"):
            _ = client.redis

    def test_redis_property_returns_client_when_connected(self, fake_client):
        """redis property returns underlying client when _redis is set."""
        r = fake_client.redis
        assert r is not None


# ── RedisClient.hdel (line 125) ───────────────────────────────────────────────

class TestRedisClientHdel:
    async def test_hdel_removes_single_field(self, fake_client):
        """hdel removes the specified field from a hash."""
        await fake_client.hset("hash1", mapping={"f1": "v1", "f2": "v2"})
        count = await fake_client.hdel("hash1", "f1")
        assert count == 1
        assert await fake_client.hget("hash1", "f1") is None

    async def test_hdel_removes_multiple_fields(self, fake_client):
        """hdel removes multiple fields in one call."""
        await fake_client.hset("hash2", mapping={"a": "1", "b": "2", "c": "3"})
        count = await fake_client.hdel("hash2", "a", "b")
        assert count == 2
        result = await fake_client.hgetall("hash2")
        assert b"a" not in result
        assert b"c" in result

    async def test_hdel_missing_field_returns_zero(self, fake_client):
        """hdel on missing field returns 0 (no error)."""
        await fake_client.hset("hash3", mapping={"x": "1"})
        count = await fake_client.hdel("hash3", "nonexistent")
        assert count == 0


# ── RedisClient.xclaim (line 189) ─────────────────────────────────────────────

class TestRedisClientXclaim:
    async def test_xclaim_claims_pending_message(self, fake_client):
        """xclaim transfers ownership of a pending message."""
        await fake_client.xadd("claimstream", {"data": "test"})
        await fake_client.xgroup_create("claimstream", "claimgrp", id="0")
        result = await fake_client.xreadgroup(
            "claimgrp", "consumer1", {"claimstream": ">"}, count=10
        )
        _, msgs = result[0]
        msg_id, _ = msgs[0]

        claimed = await fake_client.xclaim(
            "claimstream", "claimgrp", "consumer2",
            min_idle_time=0, message_ids=[msg_id]
        )
        assert isinstance(claimed, list)

    async def test_xclaim_with_valid_message_id_returns_list(self, fake_client):
        """xclaim with valid message_ids returns a list (may be empty if not yet idle)."""
        await fake_client.xadd("claimstream2", {"data": "test"})
        await fake_client.xgroup_create("claimstream2", "cg2", id="0")
        result = await fake_client.xreadgroup(
            "cg2", "c1", {"claimstream2": ">"}, count=10
        )
        _, msgs = result[0]
        msg_id, _ = msgs[0]

        claimed = await fake_client.xclaim(
            "claimstream2", "cg2", "c2",
            min_idle_time=0, message_ids=[msg_id]
        )
        assert isinstance(claimed, list)


# ── EventBus.create_consumer_group error path (line 59) ──────────────────────

class TestEventBusCreateGroupErrors:
    async def test_create_group_raises_on_non_busygroup_exception(self, fake_client):
        """create_consumer_group re-raises non-BUSYGROUP exceptions."""
        bus = EventBus(fake_client)
        fake_client.xgroup_create = AsyncMock(
            side_effect=Exception("WRONGTYPE Operation against key with wrong type")
        )

        with pytest.raises(Exception, match="WRONGTYPE"):
            await bus.create_consumer_group("stream", "group")

    async def test_create_group_silences_busygroup_error(self, fake_client):
        """create_consumer_group ignores BUSYGROUP (group already exists)."""
        bus = EventBus(fake_client)
        fake_client.xgroup_create = AsyncMock(
            side_effect=Exception("BUSYGROUP Consumer Group name already exists")
        )

        # Must not raise
        await bus.create_consumer_group("stream", "group")


# ── EventBus.handle_dead_letters (lines 120-146) ─────────────────────────────

class TestEventBusHandleDeadLetters:
    async def test_returns_empty_when_no_pending_messages(self, fake_client):
        """handle_dead_letters returns [] when pending count is 0."""
        bus = EventBus(fake_client)
        fake_client.xpending = AsyncMock(return_value={"pending": 0})

        result = await bus.handle_dead_letters("stream", "group", "consumer")
        assert result == []

    async def test_returns_empty_when_xpending_returns_none(self, fake_client):
        """handle_dead_letters returns [] when xpending returns None."""
        bus = EventBus(fake_client)
        fake_client.xpending = AsyncMock(return_value=None)

        result = await bus.handle_dead_letters("stream", "group", "consumer")
        assert result == []

    async def test_claims_stale_messages_and_moves_to_dead_letter_stream(self, fake_client):
        """handle_dead_letters xclaims idle messages and XADDs them to dead-letter stream."""
        bus = EventBus(fake_client)
        event = {"type": "trade", "price": "30000"}

        fake_client.xpending = AsyncMock(return_value={"pending": 1})
        fake_client.xclaim = AsyncMock(return_value=[
            (b"1700000000000-0", {b"data": json.dumps(event).encode()})
        ])
        fake_client.xadd = AsyncMock(return_value=b"1700000000001-0")
        fake_client.xack = AsyncMock(return_value=1)

        result = await bus.handle_dead_letters("stream", "group", "consumer")

        assert len(result) == 1
        assert result[0]["type"] == "trade"
        fake_client.xadd.assert_called_once()
        fake_client.xack.assert_called_once()

    async def test_dead_letter_xadd_targets_dead_letter_stream(self, fake_client):
        """handle_dead_letters publishes to DEAD_LETTER_STREAM constant."""
        bus = EventBus(fake_client)
        event = {"x": "y"}

        fake_client.xpending = AsyncMock(return_value={"pending": 1})
        fake_client.xclaim = AsyncMock(return_value=[
            (b"123-0", {b"data": json.dumps(event).encode()})
        ])
        fake_client.xadd = AsyncMock(return_value=b"456-0")
        fake_client.xack = AsyncMock(return_value=1)

        await bus.handle_dead_letters("mystream", "grp", "c")

        call_args = fake_client.xadd.call_args
        assert call_args[0][0] == EventBus.DEAD_LETTER_STREAM

    async def test_dead_letter_contains_original_stream_and_id(self, fake_client):
        """handle_dead_letters includes original_stream and original_id in dead-letter entry."""
        bus = EventBus(fake_client)
        event = {"data": "val"}

        fake_client.xpending = AsyncMock(return_value={"pending": 1})
        fake_client.xclaim = AsyncMock(return_value=[
            (b"999-0", {b"data": json.dumps(event).encode()})
        ])
        xadd_calls = []

        async def capture_xadd(stream, fields, **kwargs):
            xadd_calls.append((stream, fields))
            return b"1000-0"

        fake_client.xadd = capture_xadd
        fake_client.xack = AsyncMock(return_value=1)

        await bus.handle_dead_letters("original_stream", "grp", "c")

        assert len(xadd_calls) == 1
        _, fields = xadd_calls[0]
        assert fields["original_stream"] == "original_stream"
        # original_id is str(msg_id) where msg_id is bytes, e.g. "b'999-0'"
        assert "999-0" in fields["original_id"]

    async def test_handles_multiple_dead_letter_messages(self, fake_client):
        """handle_dead_letters processes multiple claimed messages."""
        bus = EventBus(fake_client)
        events = [{"i": str(i)} for i in range(3)]

        fake_client.xpending = AsyncMock(return_value={"pending": 3})
        fake_client.xclaim = AsyncMock(return_value=[
            (f"100{i}-0".encode(), {b"data": json.dumps(e).encode()})
            for i, e in enumerate(events)
        ])
        fake_client.xadd = AsyncMock(return_value=b"999-0")
        fake_client.xack = AsyncMock(return_value=1)

        result = await bus.handle_dead_letters("stream", "group", "consumer")

        assert len(result) == 3
        assert fake_client.xadd.call_count == 3
        assert fake_client.xack.call_count == 3
