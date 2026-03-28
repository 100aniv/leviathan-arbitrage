"""Unit tests for NativeUpbitAdapter."""
from __future__ import annotations

import base64
import hashlib
import json
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import Order, OrderSide, OrderType
from src.infra.exchange.native_upbit import (
    NativeUpbitAdapter,
    _b64url,
    _make_jwt,
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
    return NativeUpbitAdapter(
        api_key="upbit_key",
        api_secret="upbit_secret",
        sandbox=False,
        rate_limits=rate_limits,
    )


def _make_order(side=OrderSide.BUY, price=Decimal("50000000"), amount=Decimal("0.001")):
    return Order(
        exchange_id="upbit",
        symbol="BTC/KRW",
        side=side,
        order_type=OrderType.LIMIT,
        price=price,
        amount=amount,
    )


# ---------------------------------------------------------------------------
# Symbol normalization
# ---------------------------------------------------------------------------

class TestNormalizeSymbol:
    def test_btc_krw(self):
        assert _normalize_symbol("BTC/KRW") == "KRW-BTC"

    def test_eth_krw(self):
        assert _normalize_symbol("ETH/KRW") == "KRW-ETH"

    def test_no_slash_unchanged(self):
        assert _normalize_symbol("KRW-BTC") == "KRW-BTC"


# ---------------------------------------------------------------------------
# JWT construction helpers
# ---------------------------------------------------------------------------

class TestB64url:
    def test_no_padding(self):
        result = _b64url(b"hello")
        assert "=" not in result

    def test_url_safe(self):
        result = _b64url(b"\xff\xfe\xfd")
        assert "+" not in result
        assert "/" not in result


class TestMakeJwt:
    def test_jwt_three_parts(self):
        token = _make_jwt("key", "secret")
        parts = token.split(".")
        assert len(parts) == 3

    def test_header_alg_hs256(self):
        token = _make_jwt("key", "secret")
        header_b64 = token.split(".")[0]
        # Add padding
        padded = header_b64 + "=" * (4 - len(header_b64) % 4)
        header = json.loads(base64.urlsafe_b64decode(padded))
        assert header["alg"] == "HS256"
        assert header["typ"] == "JWT"

    def test_payload_access_key(self):
        token = _make_jwt("my_access_key", "secret")
        payload_b64 = token.split(".")[1]
        padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        assert payload["access_key"] == "my_access_key"

    def test_payload_nonce_is_uuid(self):
        token = _make_jwt("key", "secret")
        payload_b64 = token.split(".")[1]
        padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        # Should be valid UUID
        uuid.UUID(payload["nonce"])

    def test_payload_with_query_hash(self):
        params = {"market": "KRW-BTC", "side": "bid"}
        token = _make_jwt("key", "secret", params)
        payload_b64 = token.split(".")[1]
        padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        assert "query_hash" in payload
        assert payload["query_hash_alg"] == "SHA512"

    def test_query_hash_value(self):
        """query_hash must be SHA512 of sorted urlencode of params."""
        import urllib.parse
        params = {"market": "KRW-BTC", "side": "bid"}
        qs = urllib.parse.urlencode(sorted(params.items()))
        expected_hash = hashlib.sha512(qs.encode()).hexdigest()

        token = _make_jwt("key", "secret", params)
        payload_b64 = token.split(".")[1]
        padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        assert payload["query_hash"] == expected_hash

    def test_different_nonces_each_call(self):
        t1 = _make_jwt("key", "secret")
        t2 = _make_jwt("key", "secret")
        # Nonce differs, so tokens differ
        assert t1 != t2


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------

class TestInit:
    def test_exchange_id(self, adapter):
        assert adapter.exchange_id == "upbit"

    def test_rest_base_url(self, adapter):
        assert adapter._rest_base_url() == "https://api.upbit.com"

    def test_ws_url(self, adapter):
        assert adapter._ws_orderbook_url("BTC/KRW") == "wss://api.upbit.com/websocket/v1"

    def test_default_headers(self, adapter):
        assert adapter._default_headers()["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# Auth headers
# ---------------------------------------------------------------------------

class TestAuthHeaders:
    def test_authorization_header_present(self, adapter):
        headers = adapter._auth_headers("GET", "/v1/accounts", None, None)
        assert "Authorization" in headers

    def test_authorization_bearer_prefix(self, adapter):
        headers = adapter._auth_headers("GET", "/v1/accounts", None, None)
        assert headers["Authorization"].startswith("Bearer ")

    def test_jwt_embedded_in_auth(self, adapter):
        headers = adapter._auth_headers("GET", "/v1/accounts", None, None)
        token = headers["Authorization"][len("Bearer "):]
        parts = token.split(".")
        assert len(parts) == 3


# ---------------------------------------------------------------------------
# WS subscribe message
# ---------------------------------------------------------------------------

class TestWsSubscribeMessage:
    def test_subscribe_message_is_list(self, adapter):
        msg = adapter._ws_subscribe_message("BTC/KRW")
        assert isinstance(msg, list)
        assert len(msg) == 2

    def test_first_element_is_ticket(self, adapter):
        msg = adapter._ws_subscribe_message("BTC/KRW")
        assert "ticket" in msg[0]

    def test_second_element_type_orderbook(self, adapter):
        msg = adapter._ws_subscribe_message("BTC/KRW")
        assert msg[1]["type"] == "orderbook"

    def test_codes_contain_normalized_symbol(self, adapter):
        msg = adapter._ws_subscribe_message("BTC/KRW")
        assert "KRW-BTC" in msg[1]["codes"]


# ---------------------------------------------------------------------------
# Parse WS orderbook
# ---------------------------------------------------------------------------

class TestParseWsOrderbook:
    def _msg(self, bids=None, asks=None) -> bytes:
        units = []
        for p, s in (bids or [["50000000", "0.001"]]):
            units.append({"bid_price": p, "bid_size": s, "ask_price": "0", "ask_size": "0"})
        for p, s in (asks or [["50100000", "0.002"]]):
            units[-1]["ask_price"] = p
            units[-1]["ask_size"] = s
        return json.dumps({"type": "orderbook", "orderbook_units": units}).encode()

    def test_orderbook_parsed(self, adapter):
        ob = adapter._parse_ws_orderbook(self._msg(), "BTC/KRW")
        assert ob is not None
        assert ob.symbol == "BTC/KRW"

    def test_wrong_type_returns_none(self, adapter):
        msg = json.dumps({"type": "ticker"}).encode()
        ob = adapter._parse_ws_orderbook(msg, "BTC/KRW")
        assert ob is None

    def test_invalid_json_returns_none(self, adapter):
        ob = adapter._parse_ws_orderbook(b"bad-json", "BTC/KRW")
        assert ob is None

    def test_string_input(self, adapter):
        units = [{"bid_price": "50000000", "bid_size": "0.001",
                  "ask_price": "50100000", "ask_size": "0.002"}]
        msg = json.dumps({"type": "orderbook", "orderbook_units": units})
        ob = adapter._parse_ws_orderbook(msg, "BTC/KRW")
        assert ob is not None

    def test_bid_ask_prices(self, adapter):
        units = [{"bid_price": "49999000", "bid_size": "0.005",
                  "ask_price": "50001000", "ask_size": "0.003"}]
        msg = json.dumps({"type": "orderbook", "orderbook_units": units}).encode()
        ob = adapter._parse_ws_orderbook(msg, "BTC/KRW")
        assert ob is not None
        assert ob.bids[0].price == Decimal("49999000")
        assert ob.asks[0].price == Decimal("50001000")


# ---------------------------------------------------------------------------
# REST: get_orderbook_snapshot
# ---------------------------------------------------------------------------

class TestRestGetOrderbook:
    @pytest.mark.asyncio
    async def test_returns_orderbook(self, adapter):
        units = [
            {"bid_price": "50000000", "bid_size": "0.001",
             "ask_price": "50100000", "ask_size": "0.002"},
        ]
        mock_resp = [{"orderbook_units": units}]
        adapter._http = MagicMock()
        adapter._http.request = AsyncMock(return_value=MagicMock(
            json=lambda: mock_resp,
            raise_for_status=lambda: None,
        ))

        ob = await adapter._rest_get_orderbook("BTC/KRW", 20)
        assert ob.symbol == "BTC/KRW"
        assert ob.exchange_id == "upbit"
        assert ob.bids[0].price == Decimal("50000000")

    @pytest.mark.asyncio
    async def test_uses_normalized_market_param(self, adapter):
        units = [{"bid_price": "50000000", "bid_size": "0.001",
                  "ask_price": "50100000", "ask_size": "0.002"}]
        mock_resp = [{"orderbook_units": units}]
        adapter._http = MagicMock()
        adapter._http.request = AsyncMock(return_value=MagicMock(
            json=lambda: mock_resp,
            raise_for_status=lambda: None,
        ))

        await adapter._rest_get_orderbook("BTC/KRW", 20)
        call_kwargs = adapter._http.request.call_args[1]
        assert call_kwargs.get("params", {}).get("markets") == "KRW-BTC"


# ---------------------------------------------------------------------------
# REST: place_order
# ---------------------------------------------------------------------------

class TestRestPlaceOrder:
    @pytest.mark.asyncio
    async def test_buy_maps_to_bid(self, adapter):
        mock_resp = {"uuid": "order-uuid-001", "side": "bid"}
        adapter._http = MagicMock()
        adapter._http.request = AsyncMock(return_value=MagicMock(
            json=lambda: mock_resp,
            raise_for_status=lambda: None,
        ))

        order = _make_order(side=OrderSide.BUY)
        trade = await adapter._rest_place_order(order)
        assert trade.side == OrderSide.BUY
        assert trade.trade_id == "order-uuid-001"

    @pytest.mark.asyncio
    async def test_sell_maps_to_ask(self, adapter):
        mock_resp = {"uuid": "order-uuid-002", "side": "ask"}
        adapter._http = MagicMock()
        adapter._http.request = AsyncMock(return_value=MagicMock(
            json=lambda: mock_resp,
            raise_for_status=lambda: None,
        ))

        order = _make_order(side=OrderSide.SELL)
        trade = await adapter._rest_place_order(order)
        assert trade.side == OrderSide.SELL


# ---------------------------------------------------------------------------
# REST: cancel_order
# ---------------------------------------------------------------------------

class TestRestCancelOrder:
    @pytest.mark.asyncio
    async def test_cancel_success(self, adapter):
        mock_resp = {"uuid": "order-uuid-001", "state": "cancel"}
        adapter._http = MagicMock()
        adapter._http.request = AsyncMock(return_value=MagicMock(
            json=lambda: mock_resp,
            raise_for_status=lambda: None,
        ))

        result = await adapter._rest_cancel_order("order-uuid-001", None)
        assert result is True

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
        mock_resp = [
            {"currency": "KRW", "balance": "1000000.0", "locked": "0.0"},
            {"currency": "BTC", "balance": "0.5", "locked": "0.1"},
        ]
        adapter._http = MagicMock()
        adapter._http.request = AsyncMock(return_value=MagicMock(
            json=lambda: mock_resp,
            raise_for_status=lambda: None,
        ))

        balances = await adapter._rest_get_balances()
        assert "KRW" in balances
        assert balances["KRW"].free == Decimal("1000000.0")
        assert "BTC" in balances
        assert balances["BTC"].used == Decimal("0.1")
        assert balances["BTC"].total == Decimal("0.6")


# ---------------------------------------------------------------------------
# Fee rate and positions
# ---------------------------------------------------------------------------

class TestFeeAndPositions:
    @pytest.mark.asyncio
    async def test_fee_rate_upbit(self, adapter):
        """Upbit: Maker 0.05% / Taker 0.139% (SSOT 기준)."""
        fee = await adapter._rest_get_fee_rate("BTC/KRW")
        assert fee.maker == Decimal("0.0005")
        assert fee.taker == Decimal("0.00139")
        assert fee.exchange_id == "upbit"

    @pytest.mark.asyncio
    async def test_positions_empty(self, adapter):
        positions = await adapter._rest_get_positions()
        assert positions == []
