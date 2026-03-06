"""Tests for Redis Orderbook Manager using fakeredis."""
import asyncio
import pytest
import fakeredis.aioredis as aioredis_fake
from decimal import Decimal
from src.infra.redis.client import RedisClient, RedisConfig
from src.infra.redis.orderbook_manager import OrderbookManager


@pytest.fixture
def fake_client():
    config = RedisConfig()
    client = RedisClient(config)
    client._redis = aioredis_fake.FakeRedis()
    return client


@pytest.fixture
def manager(fake_client):
    return OrderbookManager(fake_client)


class TestStoreSnapshot:
    async def test_store_snapshot_and_retrieve(self, manager):
        bids = [("30000.00", "1.5"), ("29999.00", "2.0")]
        asks = [("30001.00", "1.0"), ("30002.00", "3.0")]
        await manager.store_snapshot("binance", "BTC/USDT", bids, asks)
        book = await manager.get_orderbook("binance", "BTC/USDT")
        assert book is not None
        assert book.best_bid() == Decimal("30000.00")
        assert book.best_ask() == Decimal("30001.00")

    async def test_snapshot_replaces_previous_state(self, manager):
        await manager.store_snapshot("binance", "BTC/USDT",
                                     [("29000.00", "1.0")], [("29001.00", "1.0")])
        await manager.store_snapshot("binance", "BTC/USDT",
                                     [("30000.00", "1.5")], [("30001.00", "1.0")])
        book = await manager.get_orderbook("binance", "BTC/USDT")
        assert book.best_bid() == Decimal("30000.00")
        assert Decimal("29000.00") not in book.bids

    async def test_get_orderbook_returns_none_when_empty(self, manager):
        book = await manager.get_orderbook("binance", "NONEXISTENT/USDT")
        assert book is None

    async def test_quantities_stored_correctly(self, manager):
        await manager.store_snapshot("bybit", "ETH/USDT",
                                     [("2000.00", "5.5")], [("2001.00", "3.3")])
        book = await manager.get_orderbook("bybit", "ETH/USDT")
        assert book.bids[Decimal("2000.00")] == Decimal("5.5")
        assert book.asks[Decimal("2001.00")] == Decimal("3.3")


class TestApplyDelta:
    async def test_delta_updates_bid_quantity(self, fake_client):
        manager = OrderbookManager(fake_client)
        await manager.store_snapshot("binance", "BTC/USDT",
                                     [("30000.00", "1.0")], [("30001.00", "1.0")])
        await manager.apply_delta("binance", "BTC/USDT",
                                  [("30000.00", "2.5")], [])
        book = await manager.get_orderbook("binance", "BTC/USDT")
        assert book.bids[Decimal("30000.00")] == Decimal("2.5")

    async def test_delta_removes_zero_qty_bid(self, fake_client):
        manager = OrderbookManager(fake_client)
        await manager.store_snapshot("binance", "BTC/USDT",
                                     [("30000.00", "1.0"), ("29999.00", "2.0")],
                                     [("30001.00", "1.0")])
        await manager.apply_delta("binance", "BTC/USDT",
                                  [("30000.00", "0")], [])
        book = await manager.get_orderbook("binance", "BTC/USDT")
        assert Decimal("30000.00") not in book.bids
        assert book.best_bid() == Decimal("29999.00")

    async def test_delta_removes_zero_qty_ask(self, fake_client):
        manager = OrderbookManager(fake_client)
        await manager.store_snapshot("binance", "BTC/USDT",
                                     [("30000.00", "1.0")],
                                     [("30001.00", "1.0"), ("30002.00", "2.0")])
        await manager.apply_delta("binance", "BTC/USDT",
                                  [], [("30001.00", "0")])
        book = await manager.get_orderbook("binance", "BTC/USDT")
        assert Decimal("30001.00") not in book.asks
        assert book.best_ask() == Decimal("30002.00")

    async def test_delta_adds_new_price_level(self, fake_client):
        manager = OrderbookManager(fake_client)
        await manager.store_snapshot("binance", "BTC/USDT",
                                     [("30000.00", "1.0")], [("30001.00", "1.0")])
        await manager.apply_delta("binance", "BTC/USDT",
                                  [("30000.50", "0.8")], [])
        book = await manager.get_orderbook("binance", "BTC/USDT")
        assert Decimal("30000.50") in book.bids


class TestStaleDetection:
    async def test_fresh_data_not_stale(self, fake_client):
        manager = OrderbookManager(fake_client, stale_threshold_seconds=60)
        await manager.store_snapshot("binance", "BTC/USDT",
                                     [("30000.00", "1.0")], [("30001.00", "1.0")])
        assert await manager.is_stale("binance", "BTC/USDT") is False

    async def test_stale_after_threshold(self, fake_client):
        manager = OrderbookManager(fake_client, stale_threshold_seconds=0.01)
        await manager.store_snapshot("binance", "BTC/USDT",
                                     [("30000.00", "1.0")], [("30001.00", "1.0")])
        await asyncio.sleep(0.02)
        assert await manager.is_stale("binance", "BTC/USDT") is True

    async def test_nonexistent_key_is_stale(self, fake_client):
        manager = OrderbookManager(fake_client)
        assert await manager.is_stale("binance", "GHOST/USDT") is True

    async def test_mark_stale(self, fake_client):
        manager = OrderbookManager(fake_client, stale_threshold_seconds=60)
        await manager.store_snapshot("binance", "BTC/USDT",
                                     [("30000.00", "1.0")], [("30001.00", "1.0")])
        await manager.mark_stale("binance", "BTC/USDT")
        is_stale_flag = await fake_client.hget(
            "leviathan:orderbook:binance:BTC-USDT:meta", "is_stale"
        )
        assert is_stale_flag == b"1"


class TestPriceHub:
    async def test_update_and_retrieve_price_hub(self, manager):
        await manager.update_price_hub("binance", "BTC/USDT",
                                       Decimal("30000.00"), Decimal("30001.00"))
        best = await manager.get_global_best("BTC/USDT")
        assert "binance" in best
        assert best["binance"]["bid"] == Decimal("30000.00")
        assert best["binance"]["ask"] == Decimal("30001.00")

    async def test_multiple_exchanges_in_price_hub(self, manager):
        await manager.update_price_hub("binance", "BTC/USDT",
                                       Decimal("30000.00"), Decimal("30001.00"))
        await manager.update_price_hub("bybit", "BTC/USDT",
                                       Decimal("30005.00"), Decimal("30006.00"))
        best = await manager.get_global_best("BTC/USDT")
        assert "binance" in best
        assert "bybit" in best
        assert best["bybit"]["bid"] == Decimal("30005.00")

    async def test_price_hub_empty_returns_empty_dict(self, manager):
        best = await manager.get_global_best("NONEXISTENT/USDT")
        assert best == {}


class TestChecksumValidation:
    async def test_validate_correct_checksum(self, manager):
        bids = [("30000.00", "1.0"), ("29999.00", "2.0"), ("29998.00", "0.5"),
                ("29997.00", "1.5"), ("29996.00", "3.0")]
        asks = [("30001.00", "1.0"), ("30002.00", "2.0"), ("30003.00", "0.5"),
                ("30004.00", "1.5"), ("30005.00", "3.0")]
        await manager.store_snapshot("binance", "BTC/USDT", bids, asks)
        book = await manager.get_orderbook("binance", "BTC/USDT")
        checksum = book.compute_checksum()
        assert await manager.validate_checksum("binance", "BTC/USDT", checksum) is True

    async def test_validate_wrong_checksum(self, manager):
        await manager.store_snapshot("binance", "BTC/USDT",
                                     [("30000.00", "1.0")], [("30001.00", "1.0")])
        assert await manager.validate_checksum("binance", "BTC/USDT", 99999999) is False

    async def test_validate_nonexistent_book_returns_false(self, manager):
        assert await manager.validate_checksum("binance", "GHOST/USDT", 0) is False
