"""Tests for Redis Streams event bus using fakeredis."""
import pytest
import fakeredis.aioredis as aioredis_fake
from src.infra.redis.client import RedisClient, RedisConfig
from src.infra.redis.event_bus import EventBus


@pytest.fixture
def fake_client():
    config = RedisConfig()
    client = RedisClient(config)
    client._redis = aioredis_fake.FakeRedis()
    return client


@pytest.fixture
def bus(fake_client):
    return EventBus(fake_client)


class TestEventBusPublish:
    async def test_publish_returns_message_id(self, bus):
        msg_id = await bus.publish("test_stream", {"type": "tick", "price": "30000.00"})
        assert msg_id is not None

    async def test_publish_message_readable_via_xread(self, bus, fake_client):
        event = {"type": "orderbook_update", "exchange": "binance"}
        await bus.publish("readable_stream", event)
        messages = await fake_client.xread({"readable_stream": "0-0"})
        assert len(messages) == 1
        _, stream_msgs = messages[0]
        assert len(stream_msgs) == 1

    async def test_publish_multiple_events(self, bus):
        for i in range(5):
            await bus.publish("multi_stream", {"seq": str(i)})
        # All 5 messages published without error


class TestEventBusConsumerGroups:
    async def test_create_consumer_group(self, bus):
        await bus.publish("group_stream", {"data": "init"})
        await bus.create_consumer_group("group_stream", "test_group")
        # No exception = success

    async def test_create_consumer_group_idempotent(self, bus):
        await bus.publish("group_stream2", {"data": "init"})
        await bus.create_consumer_group("group_stream2", "my_group")
        # Second call should not raise
        await bus.create_consumer_group("group_stream2", "my_group")

    async def test_create_group_on_empty_stream(self, bus):
        # mkstream=True should create the stream if it doesn't exist
        await bus.create_consumer_group("new_stream", "new_group")
        # No exception = success


class TestEventBusSubscribe:
    async def test_subscribe_receives_published_event(self, bus):
        await bus.publish("sub_stream", {"type": "tick", "value": "42"})
        await bus.create_consumer_group("sub_stream", "sub_group", start_id="0")
        messages = await bus.subscribe("sub_stream", "sub_group", "consumer-1")
        assert len(messages) == 1
        assert messages[0]["type"] == "tick"
        assert messages[0]["value"] == "42"

    async def test_subscribe_multiple_events(self, bus):
        for i in range(3):
            await bus.publish("multi_sub_stream", {"seq": str(i)})
        await bus.create_consumer_group("multi_sub_stream", "g1", start_id="0")
        messages = await bus.subscribe("multi_sub_stream", "g1", "c1", count=10)
        assert len(messages) == 3

    async def test_subscribe_returns_empty_when_no_messages(self, bus):
        await bus.create_consumer_group("empty_stream", "eg")
        messages = await bus.subscribe("empty_stream", "eg", "c1")
        assert messages == []

    async def test_subscribe_raw_mode_returns_id(self, bus, fake_client):
        await bus.publish("raw_stream", {"data": "test"})
        await bus.create_consumer_group("raw_stream", "raw_group", start_id="0")
        messages = await bus.subscribe("raw_stream", "raw_group", "c1", raw=True)
        assert len(messages) == 1
        assert "id" in messages[0]
        assert "fields" in messages[0]


class TestEventBusAck:
    async def test_ack_clears_pending_entry(self, bus, fake_client):
        await bus.publish("ack_stream", {"data": "test"})
        await bus.create_consumer_group("ack_stream", "ack_group", start_id="0")
        raw_messages = await bus.subscribe("ack_stream", "ack_group", "c1", raw=True)
        msg_id = raw_messages[0]["id"]
        await bus.ack_message("ack_stream", "ack_group", msg_id)
        pending = await fake_client.xpending("ack_stream", "ack_group")
        assert pending["pending"] == 0

    async def test_unacked_message_stays_in_pending(self, bus, fake_client):
        await bus.publish("pending_stream", {"data": "unacked"})
        await bus.create_consumer_group("pending_stream", "pg", start_id="0")
        await bus.subscribe("pending_stream", "pg", "c1", raw=True)
        # Did not ack
        pending = await fake_client.xpending("pending_stream", "pg")
        assert pending["pending"] == 1


class TestEventSerialization:
    async def test_nested_dict_survives_roundtrip(self, bus):
        event = {
            "type": "trade",
            "exchange": "binance",
            "data": {"price": "30000.00", "qty": "0.5", "side": "buy"},
        }
        await bus.publish("serde_stream", event)
        await bus.create_consumer_group("serde_stream", "sg", start_id="0")
        messages = await bus.subscribe("serde_stream", "sg", "c1")
        assert messages[0]["data"]["price"] == "30000.00"
        assert messages[0]["data"]["side"] == "buy"

    async def test_numeric_strings_preserved(self, bus):
        event = {"price": "30000.12345678", "qty": "0.00001000"}
        await bus.publish("num_stream", event)
        await bus.create_consumer_group("num_stream", "ng", start_id="0")
        messages = await bus.subscribe("num_stream", "ng", "c1")
        assert messages[0]["price"] == "30000.12345678"
        assert messages[0]["qty"] == "0.00001000"
