"""Tests for BybitFuturesCollector (US-075).

Covers: exchange_id, inheritance, WS URL, subscribe message format,
_parse_message for snapshot/delta/ack, symbol denormalization, callback invocation.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from src.collectors.base_collector import BaseCollector
from src.collectors.bybit_futures_collector import BybitFuturesCollector, _normalize_symbol, _denormalize_symbol


# ---------------------------------------------------------------------------
# Symbol conversion helpers
# ---------------------------------------------------------------------------


class TestSymbolConversion:
    def test_normalize_btc_usdt(self):
        assert _normalize_symbol("BTC/USDT") == "BTCUSDT"

    def test_normalize_eth_usdt(self):
        assert _normalize_symbol("ETH/USDT") == "ETHUSDT"

    def test_denormalize_btcusdt(self):
        assert _denormalize_symbol("BTCUSDT") == "BTC/USDT"

    def test_denormalize_ethusdt(self):
        assert _denormalize_symbol("ETHUSDT") == "ETH/USDT"


# ---------------------------------------------------------------------------
# Identity and inheritance
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_exchange_id(self):
        c = BybitFuturesCollector(symbols=["BTC/USDT"])
        assert c.exchange_id == "bybit_futures"

    def test_inherits_base_collector(self):
        c = BybitFuturesCollector(symbols=["BTC/USDT"])
        assert isinstance(c, BaseCollector)

    def test_symbols_stored(self):
        c = BybitFuturesCollector(symbols=["BTC/USDT", "ETH/USDT"])
        assert c.symbols == ["BTC/USDT", "ETH/USDT"]


# ---------------------------------------------------------------------------
# WS URL
# ---------------------------------------------------------------------------


class TestWsUrl:
    def test_ws_url_is_linear(self):
        c = BybitFuturesCollector(symbols=["BTC/USDT"])
        assert c._ws_url() == "wss://stream.bybit.com/v5/public/linear"

    def test_ws_url_not_spot(self):
        c = BybitFuturesCollector(symbols=["BTC/USDT"])
        assert "/spot" not in c._ws_url()


# ---------------------------------------------------------------------------
# Subscribe message
# ---------------------------------------------------------------------------


class TestSubscribeMessage:
    def test_subscribe_message_btc(self):
        c = BybitFuturesCollector(symbols=["BTC/USDT"])
        msg = c._subscribe_message("BTC/USDT")
        assert msg == {
            "op": "subscribe",
            "args": ["orderbook.50.BTCUSDT"],
        }

    def test_subscribe_message_eth(self):
        c = BybitFuturesCollector(symbols=["ETH/USDT"])
        msg = c._subscribe_message("ETH/USDT")
        assert msg["args"] == ["orderbook.50.ETHUSDT"]


# ---------------------------------------------------------------------------
# _parse_message
# ---------------------------------------------------------------------------


class TestParseMessage:
    def setup_method(self):
        self.c = BybitFuturesCollector(symbols=["BTC/USDT"])

    def test_parse_snapshot(self):
        data = {
            "type": "snapshot",
            "topic": "orderbook.50.BTCUSDT",
            "data": {"b": [["50000", "1"]], "a": [["50001", "0.5"]]},
        }
        result = self.c._parse_message(data)
        assert result is not None
        symbol, bids, asks = result
        assert symbol == "BTC/USDT"
        assert bids == [["50000", "1"]]
        assert asks == [["50001", "0.5"]]

    def test_parse_delta(self):
        data = {
            "type": "delta",
            "topic": "orderbook.50.ETHUSDT",
            "data": {"b": [["3000", "2"]], "a": [["3001", "1"]]},
        }
        result = self.c._parse_message(data)
        assert result is not None
        symbol, bids, asks = result
        assert symbol == "ETH/USDT"
        assert bids == [["3000", "2"]]

    def test_parse_ack_returns_none(self):
        data = {"op": "subscribe", "success": True, "ret_msg": ""}
        assert self.c._parse_message(data) is None

    def test_parse_pong_returns_none(self):
        data = {"op": "pong"}
        assert self.c._parse_message(data) is None

    def test_parse_bad_topic_returns_none(self):
        data = {"type": "snapshot", "topic": "orderbook.50", "data": {"b": [], "a": []}}
        assert self.c._parse_message(data) is None


# ---------------------------------------------------------------------------
# Callback invocation
# ---------------------------------------------------------------------------


class TestCallback:
    def test_callback_invoked(self):
        received = []

        async def cb(exchange_id, symbol, bids, asks):
            received.append((exchange_id, symbol, bids, asks))

        c = BybitFuturesCollector(symbols=["BTC/USDT"], on_orderbook=cb)
        raw = json.dumps({
            "type": "snapshot",
            "topic": "orderbook.50.BTCUSDT",
            "data": {"b": [["50000", "1"]], "a": [["50001", "0.5"]]},
        })
        asyncio.run(c._handle_message(raw))

        assert len(received) == 1
        exchange_id, symbol, bids, asks = received[0]
        assert exchange_id == "bybit_futures"
        assert symbol == "BTC/USDT"
        assert bids == [["50000", "1"]]

    def test_no_callback_does_not_raise(self):
        c = BybitFuturesCollector(symbols=["BTC/USDT"], on_orderbook=None)
        raw = json.dumps({
            "type": "snapshot",
            "topic": "orderbook.50.BTCUSDT",
            "data": {"b": [["50000", "1"]], "a": [["50001", "0.5"]]},
        })
        asyncio.run(c._handle_message(raw))  # must not raise
