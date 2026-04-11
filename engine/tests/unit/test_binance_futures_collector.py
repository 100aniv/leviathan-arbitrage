"""Tests for BinanceFuturesCollector (US-014).

Covers: exchange_id, inheritance from BaseCollector, WebSocket URL generation
(single vs multi symbol), _parse_message for all three message formats,
subscribe_message returns empty string, callback invocation.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.collectors.base_collector import BaseCollector
from src.collectors.binance_futures_collector import BinanceFuturesCollector


# ---------------------------------------------------------------------------
# Identity and inheritance
# ---------------------------------------------------------------------------


class TestBinanceFuturesCollectorIdentity:
    def test_exchange_id_is_binance_futures(self):
        collector = BinanceFuturesCollector(symbols=["BTC/USDT"])
        assert collector.exchange_id == "binance_futures"

    def test_inherits_base_collector(self):
        collector = BinanceFuturesCollector(symbols=["BTC/USDT"])
        assert isinstance(collector, BaseCollector)

    def test_symbols_stored_correctly(self):
        collector = BinanceFuturesCollector(symbols=["BTC/USDT", "ETH/USDT"])
        assert collector.symbols == ["BTC/USDT", "ETH/USDT"]


# ---------------------------------------------------------------------------
# WebSocket URL generation
# ---------------------------------------------------------------------------


class TestWsUrl:
    def test_ws_url_single_symbol(self):
        """Single symbol uses per-symbol stream endpoint (new /market/ prefix, BUG-68)."""
        collector = BinanceFuturesCollector(symbols=["BTC/USDT"])
        url = collector._ws_url()
        assert url == "wss://fstream.binance.com/market/ws/btcusdt@depth20@100ms"

    def test_ws_url_multi_symbol(self):
        """Multiple symbols use combined stream endpoint (new /market/ prefix, BUG-68)."""
        collector = BinanceFuturesCollector(symbols=["BTC/USDT", "ETH/USDT"])
        url = collector._ws_url()
        assert url == "wss://fstream.binance.com/market/stream?streams=btcusdt@depth20@100ms/ethusdt@depth20@100ms"

    def test_ws_url_symbol_normalized_to_lowercase_no_slash(self):
        """Symbols like BTC/USDT are normalized to btcusdt."""
        collector = BinanceFuturesCollector(symbols=["SOL/USDT"])
        url = collector._ws_url()
        assert "solusdt" in url
        assert "/" not in url.split("/market/ws/")[-1]

    def test_subscribe_message_returns_empty_string(self):
        """Subscription is encoded in URL path; no subscribe frame is sent."""
        collector = BinanceFuturesCollector(symbols=["BTC/USDT"])
        msg = collector._subscribe_message("BTC/USDT")
        assert msg == ""


# ---------------------------------------------------------------------------
# _parse_message: three supported formats
# ---------------------------------------------------------------------------


class TestParseMessage:
    def setup_method(self):
        self.collector = BinanceFuturesCollector(symbols=["BTC/USDT"])

    def test_parse_combined_stream_message(self):
        """Combined stream wraps payload under 'stream' and 'data' keys."""
        data = {
            "stream": "btcusdt@depth20@100ms",
            "data": {
                "bids": [["50000.0", "1.0"], ["49999.0", "2.0"]],
                "asks": [["50001.0", "0.5"]],
            },
        }
        result = self.collector._parse_message(data)

        assert result is not None
        symbol, bids, asks = result
        assert symbol == "BTC/USDT"
        assert bids == [["50000.0", "1.0"], ["49999.0", "2.0"]]
        assert asks == [["50001.0", "0.5"]]

    def test_parse_combined_stream_derives_symbol_from_stream_name(self):
        """Symbol is derived from the 'stream' field, not self.symbols."""
        collector = BinanceFuturesCollector(symbols=["ETH/USDT"])
        data = {
            "stream": "btcusdt@depth20@100ms",
            "data": {"bids": [["50000", "1"]], "asks": [["50001", "1"]]},
        }
        result = collector._parse_message(data)
        assert result is not None
        symbol, _, _ = result
        assert symbol == "BTC/USDT"

    def test_parse_single_symbol_message(self):
        """Single-symbol stream uses top-level 'bids'/'asks' keys."""
        data = {
            "lastUpdateId": 123456,
            "bids": [["49999.0", "2.0"]],
            "asks": [["50000.0", "1.5"]],
        }
        result = self.collector._parse_message(data)

        assert result is not None
        symbol, bids, asks = result
        assert symbol == "BTC/USDT"
        assert bids == [["49999.0", "2.0"]]
        assert asks == [["50000.0", "1.5"]]

    def test_parse_futures_delta_ba_format(self):
        """Futures depth update uses compact 'b' and 'a' keys."""
        data = {
            "b": [["50100.0", "0.8"]],
            "a": [["50102.0", "0.3"]],
        }
        result = self.collector._parse_message(data)

        assert result is not None
        symbol, bids, asks = result
        assert symbol == "BTC/USDT"
        assert bids == [["50100.0", "0.8"]]
        assert asks == [["50102.0", "0.3"]]

    def test_parse_irrelevant_message_returns_none(self):
        """Unknown message formats return None."""
        assert self.collector._parse_message({"type": "heartbeat"}) is None

    def test_parse_empty_dict_returns_none(self):
        assert self.collector._parse_message({}) is None

    def test_parse_subscription_ack_returns_none(self):
        assert self.collector._parse_message({"result": None, "id": 1}) is None


# ---------------------------------------------------------------------------
# Callback invocation via _handle_message
# ---------------------------------------------------------------------------


class TestCallbackInvocation:
    def test_on_orderbook_callback_invoked_with_correct_args(self):
        """_handle_message dispatches parsed orderbook to the callback."""
        received = []

        async def mock_callback(exchange_id, symbol, bids, asks):
            received.append((exchange_id, symbol, bids, asks))

        collector = BinanceFuturesCollector(
            symbols=["BTC/USDT"],
            on_orderbook=mock_callback,
        )
        raw = json.dumps({
            "bids": [["50000.0", "1.0"]],
            "asks": [["50001.0", "0.5"]],
        })

        asyncio.run(collector._handle_message(raw))

        assert len(received) == 1
        exchange_id, symbol, bids, asks = received[0]
        assert exchange_id == "binance_futures"
        assert symbol == "BTC/USDT"
        assert bids == [["50000.0", "1.0"]]
        assert asks == [["50001.0", "0.5"]]

    def test_no_callback_does_not_raise(self):
        """_handle_message with no callback runs silently."""
        collector = BinanceFuturesCollector(symbols=["BTC/USDT"], on_orderbook=None)
        raw = json.dumps({"bids": [["50000", "1"]], "asks": [["50001", "1"]]})
        asyncio.run(collector._handle_message(raw))  # must not raise

    def test_irrelevant_message_callback_not_invoked(self):
        """Callback is NOT called when message parses to None."""
        called = []

        async def mock_callback(*args):
            called.append(args)

        collector = BinanceFuturesCollector(
            symbols=["BTC/USDT"],
            on_orderbook=mock_callback,
        )
        asyncio.run(collector._handle_message(json.dumps({"result": None})))
        assert called == []
