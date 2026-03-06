"""Unit tests for NativeBithumbAdapter."""
from __future__ import annotations

import hashlib
import hmac
import json
import urllib.parse
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import Order, OrderSide, OrderType
from src.infra.exchange.native_bithumb import (
    NativeBithumbAdapter,
    _coin_from_symbol,
    _normalize_symbol,
)
from src.infra.exchange.rate_limiter import RateLimitConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def adapter():
    rate_limits = {
        "default": RateLimitConfig(requests_per_second=10000, burst=10000),
        "order": RateLimitConfig(requests_per_second=10000, burst=10000),
    }
    return NativeBithumbAdapter(
        api_key="bithumb_key",
        api_secret="bithumb_secret",
        sandbox=False,
        rate_limits=rate_limits,
    )


def _make_order(side=OrderSide.BUY, price=Decimal("50000000"), amount=Decimal("0.001")):
    return Order(
        exchange_id="bithumb",
        symbol="BTC/KRW",
        side=side,
        order_type=OrderType.LIMIT,
        price=price,
        amount=amount,
    )


# ---------------------------------------------------------------------------
# Symbol helpers
# ---------------------------------------------------------------------------

class TestSymbolHelpers:
    def test_normalize_slash_pair(self):
        assert _normalize_symbol("BTC/KRW") == "BTC_KRW"

    def test_normalize_already_normalized(self):
        assert _normalize_symbol("BTC_KRW") == "BTC_KRW"

    def test_coin_from_slash_symbol(self):
        assert _coin_from_symbol("BTC/KRW") == "BTC"

    def test_coin_from_underscore_symbol(self):
        assert _coin_from_symbol("ETH_KRW") == "ETH"

    def test_coin_from_eth(self):
        assert _coin_from_symbol("ETH/KRW") == "ETH"


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------

class TestInit:
    def test_exchange_id(self, adapter):
        assert adapter.exchange_id == "bithumb"

    def test_rest_base_url(self, adapter):
        assert adapter._rest_base_url() == "https://api.bithumb.com"

    def test_ws_url(self, adapter):
        assert adapter._ws_orderbook_url("BTC/KRW") == "wss://pubwss.bithumb.com/pub/ws"

    def test_default_headers_form_encoded(self, adapter):
        headers = adapter._default_headers()
        assert headers["Content-Type"] == "application/x-www-form-urlencoded"


# ---------------------------------------------------------------------------
# Auth headers
# ---------------------------------------------------------------------------

class TestAuthHeaders:
    def test_auth_headers_keys_present(self, adapter):
        headers = adapter._auth_headers("POST", "/trade/place", None, {"order_currency": "BTC"})
        assert "Api-Key" in headers
        assert "Api-Sign" in headers
        assert "Api-Nonce" in headers

    def test_api_key_matches(self, adapter):
        headers = adapter._auth_headers("POST", "/test", None, {})
        assert headers["Api-Key"] == "bithumb_key"

    def test_nonce_is_numeric_string(self, adapter):
        headers = adapter._auth_headers("POST", "/test", None, {})
        nonce = headers["Api-Nonce"]
        assert nonce.isdigit()

    def test_signature_is_sha512_hex(self, adapter):
        headers = adapter._auth_headers("POST", "/test", None, {})
        sig = headers["Api-Sign"]
        # SHA512 hex = 128 chars
        assert len(sig) == 128
        assert all(c in "0123456789abcdef" for c in sig)

    def test_signature_correctness(self, adapter):
        """Manually recompute the HMAC-SHA512 signature."""
        path = "/trade/place"
        form_data = {"order_currency": "BTC", "type": "bid"}

        with patch("src.infra.exchange.native_bithumb.time") as mock_time:
            mock_time.time.return_value = 1700000000.0
            headers = adapter._auth_headers("POST", path, None, form_data)

        nonce = "1700000000000"
        query_str = urllib.parse.urlencode(sorted(form_data.items()))
        prehash = path + chr(0) + query_str + chr(0) + nonce
        expected_sig = hmac.new(
            b"bithumb_secret", prehash.encode(), hashlib.sha512
        ).hexdigest()
        assert headers["Api-Sign"] == expected_sig


# ---------------------------------------------------------------------------
# WS subscribe message
# ---------------------------------------------------------------------------

class TestWsSubscribeMessage:
    def test_subscribe_message_type(self, adapter):
        msg = adapter._ws_subscribe_message("BTC/KRW")
        assert msg["type"] == "orderbooksnapshot"

    def test_subscribe_symbols(self, adapter):
        msg = adapter._ws_subscribe_message("BTC/KRW")
        assert "BTC_KRW" in msg["symbols"]

    def test_subscribe_eth(self, adapter):
        msg = adapter._ws_subscribe_message("ETH/KRW")
        assert "ETH_KRW" in msg["symbols"]


# ---------------------------------------------------------------------------
# Parse WS orderbook
# ---------------------------------------------------------------------------

class TestParseWsOrderbook:
    def _msg(self, bids=None, asks=None) -> str:
        return json.dumps({
            "type": "orderbooksnapshot",
            "content": {
                "bids": bids or [{"price": "50000000", "quantity": "0.1"}],
                "asks": asks or [{"price": "50100000", "quantity": "0.05"}],
            },
        })

    def test_orderbook_parsed(self, adapter):
        ob = adapter._parse_ws_orderbook(self._msg(), "BTC/KRW")
        assert ob is not None
        assert ob.symbol == "BTC/KRW"
        assert ob.exchange_id == "bithumb"

    def test_bid_price(self, adapter):
        ob = adapter._parse_ws_orderbook(
            self._msg(bids=[{"price": "49000000", "quantity": "0.2"}]),
            "BTC/KRW",
        )
        assert ob is not None
        assert ob.bids[0].price == Decimal("49000000")

    def test_ask_price(self, adapter):
        ob = adapter._parse_ws_orderbook(
            self._msg(asks=[{"price": "50500000", "quantity": "0.3"}]),
            "BTC/KRW",
        )
        assert ob is not None
        assert ob.asks[0].price == Decimal("50500000")

    def test_empty_bids_asks_returns_none(self, adapter):
        msg = json.dumps({"content": {"bids": [], "asks": []}})
        ob = adapter._parse_ws_orderbook(msg, "BTC/KRW")
        assert ob is None

    def test_invalid_json_returns_none(self, adapter):
        ob = adapter._parse_ws_orderbook("not-json", "BTC/KRW")
        assert ob is None

    def test_missing_content_returns_none(self, adapter):
        msg = json.dumps({"type": "ticker"})
        ob = adapter._parse_ws_orderbook(msg, "BTC/KRW")
        assert ob is None


# ---------------------------------------------------------------------------
# REST: get_orderbook_snapshot (public, no auth)
# ---------------------------------------------------------------------------

class TestRestGetOrderbook:
    @pytest.mark.asyncio
    async def test_returns_orderbook(self, adapter):
        mock_resp = {
            "status": "0000",
            "data": {
                "bids": [{"price": "50000000", "quantity": "0.5"}],
                "asks": [{"price": "50100000", "quantity": "0.3"}],
            },
        }
        adapter._http = MagicMock()
        adapter._http.request = AsyncMock(return_value=MagicMock(
            json=lambda: mock_resp,
            raise_for_status=lambda: None,
        ))

        ob = await adapter._rest_get_orderbook("BTC/KRW", 20)
        assert ob.symbol == "BTC/KRW"
        assert ob.exchange_id == "bithumb"
        assert ob.bids[0].price == Decimal("50000000")

    @pytest.mark.asyncio
    async def test_uses_correct_path(self, adapter):
        """Path includes coin name: /public/orderbook/BTC_KRW"""
        mock_resp = {"data": {"bids": [], "asks": []}}
        adapter._http = MagicMock()
        adapter._http.request = AsyncMock(return_value=MagicMock(
            json=lambda: mock_resp,
            raise_for_status=lambda: None,
        ))

        await adapter._rest_get_orderbook("ETH/KRW", 20)
        call_args = adapter._http.request.call_args
        path = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("url", "")
        assert "ETH_KRW" in path

    @pytest.mark.asyncio
    async def test_depth_passed_as_count(self, adapter):
        mock_resp = {"data": {"bids": [], "asks": []}}
        adapter._http = MagicMock()
        adapter._http.request = AsyncMock(return_value=MagicMock(
            json=lambda: mock_resp,
            raise_for_status=lambda: None,
        ))

        await adapter._rest_get_orderbook("BTC/KRW", 10)
        call_kwargs = adapter._http.request.call_args[1]
        assert call_kwargs.get("params", {}).get("count") == 10


# ---------------------------------------------------------------------------
# REST: place_order
# ---------------------------------------------------------------------------

class TestRestPlaceOrder:
    @pytest.mark.asyncio
    async def test_buy_order(self, adapter):
        mock_resp = {"status": "0000", "data": {"order_id": "ORD-123"}}
        adapter._http = MagicMock()
        adapter._http.request = AsyncMock(return_value=MagicMock(
            json=lambda: mock_resp,
            raise_for_status=lambda: None,
        ))

        order = _make_order(side=OrderSide.BUY)
        trade = await adapter._rest_place_order(order)
        assert trade.side == OrderSide.BUY
        assert trade.trade_id == "ORD-123"

    @pytest.mark.asyncio
    async def test_sell_order(self, adapter):
        mock_resp = {"status": "0000", "data": {"order_id": "ORD-456"}}
        adapter._http = MagicMock()
        adapter._http.request = AsyncMock(return_value=MagicMock(
            json=lambda: mock_resp,
            raise_for_status=lambda: None,
        ))

        order = _make_order(side=OrderSide.SELL)
        trade = await adapter._rest_place_order(order)
        assert trade.side == OrderSide.SELL
        assert trade.trade_id == "ORD-456"

    @pytest.mark.asyncio
    async def test_amount_matches_order(self, adapter):
        mock_resp = {"status": "0000", "data": {"order_id": "ORD-789"}}
        adapter._http = MagicMock()
        adapter._http.request = AsyncMock(return_value=MagicMock(
            json=lambda: mock_resp,
            raise_for_status=lambda: None,
        ))

        order = _make_order(amount=Decimal("0.05"))
        trade = await adapter._rest_place_order(order)
        assert trade.amount == Decimal("0.05")


# ---------------------------------------------------------------------------
# REST: cancel_order
# ---------------------------------------------------------------------------

class TestRestCancelOrder:
    @pytest.mark.asyncio
    async def test_cancel_success(self, adapter):
        mock_resp = {"status": "0000"}
        adapter._http = MagicMock()
        adapter._http.request = AsyncMock(return_value=MagicMock(
            json=lambda: mock_resp,
            raise_for_status=lambda: None,
        ))

        result = await adapter._rest_cancel_order("ORD-123", "BTC/KRW")
        assert result is True

    @pytest.mark.asyncio
    async def test_cancel_failure(self, adapter):
        mock_resp = {"status": "5100", "message": "Bad Request"}
        adapter._http = MagicMock()
        adapter._http.request = AsyncMock(return_value=MagicMock(
            json=lambda: mock_resp,
            raise_for_status=lambda: None,
        ))

        result = await adapter._rest_cancel_order("BAD-ID", None)
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_all_returns_zero(self, adapter):
        result = await adapter._rest_cancel_all_orders(None)
        assert result == 0


# ---------------------------------------------------------------------------
# REST: get_balances
# ---------------------------------------------------------------------------

class TestRestGetBalances:
    @pytest.mark.asyncio
    async def test_balances_parsed(self, adapter):
        mock_resp = {
            "status": "0000",
            "data": {
                "available_btc": "0.5",
                "total_btc": "0.6",
                "available_krw": "1000000",
                "total_krw": "1000000",
            },
        }
        adapter._http = MagicMock()
        adapter._http.request = AsyncMock(return_value=MagicMock(
            json=lambda: mock_resp,
            raise_for_status=lambda: None,
        ))

        balances = await adapter._rest_get_balances()
        assert "BTC" in balances
        assert balances["BTC"].free == Decimal("0.5")
        assert balances["BTC"].total == Decimal("0.6")
        assert balances["BTC"].used == Decimal("0.1")

    @pytest.mark.asyncio
    async def test_empty_data_returns_empty(self, adapter):
        mock_resp = {"status": "0000", "data": {}}
        adapter._http = MagicMock()
        adapter._http.request = AsyncMock(return_value=MagicMock(
            json=lambda: mock_resp,
            raise_for_status=lambda: None,
        ))

        balances = await adapter._rest_get_balances()
        assert balances == {}


# ---------------------------------------------------------------------------
# Fee rate and positions
# ---------------------------------------------------------------------------

class TestFeeAndPositions:
    @pytest.mark.asyncio
    async def test_fee_rate_bithumb(self, adapter):
        fee = await adapter._rest_get_fee_rate("BTC/KRW")
        assert fee.maker == Decimal("0.0025")
        assert fee.taker == Decimal("0.0025")
        assert fee.exchange_id == "bithumb"

    @pytest.mark.asyncio
    async def test_positions_empty(self, adapter):
        positions = await adapter._rest_get_positions()
        assert positions == []
