"""Unit tests for OrangeXCollector (US-350)."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.collectors.orangex_collector import OrangeXCollector, _normalize_symbol, _denormalize_symbol


# ---------------------------------------------------------------------------
# Symbol normalization helpers
# ---------------------------------------------------------------------------

class TestSymbolNormalization:
    def test_normalize_btc_usdt(self):
        assert _normalize_symbol("BTC/USDT") == "BTC_USDT"

    def test_normalize_eth_usdt(self):
        assert _normalize_symbol("ETH/USDT") == "ETH_USDT"

    def test_normalize_uppercase(self):
        assert _normalize_symbol("SOL/USDT") == "SOL_USDT"

    def test_denormalize_btc_usdt(self):
        assert _denormalize_symbol("BTC_USDT") == "BTC/USDT"

    def test_denormalize_eth_usdt(self):
        assert _denormalize_symbol("ETH_USDT") == "ETH/USDT"

    def test_denormalize_lowercase_input(self):
        assert _denormalize_symbol("btc_usdt") == "BTC/USDT"

    def test_roundtrip(self):
        original = "SOL/USDT"
        assert _denormalize_symbol(_normalize_symbol(original)) == original


# ---------------------------------------------------------------------------
# Constructor / WIRING AC: 생성
# ---------------------------------------------------------------------------

class TestOrangeXCollectorInit:
    def test_instantiation(self):
        col = OrangeXCollector(symbols=["BTC/USDT"])
        assert col.exchange_id == "orangex"
        assert col.symbols == ["BTC/USDT"]

    def test_instantiation_with_callback(self):
        cb = AsyncMock()
        col = OrangeXCollector(symbols=["BTC/USDT", "ETH/USDT"], on_orderbook=cb)
        assert col._on_orderbook is cb

    def test_ws_url(self):
        col = OrangeXCollector(symbols=["BTC/USDT"])
        assert "orangex" in col._ws_url().lower()
        assert col._ws_url().startswith("wss://")


# ---------------------------------------------------------------------------
# Subscribe message
# ---------------------------------------------------------------------------

class TestSubscribeMessage:
    def test_subscribe_format(self):
        col = OrangeXCollector(symbols=["BTC/USDT"])
        msg = col._subscribe_message("BTC/USDT")
        assert isinstance(msg, dict)
        assert msg["op"] == "subscribe"
        assert isinstance(msg["args"], list)
        assert len(msg["args"]) == 1
        assert "depth.20.BTC_USDT" in msg["args"][0]

    def test_subscribe_eth(self):
        col = OrangeXCollector(symbols=["ETH/USDT"])
        msg = col._subscribe_message("ETH/USDT")
        assert "ETH_USDT" in msg["args"][0]

    def test_subscribe_depth_level(self):
        col = OrangeXCollector(symbols=["BTC/USDT"])
        msg = col._subscribe_message("BTC/USDT")
        assert "depth.20" in msg["args"][0]


# ---------------------------------------------------------------------------
# Message parsing — WIRING AC: 호출
# ---------------------------------------------------------------------------

class TestParseMessage:
    def _make_depth_msg(self, symbol: str = "BTC_USDT", msg_type: str = "snapshot") -> dict:
        return {
            "topic": f"depth.20.{symbol}",
            "type": msg_type,
            "data": {
                "b": [["50000.00", "1.5"], ["49999.00", "2.0"]],
                "a": [["50001.00", "1.0"], ["50002.00", "0.5"]],
            },
            "ts": 1234567890123,
        }

    def test_parse_snapshot_message(self):
        col = OrangeXCollector(symbols=["BTC/USDT"])
        msg = self._make_depth_msg("BTC_USDT", "snapshot")
        result = col._parse_message(msg)
        assert result is not None
        symbol, bids, asks = result
        assert symbol == "BTC/USDT"
        assert len(bids) == 2
        assert len(asks) == 2

    def test_parse_delta_message(self):
        col = OrangeXCollector(symbols=["BTC/USDT"])
        msg = self._make_depth_msg("BTC_USDT", "delta")
        result = col._parse_message(msg)
        assert result is not None
        symbol, bids, asks = result
        assert symbol == "BTC/USDT"

    def test_parse_eth_depth(self):
        col = OrangeXCollector(symbols=["ETH/USDT"])
        msg = self._make_depth_msg("ETH_USDT")
        result = col._parse_message(msg)
        assert result is not None
        symbol, bids, asks = result
        assert symbol == "ETH/USDT"

    def test_ignore_subscribe_ack(self):
        col = OrangeXCollector(symbols=["BTC/USDT"])
        result = col._parse_message({
            "op": "subscribe",
            "success": True,
            "ret_msg": "",
        })
        assert result is None

    def test_ignore_unsubscribe_ack(self):
        col = OrangeXCollector(symbols=["BTC/USDT"])
        result = col._parse_message({"op": "unsubscribe", "success": True})
        assert result is None

    def test_ignore_pong(self):
        col = OrangeXCollector(symbols=["BTC/USDT"])
        result = col._parse_message({"op": "pong"})
        assert result is None

    def test_ignore_pong_ret_msg(self):
        col = OrangeXCollector(symbols=["BTC/USDT"])
        result = col._parse_message({"ret_msg": "pong"})
        assert result is None

    def test_ignore_non_depth_topic(self):
        col = OrangeXCollector(symbols=["BTC/USDT"])
        result = col._parse_message({
            "topic": "trade.BTC_USDT",
            "type": "snapshot",
            "data": {},
        })
        assert result is None

    def test_missing_topic_returns_none(self):
        col = OrangeXCollector(symbols=["BTC/USDT"])
        result = col._parse_message({"type": "snapshot", "data": {}})
        assert result is None

    def test_empty_data_returns_none(self):
        col = OrangeXCollector(symbols=["BTC/USDT"])
        result = col._parse_message({
            "topic": "depth.20.BTC_USDT",
            "type": "snapshot",
            "data": {},
        })
        assert result is None

    def test_bids_asks_format(self):
        col = OrangeXCollector(symbols=["BTC/USDT"])
        msg = self._make_depth_msg()
        symbol, bids, asks = col._parse_message(msg)
        assert bids[0] == ["50000.00", "1.5"]
        assert asks[0] == ["50001.00", "1.0"]

    def test_topic_with_two_parts_returns_none(self):
        """Topic without symbol part (depth.20 only) returns None."""
        col = OrangeXCollector(symbols=["BTC/USDT"])
        result = col._parse_message({
            "topic": "depth.20",
            "type": "snapshot",
            "data": {"b": [], "a": []},
        })
        assert result is None


# ---------------------------------------------------------------------------
# Callback delivery — WIRING AC: 주입
# ---------------------------------------------------------------------------

class TestCallbackDelivery:
    @pytest.mark.asyncio
    async def test_callback_called_on_valid_message(self):
        received = []

        async def capture(exchange_id, symbol, bids, asks):
            received.append((exchange_id, symbol, bids, asks))

        col = OrangeXCollector(symbols=["BTC/USDT"], on_orderbook=capture)

        import json
        raw = json.dumps({
            "topic": "depth.20.BTC_USDT",
            "type": "snapshot",
            "data": {
                "b": [["50000", "1.0"]],
                "a": [["50001", "0.5"]],
            },
            "ts": 1234567890,
        })
        await col._handle_message(raw)

        assert len(received) == 1
        exchange_id, symbol, bids, asks = received[0]
        assert exchange_id == "orangex"
        assert symbol == "BTC/USDT"

    @pytest.mark.asyncio
    async def test_no_callback_no_error(self):
        """Collector without callback processes messages without error."""
        col = OrangeXCollector(symbols=["BTC/USDT"], on_orderbook=None)

        import json
        raw = json.dumps({
            "topic": "depth.20.BTC_USDT",
            "type": "snapshot",
            "data": {
                "b": [["50000", "1.0"]],
                "a": [["50001", "0.5"]],
            },
        })
        # Should not raise
        await col._handle_message(raw)
