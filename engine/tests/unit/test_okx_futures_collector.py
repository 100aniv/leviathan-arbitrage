"""Tests for OKXFuturesCollector (US-075).

Covers: exchange_id, inheritance, WS URL, subscribe message format,
_parse_message for snapshot/update/ack, symbol normalization, callback invocation.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from src.collectors.base_collector import BaseCollector
from src.collectors.okx_futures_collector import OKXFuturesCollector, _normalize_symbol, _denormalize_symbol


# ---------------------------------------------------------------------------
# Symbol conversion helpers
# ---------------------------------------------------------------------------


class TestSymbolConversion:
    def test_normalize_btc_usdt(self):
        assert _normalize_symbol("BTC/USDT") == "BTC-USDT-SWAP"

    def test_normalize_eth_usdt(self):
        assert _normalize_symbol("ETH/USDT") == "ETH-USDT-SWAP"

    def test_denormalize_btc_usdt_swap(self):
        assert _denormalize_symbol("BTC-USDT-SWAP") == "BTC/USDT"

    def test_denormalize_eth_usdt_swap(self):
        assert _denormalize_symbol("ETH-USDT-SWAP") == "ETH/USDT"


# ---------------------------------------------------------------------------
# Identity and inheritance
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_exchange_id(self):
        c = OKXFuturesCollector(symbols=["BTC/USDT"])
        assert c.exchange_id == "okx_futures"

    def test_inherits_base_collector(self):
        c = OKXFuturesCollector(symbols=["BTC/USDT"])
        assert isinstance(c, BaseCollector)

    def test_symbols_stored(self):
        c = OKXFuturesCollector(symbols=["BTC/USDT", "ETH/USDT"])
        assert c.symbols == ["BTC/USDT", "ETH/USDT"]


# ---------------------------------------------------------------------------
# WS URL
# ---------------------------------------------------------------------------


class TestWsUrl:
    def test_ws_url(self):
        c = OKXFuturesCollector(symbols=["BTC/USDT"])
        assert c._ws_url() == "wss://ws.okx.com:8443/ws/v5/public"


# ---------------------------------------------------------------------------
# Subscribe message
# ---------------------------------------------------------------------------


class TestSubscribeMessage:
    def test_subscribe_message_btc(self):
        c = OKXFuturesCollector(symbols=["BTC/USDT"])
        msg = c._subscribe_message("BTC/USDT")
        assert msg == {
            "op": "subscribe",
            "args": [{"channel": "books5", "instId": "BTC-USDT-SWAP"}],
        }

    def test_subscribe_message_eth(self):
        c = OKXFuturesCollector(symbols=["ETH/USDT"])
        msg = c._subscribe_message("ETH/USDT")
        assert msg["args"][0]["instId"] == "ETH-USDT-SWAP"


# ---------------------------------------------------------------------------
# _parse_message
# ---------------------------------------------------------------------------


class TestParseMessage:
    def setup_method(self):
        self.c = OKXFuturesCollector(symbols=["BTC/USDT"])

    def test_parse_snapshot(self):
        data = {
            "arg": {"channel": "books5", "instId": "BTC-USDT-SWAP"},
            "action": "snapshot",
            "data": [{"bids": [["50000", "1", "0", "1"]], "asks": [["50001", "0.5", "0", "1"]]}],
        }
        result = self.c._parse_message(data)
        assert result is not None
        symbol, bids, asks = result
        assert symbol == "BTC/USDT"
        assert bids == [["50000", "1"]]
        assert asks == [["50001", "0.5"]]

    def test_parse_update(self):
        data = {
            "arg": {"channel": "books5", "instId": "ETH-USDT-SWAP"},
            "action": "update",
            "data": [{"bids": [["3000", "2", "0", "1"]], "asks": [["3001", "1", "0", "1"]]}],
        }
        result = self.c._parse_message(data)
        assert result is not None
        symbol, bids, asks = result
        assert symbol == "ETH/USDT"
        assert bids == [["3000", "2"]]

    def test_parse_ack_returns_none(self):
        data = {"event": "subscribe", "arg": {"channel": "books5", "instId": "BTC-USDT-SWAP"}}
        assert self.c._parse_message(data) is None

    def test_parse_unknown_action_returns_none(self):
        data = {
            "arg": {"instId": "BTC-USDT-SWAP"},
            "action": "heartbeat",
            "data": [{"bids": [], "asks": []}],
        }
        assert self.c._parse_message(data) is None

    def test_parse_empty_data_list_returns_none(self):
        data = {
            "arg": {"instId": "BTC-USDT-SWAP"},
            "action": "snapshot",
            "data": [],
        }
        assert self.c._parse_message(data) is None


# ---------------------------------------------------------------------------
# Callback invocation
# ---------------------------------------------------------------------------


class TestCallback:
    def test_callback_invoked(self):
        received = []

        async def cb(exchange_id, symbol, bids, asks):
            received.append((exchange_id, symbol, bids, asks))

        c = OKXFuturesCollector(symbols=["BTC/USDT"], on_orderbook=cb)
        raw = json.dumps({
            "arg": {"channel": "books5", "instId": "BTC-USDT-SWAP"},
            "action": "snapshot",
            "data": [{"bids": [["50000", "1", "0", "1"]], "asks": [["50001", "0.5", "0", "1"]]}],
        })
        asyncio.run(c._handle_message(raw))

        assert len(received) == 1
        exchange_id, symbol, bids, asks = received[0]
        assert exchange_id == "okx_futures"
        assert symbol == "BTC/USDT"
        assert bids == [["50000", "1"]]
