"""Unit tests for NativeOKXAdapter."""
from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import Order, OrderSide, OrderType
from src.infra.exchange.native_okx import NativeOKXAdapter


@pytest.fixture
def adapter():
    return NativeOKXAdapter(api_key="key", api_secret="secret", passphrase="pass", sandbox=False)


@pytest.fixture
def sandbox_adapter():
    return NativeOKXAdapter(api_key="key", api_secret="secret", passphrase="pass", sandbox=True)


# ------------------------------------------------------------------
# Symbol normalization
# ------------------------------------------------------------------

def test_normalize_symbol_slash(adapter):
    assert adapter._normalize_symbol("BTC/USDT") == "BTC-USDT"


def test_normalize_symbol_no_slash(adapter):
    assert adapter._normalize_symbol("BTC-USDT") == "BTC-USDT"


# ------------------------------------------------------------------
# URL
# ------------------------------------------------------------------

def test_rest_base_url(adapter):
    assert adapter._rest_base_url() == "https://www.okx.com"


def test_rest_base_url_sandbox(sandbox_adapter):
    assert sandbox_adapter._rest_base_url() == "https://www.okx.com"


def test_ws_orderbook_url(adapter):
    url = adapter._ws_orderbook_url("BTC/USDT")
    assert url == "wss://ws.okx.com:8443/ws/v5/public"


# ------------------------------------------------------------------
# Default headers
# ------------------------------------------------------------------

def test_default_headers_live(adapter):
    headers = adapter._default_headers()
    assert headers["Content-Type"] == "application/json"
    assert "x-simulated-trading" not in headers


def test_default_headers_sandbox(sandbox_adapter):
    headers = sandbox_adapter._default_headers()
    assert headers["x-simulated-trading"] == "1"


# ------------------------------------------------------------------
# Auth headers
# ------------------------------------------------------------------

def test_auth_headers_get(adapter):
    params = {"instType": "SPOT", "instId": "BTC-USDT"}
    headers = adapter._auth_headers("GET", "/api/v5/account/trade-fee", params, None)
    assert headers["OK-ACCESS-KEY"] == "key"
    assert headers["OK-ACCESS-PASSPHRASE"] == "pass"
    assert "OK-ACCESS-SIGN" in headers
    assert "OK-ACCESS-TIMESTAMP" in headers
    # timestamp format: 2020-12-08T09:08:57.715Z
    ts = headers["OK-ACCESS-TIMESTAMP"]
    assert ts.endswith("Z")
    assert "T" in ts


def test_auth_headers_post(adapter):
    data = {"instId": "BTC-USDT", "tdMode": "cash"}
    headers = adapter._auth_headers("POST", "/api/v5/trade/order", None, data)
    assert "OK-ACCESS-SIGN" in headers
    # Base64 signature
    import base64
    sig = headers["OK-ACCESS-SIGN"]
    # Should be valid base64
    decoded = base64.b64decode(sig)
    assert len(decoded) == 32  # SHA256 = 32 bytes


def test_auth_headers_sandbox_includes_simulated(sandbox_adapter):
    headers = sandbox_adapter._auth_headers("GET", "/api/v5/account/balance", None, None)
    assert headers.get("x-simulated-trading") == "1"


# ------------------------------------------------------------------
# WS subscribe message
# ------------------------------------------------------------------

def test_ws_subscribe_message(adapter):
    msg = adapter._ws_subscribe_message("BTC/USDT")
    assert msg["op"] == "subscribe"
    assert msg["args"][0]["channel"] == "books"
    assert msg["args"][0]["instId"] == "BTC-USDT"


# ------------------------------------------------------------------
# WS orderbook parsing
# ------------------------------------------------------------------

def test_parse_ws_orderbook_valid(adapter):
    payload = json.dumps({
        "arg": {"channel": "books", "instId": "BTC-USDT"},
        "data": [{
            "bids": [["50000", "1.5", "0", "1"], ["49999", "2.0", "0", "2"]],
            "asks": [["50001", "1.0", "0", "1"], ["50002", "0.5", "0", "1"]],
            "ts": "1701000000000",
        }],
    })
    ob = adapter._parse_ws_orderbook(payload, "BTC/USDT")
    assert ob is not None
    assert ob.exchange_id == "okx"
    assert ob.symbol == "BTC/USDT"
    assert len(ob.bids) == 2
    assert len(ob.asks) == 2
    assert ob.bids[0].price == Decimal("50000")
    assert ob.asks[0].price == Decimal("50001")


def test_parse_ws_orderbook_wrong_channel(adapter):
    payload = json.dumps({
        "arg": {"channel": "trades", "instId": "BTC-USDT"},
        "data": [{}],
    })
    assert adapter._parse_ws_orderbook(payload, "BTC/USDT") is None


def test_parse_ws_orderbook_invalid_json(adapter):
    assert adapter._parse_ws_orderbook("not-json", "BTC/USDT") is None


def test_parse_ws_orderbook_no_data(adapter):
    payload = json.dumps({"arg": {"channel": "books"}, "data": []})
    assert adapter._parse_ws_orderbook(payload, "BTC/USDT") is None


# ------------------------------------------------------------------
# REST: orderbook snapshot
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rest_get_orderbook(adapter):
    mock_resp = {
        "data": [{
            "bids": [["50000", "1.0", "0", "1"]],
            "asks": [["50001", "0.5", "0", "1"]],
        }]
    }
    with patch.object(adapter, "_request", AsyncMock(return_value=mock_resp)):
        ob = await adapter._rest_get_orderbook("BTC/USDT", depth=20)

    assert ob.exchange_id == "okx"
    assert ob.bids[0].price == Decimal("50000")
    assert ob.asks[0].price == Decimal("50001")


@pytest.mark.asyncio
async def test_rest_get_orderbook_empty(adapter):
    with patch.object(adapter, "_request", AsyncMock(return_value={"data": []})):
        ob = await adapter._rest_get_orderbook("BTC/USDT")
    assert ob.bids == []
    assert ob.asks == []


# ------------------------------------------------------------------
# REST: place order
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rest_place_order_limit(adapter):
    order = Order(
        exchange_id="okx",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=Decimal("50000"),
        amount=Decimal("0.01"),
    )
    mock_resp = {"data": [{"ordId": "okx-ord-123", "sCode": "0"}]}

    with patch.object(adapter, "_request", AsyncMock(return_value=mock_resp)):
        trade = await adapter._rest_place_order(order)

    assert trade.trade_id == "okx-ord-123"
    assert trade.exchange_id == "okx"
    assert trade.price == Decimal("50000")
    assert trade.amount == Decimal("0.01")
    assert trade.side == OrderSide.BUY


@pytest.mark.asyncio
async def test_rest_place_order_market_sell(adapter):
    order = Order(
        exchange_id="okx",
        symbol="ETH/USDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=Decimal("2.0"),
    )
    mock_resp = {"data": [{"ordId": "okx-sell-456", "sCode": "0"}]}

    with patch.object(adapter, "_request", AsyncMock(return_value=mock_resp)):
        trade = await adapter._rest_place_order(order)

    assert trade.trade_id == "okx-sell-456"
    assert trade.side == OrderSide.SELL


# ------------------------------------------------------------------
# REST: cancel order
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rest_cancel_order_success(adapter):
    mock_resp = {"data": [{"sCode": "0"}]}
    with patch.object(adapter, "_request", AsyncMock(return_value=mock_resp)):
        result = await adapter._rest_cancel_order("okx-ord-123", "BTC/USDT")
    assert result is True


@pytest.mark.asyncio
async def test_rest_cancel_order_failure(adapter):
    mock_resp = {"data": [{"sCode": "51400"}]}
    with patch.object(adapter, "_request", AsyncMock(return_value=mock_resp)):
        result = await adapter._rest_cancel_order("okx-ord-123", None)
    assert result is False


@pytest.mark.asyncio
async def test_rest_cancel_order_empty_data(adapter):
    mock_resp = {"data": []}
    with patch.object(adapter, "_request", AsyncMock(return_value=mock_resp)):
        result = await adapter._rest_cancel_order("ord", "BTC/USDT")
    assert result is False


# ------------------------------------------------------------------
# REST: balances
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rest_get_balances(adapter):
    mock_resp = {
        "data": [{
            "details": [
                {"ccy": "BTC", "availBal": "1.5", "frozenBal": "0.5"},
                {"ccy": "USDT", "availBal": "5000", "frozenBal": "0"},
            ]
        }]
    }
    with patch.object(adapter, "_request", AsyncMock(return_value=mock_resp)):
        balances = await adapter._rest_get_balances()

    assert "BTC" in balances
    assert balances["BTC"].free == Decimal("1.5")
    assert balances["BTC"].used == Decimal("0.5")
    assert balances["BTC"].total == Decimal("2.0")
    assert "USDT" in balances
    assert balances["USDT"].free == Decimal("5000")
    assert balances["USDT"].used == Decimal("0")


@pytest.mark.asyncio
async def test_rest_get_balances_empty(adapter):
    with patch.object(adapter, "_request", AsyncMock(return_value={"data": []})):
        balances = await adapter._rest_get_balances()
    assert balances == {}


# ------------------------------------------------------------------
# REST: positions (always empty for spot)
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rest_get_positions(adapter):
    positions = await adapter._rest_get_positions()
    assert positions == []


# ------------------------------------------------------------------
# REST: fee rate
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rest_get_fee_rate(adapter):
    mock_resp = {
        "data": [{"maker": "-0.0008", "taker": "-0.001"}]
    }
    with patch.object(adapter, "_request", AsyncMock(return_value=mock_resp)):
        fee = await adapter._rest_get_fee_rate("BTC/USDT")

    assert fee.maker == Decimal("0.0008")
    assert fee.taker == Decimal("0.001")
    assert fee.exchange_id == "okx"
    assert fee.symbol == "BTC/USDT"


@pytest.mark.asyncio
async def test_rest_get_fee_rate_fallback(adapter):
    with patch.object(adapter, "_request", AsyncMock(return_value={"data": []})):
        fee = await adapter._rest_get_fee_rate("BTC/USDT")
    assert fee.maker == Decimal("0.0008")
    assert fee.taker == Decimal("0.001")


# ------------------------------------------------------------------
# OKX timestamp format
# ------------------------------------------------------------------

def test_okx_timestamp_format(adapter):
    ts = adapter._okx_timestamp()
    # Must be: YYYY-MM-DDTHH:MM:SS.mmmZ
    assert ts.endswith("Z")
    assert len(ts) == 24
    assert ts[10] == "T"
    assert ts[23] == "Z"
    assert ts[19] == "."
