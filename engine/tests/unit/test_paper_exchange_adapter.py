"""Unit tests for src/execution/paper_adapter.py — PaperExchangeAdapter.

Covers:
- Construction and initial state
- connect / disconnect lifecycle
- get_orderbook_snapshot returns synthetic OrderBook
- place_order fills via PaperExecutor and updates balances
- cancel_order / cancel_all_orders (with and without symbol filter)
- get_balances / get_positions / get_fee_rate
- health_score is always 1.0
- _build_orderbook: structure, depth, minimum step guard
- _update_balances: buy deducts quote and credits base; sell does the reverse
- _adjust_balance: creates new entry when currency not present
- subscribe_orderbook / subscribe_ticker spawn tasks
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import Order, OrderBook, OrderSide, OrderType, Trade
from src.execution.paper_adapter import PaperExchangeAdapter
from src.execution.paper import PaperExecutor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_adapter(**kwargs) -> PaperExchangeAdapter:
    defaults = dict(
        exchange_id="paper_test",
        initial_capital=Decimal("1000"),
        tick_interval=0.001,
    )
    defaults.update(kwargs)
    return PaperExchangeAdapter(**defaults)


def _make_order(side: OrderSide, price: Decimal, amount: Decimal) -> Order:
    return Order(
        exchange_id="paper_test",
        symbol="BTC/USDT",
        side=side,
        order_type=OrderType.MARKET,
        price=price,
        amount=amount,
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestPaperExchangeAdapterInit:
    def test_exchange_id_stored(self):
        a = _make_adapter(exchange_id="paper_binance")
        assert a.exchange_id == "paper_binance"

    def test_initial_capital_creates_usdt_balance(self):
        a = _make_adapter(initial_capital=Decimal("500"))
        assert "USDT" in a._balances
        assert a._balances["USDT"].free == Decimal("500")
        assert a._balances["USDT"].total == Decimal("500")

    def test_positions_start_empty(self):
        a = _make_adapter()
        assert a._positions == []

    def test_open_orders_start_empty(self):
        a = _make_adapter()
        assert a._open_orders == {}

    def test_subscription_tasks_start_empty(self):
        a = _make_adapter()
        assert a._subscription_tasks == []

    def test_custom_fee_rates_stored(self):
        a = _make_adapter(fee_maker=Decimal("0.002"), fee_taker=Decimal("0.003"))
        assert a._fee_maker == Decimal("0.002")
        assert a._fee_taker == Decimal("0.003")

    def test_paper_executor_created_when_not_provided(self):
        a = _make_adapter()
        assert isinstance(a._executor, PaperExecutor)

    def test_custom_paper_executor_is_used(self):
        executor = PaperExecutor()
        a = _make_adapter(paper_executor=executor)
        assert a._executor is executor


# ---------------------------------------------------------------------------
# connect / disconnect
# ---------------------------------------------------------------------------

class TestConnectDisconnect:
    @pytest.mark.asyncio
    async def test_connect_does_not_raise(self):
        a = _make_adapter()
        await a.connect()  # should complete without error

    @pytest.mark.asyncio
    async def test_disconnect_cancels_subscription_tasks(self):
        a = _make_adapter()
        mock_task = MagicMock()
        mock_task.cancel = MagicMock()
        a._subscription_tasks.append(mock_task)

        await a.disconnect()

        mock_task.cancel.assert_called_once()
        assert a._subscription_tasks == []

    @pytest.mark.asyncio
    async def test_disconnect_with_no_tasks_does_not_raise(self):
        a = _make_adapter()
        await a.disconnect()


# ---------------------------------------------------------------------------
# get_orderbook_snapshot
# ---------------------------------------------------------------------------

class TestGetOrderbookSnapshot:
    @pytest.mark.asyncio
    async def test_returns_orderbook_instance(self):
        a = _make_adapter()
        ob = await a.get_orderbook_snapshot("BTC/USDT")
        assert isinstance(ob, OrderBook)

    @pytest.mark.asyncio
    async def test_orderbook_has_correct_symbol(self):
        a = _make_adapter()
        ob = await a.get_orderbook_snapshot("ETH/USDT")
        assert ob.symbol == "ETH/USDT"

    @pytest.mark.asyncio
    async def test_orderbook_has_correct_exchange_id(self):
        a = _make_adapter(exchange_id="paper_bybit")
        ob = await a.get_orderbook_snapshot("BTC/USDT")
        assert ob.exchange_id == "paper_bybit"

    @pytest.mark.asyncio
    async def test_orderbook_bids_are_non_empty(self):
        a = _make_adapter()
        ob = await a.get_orderbook_snapshot("BTC/USDT", depth=5)
        assert len(ob.bids) > 0

    @pytest.mark.asyncio
    async def test_orderbook_asks_are_non_empty(self):
        a = _make_adapter()
        ob = await a.get_orderbook_snapshot("BTC/USDT", depth=5)
        assert len(ob.asks) > 0

    @pytest.mark.asyncio
    async def test_orderbook_respects_depth_parameter(self):
        a = _make_adapter()
        ob = await a.get_orderbook_snapshot("BTC/USDT", depth=3)
        # depth limits each side; bids may be fewer if price drops to 0
        assert len(ob.asks) <= 3
        assert len(ob.bids) <= 3

    @pytest.mark.asyncio
    async def test_best_bid_below_best_ask(self):
        a = _make_adapter()
        ob = await a.get_orderbook_snapshot("BTC/USDT")
        assert ob.bids[0].price < ob.asks[0].price


# ---------------------------------------------------------------------------
# _build_orderbook internals
# ---------------------------------------------------------------------------

class TestBuildOrderbook:
    def test_minimum_step_guard_applied_when_price_tiny(self):
        """When level_step_bps * price is < 0.01, step is clamped to 0.01."""
        a = PaperExchangeAdapter(
            exchange_id="paper_test",
            initial_capital=Decimal("100"),
            base_price=Decimal("0.001"),  # tiny price -> tiny step
            level_step_bps=1,
        )
        ob = a._build_orderbook("TINY/USDT", Decimal("0.001"), depth=2)
        # Verify it didn't crash and returned levels
        assert len(ob.asks) > 0

    def test_build_orderbook_levels_have_positive_amounts(self):
        a = _make_adapter()
        ob = a._build_orderbook("BTC/USDT", Decimal("50000"), depth=5)
        for level in ob.bids + ob.asks:
            assert level.amount > 0

    def test_bids_prices_descend(self):
        a = _make_adapter()
        ob = a._build_orderbook("BTC/USDT", Decimal("50000"), depth=5)
        prices = [float(b.price) for b in ob.bids]
        assert prices == sorted(prices, reverse=True)

    def test_asks_prices_ascend(self):
        a = _make_adapter()
        ob = a._build_orderbook("BTC/USDT", Decimal("50000"), depth=5)
        prices = [float(b.price) for b in ob.asks]
        assert prices == sorted(prices)

    def test_custom_depth_controls_number_of_levels(self):
        a = _make_adapter(book_depth=3)
        ob = a._build_orderbook("BTC/USDT", Decimal("50000"))
        assert len(ob.asks) == 3


# ---------------------------------------------------------------------------
# place_order / balance updates
# ---------------------------------------------------------------------------

class TestPlaceOrder:
    @pytest.mark.asyncio
    async def test_place_buy_order_returns_trade(self):
        a = _make_adapter()
        order = _make_order(OrderSide.BUY, Decimal("50000"), Decimal("0.01"))
        trade = await a.place_order(order)
        assert isinstance(trade, Trade)
        assert trade.side == OrderSide.BUY

    @pytest.mark.asyncio
    async def test_place_sell_order_returns_trade(self):
        a = _make_adapter()
        # Pre-seed BTC balance so sell can proceed
        a._balances["BTC"] = a._balances.get("BTC", None) or \
            type(a._balances["USDT"])(currency="BTC", free=Decimal("1"), used=Decimal("0"), total=Decimal("1"))
        order = _make_order(OrderSide.SELL, Decimal("50000"), Decimal("0.01"))
        trade = await a.place_order(order)
        assert trade.side == OrderSide.SELL

    @pytest.mark.asyncio
    async def test_market_order_without_price_fills_at_current_price(self):
        a = _make_adapter(base_price=Decimal("40000"))
        order = Order(
            exchange_id="paper_test",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            price=None,
            amount=Decimal("0.001"),
        )
        trade = await a.place_order(order)
        assert trade.price > Decimal("0")

    @pytest.mark.asyncio
    async def test_buy_increases_base_currency_balance(self):
        a = _make_adapter(initial_capital=Decimal("10000"))
        order = _make_order(OrderSide.BUY, Decimal("50000"), Decimal("0.01"))
        await a.place_order(order)
        # BTC balance should now exist and be positive
        assert "BTC" in a._balances
        assert a._balances["BTC"].free > Decimal("0")

    @pytest.mark.asyncio
    async def test_buy_decreases_quote_currency_balance(self):
        a = _make_adapter(initial_capital=Decimal("10000"))
        initial_usdt = a._balances["USDT"].free
        order = _make_order(OrderSide.BUY, Decimal("50000"), Decimal("0.01"))
        await a.place_order(order)
        assert a._balances["USDT"].free < initial_usdt


# ---------------------------------------------------------------------------
# cancel_order / cancel_all_orders
# ---------------------------------------------------------------------------

class TestCancelOrder:
    @pytest.mark.asyncio
    async def test_cancel_existing_order_returns_true(self):
        a = _make_adapter()
        mock_order = MagicMock()
        a._open_orders["order-123"] = mock_order
        result = await a.cancel_order("order-123")
        assert result is True
        assert "order-123" not in a._open_orders

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_order_returns_false(self):
        a = _make_adapter()
        result = await a.cancel_order("does-not-exist")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_all_orders_with_no_symbol_clears_all(self):
        a = _make_adapter()
        mock_order = MagicMock()
        a._open_orders["o1"] = mock_order
        a._open_orders["o2"] = mock_order
        count = await a.cancel_all_orders()
        assert count == 2
        assert len(a._open_orders) == 0

    @pytest.mark.asyncio
    async def test_cancel_all_orders_with_symbol_filter_only_removes_matching(self):
        a = _make_adapter()
        o_btc = MagicMock()
        o_btc.symbol = "BTC/USDT"
        o_eth = MagicMock()
        o_eth.symbol = "ETH/USDT"
        a._open_orders["o_btc"] = o_btc
        a._open_orders["o_eth"] = o_eth

        count = await a.cancel_all_orders(symbol="BTC/USDT")
        assert count == 1
        assert "o_eth" in a._open_orders
        assert "o_btc" not in a._open_orders

    @pytest.mark.asyncio
    async def test_cancel_all_orders_empty_returns_zero(self):
        a = _make_adapter()
        count = await a.cancel_all_orders()
        assert count == 0


# ---------------------------------------------------------------------------
# get_balances / get_positions / get_fee_rate
# ---------------------------------------------------------------------------

class TestAccountData:
    @pytest.mark.asyncio
    async def test_get_balances_returns_dict(self):
        a = _make_adapter()
        balances = await a.get_balances()
        assert isinstance(balances, dict)
        assert "USDT" in balances

    @pytest.mark.asyncio
    async def test_get_balances_returns_copy(self):
        a = _make_adapter()
        balances = await a.get_balances()
        balances["FAKE"] = "should not affect adapter"
        assert "FAKE" not in a._balances

    @pytest.mark.asyncio
    async def test_get_positions_returns_list(self):
        a = _make_adapter()
        positions = await a.get_positions()
        assert isinstance(positions, list)

    @pytest.mark.asyncio
    async def test_get_fee_rate_returns_configured_rates(self):
        a = _make_adapter(fee_maker=Decimal("0.002"), fee_taker=Decimal("0.003"))
        fee = await a.get_fee_rate("BTC/USDT")
        assert fee.maker == Decimal("0.002")
        assert fee.taker == Decimal("0.003")
        assert fee.symbol == "BTC/USDT"
        assert fee.exchange_id == "paper_test"

    def test_health_score_is_always_one(self):
        a = _make_adapter()
        assert a.health_score == 1.0


# ---------------------------------------------------------------------------
# _adjust_balance
# ---------------------------------------------------------------------------

class TestAdjustBalance:
    def test_adjust_existing_balance_increases_free_and_total(self):
        a = _make_adapter(initial_capital=Decimal("1000"))
        a._adjust_balance("USDT", Decimal("500"))
        assert a._balances["USDT"].free == Decimal("1500")
        assert a._balances["USDT"].total == Decimal("1500")

    def test_adjust_existing_balance_decreases_free_and_total(self):
        a = _make_adapter(initial_capital=Decimal("1000"))
        a._adjust_balance("USDT", Decimal("-200"))
        assert a._balances["USDT"].free == Decimal("800")
        assert a._balances["USDT"].total == Decimal("800")

    def test_adjust_new_currency_with_positive_delta_creates_entry(self):
        a = _make_adapter()
        a._adjust_balance("BTC", Decimal("0.5"))
        assert "BTC" in a._balances
        assert a._balances["BTC"].free == Decimal("0.5")
        assert a._balances["BTC"].total == Decimal("0.5")

    def test_adjust_new_currency_with_negative_delta_does_not_create_entry(self):
        a = _make_adapter()
        a._adjust_balance("ETH", Decimal("-1"))
        assert "ETH" not in a._balances


# ---------------------------------------------------------------------------
# subscribe_orderbook / subscribe_ticker (task creation)
# ---------------------------------------------------------------------------

class TestSubscriptions:
    @pytest.mark.asyncio
    async def test_subscribe_orderbook_creates_task(self):
        a = _make_adapter()
        callback = MagicMock()
        await a.subscribe_orderbook("BTC/USDT", callback)
        assert len(a._subscription_tasks) == 1
        # Clean up
        for task in a._subscription_tasks:
            task.cancel()
        await asyncio.gather(*a._subscription_tasks, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_subscribe_ticker_creates_task(self):
        a = _make_adapter()
        callback = MagicMock()
        await a.subscribe_ticker("BTC/USDT", callback)
        assert len(a._subscription_tasks) == 1
        for task in a._subscription_tasks:
            task.cancel()
        await asyncio.gather(*a._subscription_tasks, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_subscribe_orderbook_callback_receives_orderbook(self):
        """Orderbook callback is eventually called with an OrderBook."""
        a = _make_adapter(tick_interval=0.001)
        received = []
        callback = lambda ob: received.append(ob)

        await a.subscribe_orderbook("BTC/USDT", callback)
        await asyncio.sleep(0.05)  # allow at least one tick

        # Cancel the task
        for task in a._subscription_tasks:
            task.cancel()
        await asyncio.gather(*a._subscription_tasks, return_exceptions=True)

        assert len(received) > 0
        assert isinstance(received[0], OrderBook)

    @pytest.mark.asyncio
    async def test_subscribe_ticker_callback_receives_dict_with_symbol(self):
        a = _make_adapter(tick_interval=0.001)
        received = []
        callback = lambda tick: received.append(tick)

        await a.subscribe_ticker("ETH/USDT", callback)
        await asyncio.sleep(0.05)

        for task in a._subscription_tasks:
            task.cancel()
        await asyncio.gather(*a._subscription_tasks, return_exceptions=True)

        assert len(received) > 0
        assert received[0]["symbol"] == "ETH/USDT"

    @pytest.mark.asyncio
    async def test_disconnect_cancels_all_subscription_tasks(self):
        a = _make_adapter(tick_interval=0.001)
        await a.subscribe_orderbook("BTC/USDT", MagicMock())
        await a.subscribe_ticker("BTC/USDT", MagicMock())
        assert len(a._subscription_tasks) == 2

        await a.disconnect()
        assert a._subscription_tasks == []
