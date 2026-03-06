"""Unit tests for NativeBybitAdapter."""
from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import Order, OrderSide, OrderType
from src.infra.exchange.native_bybit import NativeBybitAdapter


@pytest.fixture
def adapter():
    return NativeBybitAdapter(api_key="key", api_secret="secret", sandbox=False)


@pytest.fixture
def sandbox_adapter():
    return NativeBybitAdapter(api_key="key", api_secret="secret", sandbox=True)


# ------------------------------------------------------------------
# Symbol normalization
# ------------------------------------------------------------------

def test_normalize_symbol_slash(adapter):
    assert adapter._normalize_symbol("BTC/USDT") == "BTCUSDT"


def test_normalize_symbol_no_slash(adapter):
    assert adapter._normalize_symbol("BTCUSDT") == "BTCUSDT"


# ------------------------------------------------------------------
# URL
# ------------------------------------------------------------------

def test_rest_base_url_live(adapter):
    assert adapter._rest_base_url() == "https://api.bybit.com"


def test_rest_base_url_sandbox(sandbox_adapter):
    assert sandbox_adapter._rest_base_url() == "https://api-testnet.bybit.com"


def test_ws_orderbook_url(adapter):
    url = adapter._ws_orderbook_url("BTC/USDT")
    assert url == "wss://stream.bybit.com/v5/public/spot"


# ------------------------------------------------------------------
# Auth headers
# ------------------------------------------------------------------

def test_auth_headers_get(adapter):
    params = {"accountType": "UNIFIED"}
    headers = adapter._auth_headers("GET", "/v5/account/wallet-balance", params, None)
    assert "X-BAPI-API-KEY" in headers
    assert headers["X-BAPI-API-KEY"] == "key"
    assert "X-BAPI-TIMESTAMP" in headers
    assert "X-BAPI-SIGN" in headers
    assert headers["X-BAPI-RECV-WINDOW"] == "5000"
    assert len(headers["X-BAPI-SIGN"]) == 64  # hex sha256


def test_auth_headers_post(adapter):
    data = {"category": "spot", "symbol": "BTCUSDT"}
    headers = adapter._auth_headers("POST", "/v5/order/create", None, data)
    assert "X-BAPI-SIGN" in headers
    assert len(headers["X-BAPI-SIGN"]) == 64


def test_auth_headers_empty(adapter):
    headers = adapter._auth_headers("GET", "/v5/market/orderbook", None, None)
    assert "X-BAPI-SIGN" in headers


# ------------------------------------------------------------------
# WS subscribe message
# ------------------------------------------------------------------

def test_ws_subscribe_message(adapter):
    msg = adapter._ws_subscribe_message("BTC/USDT")
    assert msg["op"] == "subscribe"
    assert "orderbook.50.BTCUSDT" in msg["args"]


# ------------------------------------------------------------------
# WS orderbook parsing
# ------------------------------------------------------------------

def test_parse_ws_orderbook_valid(adapter):
    payload = json.dumps({
        "topic": "orderbook.50.BTCUSDT",
        "data": {
            "b": [["50000", "1.5"], ["49999", "2.0"]],
            "a": [["50001", "1.0"], ["50002", "0.5"]],
            "seq": 12345,
        },
    })
    ob = adapter._parse_ws_orderbook(payload, "BTC/USDT")
    assert ob is not None
    assert ob.exchange_id == "bybit"
    assert ob.symbol == "BTC/USDT"
    assert len(ob.bids) == 2
    assert len(ob.asks) == 2
    assert ob.bids[0].price == Decimal("50000")
    assert ob.sequence == 12345


def test_parse_ws_orderbook_non_orderbook(adapter):
    payload = json.dumps({"topic": "trade.BTCUSDT", "data": {}})
    assert adapter._parse_ws_orderbook(payload, "BTC/USDT") is None


def test_parse_ws_orderbook_invalid_json(adapter):
    assert adapter._parse_ws_orderbook("not-json", "BTC/USDT") is None


# ------------------------------------------------------------------
# REST: orderbook snapshot
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rest_get_orderbook(adapter):
    mock_resp = {
        "result": {
            "b": [["50000", "1.0"]],
            "a": [["50001", "0.5"]],
            "seq": 99,
        }
    }
    adapter._http = MagicMock()
    adapter._http.request = AsyncMock(return_value=MagicMock(
        json=lambda: mock_resp,
        raise_for_status=lambda: None,
    ))

    with patch.object(adapter, "_request", AsyncMock(return_value=mock_resp)):
        ob = await adapter._rest_get_orderbook("BTC/USDT", depth=50)

    assert ob.exchange_id == "bybit"
    assert ob.bids[0].price == Decimal("50000")
    assert ob.asks[0].price == Decimal("50001")
    assert ob.sequence == 99


# ------------------------------------------------------------------
# REST: place order
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rest_place_order_limit(adapter):
    order = Order(
        exchange_id="bybit",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=Decimal("50000"),
        amount=Decimal("0.01"),
        client_order_id="cid123",
    )
    mock_resp = {"result": {"orderId": "ord-abc"}}

    with patch.object(adapter, "_request", AsyncMock(return_value=mock_resp)):
        trade = await adapter._rest_place_order(order)

    assert trade.trade_id == "ord-abc"
    assert trade.exchange_id == "bybit"
    assert trade.price == Decimal("50000")
    assert trade.amount == Decimal("0.01")


@pytest.mark.asyncio
async def test_rest_place_order_market(adapter):
    order = Order(
        exchange_id="bybit",
        symbol="ETH/USDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=Decimal("1.0"),
    )
    mock_resp = {"result": {"orderId": "ord-xyz"}}

    with patch.object(adapter, "_request", AsyncMock(return_value=mock_resp)):
        trade = await adapter._rest_place_order(order)

    assert trade.trade_id == "ord-xyz"
    assert trade.side == OrderSide.SELL


# ------------------------------------------------------------------
# REST: cancel order
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rest_cancel_order_success(adapter):
    mock_resp = {"retCode": 0}
    with patch.object(adapter, "_request", AsyncMock(return_value=mock_resp)):
        result = await adapter._rest_cancel_order("ord-abc", "BTC/USDT")
    assert result is True


@pytest.mark.asyncio
async def test_rest_cancel_order_failure(adapter):
    mock_resp = {"retCode": 10001}
    with patch.object(adapter, "_request", AsyncMock(return_value=mock_resp)):
        result = await adapter._rest_cancel_order("ord-abc", None)
    assert result is False


# ------------------------------------------------------------------
# REST: cancel all orders
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rest_cancel_all_orders(adapter):
    mock_resp = {"result": {"list": ["ord1", "ord2", "ord3"]}}
    with patch.object(adapter, "_request", AsyncMock(return_value=mock_resp)):
        count = await adapter._rest_cancel_all_orders("BTC/USDT")
    assert count == 3


@pytest.mark.asyncio
async def test_rest_cancel_all_orders_empty(adapter):
    mock_resp = {"result": {"list": []}}
    with patch.object(adapter, "_request", AsyncMock(return_value=mock_resp)):
        count = await adapter._rest_cancel_all_orders(None)
    assert count == 0


# ------------------------------------------------------------------
# REST: balances
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rest_get_balances(adapter):
    mock_resp = {
        "result": {
            "list": [{
                "coin": [
                    {"coin": "BTC", "availableToWithdraw": "1.5", "walletBalance": "2.0"},
                    {"coin": "USDT", "availableToWithdraw": "5000", "walletBalance": "5000"},
                ]
            }]
        }
    }
    with patch.object(adapter, "_request", AsyncMock(return_value=mock_resp)):
        balances = await adapter._rest_get_balances()

    assert "BTC" in balances
    assert balances["BTC"].free == Decimal("1.5")
    assert balances["BTC"].total == Decimal("2.0")
    assert balances["BTC"].used == Decimal("0.5")
    assert "USDT" in balances
    assert balances["USDT"].free == Decimal("5000")


@pytest.mark.asyncio
async def test_rest_get_balances_empty(adapter):
    mock_resp = {"result": {"list": []}}
    with patch.object(adapter, "_request", AsyncMock(return_value=mock_resp)):
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
        "result": {
            "list": [{"makerFeeRate": "0.0002", "takerFeeRate": "0.0006"}]
        }
    }
    with patch.object(adapter, "_request", AsyncMock(return_value=mock_resp)):
        fee = await adapter._rest_get_fee_rate("BTC/USDT")

    assert fee.maker == Decimal("0.0002")
    assert fee.taker == Decimal("0.0006")
    assert fee.exchange_id == "bybit"
    assert fee.symbol == "BTC/USDT"


@pytest.mark.asyncio
async def test_rest_get_fee_rate_fallback(adapter):
    mock_resp = {"result": {"list": []}}
    with patch.object(adapter, "_request", AsyncMock(return_value=mock_resp)):
        fee = await adapter._rest_get_fee_rate("BTC/USDT")
    assert fee.maker == Decimal("0.001")
    assert fee.taker == Decimal("0.001")
