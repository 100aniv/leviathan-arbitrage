"""Unit tests for exchange-specific collector implementations.

Covers:
- BinanceCollector: symbol normalisation, URL construction, _parse_message for
  combined stream and single-symbol stream, _connect_and_listen override.
- BitgetCollector: symbol normalisation, subscribe message, _parse_message for
  snapshot/update/ack/unknown actions.
- BybitCollector: symbol normalisation, subscribe message, _parse_message for
  snapshot/delta/pong.
- OKXCollector: symbol normalisation, subscribe message, _parse_message for
  snapshot/update/event/missing-instId/empty-data.

All WebSocket I/O is mocked — no real network calls are made.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.binance_collector import (
    BinanceCollector,
    _denormalize_symbol as binance_denorm,
    _normalize_symbol as binance_norm,
)
from src.collectors.bitget_collector import (
    BitgetCollector,
    _denormalize_symbol as bitget_denorm,
    _normalize_symbol as bitget_norm,
)
from src.collectors.bybit_collector import (
    BybitCollector,
    _denormalize_symbol as bybit_denorm,
    _normalize_symbol as bybit_norm,
)
from src.collectors.okx_collector import (
    OKXCollector,
    _denormalize_symbol as okx_denorm,
    _normalize_symbol as okx_norm,
)
from src.collectors.upbit_collector import (
    UpbitCollector,
    _denormalize_symbol as upbit_denorm,
    _normalize_symbol as upbit_norm,
)
from src.collectors.bithumb_collector import (
    BithumbCollector,
    _denormalize_symbol as bithumb_denorm,
    _normalize_symbol as bithumb_norm,
)


# ===========================================================================
# BinanceCollector
# ===========================================================================


class TestBinanceNormalizeSymbol:
    def test_btc_usdt_converts_to_btcusdt_lower(self):
        assert binance_norm("BTC/USDT") == "btcusdt"

    def test_eth_usdt_converts_correctly(self):
        assert binance_norm("ETH/USDT") == "ethusdt"

    def test_symbol_without_slash_is_lowercased(self):
        assert binance_norm("BTCUSDT") == "btcusdt"


class TestBinanceDenormalizeSymbol:
    def test_btcusdt_becomes_btc_usdt(self):
        assert binance_denorm("btcusdt") == "BTC/USDT"

    def test_ethusdt_becomes_eth_usdt(self):
        assert binance_denorm("ethusdt") == "ETH/USDT"

    def test_ethbtc_becomes_eth_btc(self):
        assert binance_denorm("ethbtc") == "ETH/BTC"

    def test_unknown_quote_returns_uppercased_fallback(self):
        result = binance_denorm("xyzxxx")
        assert result == "XYZXXX"

    def test_bnb_usdt_uses_longest_match(self):
        # "bnbusdt" should split as BNB/USDT, not BNB/USD
        result = binance_denorm("bnbusdt")
        assert result == "BNB/USDT"


class TestBinanceCollectorWsUrl:
    def test_single_symbol_uses_direct_stream_url(self):
        c = BinanceCollector(symbols=["BTC/USDT"])
        url = c._ws_url()
        assert "/ws/btcusdt@depth20@100ms" in url
        assert "stream?streams" not in url

    def test_multiple_symbols_uses_combined_stream_url(self):
        c = BinanceCollector(symbols=["BTC/USDT", "ETH/USDT"])
        url = c._ws_url()
        assert "stream?streams=" in url
        assert "btcusdt@depth20@100ms" in url
        assert "ethusdt@depth20@100ms" in url

    def test_base_ws_url_is_binance_domain(self):
        c = BinanceCollector(symbols=["BTC/USDT"])
        assert c._ws_url().startswith("wss://stream.binance.com")


class TestBinanceCollectorSubscribeMessage:
    def test_subscribe_message_returns_empty_string(self):
        c = BinanceCollector(symbols=["BTC/USDT"])
        msg = c._subscribe_message("BTC/USDT")
        assert msg == ""


class TestBinanceCollectorParseMessage:
    def test_combined_stream_message_returns_symbol_bids_asks(self):
        c = BinanceCollector(symbols=["BTC/USDT", "ETH/USDT"])
        data = {
            "stream": "btcusdt@depth20@100ms",
            "data": {
                "bids": [["50000", "1"]],
                "asks": [["50001", "0.5"]],
            },
        }
        result = c._parse_message(data)
        assert result is not None
        symbol, bids, asks = result
        assert symbol == "BTC/USDT"
        assert bids == [["50000", "1"]]
        assert asks == [["50001", "0.5"]]

    def test_single_symbol_stream_message_uses_first_symbol(self):
        c = BinanceCollector(symbols=["ETH/USDT"])
        data = {
            "bids": [["3000", "2"]],
            "asks": [["3001", "1"]],
        }
        result = c._parse_message(data)
        assert result is not None
        symbol, bids, asks = result
        assert symbol == "ETH/USDT"
        assert bids == [["3000", "2"]]

    def test_unknown_message_format_returns_none(self):
        c = BinanceCollector(symbols=["BTC/USDT"])
        result = c._parse_message({"type": "ping"})
        assert result is None

    def test_single_symbol_stream_with_no_symbols_uses_unknown(self):
        c = BinanceCollector(symbols=[])
        data = {"bids": [], "asks": []}
        result = c._parse_message(data)
        assert result is not None
        symbol, _, _ = result
        assert symbol == "UNKNOWN"

    def test_combined_stream_empty_bids_asks_still_returns_result(self):
        c = BinanceCollector(symbols=["BTC/USDT"])
        data = {
            "stream": "ethusdt@depth20@100ms",
            "data": {"bids": [], "asks": []},
        }
        result = c._parse_message(data)
        assert result is not None
        symbol, bids, asks = result
        assert symbol == "ETH/USDT"
        assert bids == []
        assert asks == []


class TestBinanceCollectorConnectAndListen:
    @pytest.mark.asyncio
    async def test_connect_and_listen_calls_handle_message_for_each_raw(self):
        """_connect_and_listen iterates ws messages and calls _handle_message."""
        c = BinanceCollector(symbols=["BTC/USDT"])
        c._running = True

        raw_msg = json.dumps({"bids": [["50000", "1"]], "asks": [["50001", "1"]]})

        mock_ws = AsyncMock()
        # Simulate one message then stop
        async def _aiter():
            yield raw_msg
            c._running = False

        mock_ws.__aiter__ = lambda self: _aiter()
        mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_ws.__aexit__ = AsyncMock(return_value=False)

        with patch("websockets.connect", return_value=mock_ws):
            with patch.object(c, "_handle_message", new_callable=AsyncMock) as mock_handle:
                await c._connect_and_listen()
                mock_handle.assert_called_once_with(raw_msg)

    @pytest.mark.asyncio
    async def test_connect_and_listen_sets_connected_true_then_false(self):
        c = BinanceCollector(symbols=["BTC/USDT"])
        c._running = False  # stop immediately

        mock_ws = AsyncMock()

        async def _aiter():
            return
            yield  # make it an async generator

        mock_ws.__aiter__ = lambda self: _aiter()
        mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_ws.__aexit__ = AsyncMock(return_value=False)

        with patch("websockets.connect", return_value=mock_ws):
            await c._connect_and_listen()

        assert c._connected is False


# ===========================================================================
# BitgetCollector
# ===========================================================================


class TestBitgetNormalizeSymbol:
    def test_btc_usdt_becomes_btcusdt(self):
        assert bitget_norm("BTC/USDT") == "BTCUSDT"

    def test_eth_usdc_becomes_ethusdc(self):
        assert bitget_norm("ETH/USDC") == "ETHUSDC"


class TestBitgetDenormalizeSymbol:
    def test_btcusdt_becomes_btc_usdt(self):
        assert bitget_denorm("BTCUSDT") == "BTC/USDT"

    def test_ethbtc_becomes_eth_btc(self):
        assert bitget_denorm("ETHBTC") == "ETH/BTC"

    def test_unknown_quote_returns_unchanged(self):
        result = bitget_denorm("XYZAAA")
        assert result == "XYZAAA"


class TestBitgetCollectorWsUrl:
    def test_ws_url_is_bitget_endpoint(self):
        c = BitgetCollector(symbols=["BTC/USDT"])
        assert c._ws_url() == "wss://ws.bitget.com/v2/ws/public"


class TestBitgetCollectorSubscribeMessage:
    def test_subscribe_message_contains_correct_op_and_args(self):
        c = BitgetCollector(symbols=["BTC/USDT"])
        msg = c._subscribe_message("BTC/USDT")
        assert isinstance(msg, dict)
        assert msg["op"] == "subscribe"
        assert len(msg["args"]) == 1
        arg = msg["args"][0]
        assert arg["instType"] == "SPOT"
        assert arg["channel"] == "books15"
        assert arg["instId"] == "BTCUSDT"

    def test_subscribe_message_eth_usdt(self):
        c = BitgetCollector(symbols=["ETH/USDT"])
        msg = c._subscribe_message("ETH/USDT")
        assert msg["args"][0]["instId"] == "ETHUSDT"


class TestBitgetCollectorParseMessage:
    def test_event_ack_returns_none(self):
        c = BitgetCollector(symbols=["BTC/USDT"])
        result = c._parse_message({"event": "subscribe", "arg": {}})
        assert result is None

    def test_snapshot_action_returns_symbol_bids_asks(self):
        c = BitgetCollector(symbols=["BTC/USDT"])
        data = {
            "action": "snapshot",
            "arg": {"instId": "BTCUSDT"},
            "data": [{"bids": [["50000", "1"]], "asks": [["50001", "0.5"]]}],
        }
        result = c._parse_message(data)
        assert result is not None
        symbol, bids, asks = result
        assert symbol == "BTC/USDT"
        assert bids == [["50000", "1"]]
        assert asks == [["50001", "0.5"]]

    def test_update_action_returns_symbol_bids_asks(self):
        c = BitgetCollector(symbols=["BTC/USDT"])
        data = {
            "action": "update",
            "arg": {"instId": "ETHUSDT"},
            "data": [{"bids": [["3000", "2"]], "asks": [["3001", "1"]]}],
        }
        result = c._parse_message(data)
        assert result is not None
        symbol, bids, asks = result
        assert symbol == "ETH/USDT"

    def test_unknown_action_returns_none(self):
        c = BitgetCollector(symbols=["BTC/USDT"])
        result = c._parse_message({"action": "heartbeat", "arg": {}})
        assert result is None

    def test_missing_inst_id_returns_none(self):
        c = BitgetCollector(symbols=["BTC/USDT"])
        data = {
            "action": "snapshot",
            "arg": {},  # no instId
            "data": [{"bids": [], "asks": []}],
        }
        result = c._parse_message(data)
        assert result is None

    def test_empty_data_list_returns_none(self):
        c = BitgetCollector(symbols=["BTC/USDT"])
        data = {
            "action": "snapshot",
            "arg": {"instId": "BTCUSDT"},
            "data": [],
        }
        result = c._parse_message(data)
        assert result is None

    def test_no_action_field_returns_none(self):
        c = BitgetCollector(symbols=["BTC/USDT"])
        result = c._parse_message({"arg": {"instId": "BTCUSDT"}})
        assert result is None


# ===========================================================================
# BybitCollector
# ===========================================================================


class TestBybitNormalizeSymbol:
    def test_btc_usdt_becomes_btcusdt_upper(self):
        assert bybit_norm("BTC/USDT") == "BTCUSDT"

    def test_eth_usdt_becomes_ethusdt(self):
        assert bybit_norm("ETH/USDT") == "ETHUSDT"


class TestBybitDenormalizeSymbol:
    def test_btcusdt_becomes_btc_usdt(self):
        assert bybit_denorm("BTCUSDT") == "BTC/USDT"

    def test_ethbtc_becomes_eth_btc(self):
        assert bybit_denorm("ETHBTC") == "ETH/BTC"

    def test_unknown_quote_returns_unchanged(self):
        result = bybit_denorm("XYZAAA")
        assert result == "XYZAAA"


class TestBybitCollectorWsUrl:
    def test_ws_url_is_bybit_spot_endpoint(self):
        c = BybitCollector(symbols=["BTC/USDT"])
        assert c._ws_url() == "wss://stream.bybit.com/v5/public/spot"


class TestBybitCollectorSubscribeMessage:
    def test_subscribe_message_contains_op_and_topic(self):
        c = BybitCollector(symbols=["BTC/USDT"])
        msg = c._subscribe_message("BTC/USDT")
        assert isinstance(msg, dict)
        assert msg["op"] == "subscribe"
        assert len(msg["args"]) == 1
        assert "orderbook.50.BTCUSDT" in msg["args"][0]

    def test_subscribe_message_eth_usdt(self):
        c = BybitCollector(symbols=["ETH/USDT"])
        msg = c._subscribe_message("ETH/USDT")
        assert "ETHUSDT" in msg["args"][0]


class TestBybitCollectorParseMessage:
    def test_snapshot_returns_symbol_bids_asks(self):
        c = BybitCollector(symbols=["BTC/USDT"])
        data = {
            "type": "snapshot",
            "topic": "orderbook.50.BTCUSDT",
            "data": {"b": [["50000", "1"]], "a": [["50001", "0.5"]]},
        }
        result = c._parse_message(data)
        assert result is not None
        symbol, bids, asks = result
        assert symbol == "BTC/USDT"
        assert bids == [["50000", "1"]]
        assert asks == [["50001", "0.5"]]

    def test_delta_returns_symbol_bids_asks(self):
        c = BybitCollector(symbols=["ETH/USDT"])
        data = {
            "type": "delta",
            "topic": "orderbook.50.ETHUSDT",
            "data": {"b": [["3000", "2"]], "a": [["3001", "1"]]},
        }
        result = c._parse_message(data)
        assert result is not None
        symbol, bids, asks = result
        assert symbol == "ETH/USDT"

    def test_pong_frame_returns_none(self):
        c = BybitCollector(symbols=["BTC/USDT"])
        result = c._parse_message({"op": "pong"})
        assert result is None

    def test_subscribe_ack_returns_none(self):
        c = BybitCollector(symbols=["BTC/USDT"])
        result = c._parse_message({"op": "subscribe", "success": True})
        assert result is None

    def test_topic_with_fewer_than_3_parts_returns_none(self):
        c = BybitCollector(symbols=["BTC/USDT"])
        data = {
            "type": "snapshot",
            "topic": "orderbook.BTCUSDT",  # only 2 parts
            "data": {"b": [], "a": []},
        }
        result = c._parse_message(data)
        assert result is None

    def test_empty_bids_and_asks_still_returns_result(self):
        c = BybitCollector(symbols=["BTC/USDT"])
        data = {
            "type": "snapshot",
            "topic": "orderbook.50.BTCUSDT",
            "data": {"b": [], "a": []},
        }
        result = c._parse_message(data)
        assert result is not None
        _, bids, asks = result
        assert bids == []
        assert asks == []


# ===========================================================================
# OKXCollector
# ===========================================================================


class TestOKXNormalizeSymbol:
    def test_btc_usdt_becomes_btc_dash_usdt(self):
        assert okx_norm("BTC/USDT") == "BTC-USDT"

    def test_eth_usdt_becomes_eth_dash_usdt(self):
        assert okx_norm("ETH/USDT") == "ETH-USDT"


class TestOKXDenormalizeSymbol:
    def test_btc_dash_usdt_becomes_btc_usdt(self):
        assert okx_denorm("BTC-USDT") == "BTC/USDT"

    def test_eth_dash_btc_becomes_eth_btc(self):
        assert okx_denorm("ETH-BTC") == "ETH/BTC"

    def test_no_dash_returns_unchanged(self):
        assert okx_denorm("BTCUSDT") == "BTCUSDT"


class TestOKXCollectorWsUrl:
    def test_ws_url_is_okx_public_endpoint(self):
        c = OKXCollector(symbols=["BTC/USDT"])
        assert c._ws_url() == "wss://ws.okx.com:8443/ws/v5/public"


class TestOKXCollectorSubscribeMessage:
    def test_subscribe_message_contains_op_and_channel(self):
        c = OKXCollector(symbols=["BTC/USDT"])
        msg = c._subscribe_message("BTC/USDT")
        assert isinstance(msg, dict)
        assert msg["op"] == "subscribe"
        assert len(msg["args"]) == 1
        arg = msg["args"][0]
        assert arg["channel"] == "books50-l2-tbt"
        assert arg["instId"] == "BTC-USDT"

    def test_subscribe_message_eth_usdt(self):
        c = OKXCollector(symbols=["ETH/USDT"])
        msg = c._subscribe_message("ETH/USDT")
        assert msg["args"][0]["instId"] == "ETH-USDT"


class TestOKXCollectorParseMessage:
    def test_event_ack_returns_none(self):
        c = OKXCollector(symbols=["BTC/USDT"])
        result = c._parse_message({"event": "subscribe"})
        assert result is None

    def test_snapshot_returns_symbol_with_trimmed_levels(self):
        c = OKXCollector(symbols=["BTC/USDT"])
        data = {
            "action": "snapshot",
            "arg": {"instId": "BTC-USDT"},
            "data": [
                {
                    "bids": [["50000", "1", "0", "1"], ["49999", "2", "0", "1"]],
                    "asks": [["50001", "0.5", "0", "1"]],
                }
            ],
        }
        result = c._parse_message(data)
        assert result is not None
        symbol, bids, asks = result
        assert symbol == "BTC/USDT"
        # OKX trims to first two fields only
        assert bids == [["50000", "1"], ["49999", "2"]]
        assert asks == [["50001", "0.5"]]

    def test_update_action_also_returns_data(self):
        c = OKXCollector(symbols=["BTC/USDT"])
        data = {
            "action": "update",
            "arg": {"instId": "ETH-USDT"},
            "data": [{"bids": [["3000", "1", "0", "0"]], "asks": []}],
        }
        result = c._parse_message(data)
        assert result is not None
        symbol, bids, _ = result
        assert symbol == "ETH/USDT"
        assert bids == [["3000", "1"]]

    def test_unknown_action_returns_none(self):
        c = OKXCollector(symbols=["BTC/USDT"])
        result = c._parse_message({"action": "heartbeat", "arg": {"instId": "BTC-USDT"}})
        assert result is None

    def test_missing_inst_id_returns_none(self):
        c = OKXCollector(symbols=["BTC/USDT"])
        data = {
            "action": "snapshot",
            "arg": {},
            "data": [{"bids": [], "asks": []}],
        }
        result = c._parse_message(data)
        assert result is None

    def test_empty_data_list_returns_none(self):
        c = OKXCollector(symbols=["BTC/USDT"])
        data = {
            "action": "snapshot",
            "arg": {"instId": "BTC-USDT"},
            "data": [],
        }
        result = c._parse_message(data)
        assert result is None

    def test_no_action_field_returns_none(self):
        c = OKXCollector(symbols=["BTC/USDT"])
        result = c._parse_message({"arg": {"instId": "BTC-USDT"}, "data": [{}]})
        assert result is None

    def test_empty_bids_and_asks_in_data_returns_empty_lists(self):
        c = OKXCollector(symbols=["BTC/USDT"])
        data = {
            "action": "snapshot",
            "arg": {"instId": "BTC-USDT"},
            "data": [{"bids": [], "asks": []}],
        }
        result = c._parse_message(data)
        assert result is not None
        _, bids, asks = result
        assert bids == []
        assert asks == []


# ===========================================================================
# Collector integration: _handle_message dispatches to callback
# ===========================================================================


class TestCollectorHandleMessageDispatch:
    """Verify that _handle_message from BaseCollector works with each
    exchange-specific _parse_message (integration of base + subclass)."""

    @pytest.mark.asyncio
    async def test_binance_handle_message_calls_callback_for_valid_data(self):
        callback = AsyncMock()
        c = BinanceCollector(symbols=["BTC/USDT"], on_orderbook=callback)
        raw = json.dumps({"bids": [["50000", "1"]], "asks": [["50001", "1"]]})
        await c._handle_message(raw)
        callback.assert_called_once_with("binance", "BTC/USDT", [["50000", "1"]], [["50001", "1"]])

    @pytest.mark.asyncio
    async def test_bitget_handle_message_calls_callback_for_snapshot(self):
        callback = AsyncMock()
        c = BitgetCollector(symbols=["BTC/USDT"], on_orderbook=callback)
        raw = json.dumps({
            "action": "snapshot",
            "arg": {"instId": "BTCUSDT"},
            "data": [{"bids": [["50000", "1"]], "asks": [["50001", "0.5"]]}],
        })
        await c._handle_message(raw)
        callback.assert_called_once_with("bitget", "BTC/USDT", [["50000", "1"]], [["50001", "0.5"]])

    @pytest.mark.asyncio
    async def test_bybit_handle_message_calls_callback_for_snapshot(self):
        callback = AsyncMock()
        c = BybitCollector(symbols=["BTC/USDT"], on_orderbook=callback)
        raw = json.dumps({
            "type": "snapshot",
            "topic": "orderbook.50.BTCUSDT",
            "data": {"b": [["50000", "1"]], "a": [["50001", "1"]]},
        })
        await c._handle_message(raw)
        callback.assert_called_once_with("bybit", "BTC/USDT", [["50000", "1"]], [["50001", "1"]])

    @pytest.mark.asyncio
    async def test_okx_handle_message_calls_callback_for_snapshot(self):
        callback = AsyncMock()
        c = OKXCollector(symbols=["BTC/USDT"], on_orderbook=callback)
        raw = json.dumps({
            "action": "snapshot",
            "arg": {"instId": "BTC-USDT"},
            "data": [{"bids": [["50000", "1", "0", "1"]], "asks": [["50001", "0.5", "0", "1"]]}],
        })
        await c._handle_message(raw)
        callback.assert_called_once_with("okx", "BTC/USDT", [["50000", "1"]], [["50001", "0.5"]])

    @pytest.mark.asyncio
    async def test_handle_message_does_not_call_callback_for_ack(self):
        callback = AsyncMock()
        c = BitgetCollector(symbols=["BTC/USDT"], on_orderbook=callback)
        raw = json.dumps({"event": "subscribe", "arg": {}})
        await c._handle_message(raw)
        callback.assert_not_called()


# ===========================================================================
# UpbitCollector
# ===========================================================================


class TestUpbitNormalizeSymbol:
    def test_btc_usdt_becomes_usdt_dash_btc(self):
        assert upbit_norm("BTC/USDT") == "USDT-BTC"

    def test_btc_krw_becomes_krw_dash_btc(self):
        assert upbit_norm("BTC/KRW") == "KRW-BTC"

    def test_eth_krw_becomes_krw_dash_eth(self):
        assert upbit_norm("ETH/KRW") == "KRW-ETH"

    def test_no_slash_returns_unchanged(self):
        assert upbit_norm("KRW-BTC") == "KRW-BTC"


class TestUpbitDenormalizeSymbol:
    def test_krw_dash_btc_becomes_btc_krw(self):
        assert upbit_denorm("KRW-BTC") == "BTC/KRW"

    def test_usdt_dash_btc_becomes_btc_usdt(self):
        assert upbit_denorm("USDT-BTC") == "BTC/USDT"

    def test_no_dash_returns_unchanged(self):
        assert upbit_denorm("BTCKRW") == "BTCKRW"


class TestUpbitCollectorWsUrl:
    def test_ws_url_is_upbit_endpoint(self):
        c = UpbitCollector(symbols=["BTC/KRW"])
        assert c._ws_url() == "wss://api.upbit.com/websocket/v1"


class TestUpbitCollectorSubscribeMessage:
    def test_subscribe_message_is_json_array_with_ticket_and_orderbook(self):
        c = UpbitCollector(symbols=["BTC/KRW"])
        msg = c._subscribe_message("BTC/KRW")
        parsed = json.loads(msg)
        assert isinstance(parsed, list)
        assert len(parsed) == 2
        assert "ticket" in parsed[0]
        assert parsed[1]["type"] == "orderbook"
        assert "KRW-BTC" in parsed[1]["codes"]

    def test_subscribe_message_usdt_pair(self):
        c = UpbitCollector(symbols=["BTC/USDT"])
        msg = c._subscribe_message("BTC/USDT")
        parsed = json.loads(msg)
        assert "USDT-BTC" in parsed[1]["codes"]


class TestUpbitCollectorParseMessage:
    def test_orderbook_returns_symbol_bids_asks(self):
        c = UpbitCollector(symbols=["BTC/KRW"])
        data = {
            "type": "orderbook",
            "code": "KRW-BTC",
            "orderbook_units": [
                {"bid_price": 90000000, "bid_size": 0.1, "ask_price": 90010000, "ask_size": 0.2},
                {"bid_price": 89990000, "bid_size": 0.3, "ask_price": 90020000, "ask_size": 0.4},
            ],
        }
        result = c._parse_message(data)
        assert result is not None
        symbol, bids, asks = result
        assert symbol == "BTC/KRW"
        assert len(bids) == 2
        assert bids[0] == ["90000000", "0.1"]
        assert len(asks) == 2
        assert asks[0] == ["90010000", "0.2"]

    def test_non_orderbook_type_returns_none(self):
        c = UpbitCollector(symbols=["BTC/KRW"])
        result = c._parse_message({"type": "trade", "code": "KRW-BTC"})
        assert result is None

    def test_empty_orderbook_units_returns_none(self):
        c = UpbitCollector(symbols=["BTC/KRW"])
        result = c._parse_message({"type": "orderbook", "code": "KRW-BTC", "orderbook_units": []})
        assert result is None

    def test_usdt_pair_symbol_denormalization(self):
        c = UpbitCollector(symbols=["BTC/USDT"])
        data = {
            "type": "orderbook",
            "code": "USDT-BTC",
            "orderbook_units": [
                {"bid_price": 87000, "bid_size": 0.5, "ask_price": 87010, "ask_size": 0.3},
            ],
        }
        result = c._parse_message(data)
        assert result is not None
        symbol, _, _ = result
        assert symbol == "BTC/USDT"


class TestUpbitCollectorHandleMessage:
    @pytest.mark.asyncio
    async def test_handle_message_binary_bytes(self):
        """Upbit sends binary (bytes) WebSocket messages."""
        callback = AsyncMock()
        c = UpbitCollector(symbols=["BTC/KRW"], on_orderbook=callback)
        raw = json.dumps({
            "type": "orderbook",
            "code": "KRW-BTC",
            "orderbook_units": [
                {"bid_price": 90000000, "bid_size": 0.1, "ask_price": 90010000, "ask_size": 0.2},
            ],
        }).encode("utf-8")  # bytes, not str
        await c._handle_message(raw)
        callback.assert_called_once()
        args = callback.call_args[0]
        assert args[0] == "upbit"
        assert args[1] == "BTC/KRW"

    @pytest.mark.asyncio
    async def test_handle_message_string(self):
        callback = AsyncMock()
        c = UpbitCollector(symbols=["BTC/KRW"], on_orderbook=callback)
        raw = json.dumps({
            "type": "orderbook",
            "code": "KRW-BTC",
            "orderbook_units": [
                {"bid_price": 90000000, "bid_size": 0.1, "ask_price": 90010000, "ask_size": 0.2},
            ],
        })
        await c._handle_message(raw)
        callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_message_non_orderbook_skips_callback(self):
        callback = AsyncMock()
        c = UpbitCollector(symbols=["BTC/KRW"], on_orderbook=callback)
        raw = json.dumps({"type": "trade", "code": "KRW-BTC"}).encode("utf-8")
        await c._handle_message(raw)
        callback.assert_not_called()


# ===========================================================================
# BithumbCollector
# ===========================================================================


class TestBithumbNormalizeSymbol:
    def test_btc_krw_becomes_btc_underscore_krw(self):
        assert bithumb_norm("BTC/KRW") == "BTC_KRW"

    def test_eth_krw_becomes_eth_underscore_krw(self):
        assert bithumb_norm("ETH/KRW") == "ETH_KRW"


class TestBithumbDenormalizeSymbol:
    def test_btc_underscore_krw_becomes_btc_krw(self):
        assert bithumb_denorm("BTC_KRW") == "BTC/KRW"

    def test_eth_underscore_krw_becomes_eth_krw(self):
        assert bithumb_denorm("ETH_KRW") == "ETH/KRW"


class TestBithumbCollectorWsUrl:
    def test_ws_url_is_bithumb_endpoint(self):
        c = BithumbCollector(symbols=["BTC/KRW"])
        assert c._ws_url() == "wss://pubwss.bithumb.com/pub/ws"


class TestBithumbCollectorSubscribeMessage:
    def test_subscribe_message_contains_type_symbols_ticktypes(self):
        c = BithumbCollector(symbols=["BTC/KRW"])
        msg = c._subscribe_message("BTC/KRW")
        assert isinstance(msg, dict)
        assert msg["type"] == "orderbookdepth"
        assert "BTC_KRW" in msg["symbols"]
        assert "1H" in msg["tickTypes"]

    def test_subscribe_message_eth(self):
        c = BithumbCollector(symbols=["ETH/KRW"])
        msg = c._subscribe_message("ETH/KRW")
        assert "ETH_KRW" in msg["symbols"]


class TestBithumbCollectorParseMessage:
    def test_orderbookdepth_returns_sorted_bids_asks(self):
        c = BithumbCollector(symbols=["BTC/KRW"])
        data = {
            "type": "orderbookdepth",
            "content": {
                "list": [
                    {"symbol": "BTC_KRW", "orderType": "ask", "price": "90010000", "quantity": "0.2"},
                    {"symbol": "BTC_KRW", "orderType": "bid", "price": "89990000", "quantity": "0.3"},
                    {"symbol": "BTC_KRW", "orderType": "ask", "price": "90020000", "quantity": "0.4"},
                    {"symbol": "BTC_KRW", "orderType": "bid", "price": "90000000", "quantity": "0.1"},
                ],
            },
        }
        result = c._parse_message(data)
        assert result is not None
        symbol, bids, asks = result
        assert symbol == "BTC/KRW"
        # Bids sorted descending by price
        assert bids[0] == ["90000000", "0.1"]
        assert bids[1] == ["89990000", "0.3"]
        # Asks sorted ascending by price
        assert asks[0] == ["90010000", "0.2"]
        assert asks[1] == ["90020000", "0.4"]

    def test_non_orderbookdepth_type_returns_none(self):
        c = BithumbCollector(symbols=["BTC/KRW"])
        result = c._parse_message({"type": "transaction", "content": {}})
        assert result is None

    def test_empty_list_returns_none(self):
        c = BithumbCollector(symbols=["BTC/KRW"])
        result = c._parse_message({"type": "orderbookdepth", "content": {"list": []}})
        assert result is None

    def test_connected_status_message_returns_none(self):
        c = BithumbCollector(symbols=["BTC/KRW"])
        result = c._parse_message({"type": "connected"})
        assert result is None


class TestBithumbCollectorHandleMessage:
    @pytest.mark.asyncio
    async def test_handle_message_calls_callback_for_orderbookdepth(self):
        callback = AsyncMock()
        c = BithumbCollector(symbols=["BTC/KRW"], on_orderbook=callback)
        raw = json.dumps({
            "type": "orderbookdepth",
            "content": {
                "list": [
                    {"symbol": "BTC_KRW", "orderType": "bid", "price": "90000000", "quantity": "0.1"},
                    {"symbol": "BTC_KRW", "orderType": "ask", "price": "90010000", "quantity": "0.2"},
                ],
            },
        })
        await c._handle_message(raw)
        callback.assert_called_once()
        args = callback.call_args[0]
        assert args[0] == "bithumb"
        assert args[1] == "BTC/KRW"

    @pytest.mark.asyncio
    async def test_handle_message_skips_non_orderbook(self):
        callback = AsyncMock()
        c = BithumbCollector(symbols=["BTC/KRW"], on_orderbook=callback)
        raw = json.dumps({"type": "connected"})
        await c._handle_message(raw)
        callback.assert_not_called()
