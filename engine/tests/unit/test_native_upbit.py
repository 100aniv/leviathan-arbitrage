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
    _align_upbit_price,
    _b64url,
    _make_jwt,
    _normalize_symbol,
    _upbit_tick_size,
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
# BUG-221: Tick size alignment (USDT + KRW markets)
# ---------------------------------------------------------------------------

class TestUpbitTickSize:
    """Upbit tick sizes — source: docs.upbit.com (호가 정책 / USDT Market Order Price Unit)."""

    # USDT market bands
    def test_usdt_above_10(self):
        assert _upbit_tick_size("SOL/USDT", Decimal("84.79")) == Decimal("0.01")
        assert _upbit_tick_size("BTC/USDT", Decimal("50000")) == Decimal("0.01")

    def test_usdt_1_to_10(self):
        assert _upbit_tick_size("XRP/USDT", Decimal("2.345")) == Decimal("0.001")

    def test_usdt_0p1_to_1(self):
        assert _upbit_tick_size("DOGE/USDT", Decimal("0.5")) == Decimal("0.0001")

    def test_usdt_0p01_to_0p1(self):
        assert _upbit_tick_size("PEPE/USDT", Decimal("0.05")) == Decimal("0.00001")

    # KRW market
    def test_krw_above_2M(self):
        assert _upbit_tick_size("BTC/KRW", Decimal("50000000")) == Decimal("1000")

    def test_krw_100k_to_500k(self):
        assert _upbit_tick_size("ETH/KRW", Decimal("200000")) == Decimal("50")


class TestAlignUpbitPrice:
    """BUG-221: Upbit rejects prices not on the tick grid — must truncate down."""

    def test_repro_bug_221(self):
        # Literal price from the reported error log.
        raw = Decimal("84.79138627187079407806191117")
        aligned = _align_upbit_price("SOL/USDT", raw, OrderSide.SELL)
        # ≥10 USDT → 0.01 tick, truncated
        assert aligned == Decimal("84.79")

    def test_usdt_truncates_down(self):
        assert _align_upbit_price("SOL/USDT", Decimal("84.799999"), OrderSide.BUY) == Decimal("84.79")

    def test_usdt_already_aligned(self):
        assert _align_upbit_price("SOL/USDT", Decimal("84.79"), OrderSide.BUY) == Decimal("84.79")

    def test_krw_large_price(self):
        # 50,000,000 KRW band → 1,000원 tick
        aligned = _align_upbit_price("BTC/KRW", Decimal("52345678.99"), OrderSide.BUY)
        assert aligned == Decimal("52345000")

    def test_small_usdt_micro_tick(self):
        # < 0.0001 USDT → 0.00000001 tick
        aligned = _align_upbit_price("MEME/USDT", Decimal("0.000009876543"), OrderSide.BUY)
        assert aligned == Decimal("0.00000987")

    def test_zero_or_negative_unchanged(self):
        assert _align_upbit_price("SOL/USDT", Decimal("0"), OrderSide.BUY) == Decimal("0")

    def test_order_body_uses_aligned_price(self):
        """_rest_place_order must serialise the aligned price in the request body."""
        import asyncio

        captured: dict = {}

        async def fake_request(self, method, path, params=None, data=None, signed=False, headers=None):  # noqa: ARG001
            captured["params"] = params
            return {"uuid": "abc-123"}

        rate_limits = {
            "default": RateLimitConfig(requests_per_second=10000, burst=10000),
            "order": RateLimitConfig(requests_per_second=10000, burst=10000),
        }
        a = NativeUpbitAdapter(api_key="k", api_secret="s", rate_limits=rate_limits)
        order = Order(
            exchange_id="upbit",
            symbol="SOL/USDT",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            price=Decimal("84.79138627187079407806191117"),
            amount=Decimal("0.08"),
        )
        with patch.object(NativeUpbitAdapter, "_request", fake_request):
            trade = asyncio.run(a._rest_place_order(order))
        assert captured["params"]["price"] == "84.79"
        assert trade.trade_id == "abc-123"


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
