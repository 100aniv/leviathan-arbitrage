"""US-169: MultiStrategySignalProducer — LIVE mode instantiation and signal routing."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.multi_signal import MultiStrategySignalProducer, MultiSignalConfig


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------


class TestMultiStrategySignalProducerInstantiation:
    def test_instantiates_with_event_bus(self):
        """MultiStrategySignalProducer accepts an event_bus and stores config."""
        bus = MagicMock()
        producer = MultiStrategySignalProducer(event_bus=bus)
        assert producer._event_bus is bus

    def test_uses_default_config_when_none_provided(self):
        """MultiStrategySignalProducer creates default config when not given."""
        bus = MagicMock()
        producer = MultiStrategySignalProducer(event_bus=bus)
        assert producer._config is not None
        assert isinstance(producer._config, MultiSignalConfig)

    def test_accepts_custom_config(self):
        """MultiStrategySignalProducer stores provided config object."""
        bus = MagicMock()
        config = MultiSignalConfig(default_notional_usd=__import__("decimal").Decimal("1000"))
        producer = MultiStrategySignalProducer(event_bus=bus, config=config)
        assert producer._config is config

    def test_not_running_on_init(self):
        """Producer is not running until start() is called."""
        bus = MagicMock()
        producer = MultiStrategySignalProducer(event_bus=bus)
        assert producer._running is False

    def test_accepts_latency_tracker(self):
        """MultiStrategySignalProducer stores optional latency_tracker."""
        bus = MagicMock()
        tracker = MagicMock()
        producer = MultiStrategySignalProducer(event_bus=bus, latency_tracker=tracker)
        assert producer._latency_tracker is tracker


# ---------------------------------------------------------------------------
# Orderbook update — signal production
# ---------------------------------------------------------------------------


class TestOnOrderbook:
    def test_on_orderbook_caches_book_per_exchange_and_symbol(self):
        """on_orderbook stores the book in internal cache by (exchange, symbol)."""
        bus = MagicMock()
        producer = MultiStrategySignalProducer(event_bus=bus)
        book = MagicMock()
        producer.on_orderbook("binance", "BTC/USDT", book)
        assert "binance" in producer._orderbooks
        assert "BTC/USDT" in producer._orderbooks["binance"]

    def test_on_orderbook_updates_existing_book(self):
        """on_orderbook overwrites stale book in cache."""
        bus = MagicMock()
        producer = MultiStrategySignalProducer(event_bus=bus)
        book1, book2 = MagicMock(), MagicMock()
        producer.on_orderbook("binance", "BTC/USDT", book1)
        producer.on_orderbook("binance", "BTC/USDT", book2)
        assert producer._orderbooks["binance"]["BTC/USDT"] is book2

    def test_on_orderbook_handles_multiple_exchanges(self):
        """on_orderbook keeps independent caches per exchange."""
        bus = MagicMock()
        producer = MultiStrategySignalProducer(event_bus=bus)
        book_a, book_b = MagicMock(), MagicMock()
        producer.on_orderbook("binance", "BTC/USDT", book_a)
        producer.on_orderbook("okx", "BTC/USDT", book_b)
        assert producer._orderbooks["binance"]["BTC/USDT"] is not producer._orderbooks["okx"]["BTC/USDT"]


# ---------------------------------------------------------------------------
# Volume calculation
# ---------------------------------------------------------------------------


class TestVolumeFromPrice:
    def test_volume_from_price_uses_notional(self):
        """_volume_from_price returns notional / price."""
        from decimal import Decimal
        bus = MagicMock()
        config = MultiSignalConfig(default_notional_usd=Decimal("500"))
        producer = MultiStrategySignalProducer(event_bus=bus, config=config)
        vol = producer._volume_from_price(Decimal("50000"))
        assert vol == Decimal("500") / Decimal("50000")

    def test_volume_from_price_minimum_guard(self):
        """_volume_from_price returns at least 0.0001 for very high prices."""
        from decimal import Decimal
        bus = MagicMock()
        config = MultiSignalConfig(default_notional_usd=Decimal("0.000001"))
        producer = MultiStrategySignalProducer(event_bus=bus, config=config)
        vol = producer._volume_from_price(Decimal("99999999"))
        assert vol >= Decimal("0.0001")

    def test_volume_from_price_zero_price_guard(self):
        """_volume_from_price handles zero price without ZeroDivisionError."""
        from decimal import Decimal
        bus = MagicMock()
        producer = MultiStrategySignalProducer(event_bus=bus)
        vol = producer._volume_from_price(Decimal("0"))
        assert vol >= Decimal("0.0001")
