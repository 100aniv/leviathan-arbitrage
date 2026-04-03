"""Tests for futures/spot orderbook separation in CollectorManager and ShadowMode (US-015).

Covers:
- CollectorManager.DEFAULT_EXCHANGES includes "binance_futures"
- CollectorManager._create_collector returns BinanceFuturesCollector for "binance_futures"
- ShadowMode._futures_exchanges contains "binance_futures"
- ShadowMode._futures_books dict exists and is separate from _books
- ShadowMode._on_orderbook stores futures data in _futures_books when
  multi_signal_producer is set
- ShadowMode._on_orderbook stores spot data ONLY in _books, not _futures_books
- _futures_books is keyed by symbol then exchange_id
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.binance_futures_collector import BinanceFuturesCollector
from src.collectors.manager import CollectorManager
from src.modes.shadow import ShadowMode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_shadow(multi_signal_producer=None) -> ShadowMode:
    """Create a ShadowMode with all external dependencies mocked."""
    signal_gen = MagicMock()
    signal_gen.on_orderbook_update = AsyncMock(return_value=None)

    collector_manager = MagicMock()
    collector_manager.start = AsyncMock()
    collector_manager.stop = AsyncMock()

    if multi_signal_producer is None:
        multi_signal_producer = MagicMock()
        multi_signal_producer.on_orderbook = MagicMock()

    return ShadowMode(
        signal_generator=signal_gen,
        collector_manager=collector_manager,
        symbols=["BTC/USDT"],
        multi_signal_producer=multi_signal_producer,
    )


def _run(coro):
    """Run a coroutine synchronously in tests."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# CollectorManager — futures factory
# ---------------------------------------------------------------------------


class TestCollectorManagerFutures:
    def test_default_exchanges_includes_binance_futures(self):
        """binance_futures must be in DEFAULT_EXCHANGES."""
        assert "binance_futures" in CollectorManager.DEFAULT_EXCHANGES

    def test_default_exchanges_has_thirteen_entries(self):
        """All 13 exchanges registered (9 spot + 4 futures). MEXC+Gate.io+Bitget Futures 추가."""
        assert len(CollectorManager.DEFAULT_EXCHANGES) == 13

    def test_factory_creates_binance_futures_collector(self):
        """_create_collector('binance_futures') returns a BinanceFuturesCollector."""
        manager = CollectorManager(
            symbols=["BTC/USDT"],
            exchanges=["binance_futures"],
        )
        collector = manager._create_collector("binance_futures")

        assert collector is not None
        assert isinstance(collector, BinanceFuturesCollector)

    def test_binance_futures_collector_exchange_id(self):
        """Collector created by factory has exchange_id='binance_futures'."""
        manager = CollectorManager(symbols=["BTC/USDT"])
        collector = manager._create_collector("binance_futures")
        assert collector.exchange_id == "binance_futures"

    def test_binance_futures_not_treated_as_korean_exchange(self):
        """binance_futures symbols are NOT remapped to KRW pairs."""
        manager = CollectorManager(symbols=["BTC/USDT"])
        collector = manager._create_collector("binance_futures")
        assert "BTC/USDT" in collector.symbols
        assert "BTC/KRW" not in collector.symbols


# ---------------------------------------------------------------------------
# ShadowMode — _futures_exchanges attribute
# ---------------------------------------------------------------------------


class TestShadowFuturesExchangeAttribute:
    def test_futures_exchanges_contains_binance_futures(self):
        shadow = _make_shadow()
        assert "binance_futures" in shadow._futures_exchanges

    def test_futures_books_dict_initialized_empty(self):
        shadow = _make_shadow()
        assert hasattr(shadow, "_futures_books")
        assert isinstance(shadow._futures_books, dict)
        assert shadow._futures_books == {}

    def test_futures_books_is_separate_from_books(self):
        """_futures_books and _books are distinct dict objects."""
        shadow = _make_shadow()
        assert shadow._futures_books is not shadow._books


# ---------------------------------------------------------------------------
# ShadowMode — _on_orderbook routing: futures vs spot
# ---------------------------------------------------------------------------


class TestShadowOnOrderbookRouting:
    def test_futures_exchange_stored_in_futures_books(self):
        """binance_futures orderbook arrives → stored in _futures_books."""
        shadow = _make_shadow()
        shadow._running = True

        with patch.object(shadow, "_evaluate_multi_strategies", new_callable=AsyncMock):
            _run(shadow._on_orderbook(
                "binance_futures", "BTC/USDT",
                [["50000.0", "1.0"]], [["50001.0", "0.5"]],
            ))

        assert "BTC/USDT" in shadow._futures_books
        assert "binance_futures" in shadow._futures_books["BTC/USDT"]

    def test_futures_exchange_also_stored_in_books(self):
        """Futures orderbook is stored in _books too (for signal generation)."""
        shadow = _make_shadow()
        shadow._running = True

        with patch.object(shadow, "_evaluate_multi_strategies", new_callable=AsyncMock):
            _run(shadow._on_orderbook(
                "binance_futures", "BTC/USDT",
                [["50000.0", "1.0"]], [["50001.0", "0.5"]],
            ))

        assert "BTC/USDT" in shadow._books
        assert "binance_futures" in shadow._books["BTC/USDT"]

    def test_spot_exchange_stored_in_books_only(self):
        """Spot exchange orderbook → _books only, NOT in _futures_books."""
        shadow = _make_shadow()
        shadow._running = True

        with patch.object(shadow, "_evaluate_multi_strategies", new_callable=AsyncMock):
            _run(shadow._on_orderbook(
                "binance", "BTC/USDT",
                [["50000.0", "1.0"]], [["50001.0", "0.5"]],
            ))

        assert "BTC/USDT" in shadow._books
        assert "binance" in shadow._books["BTC/USDT"]
        # Spot exchange must NOT appear in _futures_books
        assert "binance" not in shadow._futures_books.get("BTC/USDT", {})

    def test_futures_books_keyed_by_symbol_then_exchange(self):
        """_futures_books structure: {symbol: {exchange_id: OrderBook}}."""
        shadow = _make_shadow()
        shadow._running = True

        with patch.object(shadow, "_evaluate_multi_strategies", new_callable=AsyncMock):
            _run(shadow._on_orderbook(
                "binance_futures", "BTC/USDT",
                [["50000.0", "1.0"]], [["50001.0", "0.5"]],
            ))

        book = shadow._futures_books["BTC/USDT"]["binance_futures"]
        assert book is not None
        # The OrderBook should have best_bid and best_ask populated
        assert book.best_bid() is not None
        assert book.best_ask() is not None

    def test_without_multi_signal_producer_futures_books_not_populated(self):
        """_futures_books is only populated when multi_signal_producer is set."""
        signal_gen = MagicMock()
        signal_gen.on_orderbook_update = AsyncMock(return_value=None)
        collector_manager = MagicMock()
        collector_manager.start = AsyncMock()
        collector_manager.stop = AsyncMock()

        shadow = ShadowMode(
            signal_generator=signal_gen,
            collector_manager=collector_manager,
            symbols=["BTC/USDT"],
            multi_signal_producer=None,  # no multi producer
        )
        shadow._running = True

        _run(shadow._on_orderbook(
            "binance_futures", "BTC/USDT",
            [["50000.0", "1.0"]], [["50001.0", "0.5"]],
        ))

        # _books is updated regardless
        assert "BTC/USDT" in shadow._books
        # _futures_books is NOT updated when multi_signal_producer is None
        assert shadow._futures_books == {}
