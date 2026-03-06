"""Tests for market data normalizer."""
import pytest
import fakeredis.aioredis as aioredis_fake
from decimal import Decimal
from src.infra.redis.client import RedisClient, RedisConfig
from src.infra.redis.market_data import MarketDataNormalizer, NormalizedTicker


@pytest.fixture
def fake_client():
    config = RedisConfig()
    client = RedisClient(config)
    client._redis = aioredis_fake.FakeRedis()
    return client


@pytest.fixture
def normalizer():
    return MarketDataNormalizer()


class TestNormalizeBinance:
    def test_normalize_binance_book_ticker(self, normalizer):
        raw = {
            "s": "BTCUSDT",
            "b": "30000.00",
            "B": "1.5",
            "a": "30001.00",
            "A": "0.8",
            "c": "30000.50",
            "v": "12345.67",
        }
        ticker = normalizer.normalize_binance(raw)
        assert ticker.symbol == "BTC/USDT"
        assert ticker.exchange == "binance"
        assert ticker.bid == Decimal("30000.00")
        assert ticker.ask == Decimal("30001.00")
        assert ticker.last == Decimal("30000.50")
        assert isinstance(ticker.bid, Decimal)
        assert isinstance(ticker.ask, Decimal)

    def test_normalize_binance_uses_decimal_not_float(self, normalizer):
        raw = {"s": "ETHUSDT", "b": "2000.12345678", "B": "1.0",
               "a": "2000.12345679", "A": "1.0", "c": "2000.12", "v": "500"}
        ticker = normalizer.normalize_binance(raw)
        # Decimal preserves exact precision
        assert ticker.bid == Decimal("2000.12345678")
        assert ticker.ask == Decimal("2000.12345679")

    def test_normalize_binance_volume(self, normalizer):
        raw = {"s": "SOLUSDT", "b": "100.00", "B": "10", "a": "100.01",
               "A": "5", "c": "100.00", "v": "9999.99"}
        ticker = normalizer.normalize_binance(raw)
        assert ticker.volume == Decimal("9999.99")


class TestNormalizeBybit:
    def test_normalize_bybit_ticker(self, normalizer):
        raw = {
            "symbol": "BTCUSDT",
            "bid1Price": "30000.00",
            "bid1Size": "1.5",
            "ask1Price": "30001.00",
            "ask1Size": "0.8",
            "lastPrice": "30000.50",
            "volume24h": "12345.67",
        }
        ticker = normalizer.normalize_bybit(raw)
        assert ticker.symbol == "BTC/USDT"
        assert ticker.exchange == "bybit"
        assert ticker.bid == Decimal("30000.00")
        assert ticker.ask == Decimal("30001.00")
        assert ticker.last == Decimal("30000.50")
        assert isinstance(ticker.bid, Decimal)

    def test_normalize_bybit_uses_decimal_not_float(self, normalizer):
        raw = {"symbol": "ETHUSDT", "bid1Price": "2000.00000001",
               "bid1Size": "1", "ask1Price": "2000.00000002", "ask1Size": "1",
               "lastPrice": "2000.00", "volume24h": "100"}
        ticker = normalizer.normalize_bybit(raw)
        assert ticker.bid == Decimal("2000.00000001")


class TestCrossExchangeSpread:
    def make_ticker(self, exchange, bid, ask):
        return NormalizedTicker(
            exchange=exchange, symbol="BTC/USDT",
            bid=Decimal(bid), ask=Decimal(ask),
            last=Decimal(bid), volume=Decimal("100"),
            timestamp=0,
        )

    def test_positive_spread_is_arb_opportunity(self, normalizer):
        # Buy on binance (ask=30001), sell on bybit (bid=30005) → profit=4
        ticker_a = self.make_ticker("binance", "30000.00", "30001.00")
        ticker_b = self.make_ticker("bybit", "30005.00", "30006.00")
        spread = normalizer.cross_exchange_spread(ticker_a, ticker_b)
        assert spread == Decimal("4.00")

    def test_negative_spread_no_opportunity(self, normalizer):
        # Buy on binance (ask=30011), sell on bybit (bid=30000) → loss=11
        ticker_a = self.make_ticker("binance", "30010.00", "30011.00")
        ticker_b = self.make_ticker("bybit", "30000.00", "30001.00")
        spread = normalizer.cross_exchange_spread(ticker_a, ticker_b)
        assert spread == Decimal("-11.00")

    def test_zero_spread(self, normalizer):
        ticker_a = self.make_ticker("binance", "30000.00", "30001.00")
        ticker_b = self.make_ticker("bybit", "30001.00", "30002.00")
        spread = normalizer.cross_exchange_spread(ticker_a, ticker_b)
        assert spread == Decimal("0.00")

    def test_spread_uses_decimal_arithmetic(self, normalizer):
        # Ensures no floating point errors
        ticker_a = self.make_ticker("binance", "0.00000001", "0.00000002")
        ticker_b = self.make_ticker("bybit", "0.00000003", "0.00000004")
        spread = normalizer.cross_exchange_spread(ticker_a, ticker_b)
        assert spread == Decimal("0.00000001")


class TestMarketDataStore:
    async def test_store_and_retrieve_ticker(self, fake_client):
        normalizer = MarketDataNormalizer(fake_client)
        ticker = NormalizedTicker(
            exchange="binance", symbol="BTC/USDT",
            bid=Decimal("30000.00"), ask=Decimal("30001.00"),
            last=Decimal("30000.50"), volume=Decimal("12345.67"),
            timestamp=1234567890,
        )
        await normalizer.store_ticker(ticker)
        retrieved = await normalizer.get_ticker("binance", "BTC/USDT")
        assert retrieved is not None
        assert retrieved.exchange == "binance"
        assert retrieved.symbol == "BTC/USDT"
        assert retrieved.bid == Decimal("30000.00")
        assert retrieved.ask == Decimal("30001.00")
        assert retrieved.last == Decimal("30000.50")

    async def test_get_missing_ticker_returns_none(self, fake_client):
        normalizer = MarketDataNormalizer(fake_client)
        result = await normalizer.get_ticker("binance", "GHOST/USDT")
        assert result is None

    async def test_store_ticker_overwrites_previous(self, fake_client):
        normalizer = MarketDataNormalizer(fake_client)
        ticker1 = NormalizedTicker("binance", "BTC/USDT", Decimal("30000"), Decimal("30001"),
                                   Decimal("30000"), Decimal("100"), 1000)
        ticker2 = NormalizedTicker("binance", "BTC/USDT", Decimal("31000"), Decimal("31001"),
                                   Decimal("31000"), Decimal("200"), 2000)
        await normalizer.store_ticker(ticker1)
        await normalizer.store_ticker(ticker2)
        retrieved = await normalizer.get_ticker("binance", "BTC/USDT")
        assert retrieved.bid == Decimal("31000")

    async def test_store_without_client_raises(self):
        normalizer = MarketDataNormalizer()  # no client
        ticker = NormalizedTicker("binance", "BTC/USDT", Decimal("1"), Decimal("2"),
                                  Decimal("1"), Decimal("1"), 0)
        with pytest.raises(RuntimeError):
            await normalizer.store_ticker(ticker)
