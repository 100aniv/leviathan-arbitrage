"""Unit tests for LBankCollector (US-350)."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.collectors.lbank_collector import LBankCollector, _normalize_symbol, _denormalize_symbol


# ---------------------------------------------------------------------------
# Symbol normalization helpers
# ---------------------------------------------------------------------------

class TestSymbolNormalization:
    def test_normalize_btc_usdt(self):
        assert _normalize_symbol("BTC/USDT") == "btc_usdt"

    def test_normalize_eth_usdt(self):
        assert _normalize_symbol("ETH/USDT") == "eth_usdt"

    def test_normalize_lowercase(self):
        assert _normalize_symbol("SOL/USDT") == "sol_usdt"

    def test_denormalize_btc_usdt(self):
        assert _denormalize_symbol("btc_usdt") == "BTC/USDT"

    def test_denormalize_eth_usdt(self):
        assert _denormalize_symbol("eth_usdt") == "ETH/USDT"

    def test_denormalize_uppercase(self):
        assert _denormalize_symbol("BTC_USDT") == "BTC/USDT"

    def test_roundtrip(self):
        original = "SOL/USDT"
        assert _denormalize_symbol(_normalize_symbol(original)) == original


# ---------------------------------------------------------------------------
# Constructor / WIRING AC: 생성
# ---------------------------------------------------------------------------

class TestLBankCollectorInit:
    def test_instantiation(self):
        col = LBankCollector(symbols=["BTC/USDT"])
        assert col.exchange_id == "lbank"
        assert col.symbols == ["BTC/USDT"]

    def test_instantiation_with_callback(self):
        cb = AsyncMock()
        col = LBankCollector(symbols=["BTC/USDT", "ETH/USDT"], on_orderbook=cb)
        assert col._on_orderbook is cb

    def test_ws_url(self):
        col = LBankCollector(symbols=["BTC/USDT"])
        assert "lbkex.net" in col._ws_url()
        assert col._ws_url().startswith("wss://")


# ---------------------------------------------------------------------------
# Subscribe message
# ---------------------------------------------------------------------------

class TestSubscribeMessage:
    def test_subscribe_format(self):
        col = LBankCollector(symbols=["BTC/USDT"])
        msg = col._subscribe_message("BTC/USDT")
        assert isinstance(msg, dict)
        assert msg["action"] == "subscribe"
        assert msg["subscribe"] == "orderBook"
        assert msg["pair"] == "btc_usdt"

    def test_subscribe_eth(self):
        col = LBankCollector(symbols=["ETH/USDT"])
        msg = col._subscribe_message("ETH/USDT")
        assert msg["pair"] == "eth_usdt"

    def test_depth_field(self):
        col = LBankCollector(symbols=["BTC/USDT"])
        msg = col._subscribe_message("BTC/USDT")
        assert "depth" in msg
        assert msg["depth"] == "20"


# ---------------------------------------------------------------------------
# Message parsing — WIRING AC: 호출
# ---------------------------------------------------------------------------

class TestParseMessage:
    def _make_orderbook_msg(self, pair: str = "btc_usdt") -> dict:
        return {
            "type": "orderBook",
            "pair": pair,
            "depth": "20",
            "asks": [["50001.00", "1.0"], ["50002.00", "0.5"]],
            "bids": [["50000.00", "1.5"], ["49999.00", "2.0"]],
        }

    def test_parse_orderbook_message(self):
        col = LBankCollector(symbols=["BTC/USDT"])
        msg = self._make_orderbook_msg("btc_usdt")
        result = col._parse_message(msg)
        assert result is not None
        symbol, bids, asks = result
        assert symbol == "BTC/USDT"
        assert len(bids) == 2
        assert len(asks) == 2

    def test_parse_eth_orderbook(self):
        col = LBankCollector(symbols=["ETH/USDT"])
        msg = self._make_orderbook_msg("eth_usdt")
        result = col._parse_message(msg)
        assert result is not None
        symbol, bids, asks = result
        assert symbol == "ETH/USDT"

    def test_ignore_subscribe_ack(self):
        col = LBankCollector(symbols=["BTC/USDT"])
        result = col._parse_message({
            "status": "success",
            "action": "subscribe",
            "subscribe": "orderBook",
            "pair": "btc_usdt",
        })
        assert result is None

    def test_ignore_unsubscribe_ack(self):
        col = LBankCollector(symbols=["BTC/USDT"])
        result = col._parse_message({"action": "unsubscribe", "pair": "btc_usdt"})
        assert result is None

    def test_ignore_ping(self):
        col = LBankCollector(symbols=["BTC/USDT"])
        result = col._parse_message({"ping": "12345", "type": "ping"})
        assert result is None

    def test_ignore_ping_type(self):
        col = LBankCollector(symbols=["BTC/USDT"])
        result = col._parse_message({"type": "ping"})
        assert result is None

    def test_ignore_non_orderbook_type(self):
        col = LBankCollector(symbols=["BTC/USDT"])
        result = col._parse_message({"type": "trade", "pair": "btc_usdt"})
        assert result is None

    def test_missing_pair_returns_none(self):
        col = LBankCollector(symbols=["BTC/USDT"])
        result = col._parse_message({"type": "orderBook", "pair": ""})
        assert result is None

    def test_bids_asks_format(self):
        col = LBankCollector(symbols=["BTC/USDT"])
        msg = self._make_orderbook_msg()
        symbol, bids, asks = col._parse_message(msg)
        assert bids[0] == ["50000.00", "1.5"]
        assert asks[0] == ["50001.00", "1.0"]


# ---------------------------------------------------------------------------
# Callback delivery — WIRING AC: 주입
# ---------------------------------------------------------------------------

class TestCallbackDelivery:
    @pytest.mark.asyncio
    async def test_callback_called_on_valid_message(self):
        received = []

        async def capture(exchange_id, symbol, bids, asks):
            received.append((exchange_id, symbol, bids, asks))

        col = LBankCollector(symbols=["BTC/USDT"], on_orderbook=capture)

        import json
        raw = json.dumps({
            "type": "orderBook",
            "pair": "btc_usdt",
            "depth": "20",
            "bids": [["50000", "1.0"]],
            "asks": [["50001", "0.5"]],
        })
        await col._handle_message(raw)

        assert len(received) == 1
        exchange_id, symbol, bids, asks = received[0]
        assert exchange_id == "lbank"
        assert symbol == "BTC/USDT"
