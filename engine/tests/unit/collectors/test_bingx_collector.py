"""Unit tests for BingXCollector (US-350)."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from src.collectors.bingx_collector import BingXCollector, _normalize_symbol, _denormalize_symbol


# ---------------------------------------------------------------------------
# Symbol normalization helpers
# ---------------------------------------------------------------------------

class TestSymbolNormalization:
    def test_normalize_btc_usdt(self):
        assert _normalize_symbol("BTC/USDT") == "BTC-USDT"

    def test_normalize_eth_usdt(self):
        assert _normalize_symbol("ETH/USDT") == "ETH-USDT"

    def test_normalize_already_uppercase(self):
        assert _normalize_symbol("BTC/USDT") == "BTC-USDT"

    def test_denormalize_btc_usdt(self):
        assert _denormalize_symbol("BTC-USDT") == "BTC/USDT"

    def test_denormalize_eth_usdt(self):
        assert _denormalize_symbol("ETH-USDT") == "ETH/USDT"

    def test_roundtrip(self):
        original = "SOL/USDT"
        assert _denormalize_symbol(_normalize_symbol(original)) == original


# ---------------------------------------------------------------------------
# Constructor / WIRING AC: 생성
# ---------------------------------------------------------------------------

class TestBingXCollectorInit:
    def test_instantiation(self):
        col = BingXCollector(symbols=["BTC/USDT"])
        assert col.exchange_id == "bingx"
        assert col.symbols == ["BTC/USDT"]

    def test_instantiation_with_callback(self):
        cb = AsyncMock()
        col = BingXCollector(symbols=["BTC/USDT", "ETH/USDT"], on_orderbook=cb)
        assert col._on_orderbook is cb

    def test_ws_url(self):
        col = BingXCollector(symbols=["BTC/USDT"])
        assert "bingx" in col._ws_url().lower()
        assert col._ws_url().startswith("wss://")


# ---------------------------------------------------------------------------
# Subscribe message
# ---------------------------------------------------------------------------

class TestSubscribeMessage:
    def test_subscribe_format(self):
        col = BingXCollector(symbols=["BTC/USDT"])
        msg = col._subscribe_message("BTC/USDT")
        assert isinstance(msg, dict)
        assert msg["reqType"] == "sub"
        assert "BTC-USDT" in msg["dataType"]
        assert "@depth" in msg["dataType"]

    def test_subscribe_eth(self):
        col = BingXCollector(symbols=["ETH/USDT"])
        msg = col._subscribe_message("ETH/USDT")
        assert "ETH-USDT" in msg["dataType"]


# ---------------------------------------------------------------------------
# Message parsing — WIRING AC: 호출
# ---------------------------------------------------------------------------

class TestParseMessage:
    def _make_depth_msg(self, symbol: str = "BTC-USDT") -> dict:
        return {
            "code": 0,
            "dataType": f"{symbol}@depth20",
            "data": {
                "bids": [["50000.00", "1.5"], ["49999.00", "2.0"]],
                "asks": [["50001.00", "1.0"], ["50002.00", "0.5"]],
            },
        }

    def test_parse_depth_message(self):
        col = BingXCollector(symbols=["BTC/USDT"])
        msg = self._make_depth_msg("BTC-USDT")
        result = col._parse_message(msg)
        assert result is not None
        symbol, bids, asks = result
        assert symbol == "BTC/USDT"
        assert len(bids) == 2
        assert len(asks) == 2

    def test_parse_eth_depth(self):
        col = BingXCollector(symbols=["ETH/USDT"])
        msg = self._make_depth_msg("ETH-USDT")
        result = col._parse_message(msg)
        assert result is not None
        symbol, bids, asks = result
        assert symbol == "ETH/USDT"

    def test_ignore_ping(self):
        col = BingXCollector(symbols=["BTC/USDT"])
        result = col._parse_message({"ping": 1234567890})
        assert result is None

    def test_ignore_subscribe_ack(self):
        col = BingXCollector(symbols=["BTC/USDT"])
        result = col._parse_message({"reqType": "sub", "id": "abc"})
        assert result is None

    def test_ignore_error_code(self):
        col = BingXCollector(symbols=["BTC/USDT"])
        result = col._parse_message({"code": 100001, "msg": "Invalid symbol"})
        assert result is None

    def test_ignore_unrelated_data_type(self):
        col = BingXCollector(symbols=["BTC/USDT"])
        result = col._parse_message({"code": 0, "dataType": "BTC-USDT@trade"})
        assert result is None

    def test_empty_data_returns_none(self):
        col = BingXCollector(symbols=["BTC/USDT"])
        result = col._parse_message({"code": 0, "dataType": "BTC-USDT@depth20", "data": {}})
        assert result is None

    def test_bids_asks_format(self):
        col = BingXCollector(symbols=["BTC/USDT"])
        msg = self._make_depth_msg()
        symbol, bids, asks = col._parse_message(msg)
        assert bids[0] == ["50000.00", "1.5"]
        assert asks[0] == ["50001.00", "1.0"]


# ---------------------------------------------------------------------------
# GZip decompression
# ---------------------------------------------------------------------------

class TestGzipDecompression:
    @pytest.mark.asyncio
    async def test_gzip_decompression(self):
        """_handle_message decompresses gzip bytes correctly."""
        import gzip
        import json

        col = BingXCollector(symbols=["BTC/USDT"])
        received = []

        async def capture(exchange_id, symbol, bids, asks):
            received.append((exchange_id, symbol, bids, asks))

        col._on_orderbook = capture

        raw_dict = {
            "code": 0,
            "dataType": "BTC-USDT@depth20",
            "data": {
                "bids": [["50000", "1.0"]],
                "asks": [["50001", "0.5"]],
            },
        }
        compressed = gzip.compress(json.dumps(raw_dict).encode("utf-8"))
        await col._handle_message(compressed)

        assert len(received) == 1
        assert received[0][1] == "BTC/USDT"

    @pytest.mark.asyncio
    async def test_plain_text_passthrough(self):
        """_handle_message handles plain JSON string (no gzip)."""
        import json

        col = BingXCollector(symbols=["BTC/USDT"])
        received = []

        async def capture(exchange_id, symbol, bids, asks):
            received.append((exchange_id, symbol, bids, asks))

        col._on_orderbook = capture

        raw = json.dumps({
            "code": 0,
            "dataType": "BTC-USDT@depth20",
            "data": {
                "bids": [["50000", "1.0"]],
                "asks": [["50001", "0.5"]],
            },
        })
        await col._handle_message(raw)
        assert len(received) == 1
