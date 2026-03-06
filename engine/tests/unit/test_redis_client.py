"""Tests for async Redis client wrapper using fakeredis."""
import pytest
import fakeredis.aioredis as aioredis_fake
from src.infra.redis.client import RedisClient, RedisConfig


@pytest.fixture
def config():
    return RedisConfig(host="localhost", port=6379, db=0)


@pytest.fixture
def fake_client(config):
    client = RedisClient(config)
    client._redis = aioredis_fake.FakeRedis()
    return client


class TestRedisConfig:
    def test_default_values(self):
        config = RedisConfig()
        assert config.host == "localhost"
        assert config.port == 6379
        assert config.db == 0
        assert config.max_connections == 20

    def test_custom_values(self):
        config = RedisConfig(host="redis", port=6380, db=1, max_connections=10)
        assert config.host == "redis"
        assert config.port == 6380
        assert config.max_connections == 10


class TestRedisClientHealthCheck:
    async def test_health_check_ok_when_connected(self, fake_client):
        result = await fake_client.health_check()
        assert result["status"] == "ok"
        assert "ping" in result
        assert "memory_used_bytes" in result

    async def test_health_check_error_when_not_connected(self, config):
        client = RedisClient(config)
        # _redis is None (not connected)
        result = await client.health_check()
        assert result["status"] == "error"


class TestRedisClientSetGet:
    async def test_set_and_get_value(self, fake_client):
        await fake_client.set("key1", "value1")
        val = await fake_client.get("key1")
        assert val == b"value1"

    async def test_get_missing_key_returns_none(self, fake_client):
        val = await fake_client.get("nonexistent_key")
        assert val is None

    async def test_set_with_expiry(self, fake_client):
        await fake_client.set("expiring_key", "temp", ex=3600)
        val = await fake_client.get("expiring_key")
        assert val == b"temp"

    async def test_delete_key(self, fake_client):
        await fake_client.set("del_key", "value")
        await fake_client.delete("del_key")
        val = await fake_client.get("del_key")
        assert val is None

    async def test_delete_multiple_keys(self, fake_client):
        await fake_client.set("k1", "v1")
        await fake_client.set("k2", "v2")
        await fake_client.delete("k1", "k2")
        assert await fake_client.get("k1") is None
        assert await fake_client.get("k2") is None


class TestRedisClientHash:
    async def test_hset_and_hgetall(self, fake_client):
        await fake_client.hset("myhash", mapping={"field1": "val1", "field2": "val2"})
        result = await fake_client.hgetall("myhash")
        assert b"field1" in result
        assert result[b"field1"] == b"val1"

    async def test_hget_single_field(self, fake_client):
        await fake_client.hset("myhash2", mapping={"f": "v"})
        val = await fake_client.hget("myhash2", "f")
        assert val == b"v"

    async def test_hget_missing_field_returns_none(self, fake_client):
        await fake_client.hset("myhash3", mapping={"f": "v"})
        val = await fake_client.hget("myhash3", "missing")
        assert val is None


class TestRedisClientSortedSet:
    async def test_zadd_and_zrangebyscore(self, fake_client):
        await fake_client.zadd("myzset", {"item1": 1.0, "item2": 2.0, "item3": 3.0})
        result = await fake_client.zrangebyscore("myzset", 1.5, 3.0)
        assert b"item2" in result
        assert b"item3" in result
        assert b"item1" not in result

    async def test_zrangebyscore_full_range(self, fake_client):
        await fake_client.zadd("myzset2", {"a": 100.0, "b": 200.0})
        result = await fake_client.zrangebyscore("myzset2", "-inf", "+inf")
        assert len(result) == 2

    async def test_zremrangebyscore(self, fake_client):
        await fake_client.zadd("myzset3", {"a": 1.0, "b": 2.0, "c": 3.0})
        removed = await fake_client.zremrangebyscore("myzset3", 1.0, 2.0)
        assert removed == 2
        result = await fake_client.zrangebyscore("myzset3", "-inf", "+inf")
        assert b"c" in result

    async def test_zrem_single_member(self, fake_client):
        await fake_client.zadd("myzset4", {"x": 1.0, "y": 2.0})
        await fake_client.zrem("myzset4", "x")
        result = await fake_client.zrangebyscore("myzset4", "-inf", "+inf")
        assert b"x" not in result
        assert b"y" in result


class TestRedisClientStreams:
    async def test_xadd_returns_message_id(self, fake_client):
        msg_id = await fake_client.xadd("mystream", {"field": "value"})
        assert msg_id is not None

    async def test_xread_after_xadd(self, fake_client):
        await fake_client.xadd("mystream2", {"data": "test"})
        messages = await fake_client.xread({"mystream2": "0-0"})
        assert len(messages) == 1
        stream_name, stream_msgs = messages[0]
        assert len(stream_msgs) == 1

    async def test_xgroup_create_and_xreadgroup(self, fake_client):
        await fake_client.xadd("grpstream", {"data": "hello"})
        await fake_client.xgroup_create("grpstream", "grp1", id="0")
        result = await fake_client.xreadgroup("grp1", "consumer1", {"grpstream": ">"}, count=10)
        assert len(result) == 1
        _, msgs = result[0]
        assert len(msgs) == 1

    async def test_xack_clears_pending(self, fake_client):
        await fake_client.xadd("ackstream", {"data": "msg"})
        await fake_client.xgroup_create("ackstream", "ackgrp", id="0")
        result = await fake_client.xreadgroup("ackgrp", "c1", {"ackstream": ">"}, count=10)
        _, msgs = result[0]
        msg_id, _ = msgs[0]
        await fake_client.xack("ackstream", "ackgrp", msg_id)
        pending = await fake_client.xpending("ackstream", "ackgrp")
        assert pending["pending"] == 0
