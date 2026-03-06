"""Tests for BitgetAdapter — Spot and Futures."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import Order, OrderSide, OrderType
from src.infra.exchange.bitget import BitgetAdapter
from src.infra.exchange.health_checker import HealthChecker
from src.infra.exchange.rate_limiter import ExchangeRateLimiter, RateLimitConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_ccxt_exchange():
    """Mock ccxt.pro exchange instance for Bitget."""
    exchange = MagicMock()
    exchange.has = {"fetchPositions": True, "fetchTradingFee": True}
    exchange.load_markets = AsyncMock(return_value={})
    exchange.close = AsyncMock()
    exchange.fetch_order_book = AsyncMock(
        return_value={
            "bids": [[30000.0, 1.0], [29999.0, 2.0]],
            "asks": [[30001.0, 0.5], [30002.0, 1.0]],
        }
    )
    exchange.fetch_balance = AsyncMock(
        return_value={
            "total": {"USDT": 5000.0, "BTC": 0.1},
            "free": {"USDT": 4000.0, "BTC": 0.08},
            "used": {"USDT": 1000.0, "BTC": 0.02},
        }
    )
    exchange.create_order = AsyncMock(
        return_value={
            "id": "bg-order-001",
            "price": 30000.0,
            "amount": 0.001,
            "filled": 0.001,
            "average": 30000.0,
            "fee": {"cost": 0.03, "currency": "USDT"},
        }
    )
    exchange.cancel_order = AsyncMock(return_value={"id": "bg-order-001"})
    exchange.cancel_all_orders = AsyncMock(return_value=[{"id": "o1"}, {"id": "o2"}])
    exchange.fetch_positions = AsyncMock(return_value=[])
    exchange.fetch_trading_fee = AsyncMock(return_value={"maker": 0.001, "taker": 0.001})
    return exchange


def _make_adapter(mock_exchange: MagicMock, market_type: str = "spot") -> BitgetAdapter:
    """Bypass __init__ to inject a mock exchange, mirroring test_ccxt_adapter pattern."""
    adapter = BitgetAdapter.__new__(BitgetAdapter)
    adapter.exchange_id = "bitget"
    adapter._market_type = market_type
    adapter._sandbox = False
    adapter._health = HealthChecker("bitget")
    adapter._rate_limiter = ExchangeRateLimiter(
        "bitget",
        {
            "default": RateLimitConfig(requests_per_second=1000, burst=1000),
            "order": RateLimitConfig(requests_per_second=1000, burst=1000),
        },
    )
    adapter._exchange = mock_exchange
    adapter._subscriptions = {}
    adapter._connected = False
    return adapter


@pytest.fixture
def bitget_spot(mock_ccxt_exchange):
    return _make_adapter(mock_ccxt_exchange, "spot")


@pytest.fixture
def bitget_futures(mock_ccxt_exchange):
    return _make_adapter(mock_ccxt_exchange, "futures")


# ---------------------------------------------------------------------------
# Tests: initialisation
# ---------------------------------------------------------------------------


class TestBitgetAdapterInit:
    def test_spot_uses_bitget_exchange_id(self):
        with patch("ccxt.pro.bitget") as mock_cls:
            mock_cls.return_value = MagicMock()
            adapter = BitgetAdapter(market_type="spot")
        assert adapter.exchange_id == "bitget"
        assert adapter._market_type == "spot"

    def test_futures_uses_bitget_exchange_id(self):
        with patch("ccxt.pro.bitget") as mock_cls:
            mock_cls.return_value = MagicMock()
            adapter = BitgetAdapter(market_type="futures")
        assert adapter.exchange_id == "bitget"
        assert adapter._market_type == "futures"

    def test_futures_sets_swap_default_type(self):
        """Futures adapter configures defaultType=swap in exchange options."""
        captured_config: dict = {}

        def capture_init(cfg):
            captured_config.update(cfg)
            return MagicMock()

        with patch("ccxt.pro.bitget", side_effect=capture_init):
            BitgetAdapter(market_type="futures")

        opts = captured_config.get("options", {})
        assert opts.get("defaultType") == "swap"

    def test_spot_does_not_set_swap_default_type(self):
        captured_config: dict = {}

        def capture_init(cfg):
            captured_config.update(cfg)
            return MagicMock()

        with patch("ccxt.pro.bitget", side_effect=capture_init):
            BitgetAdapter(market_type="spot")

        opts = captured_config.get("options", {})
        assert opts.get("defaultType") != "swap"


# ---------------------------------------------------------------------------
# Tests: connection lifecycle
# ---------------------------------------------------------------------------


class TestBitgetAdapterConnection:
    @pytest.mark.asyncio
    async def test_connect_loads_markets(self, bitget_spot, mock_ccxt_exchange):
        await bitget_spot.connect()
        mock_ccxt_exchange.load_markets.assert_called_once()
        assert bitget_spot._connected is True

    @pytest.mark.asyncio
    async def test_disconnect_closes_exchange(self, bitget_spot, mock_ccxt_exchange):
        bitget_spot._connected = True
        await bitget_spot.disconnect()
        mock_ccxt_exchange.close.assert_called_once()
        assert bitget_spot._connected is False


# ---------------------------------------------------------------------------
# Tests: order book
# ---------------------------------------------------------------------------


class TestBitgetAdapterOrderBook:
    @pytest.mark.asyncio
    async def test_get_orderbook_snapshot_returns_correct_prices(self, bitget_spot):
        ob = await bitget_spot.get_orderbook_snapshot("BTC/USDT", depth=20)
        assert ob.exchange_id == "bitget"
        assert ob.symbol == "BTC/USDT"
        assert ob.best_bid == Decimal("30000")
        assert ob.best_ask == Decimal("30001")

    @pytest.mark.asyncio
    async def test_orderbook_levels_count(self, bitget_spot):
        ob = await bitget_spot.get_orderbook_snapshot("BTC/USDT")
        assert len(ob.bids) == 2
        assert len(ob.asks) == 2

    @pytest.mark.asyncio
    async def test_orderbook_futures_same_parsing(self, bitget_futures):
        ob = await bitget_futures.get_orderbook_snapshot("BTC/USDT:USDT")
        assert ob.best_bid == Decimal("30000")
        assert ob.best_ask == Decimal("30001")


# ---------------------------------------------------------------------------
# Tests: balances
# ---------------------------------------------------------------------------


class TestBitgetAdapterBalances:
    @pytest.mark.asyncio
    async def test_get_balances_returns_non_zero_assets(self, bitget_spot):
        balances = await bitget_spot.get_balances()
        assert "USDT" in balances
        assert "BTC" in balances

    @pytest.mark.asyncio
    async def test_balance_values_correct(self, bitget_spot):
        balances = await bitget_spot.get_balances()
        assert balances["USDT"].total == Decimal("5000")
        assert balances["USDT"].free == Decimal("4000")
        assert balances["USDT"].used == Decimal("1000")

    @pytest.mark.asyncio
    async def test_futures_balances(self, bitget_futures):
        balances = await bitget_futures.get_balances()
        assert "USDT" in balances


# ---------------------------------------------------------------------------
# Tests: orders
# ---------------------------------------------------------------------------


class TestBitgetAdapterOrders:
    @pytest.mark.asyncio
    async def test_place_market_order(self, bitget_spot, mock_ccxt_exchange):
        order = Order(
            exchange_id="bitget",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            amount=Decimal("0.001"),
        )
        trade = await bitget_spot.place_order(order)
        assert trade.trade_id == "bg-order-001"
        assert trade.exchange_id == "bitget"
        assert trade.amount == Decimal("0.001")

    @pytest.mark.asyncio
    async def test_place_limit_order(self, bitget_spot, mock_ccxt_exchange):
        order = Order(
            exchange_id="bitget",
            symbol="BTC/USDT",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            price=Decimal("30000"),
            amount=Decimal("0.001"),
        )
        trade = await bitget_spot.place_order(order)
        assert trade.trade_id == "bg-order-001"
        mock_ccxt_exchange.create_order.assert_called_once_with(
            "BTC/USDT", "limit", "sell", 0.001, 30000.0
        )

    @pytest.mark.asyncio
    async def test_cancel_order_success(self, bitget_spot):
        result = await bitget_spot.cancel_order("bg-order-001", "BTC/USDT")
        assert result is True

    @pytest.mark.asyncio
    async def test_cancel_order_exception_returns_false(self, bitget_spot, mock_ccxt_exchange):
        mock_ccxt_exchange.cancel_order.side_effect = Exception("Not found")
        result = await bitget_spot.cancel_order("missing-id")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_all_orders_returns_count(self, bitget_spot):
        count = await bitget_spot.cancel_all_orders("BTC/USDT")
        assert count == 2

    @pytest.mark.asyncio
    async def test_cancel_all_orders_no_symbol(self, bitget_spot, mock_ccxt_exchange):
        count = await bitget_spot.cancel_all_orders()
        assert count == 2
        mock_ccxt_exchange.cancel_all_orders.assert_called_once_with()


# ---------------------------------------------------------------------------
# Tests: fee rate
# ---------------------------------------------------------------------------


class TestBitgetAdapterFeeRate:
    @pytest.mark.asyncio
    async def test_get_fee_rate_returns_values(self, bitget_spot):
        fee = await bitget_spot.get_fee_rate("BTC/USDT")
        assert fee.maker == Decimal("0.001")
        assert fee.taker == Decimal("0.001")
        assert fee.symbol == "BTC/USDT"
        assert fee.exchange_id == "bitget"

    @pytest.mark.asyncio
    async def test_get_fee_rate_fallback_on_error(self, bitget_spot, mock_ccxt_exchange):
        mock_ccxt_exchange.fetch_trading_fee.side_effect = Exception("unsupported")
        fee = await bitget_spot.get_fee_rate("BTC/USDT")
        # Falls back to defaults
        assert fee.maker == Decimal("0.001")
        assert fee.taker == Decimal("0.001")


# ---------------------------------------------------------------------------
# Tests: health
# ---------------------------------------------------------------------------


class TestBitgetAdapterHealth:
    def test_health_score_in_range(self, bitget_spot):
        assert 0.0 <= bitget_spot.health_score <= 1.0

    @pytest.mark.asyncio
    async def test_health_score_after_connect(self, bitget_spot, mock_ccxt_exchange):
        initial = bitget_spot.health_score
        await bitget_spot.connect()
        assert bitget_spot.health_score >= initial
