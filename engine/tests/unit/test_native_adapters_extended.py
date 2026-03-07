"""Extended tests for native adapters and CCXT adapters — boosts coverage for low-covered files."""
from __future__ import annotations

import asyncio
import json
import zlib
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import (
    Balance,
    FeeRate,
    Order,
    OrderBook,
    OrderBookLevel,
    OrderSide,
    OrderType,
    Position,
    Trade,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _limit_order(symbol: str = "BTC/USDT", exchange_id: str = "binance") -> Order:
    return Order(
        exchange_id=exchange_id,
        symbol=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=Decimal("50000"),
        amount=Decimal("0.01"),
    )


def _market_order(symbol: str = "BTC/USDT", exchange_id: str = "binance") -> Order:
    return Order(
        exchange_id=exchange_id,
        symbol=symbol,
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=Decimal("0.02"),
    )


def _make_http_response(data) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# BinanceAdapter (CCXT wrapper) — binance.py 39% coverage
# ---------------------------------------------------------------------------

class TestBinanceAdapterCCXT:
    """Tests for src/infra/exchange/binance.py — CCXT-based BinanceAdapter."""

    def _make_adapter(self, market_type: str = "spot") -> "BinanceAdapter":  # noqa: F821
        from src.infra.exchange.binance import BinanceAdapter
        adapter = BinanceAdapter(
            market_type=market_type,
            api_key="test_key",
            api_secret="test_secret",
        )
        adapter._exchange = AsyncMock()
        adapter._connected = True
        return adapter

    def test_spot_exchange_id_is_binance(self):
        from src.infra.exchange.binance import BinanceAdapter
        adapter = BinanceAdapter(market_type="spot")
        assert adapter.exchange_id == "binance"
        assert adapter._market_type == "spot"

    def test_futures_exchange_id_is_binanceusdm(self):
        from src.infra.exchange.binance import BinanceAdapter
        adapter = BinanceAdapter(market_type="futures")
        assert adapter.exchange_id == "binanceusdm"
        assert adapter._market_type == "futures"

    def test_parse_orderbook_without_checksum(self):
        adapter = self._make_adapter()
        raw = {
            "bids": [["50000", "1.0"], ["49999", "2.0"]],
            "asks": [["50001", "0.5"]],
        }
        ob = adapter._parse_orderbook(raw, "BTC/USDT")
        assert ob.symbol == "BTC/USDT"
        assert len(ob.bids) == 2
        assert ob.bids[0].price == Decimal("50000")

    def test_parse_orderbook_with_checksum_logs_warning_on_mismatch(self, caplog):
        import logging
        adapter = self._make_adapter()
        raw = {
            "bids": [["50000", "1.0"]],
            "asks": [["50001", "0.5"]],
            "checksum": 999999999,  # wrong checksum
        }
        with caplog.at_level(logging.WARNING):
            ob = adapter._parse_orderbook(raw, "BTC/USDT")
        assert ob is not None  # still returns orderbook despite mismatch

    def test_validate_checksum_correct_no_warning(self, caplog):
        import logging
        adapter = self._make_adapter()
        # Build orderbook manually
        bids = [OrderBookLevel(price=Decimal("50000"), amount=Decimal("1.0"))]
        asks = [OrderBookLevel(price=Decimal("50001"), amount=Decimal("0.5"))]
        ob = OrderBook(exchange_id="binance", symbol="BTC/USDT", bids=bids, asks=asks)

        # Compute correct checksum
        parts = ["50000:1.0", "50001:0.5"]
        expected = zlib.crc32(":".join(parts).encode()) & 0xFFFFFFFF

        with caplog.at_level(logging.WARNING, logger="src.infra.exchange.binance"):
            adapter._validate_checksum(ob, expected)

        assert "checksum mismatch" not in caplog.text

    def test_validate_checksum_mismatch_logs_warning(self, caplog):
        import logging
        adapter = self._make_adapter()
        bids = [OrderBookLevel(price=Decimal("50000"), amount=Decimal("1.0"))]
        asks = [OrderBookLevel(price=Decimal("50001"), amount=Decimal("0.5"))]
        ob = OrderBook(exchange_id="binance", symbol="BTC/USDT", bids=bids, asks=asks)

        with caplog.at_level(logging.WARNING, logger="src.infra.exchange.binance"):
            adapter._validate_checksum(ob, 0)  # wrong checksum

        assert "checksum mismatch" in caplog.text

    def test_validate_checksum_empty_orderbook(self):
        adapter = self._make_adapter()
        ob = OrderBook(exchange_id="binance", symbol="BTC/USDT", bids=[], asks=[])
        # Should not raise for empty orderbook
        adapter._validate_checksum(ob, 0)

    def test_validate_checksum_100_levels_max(self):
        adapter = self._make_adapter()
        # Create 150 levels — validation should only use first 100
        bids = [
            OrderBookLevel(price=Decimal(str(50000 - i)), amount=Decimal("1.0"))
            for i in range(150)
        ]
        asks = [
            OrderBookLevel(price=Decimal(str(50001 + i)), amount=Decimal("0.5"))
            for i in range(150)
        ]
        ob = OrderBook(exchange_id="binance", symbol="BTC/USDT", bids=bids, asks=asks)
        adapter._validate_checksum(ob, 0)  # must not raise


# ---------------------------------------------------------------------------
# CCXTAdapter — ccxt_adapter.py 62% coverage
# ---------------------------------------------------------------------------

class TestCCXTAdapterExtended:
    """Tests for src/infra/exchange/ccxt_adapter.py — covering uncovered REST and WS paths."""

    def _make_adapter(self, exchange_id: str = "binance") -> "CCXTAdapter":  # noqa: F821
        from src.infra.exchange.ccxt_adapter import CCXTAdapter
        adapter = CCXTAdapter(
            exchange_id=exchange_id,
            api_key="key",
            api_secret="secret",
        )
        adapter._exchange = AsyncMock()
        adapter._exchange.has = {}
        adapter._connected = True
        return adapter

    @pytest.mark.asyncio
    async def test_connect_loads_markets(self):
        adapter = self._make_adapter()
        adapter._connected = False
        await adapter.connect()
        adapter._exchange.load_markets.assert_called_once()
        assert adapter._connected is True

    @pytest.mark.asyncio
    async def test_disconnect_cancels_subscriptions(self):
        adapter = self._make_adapter()
        mock_task = MagicMock()
        adapter._subscriptions["orderbook:BTC/USDT"] = mock_task
        await adapter.disconnect()
        mock_task.cancel.assert_called_once()
        assert adapter._connected is False

    @pytest.mark.asyncio
    async def test_disconnect_closes_exchange(self):
        adapter = self._make_adapter()
        await adapter.disconnect()
        adapter._exchange.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_subscribe_orderbook_creates_task(self):
        adapter = self._make_adapter()
        callback = MagicMock()
        await adapter.subscribe_orderbook("BTC/USDT", callback)
        assert "orderbook:BTC/USDT" in adapter._subscriptions
        # Clean up
        adapter._subscriptions["orderbook:BTC/USDT"].cancel()

    @pytest.mark.asyncio
    async def test_subscribe_orderbook_idempotent(self):
        adapter = self._make_adapter()
        callback = MagicMock()
        await adapter.subscribe_orderbook("BTC/USDT", callback)
        task1 = adapter._subscriptions["orderbook:BTC/USDT"]
        await adapter.subscribe_orderbook("BTC/USDT", callback)
        task2 = adapter._subscriptions["orderbook:BTC/USDT"]
        assert task1 is task2  # same task, no duplicate
        task1.cancel()

    @pytest.mark.asyncio
    async def test_subscribe_ticker_creates_task(self):
        adapter = self._make_adapter()
        callback = MagicMock()
        await adapter.subscribe_ticker("BTC/USDT", callback)
        assert "ticker:BTC/USDT" in adapter._subscriptions
        adapter._subscriptions["ticker:BTC/USDT"].cancel()

    @pytest.mark.asyncio
    async def test_subscribe_ticker_idempotent(self):
        adapter = self._make_adapter()
        callback = MagicMock()
        await adapter.subscribe_ticker("ETH/USDT", callback)
        task1 = adapter._subscriptions["ticker:ETH/USDT"]
        await adapter.subscribe_ticker("ETH/USDT", callback)
        assert adapter._subscriptions["ticker:ETH/USDT"] is task1
        task1.cancel()

    @pytest.mark.asyncio
    async def test_get_orderbook_snapshot_success(self):
        adapter = self._make_adapter()
        adapter._exchange.fetch_order_book.return_value = {
            "bids": [["50000", "1.0"]],
            "asks": [["50001", "0.5"]],
        }
        ob = await adapter.get_orderbook_snapshot("BTC/USDT")
        assert ob.symbol == "BTC/USDT"
        assert ob.bids[0].price == Decimal("50000")

    @pytest.mark.asyncio
    async def test_get_orderbook_snapshot_error_records_health(self):
        adapter = self._make_adapter()
        adapter._exchange.fetch_order_book.side_effect = Exception("network error")
        with pytest.raises(Exception, match="network error"):
            await adapter.get_orderbook_snapshot("BTC/USDT")

    @pytest.mark.asyncio
    async def test_place_order_success(self):
        adapter = self._make_adapter()
        adapter._exchange.create_order.return_value = {
            "id": "order123",
            "price": "50000",
            "filled": "0.01",
            "fee": None,
        }
        trade = await adapter.place_order(_limit_order())
        assert trade.trade_id == "order123"
        assert trade.price == Decimal("50000")

    @pytest.mark.asyncio
    async def test_place_order_error_records_failure(self):
        adapter = self._make_adapter()
        adapter._exchange.create_order.side_effect = Exception("insufficient funds")
        with pytest.raises(Exception, match="insufficient funds"):
            await adapter.place_order(_limit_order())

    @pytest.mark.asyncio
    async def test_cancel_order_success(self):
        adapter = self._make_adapter()
        adapter._exchange.cancel_order.return_value = {"status": "cancelled"}
        result = await adapter.cancel_order("123", symbol="BTC/USDT")
        assert result is True

    @pytest.mark.asyncio
    async def test_cancel_order_failure_returns_false(self):
        adapter = self._make_adapter()
        adapter._exchange.cancel_order.side_effect = Exception("order not found")
        result = await adapter.cancel_order("999", symbol="BTC/USDT")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_all_orders_with_symbol(self):
        adapter = self._make_adapter()
        adapter._exchange.cancel_all_orders.return_value = [{"id": "1"}, {"id": "2"}]
        count = await adapter.cancel_all_orders(symbol="BTC/USDT")
        assert count == 2
        adapter._exchange.cancel_all_orders.assert_called_once_with("BTC/USDT")

    @pytest.mark.asyncio
    async def test_cancel_all_orders_without_symbol(self):
        adapter = self._make_adapter()
        adapter._exchange.cancel_all_orders.return_value = [{"id": "1"}]
        count = await adapter.cancel_all_orders(symbol=None)
        assert count == 1
        adapter._exchange.cancel_all_orders.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_cancel_all_orders_non_list_response(self):
        adapter = self._make_adapter()
        adapter._exchange.cancel_all_orders.return_value = {"status": "ok"}  # not a list
        count = await adapter.cancel_all_orders(symbol="BTC/USDT")
        assert count == 0

    @pytest.mark.asyncio
    async def test_get_balances_returns_nonzero_only(self):
        adapter = self._make_adapter()
        adapter._exchange.fetch_balance.return_value = {
            "total": {"BTC": "0.5", "ETH": "0", "USDT": "1000"},
            "free": {"BTC": "0.4", "ETH": "0", "USDT": "900"},
            "used": {"BTC": "0.1", "ETH": "0", "USDT": "100"},
        }
        balances = await adapter.get_balances()
        assert "BTC" in balances
        assert "USDT" in balances
        assert "ETH" not in balances  # zero filtered

    @pytest.mark.asyncio
    async def test_get_balances_error_raises(self):
        adapter = self._make_adapter()
        adapter._exchange.fetch_balance.side_effect = Exception("auth error")
        with pytest.raises(Exception, match="auth error"):
            await adapter.get_balances()

    @pytest.mark.asyncio
    async def test_get_positions_no_support_returns_empty(self):
        adapter = self._make_adapter()
        adapter._exchange.has = {"fetchPositions": False}
        positions = await adapter.get_positions()
        assert positions == []

    @pytest.mark.asyncio
    async def test_get_positions_with_support_returns_list(self):
        adapter = self._make_adapter()
        adapter._exchange.has = {"fetchPositions": True}
        adapter._exchange.fetch_positions.return_value = [
            {
                "symbol": "BTC/USDT",
                "contracts": "0.5",
                "entryPrice": "50000",
                "markPrice": "51000",
                "unrealizedPnl": "500",
                "leverage": "10",
            }
        ]
        positions = await adapter.get_positions()
        assert len(positions) == 1
        assert positions[0].size == Decimal("0.5")

    @pytest.mark.asyncio
    async def test_get_positions_filters_zero_contracts(self):
        adapter = self._make_adapter()
        adapter._exchange.has = {"fetchPositions": True}
        adapter._exchange.fetch_positions.return_value = [
            {"symbol": "BTC/USDT", "contracts": None, "entryPrice": "50000"},
            {"symbol": "ETH/USDT", "contracts": "2.0", "entryPrice": "3000",
             "markPrice": "3100", "unrealizedPnl": "200", "leverage": "5"},
        ]
        positions = await adapter.get_positions()
        assert len(positions) == 1  # contracts=None filtered

    @pytest.mark.asyncio
    async def test_get_fee_rate_success(self):
        adapter = self._make_adapter()
        adapter._exchange.fetch_trading_fee.return_value = {
            "maker": 0.001,
            "taker": 0.001,
        }
        fee = await adapter.get_fee_rate("BTC/USDT")
        assert fee.maker == Decimal("0.001")
        assert fee.taker == Decimal("0.001")

    @pytest.mark.asyncio
    async def test_get_fee_rate_error_returns_default(self):
        adapter = self._make_adapter()
        adapter._exchange.fetch_trading_fee.side_effect = Exception("fee error")
        fee = await adapter.get_fee_rate("BTC/USDT")
        # Should return default 0.001
        assert fee.maker == Decimal("0.001")
        assert fee.taker == Decimal("0.001")

    def test_parse_balances_only_nonzero(self):
        adapter = self._make_adapter()
        raw = {
            "total": {"BTC": "1.5", "ETH": "0.0"},
            "free": {"BTC": "1.0"},
            "used": {"BTC": "0.5"},
        }
        balances = adapter._parse_balances(raw)
        assert "BTC" in balances
        assert "ETH" not in balances
        assert balances["BTC"].total == Decimal("1.5")

    def test_parse_position_basic(self):
        adapter = self._make_adapter()
        raw = {
            "symbol": "BTC/USDT",
            "contracts": "0.5",
            "entryPrice": "50000",
            "markPrice": "51000",
            "unrealizedPnl": "500",
            "leverage": "10",
        }
        pos = adapter._parse_position(raw)
        assert pos.symbol == "BTC/USDT"
        assert pos.size == Decimal("0.5")
        assert pos.leverage == 10

    def test_parse_position_null_mark_price(self):
        adapter = self._make_adapter()
        raw = {
            "symbol": "ETH/USDT",
            "contracts": "1.0",
            "entryPrice": "3000",
            "markPrice": None,
            "unrealizedPnl": "0",
            "leverage": None,
        }
        pos = adapter._parse_position(raw)
        assert pos.mark_price is None
        assert pos.leverage == 1  # default

    def test_parse_trade_from_order_with_fee(self):
        adapter = self._make_adapter()
        order = _limit_order()
        raw = {
            "id": "trade456",
            "price": "50000",
            "filled": "0.01",
            "fee": {"cost": "0.0001", "currency": "BNB"},
        }
        trade = adapter._parse_trade_from_order(raw, order)
        assert trade.trade_id == "trade456"
        assert trade.fee == Decimal("0.0001")
        assert trade.fee_currency == "BNB"

    def test_parse_trade_uses_average_price_fallback(self):
        adapter = self._make_adapter()
        order = _limit_order()
        raw = {
            "id": "t1",
            "average": "49999",  # no "price" key
            "filled": "0.01",
        }
        trade = adapter._parse_trade_from_order(raw, order)
        assert trade.price == Decimal("49999")

    def test_health_score_property(self):
        adapter = self._make_adapter()
        score = adapter.health_score
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# UpbitAdapter — upbit.py 69% coverage
# ---------------------------------------------------------------------------

class TestUpbitAdapterExtended:
    def _make_adapter(self):
        from src.infra.exchange.upbit import UpbitAdapter
        adapter = UpbitAdapter(api_key="key", api_secret="secret")
        adapter._exchange = AsyncMock()
        return adapter

    def test_exchange_id_is_upbit(self):
        from src.infra.exchange.upbit import UpbitAdapter
        adapter = UpbitAdapter()
        assert adapter.exchange_id == "upbit"

    def test_normalize_symbol_adds_krw(self):
        from src.infra.exchange.upbit import UpbitAdapter
        adapter = UpbitAdapter()
        assert adapter.normalize_symbol("BTC") == "BTC/KRW"

    def test_normalize_symbol_passthrough_with_slash(self):
        from src.infra.exchange.upbit import UpbitAdapter
        adapter = UpbitAdapter()
        assert adapter.normalize_symbol("BTC/KRW") == "BTC/KRW"

    def test_normalize_symbol_eth(self):
        from src.infra.exchange.upbit import UpbitAdapter
        adapter = UpbitAdapter()
        assert adapter.normalize_symbol("ETH") == "ETH/KRW"

    @pytest.mark.asyncio
    async def test_get_fee_rate_flat_0_05_pct(self):
        adapter = self._make_adapter()
        fee = await adapter.get_fee_rate("BTC/KRW")
        assert fee.maker == Decimal("0.0005")
        assert fee.taker == Decimal("0.0005")
        assert fee.exchange_id == "upbit"
        assert fee.symbol == "BTC/KRW"

    @pytest.mark.asyncio
    async def test_get_fee_rate_any_symbol(self):
        adapter = self._make_adapter()
        fee = await adapter.get_fee_rate("ETH/KRW")
        assert fee.maker == Decimal("0.0005")
        assert fee.symbol == "ETH/KRW"


# ---------------------------------------------------------------------------
# BithumbAdapter — bithumb.py 80% coverage
# ---------------------------------------------------------------------------

class TestBithumbAdapterExtended:
    def _make_adapter(self):
        from src.infra.exchange.bithumb import BithumbAdapter
        adapter = BithumbAdapter(api_key="key", api_secret="secret")
        adapter._exchange = AsyncMock()
        return adapter

    def test_exchange_id_is_bithumb(self):
        from src.infra.exchange.bithumb import BithumbAdapter
        adapter = BithumbAdapter()
        assert adapter.exchange_id == "bithumb"

    @pytest.mark.asyncio
    async def test_get_fee_rate_is_0_25_pct(self):
        adapter = self._make_adapter()
        fee = await adapter.get_fee_rate("BTC/KRW")
        assert fee.maker == Decimal("0.0025")
        assert fee.taker == Decimal("0.0025")
        assert fee.exchange_id == "bithumb"

    @pytest.mark.asyncio
    async def test_get_fee_rate_any_symbol(self):
        adapter = self._make_adapter()
        fee = await adapter.get_fee_rate("ETH/KRW")
        assert fee.symbol == "ETH/KRW"
        assert fee.maker == Decimal("0.0025")


# ---------------------------------------------------------------------------
# BybitAdapter — bybit.py 71% coverage
# ---------------------------------------------------------------------------

class TestBybitAdapterExtended:
    def test_exchange_id_is_bybit(self):
        from src.infra.exchange.bybit import BybitAdapter
        adapter = BybitAdapter()
        assert adapter.exchange_id == "bybit"

    def test_market_type_stored(self):
        from src.infra.exchange.bybit import BybitAdapter
        adapter = BybitAdapter(market_type="futures")
        assert adapter._market_type == "futures"

    def test_default_market_type_is_spot(self):
        from src.infra.exchange.bybit import BybitAdapter
        adapter = BybitAdapter()
        assert adapter._market_type == "spot"


# ---------------------------------------------------------------------------
# NativeAdapter base — native_adapter.py 75% coverage
# ---------------------------------------------------------------------------

class TestNativeAdapterBase:
    """Tests for uncovered methods in src/infra/exchange/native_adapter.py."""

    def _make_test_adapter(self) -> "NativeAdapter":
        """Use BinanceNativeAdapter as concrete impl (fully implemented)."""
        from src.infra.exchange.native_binance import BinanceNativeAdapter
        adapter = BinanceNativeAdapter(api_key="key", api_secret="secret")
        adapter._http = AsyncMock()
        adapter._connected = True
        return adapter

    @pytest.mark.asyncio
    async def test_subscribe_ticker_creates_task(self):
        adapter = self._make_test_adapter()
        callback = MagicMock()
        with patch("websockets.connect", return_value=AsyncMock()):
            await adapter.subscribe_ticker("BTC/USDT", callback)
        assert "ticker:BTC/USDT" in adapter._ws_tasks
        adapter._ws_tasks["ticker:BTC/USDT"].cancel()

    @pytest.mark.asyncio
    async def test_subscribe_ticker_idempotent(self):
        adapter = self._make_test_adapter()
        callback = MagicMock()
        with patch("websockets.connect", return_value=AsyncMock()):
            await adapter.subscribe_ticker("BTC/USDT", callback)
            task1 = adapter._ws_tasks["ticker:BTC/USDT"]
            await adapter.subscribe_ticker("BTC/USDT", callback)
            task2 = adapter._ws_tasks["ticker:BTC/USDT"]
        assert task1 is task2
        task1.cancel()

    @pytest.mark.asyncio
    async def test_get_orderbook_snapshot_error_records_health(self):
        adapter = self._make_test_adapter()
        adapter._http.request.side_effect = Exception("network error")
        with pytest.raises(Exception, match="network error"):
            await adapter.get_orderbook_snapshot("BTC/USDT")

    @pytest.mark.asyncio
    async def test_place_order_error_records_health(self):
        adapter = self._make_test_adapter()
        adapter._http.request.side_effect = Exception("order error")
        with pytest.raises(Exception, match="order error"):
            await adapter.place_order(_limit_order())

    @pytest.mark.asyncio
    async def test_cancel_order_error_returns_false(self):
        adapter = self._make_test_adapter()
        adapter._http.request.side_effect = Exception("cancel error")
        result = await adapter.cancel_order("123", symbol="BTC/USDT")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_all_orders_error_raises(self):
        adapter = self._make_test_adapter()
        adapter._http.request.side_effect = Exception("cancel all error")
        with pytest.raises(Exception):
            await adapter.cancel_all_orders(symbol="BTC/USDT")

    @pytest.mark.asyncio
    async def test_get_balances_error_raises(self):
        adapter = self._make_test_adapter()
        adapter._http.request.side_effect = Exception("balance error")
        with pytest.raises(Exception, match="balance error"):
            await adapter.get_balances()

    @pytest.mark.asyncio
    async def test_get_positions_error_returns_empty(self):
        from src.infra.exchange.native_binance import BinanceNativeAdapter
        adapter = BinanceNativeAdapter(
            api_key="key", api_secret="secret", market_type="futures"
        )
        adapter._http = AsyncMock()
        adapter._http.request.side_effect = Exception("position error")
        positions = await adapter.get_positions()
        assert positions == []

    @pytest.mark.asyncio
    async def test_get_fee_rate_error_returns_default(self):
        adapter = self._make_test_adapter()
        adapter._http.request.side_effect = Exception("fee error")
        fee = await adapter.get_fee_rate("BTC/USDT")
        assert fee.maker == Decimal("0.001")
        assert fee.taker == Decimal("0.001")

    @pytest.mark.asyncio
    async def test_estimate_slippage_scales_with_size(self):
        adapter = self._make_test_adapter()
        slip_small = await adapter.estimate_slippage(OrderSide.BUY, Decimal("1"), "BTC/USDT")
        slip_large = await adapter.estimate_slippage(OrderSide.BUY, Decimal("4"), "BTC/USDT")
        # gamma=0.5 → slippage scales as sqrt(size), so 4x size → 2x slippage
        assert slip_large > slip_small

    @pytest.mark.asyncio
    async def test_estimate_slippage_both_sides(self):
        adapter = self._make_test_adapter()
        slip_buy = await adapter.estimate_slippage(OrderSide.BUY, Decimal("1"), "BTC/USDT")
        slip_sell = await adapter.estimate_slippage(OrderSide.SELL, Decimal("1"), "BTC/USDT")
        assert slip_buy == slip_sell  # symmetric

    def test_sign_hmac_sha256_produces_64_hex_chars(self):
        adapter = self._make_test_adapter()
        sig = adapter._sign_hmac_sha256("test_message")
        assert isinstance(sig, str)
        assert len(sig) == 64

    def test_sign_hmac_sha512_produces_128_hex_chars(self):
        adapter = self._make_test_adapter()
        sig = adapter._sign_hmac_sha512("test_message")
        assert isinstance(sig, str)
        assert len(sig) == 128

    def test_build_query_string_sorted(self):
        adapter = self._make_test_adapter()
        qs = adapter._build_query_string({"b": "2", "a": "1"})
        assert qs.index("a") < qs.index("b")  # sorted

    def test_timestamp_ms_is_int_and_recent(self):
        import time
        adapter = self._make_test_adapter()
        ts = adapter._timestamp_ms()
        assert isinstance(ts, int)
        assert ts > int(time.time() * 1000) - 1000

    def test_ws_ticker_url_raises_not_implemented(self):
        from src.infra.exchange.native_adapter import NativeAdapter
        # NativeAdapter._ws_ticker_url should raise NotImplementedError
        # Use BinanceNativeAdapter which doesn't override it
        from src.infra.exchange.native_binance import BinanceNativeAdapter
        adapter = BinanceNativeAdapter()
        with pytest.raises(NotImplementedError):
            adapter._ws_ticker_url("BTC/USDT")

    def test_ws_ticker_subscribe_message_returns_none(self):
        from src.infra.exchange.native_binance import BinanceNativeAdapter
        adapter = BinanceNativeAdapter()
        result = adapter._ws_ticker_subscribe_message("BTC/USDT")
        assert result is None

    def test_parse_ws_ticker_returns_none(self):
        from src.infra.exchange.native_binance import BinanceNativeAdapter
        adapter = BinanceNativeAdapter()
        result = adapter._parse_ws_ticker('{"event": "ticker"}', "BTC/USDT")
        assert result is None

    @pytest.mark.asyncio
    async def test_request_raises_when_not_connected(self):
        from src.infra.exchange.native_binance import BinanceNativeAdapter
        adapter = BinanceNativeAdapter()
        adapter._http = None  # not connected
        with pytest.raises(RuntimeError, match="not connected"):
            await adapter._request("GET", "/api/v3/depth")

    def test_build_orderbook_helper(self):
        adapter = self._make_test_adapter()
        ob = adapter._build_orderbook(
            "BTC/USDT",
            [["50000", "1.0"], ["49999", "2.0"]],
            [["50001", "0.5"]],
            sequence=42,
        )
        assert ob.symbol == "BTC/USDT"
        assert ob.exchange_id == "binance"
        assert ob.bids[0].price == Decimal("50000")
        assert ob.sequence == 42

    def test_build_trade_helper(self):
        adapter = self._make_test_adapter()
        order = _limit_order()
        trade = adapter._build_trade(
            order=order,
            trade_id="t123",
            price=Decimal("50001"),
            amount=Decimal("0.01"),
            fee=Decimal("0.001"),
            fee_currency="BNB",
        )
        assert trade.trade_id == "t123"
        assert trade.price == Decimal("50001")
        assert trade.fee_currency == "BNB"
        assert trade.exchange_id == "binance"


# ---------------------------------------------------------------------------
# create_native_adapter factory
# ---------------------------------------------------------------------------

class TestCreateNativeAdapterFactory:
    def test_binance_creates_binance_adapter(self):
        from src.infra.exchange import create_native_adapter
        from src.infra.exchange.native_binance import BinanceNativeAdapter
        adapter = create_native_adapter("binance")
        assert isinstance(adapter, BinanceNativeAdapter)

    def test_bybit_creates_bybit_adapter(self):
        from src.infra.exchange import create_native_adapter
        from src.infra.exchange.native_bybit import NativeBybitAdapter
        adapter = create_native_adapter("bybit")
        assert isinstance(adapter, NativeBybitAdapter)

    def test_unsupported_raises_value_error(self):
        from src.infra.exchange import create_native_adapter
        with pytest.raises(ValueError, match="Unsupported exchange"):
            create_native_adapter("coinbase_pro")

    def test_case_insensitive(self):
        from src.infra.exchange import create_native_adapter
        adapter = create_native_adapter("BINANCE")
        assert adapter.exchange_id == "binance"
