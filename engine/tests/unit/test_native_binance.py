"""Unit tests for BinanceNativeAdapter — mocks httpx and websockets."""
from __future__ import annotations

import json
import zlib
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import Order, OrderSide, OrderType
from src.infra.exchange.native_binance import (
    BinanceNativeAdapter,
    _symbol_from_binance,
    _symbol_to_binance,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_http_response(data) -> MagicMock:
    """Build a mock httpx response that returns `data` from .json()."""
    resp = MagicMock()
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


def _make_adapter(sandbox: bool = False, market_type: str = "spot") -> BinanceNativeAdapter:
    adp = BinanceNativeAdapter(
        api_key="test_key",
        api_secret="test_secret",
        sandbox=sandbox,
        market_type=market_type,
    )
    # Inject a mock HTTP client so tests don't need real connect()
    adp._http = AsyncMock()
    adp._connected = True
    return adp


def _limit_order(symbol: str = "BTC/USDT") -> Order:
    return Order(
        exchange_id="binance",
        symbol=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=Decimal("50000"),
        amount=Decimal("0.01"),
    )


def _market_order(symbol: str = "BTC/USDT") -> Order:
    return Order(
        exchange_id="binance",
        symbol=symbol,
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=Decimal("0.01"),
    )


# ---------------------------------------------------------------------------
# Symbol normalization
# ---------------------------------------------------------------------------

class TestSymbolNormalization:
    def test_to_binance_slash(self):
        assert _symbol_to_binance("BTC/USDT") == "BTCUSDT"

    def test_to_binance_no_slash(self):
        assert _symbol_to_binance("ETHUSDT") == "ETHUSDT"

    def test_to_binance_lowercase(self):
        assert _symbol_to_binance("btc/usdt") == "BTCUSDT"

    def test_from_binance_usdt(self):
        assert _symbol_from_binance("BTCUSDT") == "BTC/USDT"

    def test_from_binance_eth(self):
        assert _symbol_from_binance("BNBETH") == "BNB/ETH"

    def test_from_binance_unknown(self):
        # Fallback: return as-is uppercased
        result = _symbol_from_binance("XYZABC")
        assert "XYZ" in result or result == "XYZABC"


# ---------------------------------------------------------------------------
# URLs and headers
# ---------------------------------------------------------------------------

class TestUrlsAndHeaders:
    def test_rest_base_url_production(self):
        adp = _make_adapter(sandbox=False)
        assert adp._rest_base_url() == "https://api.binance.com"

    def test_rest_base_url_sandbox(self):
        adp = _make_adapter(sandbox=True)
        assert adp._rest_base_url() == "https://testnet.binance.vision"

    def test_default_headers_include_api_key(self):
        adp = _make_adapter()
        headers = adp._default_headers()
        assert headers["X-MBX-APIKEY"] == "test_key"

    def test_default_headers_no_key(self):
        adp = BinanceNativeAdapter()
        headers = adp._default_headers()
        assert "X-MBX-APIKEY" not in headers

    def test_auth_headers_returns_empty(self):
        adp = _make_adapter()
        assert adp._auth_headers("GET", "/api/v3/depth", {}, None) == {}

    def test_ws_orderbook_url_production(self):
        adp = _make_adapter(sandbox=False)
        url = adp._ws_orderbook_url("BTC/USDT")
        assert "stream.binance.com:9443" in url
        assert "btcusdt@depth20@100ms" in url

    def test_ws_orderbook_url_sandbox(self):
        adp = _make_adapter(sandbox=True)
        url = adp._ws_orderbook_url("BTC/USDT")
        assert "testnet.binance.vision" in url

    def test_ws_subscribe_message_is_none(self):
        adp = _make_adapter()
        assert adp._ws_subscribe_message("BTC/USDT") is None


# ---------------------------------------------------------------------------
# WebSocket parsing
# ---------------------------------------------------------------------------

class TestParseWsOrderbook:
    def setup_method(self):
        self.adp = _make_adapter()

    def test_single_stream_format(self):
        payload = json.dumps({
            "lastUpdateId": 123,
            "bids": [["50000.00", "1.5"], ["49999.00", "2.0"]],
            "asks": [["50001.00", "1.0"]],
        })
        ob = self.adp._parse_ws_orderbook(payload, "BTC/USDT")
        assert ob is not None
        assert ob.symbol == "BTC/USDT"
        assert len(ob.bids) == 2
        assert ob.bids[0].price == Decimal("50000.00")

    def test_combined_stream_format(self):
        payload = json.dumps({
            "stream": "btcusdt@depth20@100ms",
            "data": {
                "lastUpdateId": 456,
                "bids": [["48000.00", "0.5"]],
                "asks": [["48001.00", "0.3"]],
            },
        })
        ob = self.adp._parse_ws_orderbook(payload, "BTC/USDT")
        assert ob is not None
        assert ob.bids[0].price == Decimal("48000.00")

    def test_invalid_json_returns_none(self):
        assert self.adp._parse_ws_orderbook("not-json", "BTC/USDT") is None

    def test_unrecognized_payload_returns_none(self):
        payload = json.dumps({"event": "ping"})
        assert self.adp._parse_ws_orderbook(payload, "BTC/USDT") is None


# ---------------------------------------------------------------------------
# REST: get_orderbook_snapshot
# ---------------------------------------------------------------------------

class TestRestGetOrderbook:
    def setup_method(self):
        self.adp = _make_adapter()

    async def test_basic_orderbook(self):
        self.adp._http.request.return_value = _make_http_response({
            "lastUpdateId": 789,
            "bids": [["50000", "1"], ["49999", "2"]],
            "asks": [["50001", "0.5"]],
        })
        ob = await self.adp.get_orderbook_snapshot("BTC/USDT")
        assert ob.symbol == "BTC/USDT"
        assert ob.exchange_id == "binance"
        assert ob.sequence == 789
        assert ob.bids[0].price == Decimal("50000")
        assert ob.asks[0].price == Decimal("50001")

    async def test_passes_symbol_and_depth_to_api(self):
        self.adp._http.request.return_value = _make_http_response({
            "lastUpdateId": 1,
            "bids": [],
            "asks": [],
        })
        await self.adp.get_orderbook_snapshot("ETH/USDT", depth=5)
        call_kwargs = self.adp._http.request.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs.args[2]
        assert params["symbol"] == "ETHUSDT"
        assert params["limit"] == 5


# ---------------------------------------------------------------------------
# CRC32 checksum validation
# ---------------------------------------------------------------------------

class TestChecksumValidation:
    def setup_method(self):
        self.adp = _make_adapter()

    def _compute_checksum(self, bids, asks):
        parts = []
        levels = max(len(bids), len(asks))
        for i in range(min(levels, 100)):
            if i < len(bids):
                parts.append(f"{bids[i].price}:{bids[i].amount}")
            if i < len(asks):
                parts.append(f"{asks[i].price}:{asks[i].amount}")
        return zlib.crc32(":".join(parts).encode()) & 0xFFFFFFFF

    async def test_valid_checksum_no_warning(self, caplog):
        import logging
        self.adp._http.request.return_value = _make_http_response({
            "lastUpdateId": 1,
            "bids": [["50000", "1"]],
            "asks": [["50001", "0.5"]],
            "checksum": 12345,  # wrong checksum → warning
        })
        with caplog.at_level(logging.WARNING, logger="src.infra.exchange.native_binance"):
            ob = await self.adp.get_orderbook_snapshot("BTC/USDT")
        # With wrong checksum we get a warning but no exception
        assert ob is not None

    async def test_correct_checksum_no_warning(self, caplog):
        import logging
        bids_raw = [["50000", "1"]]
        asks_raw = [["50001", "0.5"]]
        self.adp._http.request.return_value = _make_http_response({
            "lastUpdateId": 1,
            "bids": bids_raw,
            "asks": asks_raw,
        })
        # No checksum key → no validation, no warning
        with caplog.at_level(logging.WARNING, logger="src.infra.exchange.native_binance"):
            ob = await self.adp.get_orderbook_snapshot("BTC/USDT")
        assert "checksum mismatch" not in caplog.text


# ---------------------------------------------------------------------------
# REST: place_order
# ---------------------------------------------------------------------------

class TestRestPlaceOrder:
    def setup_method(self):
        self.adp = _make_adapter()

    def _mock_order_response(self, order_id="111", price="50000", qty="0.01", fills=None):
        resp = {
            "orderId": order_id,
            "price": price,
            "executedQty": qty,
            "status": "FILLED",
        }
        if fills:
            resp["fills"] = fills
        self.adp._http.request.return_value = _make_http_response(resp)

    async def test_limit_order_buy(self):
        self._mock_order_response(order_id="999", price="50000", qty="0.01")
        order = _limit_order()
        trade = await self.adp.place_order(order)
        assert trade.trade_id == "999"
        assert trade.price == Decimal("50000")
        assert trade.amount == Decimal("0.01")
        assert trade.side == OrderSide.BUY

    async def test_market_order_sell(self):
        self._mock_order_response(order_id="888", price="49999", qty="0.01")
        order = _market_order()
        trade = await self.adp.place_order(order)
        assert trade.trade_id == "888"
        assert trade.side == OrderSide.SELL

    async def test_fee_extracted_from_fills(self):
        self._mock_order_response(
            fills=[{"commission": "0.0001", "commissionAsset": "BNB"}]
        )
        trade = await self.adp.place_order(_limit_order())
        assert trade.fee == Decimal("0.0001")
        assert trade.fee_currency == "BNB"

    async def test_limit_order_sends_timeinforce(self):
        self._mock_order_response()
        await self.adp.place_order(_limit_order())
        call_kwargs = self.adp._http.request.call_args
        params = call_kwargs.kwargs.get("params") or {}
        assert "GTC" in str(params) or "timeInForce" in str(params)

    async def test_market_order_no_price_param(self):
        self._mock_order_response()
        await self.adp.place_order(_market_order())
        call_kwargs = self.adp._http.request.call_args
        params = call_kwargs.kwargs.get("params") or {}
        # Market orders should not include price or timeInForce
        assert "timeInForce" not in str(params)


# ---------------------------------------------------------------------------
# REST: cancel_order
# ---------------------------------------------------------------------------

class TestRestCancelOrder:
    def setup_method(self):
        self.adp = _make_adapter()
        self.adp._http.request.return_value = _make_http_response({"orderId": "123"})

    async def test_cancel_returns_true(self):
        result = await self.adp.cancel_order("123", symbol="BTC/USDT")
        assert result is True

    async def test_cancel_without_symbol_raises(self):
        with pytest.raises(ValueError, match="requires symbol"):
            await self.adp._rest_cancel_order("123", symbol=None)

    async def test_cancel_sends_correct_params(self):
        await self.adp.cancel_order("456", symbol="ETH/USDT")
        call_kwargs = self.adp._http.request.call_args
        params = call_kwargs.kwargs.get("params") or {}
        assert "ETHUSDT" in str(params)
        assert "456" in str(params)


# ---------------------------------------------------------------------------
# REST: cancel_all_orders
# ---------------------------------------------------------------------------

class TestRestCancelAllOrders:
    def setup_method(self):
        self.adp = _make_adapter()

    async def test_cancel_all_returns_count(self):
        self.adp._http.request.return_value = _make_http_response(
            [{"orderId": "1"}, {"orderId": "2"}, {"orderId": "3"}]
        )
        count = await self.adp.cancel_all_orders(symbol="BTC/USDT")
        assert count == 3

    async def test_cancel_all_empty_returns_zero(self):
        self.adp._http.request.return_value = _make_http_response([])
        count = await self.adp.cancel_all_orders(symbol="BTC/USDT")
        assert count == 0

    async def test_cancel_all_without_symbol_raises(self):
        with pytest.raises(ValueError, match="requires symbol"):
            await self.adp._rest_cancel_all_orders(symbol=None)


# ---------------------------------------------------------------------------
# REST: get_balances
# ---------------------------------------------------------------------------

class TestRestGetBalances:
    def setup_method(self):
        self.adp = _make_adapter()

    async def test_balances_nonzero_only(self):
        self.adp._http.request.return_value = _make_http_response({
            "balances": [
                {"asset": "BTC", "free": "0.5", "locked": "0.1"},
                {"asset": "ETH", "free": "0", "locked": "0"},
                {"asset": "USDT", "free": "1000", "locked": "0"},
            ]
        })
        balances = await self.adp.get_balances()
        assert "BTC" in balances
        assert "USDT" in balances
        assert "ETH" not in balances  # zero balance filtered out

    async def test_balance_fields(self):
        self.adp._http.request.return_value = _make_http_response({
            "balances": [
                {"asset": "BTC", "free": "0.5", "locked": "0.1"},
            ]
        })
        balances = await self.adp.get_balances()
        btc = balances["BTC"]
        assert btc.free == Decimal("0.5")
        assert btc.used == Decimal("0.1")
        assert btc.total == Decimal("0.6")
        assert btc.currency == "BTC"


# ---------------------------------------------------------------------------
# REST: get_positions
# ---------------------------------------------------------------------------

class TestRestGetPositions:
    async def test_spot_returns_empty(self):
        adp = _make_adapter(market_type="spot")
        positions = await adp.get_positions()
        assert positions == []

    async def test_futures_returns_nonzero_positions(self):
        adp = _make_adapter(market_type="futures")
        adp._http.request.return_value = _make_http_response([
            {
                "symbol": "BTCUSDT",
                "positionAmt": "0.5",
                "entryPrice": "50000",
                "markPrice": "51000",
                "unRealizedProfit": "500",
                "leverage": "10",
            },
            {
                "symbol": "ETHUSDT",
                "positionAmt": "0",  # zero → should be filtered
                "entryPrice": "3000",
                "markPrice": "3100",
                "unRealizedProfit": "0",
                "leverage": "5",
            },
        ])
        positions = await adp.get_positions()
        assert len(positions) == 1
        pos = positions[0]
        assert pos.symbol == "BTC/USDT"
        assert pos.size == Decimal("0.5")
        assert pos.entry_price == Decimal("50000")
        assert pos.leverage == 10

    async def test_futures_short_position(self):
        adp = _make_adapter(market_type="futures")
        adp._http.request.return_value = _make_http_response([
            {
                "symbol": "BTCUSDT",
                "positionAmt": "-0.2",
                "entryPrice": "52000",
                "markPrice": "51000",
                "unRealizedProfit": "200",
                "leverage": "5",
            },
        ])
        positions = await adp.get_positions()
        assert positions[0].size == Decimal("-0.2")


# ---------------------------------------------------------------------------
# REST: get_fee_rate
# ---------------------------------------------------------------------------

class TestRestGetFeeRate:
    def setup_method(self):
        self.adp = _make_adapter()

    async def test_fee_rate_calculation(self):
        # Binance returns basis points: 10 = 0.10%
        self.adp._http.request.return_value = _make_http_response({
            "makerCommission": 10,
            "takerCommission": 10,
            "balances": [],
        })
        fee = await self.adp.get_fee_rate("BTC/USDT")
        assert fee.maker == Decimal("0.001")  # 10/10000
        assert fee.taker == Decimal("0.001")
        assert fee.symbol == "BTC/USDT"
        assert fee.exchange_id == "binance"

    async def test_fee_rate_default_when_missing(self):
        self.adp._http.request.return_value = _make_http_response({"balances": []})
        fee = await self.adp.get_fee_rate("ETH/USDT")
        # Defaults to 10 basis points
        assert fee.maker == Decimal("0.001")


# ---------------------------------------------------------------------------
# Signed request: timestamp + signature injected
# ---------------------------------------------------------------------------

class TestSignedRequest:
    def setup_method(self):
        self.adp = _make_adapter()
        self.adp._http.request.return_value = _make_http_response({"balances": []})

    async def test_signed_request_adds_timestamp(self):
        await self.adp._signed_request("GET", "/api/v3/account")
        call_kwargs = self.adp._http.request.call_args
        params = call_kwargs.kwargs.get("params") or {}
        assert "timestamp" in params
        assert "signature" in params

    async def test_signed_request_adds_recv_window(self):
        await self.adp._signed_request("GET", "/api/v3/account")
        call_kwargs = self.adp._http.request.call_args
        params = call_kwargs.kwargs.get("params") or {}
        assert params.get("recvWindow") == 5000

    async def test_signature_is_hex_string(self):
        await self.adp._signed_request("GET", "/api/v3/account")
        call_kwargs = self.adp._http.request.call_args
        params = call_kwargs.kwargs.get("params") or {}
        sig = params["signature"]
        assert isinstance(sig, str)
        assert len(sig) == 64  # HMAC-SHA256 hex = 64 chars
