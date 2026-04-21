"""Unit tests for NativeBitgetAdapter."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import Order, OrderSide, OrderType
from src.infra.exchange.native_bitget import NativeBitgetAdapter, _normalize_symbol
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
    return NativeBitgetAdapter(
        api_key="test_api_key",
        api_secret="test_secret",
        passphrase="test_pass",
        sandbox=False,
        rate_limits=rate_limits,
    )


def _make_order(side=OrderSide.BUY, price=Decimal("50000"), amount=Decimal("0.01")):
    return Order(
        exchange_id="bitget",
        symbol="BTC/USDT",
        side=side,
        order_type=OrderType.LIMIT,
        price=price,
        amount=amount,
    )


# ---------------------------------------------------------------------------
# Symbol normalization
# ---------------------------------------------------------------------------

class TestNormalizeSymbol:
    def test_slash_removed(self):
        assert _normalize_symbol("BTC/USDT") == "BTCUSDT"

    def test_krw_pair(self):
        assert _normalize_symbol("ETH/USDT") == "ETHUSDT"

    def test_no_slash_unchanged(self):
        assert _normalize_symbol("BTCUSDT") == "BTCUSDT"


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------

class TestInit:
    def test_exchange_id(self, adapter):
        assert adapter.exchange_id == "bitget"

    def test_api_key_stored(self, adapter):
        assert adapter._api_key == "test_api_key"

    def test_passphrase_stored(self, adapter):
        assert adapter._passphrase == "test_pass"

    def test_rest_base_url(self, adapter):
        assert adapter._rest_base_url() == "https://api.bitget.com"

    def test_default_headers_content_type(self, adapter):
        headers = adapter._default_headers()
        assert headers["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# Auth headers
# ---------------------------------------------------------------------------

class TestAuthHeaders:
    def test_auth_headers_keys_present(self, adapter):
        headers = adapter._auth_headers("GET", "/api/v2/spot/account/assets", None, None)
        assert "ACCESS-KEY" in headers
        assert "ACCESS-SIGN" in headers
        assert "ACCESS-TIMESTAMP" in headers
        assert "ACCESS-PASSPHRASE" in headers

    def test_access_key_matches(self, adapter):
        headers = adapter._auth_headers("GET", "/test", None, None)
        assert headers["ACCESS-KEY"] == "test_api_key"

    def test_passphrase_matches(self, adapter):
        headers = adapter._auth_headers("GET", "/test", None, None)
        assert headers["ACCESS-PASSPHRASE"] == "test_pass"

    def test_signature_is_base64(self, adapter):
        headers = adapter._auth_headers("GET", "/test", None, None)
        sign = headers["ACCESS-SIGN"]
        # Must decode without error
        decoded = base64.b64decode(sign)
        assert len(decoded) == 32  # SHA256 = 32 bytes

    def test_signature_correctness(self, adapter):
        """Manually recompute and verify signature."""
        import time as _time
        # Patch time to get deterministic timestamp
        with patch("src.infra.exchange.native_bitget.time") as mock_time:
            mock_time.time.return_value = 1700000000.0
            headers = adapter._auth_headers("POST", "/api/v2/spot/trade/place-order", None, {"symbol": "BTCUSDT"})

        ts = "1700000000000"
        body = json.dumps({"symbol": "BTCUSDT"}, separators=(",", ":"))
        prehash = ts + "POST" + "/api/v2/spot/trade/place-order" + body
        expected_sign = base64.b64encode(
            hmac.new(b"test_secret", prehash.encode(), hashlib.sha256).digest()
        ).decode()
        assert headers["ACCESS-SIGN"] == expected_sign

    def test_query_string_appended_for_get(self, adapter):
        """GET params are included in prehash."""
        with patch("src.infra.exchange.native_bitget.time") as mock_time:
            mock_time.time.return_value = 1700000000.0
            h1 = adapter._auth_headers("GET", "/api/test", {"limit": "20"}, None)
            h2 = adapter._auth_headers("GET", "/api/test", None, None)
        # Signatures must differ because query string differs
        assert h1["ACCESS-SIGN"] != h2["ACCESS-SIGN"]


# ---------------------------------------------------------------------------
# WS subscribe message
# ---------------------------------------------------------------------------

class TestWsSubscribeMessage:
    def test_subscribe_message_structure(self, adapter):
        msg = adapter._ws_subscribe_message("BTC/USDT")
        assert msg["op"] == "subscribe"
        assert len(msg["args"]) == 1
        arg = msg["args"][0]
        # BUG-182: V3 UTA uses lowercase instType and topic/symbol (not channel/instId)
        assert arg["instType"] == "spot"
        assert arg["topic"] == "books5"
        assert arg["symbol"] == "BTCUSDT"

    def test_ws_url(self, adapter):
        # BUG-182: V3 UTA endpoint
        assert adapter._ws_orderbook_url("BTC/USDT") == "wss://ws.bitget.com/v3/ws/public"


# ---------------------------------------------------------------------------
# Parse WS orderbook
# ---------------------------------------------------------------------------

class TestParseWsOrderbook:
    def _msg(self, action="snapshot", bids=None, asks=None) -> str:
        return json.dumps({
            "action": action,
            "data": [{
                "bids": bids or [["50000", "1.0", "0"]],
                "asks": asks or [["50001", "0.5", "0"]],
            }],
        })

    def test_snapshot_parsed(self, adapter):
        ob = adapter._parse_ws_orderbook(self._msg("snapshot"), "BTC/USDT")
        assert ob is not None
        assert ob.symbol == "BTC/USDT"
        assert ob.exchange_id == "bitget"

    def test_update_parsed(self, adapter):
        ob = adapter._parse_ws_orderbook(self._msg("update"), "BTC/USDT")
        assert ob is not None

    def test_non_orderbook_message_returns_none(self, adapter):
        msg = json.dumps({"event": "subscribe", "arg": {}})
        ob = adapter._parse_ws_orderbook(msg, "BTC/USDT")
        assert ob is None

    def test_invalid_json_returns_none(self, adapter):
        ob = adapter._parse_ws_orderbook("not-json", "BTC/USDT")
        assert ob is None

    def test_bid_prices_are_decimal(self, adapter):
        ob = adapter._parse_ws_orderbook(
            self._msg(bids=[["49500.50", "2.0"]], asks=[["49501", "1.0"]]),
            "BTC/USDT",
        )
        assert ob is not None
        assert ob.bids[0].price == Decimal("49500.50")


# ---------------------------------------------------------------------------
# REST: get_orderbook_snapshot
# ---------------------------------------------------------------------------

class TestRestGetOrderbook:
    @pytest.mark.asyncio
    async def test_returns_orderbook(self, adapter):
        mock_resp = {
            "code": "00000",
            "data": {
                "bids": [["50000", "1.0"], ["49999", "2.0"]],
                "asks": [["50001", "0.5"], ["50002", "1.5"]],
            },
        }
        adapter._http = MagicMock()
        adapter._http.request = AsyncMock(return_value=MagicMock(
            json=lambda: mock_resp,
            raise_for_status=lambda: None,
        ))

        ob = await adapter._rest_get_orderbook("BTC/USDT", 20)
        assert ob.symbol == "BTC/USDT"
        assert ob.exchange_id == "bitget"
        assert len(ob.bids) == 2
        assert len(ob.asks) == 2
        assert ob.best_bid == Decimal("50000")
        assert ob.best_ask == Decimal("50001")

    @pytest.mark.asyncio
    async def test_uses_correct_symbol(self, adapter):
        """Symbol is normalized to BTCUSDT in request params."""
        mock_resp = {"data": {"bids": [["50000", "1.0"]], "asks": [["50001", "0.5"]]}}
        adapter._http = MagicMock()
        adapter._http.request = AsyncMock(return_value=MagicMock(
            json=lambda: mock_resp,
            raise_for_status=lambda: None,
        ))

        await adapter._rest_get_orderbook("BTC/USDT", 20)
        call_kwargs = adapter._http.request.call_args
        params = call_kwargs[1].get("params") or call_kwargs[0][2]
        assert params["symbol"] == "BTCUSDT"


# ---------------------------------------------------------------------------
# REST: place_order
# ---------------------------------------------------------------------------

class TestRestPlaceOrder:
    @pytest.mark.asyncio
    async def test_buy_order_side(self, adapter):
        mock_resp = {"code": "00000", "data": {"orderId": "ORD-001"}}
        adapter._http = MagicMock()
        adapter._http.request = AsyncMock(return_value=MagicMock(
            json=lambda: mock_resp,
            raise_for_status=lambda: None,
        ))

        order = _make_order(side=OrderSide.BUY)
        trade = await adapter._rest_place_order(order)
        assert trade.side == OrderSide.BUY
        assert trade.trade_id == "ORD-001"

    @pytest.mark.asyncio
    async def test_sell_order_side(self, adapter):
        mock_resp = {"code": "00000", "data": {"orderId": "ORD-002"}}
        adapter._http = MagicMock()
        adapter._http.request = AsyncMock(return_value=MagicMock(
            json=lambda: mock_resp,
            raise_for_status=lambda: None,
        ))

        order = _make_order(side=OrderSide.SELL)
        trade = await adapter._rest_place_order(order)
        assert trade.side == OrderSide.SELL

    @pytest.mark.asyncio
    async def test_trade_amount_matches_order(self, adapter):
        # BUG-93: LIMIT order response has no fill data → fill_qty defaults to 0 (not order.amount).
        # A LIMIT order is submitted but not yet filled at placement time.
        # Returning order.amount was phantom-fill behavior; 0 is the correct unfilled state.
        mock_resp = {"code": "00000", "data": {"orderId": "ORD-003"}}
        adapter._http = MagicMock()
        adapter._http.request = AsyncMock(return_value=MagicMock(
            json=lambda: mock_resp,
            raise_for_status=lambda: None,
        ))

        order = _make_order(amount=Decimal("0.05"))
        trade = await adapter._rest_place_order(order)
        # LIMIT order → no immediate fill, executor sees 0 and routes correctly via rollback/cancel
        assert trade.amount == Decimal("0")


# ---------------------------------------------------------------------------
# REST: cancel_order
# ---------------------------------------------------------------------------

class TestRestCancelOrder:
    @pytest.mark.asyncio
    async def test_cancel_success(self, adapter):
        mock_resp = {"code": "00000"}
        adapter._http = MagicMock()
        adapter._http.request = AsyncMock(return_value=MagicMock(
            json=lambda: mock_resp,
            raise_for_status=lambda: None,
        ))

        result = await adapter._rest_cancel_order("ORD-001", "BTC/USDT")
        assert result is True

    @pytest.mark.asyncio
    async def test_cancel_failure(self, adapter):
        mock_resp = {"code": "40001", "msg": "Order not found"}
        adapter._http = MagicMock()
        adapter._http.request = AsyncMock(return_value=MagicMock(
            json=lambda: mock_resp,
            raise_for_status=lambda: None,
        ))

        result = await adapter._rest_cancel_order("BAD-ID", None)
        assert result is False


# ---------------------------------------------------------------------------
# REST: get_balances
# ---------------------------------------------------------------------------

class TestRestGetBalances:
    @pytest.mark.asyncio
    async def test_balances_parsed(self, adapter):
        mock_resp = {
            "code": "00000",
            "data": [
                {"coin": "BTC", "available": "0.5", "frozen": "0.1"},
                {"coin": "USDT", "available": "1000.0", "frozen": "0.0"},
            ],
        }
        adapter._http = MagicMock()
        adapter._http.request = AsyncMock(return_value=MagicMock(
            json=lambda: mock_resp,
            raise_for_status=lambda: None,
        ))

        balances = await adapter._rest_get_balances()
        assert "BTC" in balances
        assert balances["BTC"].free == Decimal("0.5")
        assert balances["BTC"].used == Decimal("0.1")
        assert balances["BTC"].total == Decimal("0.6")

    @pytest.mark.asyncio
    async def test_empty_balances(self, adapter):
        mock_resp = {"code": "00000", "data": []}
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
    async def test_fee_rate_defaults(self, adapter):
        fee = await adapter._rest_get_fee_rate("BTC/USDT")
        assert fee.maker == Decimal("0.001")
        assert fee.taker == Decimal("0.001")
        assert fee.exchange_id == "bitget"

    @pytest.mark.asyncio
    async def test_positions_empty(self, adapter):
        positions = await adapter._rest_get_positions()
        assert positions == []


# ---------------------------------------------------------------------------
# BUG-214: hedge_mode position netting regression tests
# ---------------------------------------------------------------------------

class TestHedgeModePositionNetting:
    """BUG-214: Bitget V3 hedge_mode returns separate long + short rows per
    symbol. Prior behaviour emitted two Position objects which caused
    reconciler dedup collisions and apparent 2× size doubling when downstream
    code aggregated by (exchange_id, symbol). Now netted into a single
    Position with signed size (long positive, short negative)."""

    @pytest.fixture
    def futures_adapter(self):
        rate_limits = {
            "default": RateLimitConfig(requests_per_second=10000, burst=10000),
            "order": RateLimitConfig(requests_per_second=10000, burst=10000),
        }
        adp = NativeBitgetAdapter(
            api_key="k", api_secret="s", passphrase="p",
            exchange_id="bitget_futures", rate_limits=rate_limits,
        )
        adp._market_type = "futures"
        return adp

    @pytest.mark.asyncio
    async def test_v3_hedge_both_sides_open_nets_to_single_position(self, futures_adapter):
        """When hedge-mode returns long=338 and short=339, net to size=-1 (single Position)."""
        v3_resp = {
            "code": "00000",
            "data": {
                "list": [
                    {"symbol": "BLURUSDT", "posSide": "long", "total": "338",
                     "avgPrice": "0.20", "markPrice": "0.19",
                     "unrealisedPnl": "-5.0", "leverage": "5"},
                    {"symbol": "BLURUSDT", "posSide": "short", "total": "339",
                     "avgPrice": "0.20", "markPrice": "0.19",
                     "unrealisedPnl": "3.0", "leverage": "5"},
                ],
                "cursor": "",
            },
        }
        futures_adapter._request = AsyncMock(return_value=v3_resp)
        with patch.object(futures_adapter, "_is_uta", return_value=True):
            positions = await futures_adapter._rest_get_positions()

        assert len(positions) == 1, \
            f"expected 1 netted position, got {len(positions)}: {positions}"
        assert positions[0].symbol == "BLUR/USDT"
        assert positions[0].size == Decimal("-1")  # 338 + (-339) = -1 (net short)

    @pytest.mark.asyncio
    async def test_v3_hedge_single_side_unchanged(self, futures_adapter):
        """Non-hedge / single-side case: behaviour matches pre-fix (-339)."""
        v3_resp = {
            "code": "00000",
            "data": {
                "list": [
                    {"symbol": "BLURUSDT", "posSide": "short", "total": "339",
                     "avgPrice": "0.20", "markPrice": "0.19",
                     "unrealisedPnl": "3.0", "leverage": "5"},
                ],
                "cursor": "",
            },
        }
        futures_adapter._request = AsyncMock(return_value=v3_resp)
        with patch.object(futures_adapter, "_is_uta", return_value=True):
            positions = await futures_adapter._rest_get_positions()

        assert len(positions) == 1
        assert positions[0].size == Decimal("-339")

    @pytest.mark.asyncio
    async def test_v3_hedge_perfectly_hedged_net_zero_returns_empty(self, futures_adapter):
        """Long 339 + Short 339 = net-zero exposure → no Position reported."""
        v3_resp = {
            "code": "00000",
            "data": {
                "list": [
                    {"symbol": "BLURUSDT", "posSide": "long", "total": "339",
                     "avgPrice": "0.20", "markPrice": "0.19",
                     "unrealisedPnl": "0", "leverage": "5"},
                    {"symbol": "BLURUSDT", "posSide": "short", "total": "339",
                     "avgPrice": "0.20", "markPrice": "0.19",
                     "unrealisedPnl": "0", "leverage": "5"},
                ],
                "cursor": "",
            },
        }
        futures_adapter._request = AsyncMock(return_value=v3_resp)
        with patch.object(futures_adapter, "_is_uta", return_value=True):
            positions = await futures_adapter._rest_get_positions()

        assert positions == []

    @pytest.mark.asyncio
    async def test_v2_holdSide_hedge_netting(self, futures_adapter):
        """V2 endpoint (holdSide field, data=list) also nets correctly."""
        v2_resp = {
            "code": "00000",
            "data": [
                {"symbol": "BLURUSDT", "holdSide": "long", "total": "338",
                 "averageOpenPrice": "0.20", "markPrice": "0.19",
                 "unrealizedPL": "-5.0", "leverage": "5"},
                {"symbol": "BLURUSDT", "holdSide": "short", "total": "339",
                 "averageOpenPrice": "0.20", "markPrice": "0.19",
                 "unrealizedPL": "3.0", "leverage": "5"},
            ],
        }
        futures_adapter._request = AsyncMock(return_value=v2_resp)
        with patch.object(futures_adapter, "_is_uta", return_value=False):
            positions = await futures_adapter._rest_get_positions()

        assert len(positions) == 1
        assert positions[0].size == Decimal("-1")
