"""Integration tests for futures infrastructure (US-018).

Covers the full data pipeline for GAP 5 (futures orderbook) and GAP 6 (funding rates):

GAP 5 — Futures orderbook flow:
  BinanceFuturesCollector WS message → _parse_message → callback → ShadowMode._futures_books
  Spot vs futures orderbook separation end-to-end

GAP 6 — Funding rate flow:
  FundingRateCollector.poll_once() [mock HTTP] → FundingRateStore populated
  All 4 exchanges (binance_futures, bybit, okx, bitget) parsed correctly
  Error resilience: one exchange HTTP fails → others still succeed
  FundingRateStore.get_rate_diff() enables cross-exchange rate comparison

All network calls are mocked (no live connections).
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.binance_futures_collector import BinanceFuturesCollector
from src.collectors.funding_rate_collector import (
    DEFAULT_EXCHANGES,
    DEFAULT_SYMBOLS,
    FundingRateCollector,
    FundingRateEntry,
    FundingRateStore,
)
from src.modes.shadow import ShadowMode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_http_response(json_data: dict, status_code: int = 200) -> MagicMock:
    """Create a mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    return resp


def _make_shadow(multi_signal_producer=None) -> ShadowMode:
    """Create ShadowMode with all external deps mocked."""
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
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# GAP 6: FundingRateStore — unit-level data structure tests
# ---------------------------------------------------------------------------


class TestFundingRateStore:
    def test_set_and_get_rate(self):
        """set_rate stores entry; get_rate retrieves it."""
        store = FundingRateStore()
        store.set_rate("binance_futures", "BTC/USDT", rate=0.0001, next_funding_time=1710979200.0)

        entry = store.get_rate("binance_futures", "BTC/USDT")
        assert entry is not None
        assert entry.rate == pytest.approx(0.0001)
        assert entry.next_funding_time == pytest.approx(1710979200.0)

    def test_get_rate_returns_none_for_missing_exchange(self):
        store = FundingRateStore()
        assert store.get_rate("unknown_exchange", "BTC/USDT") is None

    def test_get_rate_diff_returns_difference(self):
        """get_rate_diff returns rate_a - rate_b for same symbol."""
        store = FundingRateStore()
        store.set_rate("binance_futures", "BTC/USDT", rate=0.0003)
        store.set_rate("bybit", "BTC/USDT", rate=0.0001)

        diff = store.get_rate_diff("BTC/USDT", "binance_futures", "bybit")
        assert diff is not None
        assert diff == pytest.approx(0.0002)

    def test_get_rate_diff_returns_none_when_one_missing(self):
        store = FundingRateStore()
        store.set_rate("binance_futures", "BTC/USDT", rate=0.0001)
        # bybit missing
        assert store.get_rate_diff("BTC/USDT", "binance_futures", "bybit") is None

    def test_get_all_rates_returns_all_exchanges(self):
        store = FundingRateStore()
        store.set_rate("binance_futures", "BTC/USDT", rate=0.0001)
        store.set_rate("bybit", "ETH/USDT", rate=0.0002)

        all_rates = store.get_all_rates()
        assert "binance_futures" in all_rates
        assert "bybit" in all_rates


# ---------------------------------------------------------------------------
# GAP 6: FundingRateCollector — mock HTTP polling (all 4 exchanges)
# ---------------------------------------------------------------------------


class TestFundingRateCollectorPollOnce:
    def _make_collector(self, mock_client: MagicMock) -> FundingRateCollector:
        collector = FundingRateCollector(
            symbols=["BTC/USDT"],
            exchanges=["binance_futures", "bybit", "okx", "bitget"],
            poll_interval=60.0,
            http_client=mock_client,
        )
        return collector

    def _binance_response(self) -> MagicMock:
        return _make_http_response({
            "lastFundingRate": "0.00010000",
            "nextFundingTime": 1710979200000,
        })

    def _bybit_response(self) -> MagicMock:
        return _make_http_response({
            "result": {
                "list": [{"fundingRate": "0.00012345", "nextFundingTime": "1710979200000"}]
            }
        })

    def _okx_response(self) -> MagicMock:
        return _make_http_response({
            "data": [{"fundingRate": "0.00015000", "nextFundingTime": "1710979200000"}]
        })

    def _bitget_response(self) -> MagicMock:
        return _make_http_response({
            "data": [{"fundingRate": "0.00008000"}]
        })

    def test_poll_once_populates_all_4_exchanges(self):
        """poll_once() with mocked HTTP → all 4 exchange entries in store."""
        client = MagicMock()
        client.get = AsyncMock(side_effect=[
            self._binance_response(),
            self._bybit_response(),
            self._okx_response(),
            self._bitget_response(),
        ])

        collector = self._make_collector(client)
        result = _run(collector.poll_once())

        assert "binance_futures" in result
        assert "bybit" in result
        assert "okx" in result
        assert "bitget" in result

    def test_poll_once_binance_rate_parsed_correctly(self):
        """Binance lastFundingRate is correctly extracted."""
        client = MagicMock()
        client.get = AsyncMock(side_effect=[
            self._binance_response(),
            self._bybit_response(),
            self._okx_response(),
            self._bitget_response(),
        ])

        collector = self._make_collector(client)
        _run(collector.poll_once())

        entry = collector.store.get_rate("binance_futures", "BTC/USDT")
        assert entry is not None
        assert entry.rate == pytest.approx(0.0001)
        assert entry.next_funding_time == pytest.approx(1710979200.0)

    def test_poll_once_bybit_rate_parsed_correctly(self):
        client = MagicMock()
        client.get = AsyncMock(side_effect=[
            self._binance_response(),
            self._bybit_response(),
            self._okx_response(),
            self._bitget_response(),
        ])

        collector = self._make_collector(client)
        _run(collector.poll_once())

        entry = collector.store.get_rate("bybit", "BTC/USDT")
        assert entry is not None
        assert entry.rate == pytest.approx(0.00012345)

    def test_poll_once_okx_rate_parsed_correctly(self):
        client = MagicMock()
        client.get = AsyncMock(side_effect=[
            self._binance_response(),
            self._bybit_response(),
            self._okx_response(),
            self._bitget_response(),
        ])

        collector = self._make_collector(client)
        _run(collector.poll_once())

        entry = collector.store.get_rate("okx", "BTC/USDT")
        assert entry is not None
        assert entry.rate == pytest.approx(0.00015)

    def test_poll_once_bitget_rate_parsed_correctly(self):
        client = MagicMock()
        client.get = AsyncMock(side_effect=[
            self._binance_response(),
            self._bybit_response(),
            self._okx_response(),
            self._bitget_response(),
        ])

        collector = self._make_collector(client)
        _run(collector.poll_once())

        entry = collector.store.get_rate("bitget", "BTC/USDT")
        assert entry is not None
        assert entry.rate == pytest.approx(0.00008)

    def test_default_poll_interval_is_60_seconds(self):
        collector = FundingRateCollector()
        assert collector.poll_interval == 60.0

    def test_default_exchanges_includes_all_4(self):
        assert set(DEFAULT_EXCHANGES) == {"binance_futures", "bybit", "okx", "bitget"}

    def test_one_exchange_http_error_others_still_succeed(self):
        """When one exchange returns non-200, others are still populated."""
        client = MagicMock()
        client.get = AsyncMock(side_effect=[
            _make_http_response({}, status_code=500),   # binance fails
            self._bybit_response(),
            self._okx_response(),
            self._bitget_response(),
        ])

        collector = self._make_collector(client)
        result = _run(collector.poll_once())

        # binance failed, so not in result
        assert "binance_futures" not in result
        # others still succeed
        assert "bybit" in result
        assert "okx" in result
        assert "bitget" in result

    def test_one_exchange_network_exception_others_still_succeed(self):
        """When one exchange raises an exception, others are still populated."""
        client = MagicMock()
        client.get = AsyncMock(side_effect=[
            Exception("Connection refused"),            # binance raises
            self._bybit_response(),
            self._okx_response(),
            self._bitget_response(),
        ])

        collector = self._make_collector(client)
        result = _run(collector.poll_once())

        assert "binance_futures" not in result
        assert "bybit" in result
        assert "okx" in result
        assert "bitget" in result

    def test_store_persists_across_multiple_polls(self):
        """Successive poll_once() calls update the store (data persists)."""
        client = MagicMock()
        client.get = AsyncMock(side_effect=[
            # First poll
            self._binance_response(),
            self._bybit_response(),
            self._okx_response(),
            self._bitget_response(),
            # Second poll with updated rate
            _make_http_response({"lastFundingRate": "0.00020000", "nextFundingTime": None}),
            self._bybit_response(),
            self._okx_response(),
            self._bitget_response(),
        ])

        collector = self._make_collector(client)
        _run(collector.poll_once())
        _run(collector.poll_once())

        # Rate should be updated to latest value
        entry = collector.store.get_rate("binance_futures", "BTC/USDT")
        assert entry is not None
        assert entry.rate == pytest.approx(0.0002)


# ---------------------------------------------------------------------------
# GAP 5: BinanceFuturesCollector → ShadowMode._futures_books (integration)
# ---------------------------------------------------------------------------


class TestFuturesCollectorToShadowIntegration:
    def test_binance_futures_collector_delivers_to_shadow_futures_books(self):
        """End-to-end: BinanceFuturesCollector callback → ShadowMode._futures_books."""
        shadow = _make_shadow()
        shadow._running = True

        # Create collector wired directly to shadow's _on_orderbook callback
        collector = BinanceFuturesCollector(
            symbols=["BTC/USDT"],
            on_orderbook=shadow._on_orderbook,
        )

        raw_msg = json.dumps({
            "bids": [["50000.0", "1.0"], ["49999.0", "2.0"]],
            "asks": [["50001.0", "0.5"]],
        })

        with patch.object(shadow, "_evaluate_multi_strategies", new_callable=AsyncMock):
            _run(collector._handle_message(raw_msg))

        assert "BTC/USDT" in shadow._futures_books
        assert "binance_futures" in shadow._futures_books["BTC/USDT"]

    def test_shadow_spot_vs_futures_books_are_separated(self):
        """Spot (binance) and futures (binance_futures) stored in separate dicts."""
        shadow = _make_shadow()
        shadow._running = True

        spot_collector = BinanceFuturesCollector.__new__(BinanceFuturesCollector)
        # Manually simulate spot callback (exchange_id = "binance")
        with patch.object(shadow, "_evaluate_multi_strategies", new_callable=AsyncMock):
            _run(shadow._on_orderbook(
                "binance", "BTC/USDT",
                [["50000.0", "1.0"]], [["50001.0", "0.5"]],
            ))
            _run(shadow._on_orderbook(
                "binance_futures", "BTC/USDT",
                [["50050.0", "0.8"]], [["50052.0", "0.3"]],
            ))

        # Both in _books
        assert "binance" in shadow._books.get("BTC/USDT", {})
        assert "binance_futures" in shadow._books.get("BTC/USDT", {})

        # Only futures in _futures_books
        assert "binance_futures" in shadow._futures_books.get("BTC/USDT", {})
        assert "binance" not in shadow._futures_books.get("BTC/USDT", {})

    def test_futures_orderbook_best_bid_ask_readable_from_shadow(self):
        """Futures OrderBook stored in shadow has readable best_bid/best_ask."""
        shadow = _make_shadow()
        shadow._running = True

        with patch.object(shadow, "_evaluate_multi_strategies", new_callable=AsyncMock):
            _run(shadow._on_orderbook(
                "binance_futures", "BTC/USDT",
                [["50000.0", "1.5"]], [["50001.0", "0.5"]],
            ))

        book = shadow._futures_books["BTC/USDT"]["binance_futures"]
        assert float(book.best_bid()) == pytest.approx(50000.0)
        assert float(book.best_ask()) == pytest.approx(50001.0)

    def test_funding_rate_store_and_futures_books_independent_data_sources(self):
        """FundingRateStore and _futures_books can both be populated independently."""
        shadow = _make_shadow()
        shadow._running = True

        # Populate funding rates directly (simulating FundingRateCollector output)
        store = FundingRateStore()
        store.set_rate("binance_futures", "BTC/USDT", rate=0.0001)
        store.set_rate("bybit", "BTC/USDT", rate=0.0003)

        # Verify rate diff is detectable (would trigger funding rate arb signal)
        diff = store.get_rate_diff("BTC/USDT", "bybit", "binance_futures")
        assert diff is not None
        assert diff == pytest.approx(0.0002)

        # Also populate futures books
        with patch.object(shadow, "_evaluate_multi_strategies", new_callable=AsyncMock):
            _run(shadow._on_orderbook(
                "binance_futures", "BTC/USDT",
                [["50000.0", "1.0"]], [["50001.0", "0.5"]],
            ))

        # Both data sources populated and readable
        assert store.get_rate("binance_futures", "BTC/USDT") is not None
        assert "binance_futures" in shadow._futures_books.get("BTC/USDT", {})
