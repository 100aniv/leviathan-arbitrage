"""Tests for CCXTAdapter with mocked ccxt responses."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import Order, OrderSide, OrderType
from src.infra.exchange.ccxt_adapter import CCXTAdapter
from src.infra.exchange.health_checker import HealthChecker
from src.infra.exchange.rate_limiter import ExchangeRateLimiter, RateLimitConfig


@pytest.fixture
def mock_ccxt_exchange():
    """Mock ccxt.pro exchange instance."""
    exchange = MagicMock()
    exchange.has = {"fetchPositions": True, "fetchTradingFee": True}
    exchange.load_markets = AsyncMock(return_value={})
    exchange.close = AsyncMock()
    exchange.fetch_order_book = AsyncMock(
        return_value={
            "bids": [[50000.0, 1.0], [49999.0, 2.0]],
            "asks": [[50001.0, 0.5], [50002.0, 1.0]],
            "timestamp": 1704067200000,
        }
    )
    exchange.fetch_balance = AsyncMock(
        return_value={
            "total": {"USDT": 10000.0, "BTC": 0.5},
            "free": {"USDT": 8000.0, "BTC": 0.4},
            "used": {"USDT": 2000.0, "BTC": 0.1},
        }
    )
    exchange.create_order = AsyncMock(
        return_value={
            "id": "order-001",
            "price": 50000.0,
            "amount": 0.001,
            "filled": 0.001,
            "average": 50000.0,
            "fee": {"cost": 0.05, "currency": "USDT"},
        }
    )
    exchange.cancel_order = AsyncMock(return_value={"id": "order-001"})
    exchange.cancel_all_orders = AsyncMock(return_value=[{"id": "o1"}, {"id": "o2"}])
    exchange.fetch_positions = AsyncMock(return_value=[])
    exchange.fetch_trading_fee = AsyncMock(return_value={"maker": 0.001, "taker": 0.001})
    return exchange


@pytest.fixture
def ccxt_adapter(mock_ccxt_exchange):
    """CCXTAdapter with mocked ccxt exchange, bypassing __init__."""
    adapter = CCXTAdapter.__new__(CCXTAdapter)
    adapter.exchange_id = "binance"
    adapter._sandbox = False
    adapter._health = HealthChecker("binance")
    adapter._rate_limiter = ExchangeRateLimiter(
        "binance",
        {
            "default": RateLimitConfig(requests_per_second=1000, burst=1000),
            "order": RateLimitConfig(requests_per_second=1000, burst=1000),
        },
    )
    adapter._exchange = mock_ccxt_exchange
    adapter._subscriptions = {}
    adapter._connected = False
    return adapter


class TestCCXTAdapterConnect:
    @pytest.mark.asyncio
    async def test_connect_loads_markets(self, ccxt_adapter, mock_ccxt_exchange):
        await ccxt_adapter.connect()
        mock_ccxt_exchange.load_markets.assert_called_once()
        assert ccxt_adapter._connected is True

    @pytest.mark.asyncio
    async def test_disconnect_closes_exchange(self, ccxt_adapter, mock_ccxt_exchange):
        ccxt_adapter._connected = True
        await ccxt_adapter.disconnect()
        mock_ccxt_exchange.close.assert_called_once()
        assert ccxt_adapter._connected is False


class TestCCXTAdapterOrderBook:
    @pytest.mark.asyncio
    async def test_get_orderbook_snapshot(self, ccxt_adapter, mock_ccxt_exchange):
        ob = await ccxt_adapter.get_orderbook_snapshot("BTC/USDT", depth=20)
        assert ob.exchange_id == "binance"
        assert ob.symbol == "BTC/USDT"
        assert ob.best_bid == Decimal("50000")
        assert ob.best_ask == Decimal("50001")
        mock_ccxt_exchange.fetch_order_book.assert_called_once_with("BTC/USDT", 20)

    @pytest.mark.asyncio
    async def test_orderbook_bids_asks_parsed(self, ccxt_adapter):
        ob = await ccxt_adapter.get_orderbook_snapshot("BTC/USDT")
        assert len(ob.bids) == 2
        assert len(ob.asks) == 2
        assert ob.bids[0].amount == Decimal("1.0")
        assert ob.asks[0].amount == Decimal("0.5")


class TestCCXTAdapterBalances:
    @pytest.mark.asyncio
    async def test_get_balances(self, ccxt_adapter, mock_ccxt_exchange):
        balances = await ccxt_adapter.get_balances()
        assert "USDT" in balances
        assert "BTC" in balances
        assert balances["USDT"].free == Decimal("8000")
        assert balances["USDT"].used == Decimal("2000")
        assert balances["USDT"].total == Decimal("10000")

    @pytest.mark.asyncio
    async def test_get_balances_filters_zero(self, ccxt_adapter, mock_ccxt_exchange):
        mock_ccxt_exchange.fetch_balance.return_value = {
            "total": {"USDT": 0.0, "BTC": 0.5},
            "free": {"USDT": 0.0, "BTC": 0.4},
            "used": {"USDT": 0.0, "BTC": 0.1},
        }
        balances = await ccxt_adapter.get_balances()
        assert "USDT" not in balances  # zero balance filtered out
        assert "BTC" in balances


class TestCCXTAdapterOrders:
    @pytest.mark.asyncio
    async def test_place_limit_order(self, ccxt_adapter, mock_ccxt_exchange):
        order = Order(
            exchange_id="binance",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("50000"),
            amount=Decimal("0.001"),
        )
        trade = await ccxt_adapter.place_order(order)
        assert trade.trade_id == "order-001"
        assert trade.amount == Decimal("0.001")
        mock_ccxt_exchange.create_order.assert_called_once_with(
            "BTC/USDT", "limit", "buy", 0.001, 50000.0
        )

    @pytest.mark.asyncio
    async def test_place_market_order(self, ccxt_adapter, mock_ccxt_exchange):
        order = Order(
            exchange_id="binance",
            symbol="BTC/USDT",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            amount=Decimal("0.001"),
        )
        trade = await ccxt_adapter.place_order(order)
        assert trade.trade_id == "order-001"
        # Market orders pass None price
        call_args = mock_ccxt_exchange.create_order.call_args
        assert call_args[0][4] is None  # price is None

    @pytest.mark.asyncio
    async def test_cancel_order(self, ccxt_adapter, mock_ccxt_exchange):
        result = await ccxt_adapter.cancel_order("order-001", "BTC/USDT")
        assert result is True
        mock_ccxt_exchange.cancel_order.assert_called_once_with("order-001", "BTC/USDT")

    @pytest.mark.asyncio
    async def test_cancel_order_on_exception_returns_false(self, ccxt_adapter, mock_ccxt_exchange):
        mock_ccxt_exchange.cancel_order.side_effect = Exception("Order not found")
        result = await ccxt_adapter.cancel_order("missing-id")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_all_orders(self, ccxt_adapter, mock_ccxt_exchange):
        count = await ccxt_adapter.cancel_all_orders("BTC/USDT")
        assert count == 2
        mock_ccxt_exchange.cancel_all_orders.assert_called_once_with("BTC/USDT")

    @pytest.mark.asyncio
    async def test_cancel_all_orders_no_symbol(self, ccxt_adapter, mock_ccxt_exchange):
        count = await ccxt_adapter.cancel_all_orders()
        assert count == 2
        mock_ccxt_exchange.cancel_all_orders.assert_called_once_with()


class TestCCXTAdapterFeeAndPositions:
    @pytest.mark.asyncio
    async def test_get_fee_rate(self, ccxt_adapter, mock_ccxt_exchange):
        fee = await ccxt_adapter.get_fee_rate("BTC/USDT")
        assert fee.maker == Decimal("0.001")
        assert fee.taker == Decimal("0.001")
        assert fee.symbol == "BTC/USDT"

    @pytest.mark.asyncio
    async def test_get_fee_rate_fallback_on_error(self, ccxt_adapter, mock_ccxt_exchange):
        mock_ccxt_exchange.fetch_trading_fee.side_effect = Exception("Not supported")
        fee = await ccxt_adapter.get_fee_rate("BTC/USDT")
        # Should return defaults, not raise
        assert fee.maker == Decimal("0.001")
        assert fee.taker == Decimal("0.001")

    @pytest.mark.asyncio
    async def test_get_positions_empty(self, ccxt_adapter, mock_ccxt_exchange):
        positions = await ccxt_adapter.get_positions()
        assert positions == []

    @pytest.mark.asyncio
    async def test_get_positions_unsupported(self, ccxt_adapter, mock_ccxt_exchange):
        mock_ccxt_exchange.has = {"fetchPositions": False}
        positions = await ccxt_adapter.get_positions()
        assert positions == []


class TestCCXTAdapterHealth:
    def test_initial_health_score_in_range(self, ccxt_adapter):
        score = ccxt_adapter.health_score
        assert 0.0 <= score <= 1.0

    @pytest.mark.asyncio
    async def test_health_score_improves_after_connect(self, ccxt_adapter, mock_ccxt_exchange):
        initial = ccxt_adapter.health_score
        await ccxt_adapter.connect()
        connected = ccxt_adapter.health_score
        assert connected >= initial
