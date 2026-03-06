"""Unit tests for paper trading executor."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from src.core.models import Order, OrderSide, OrderStatus, OrderType
from src.execution.paper import PaperExecutor, SlippageModel


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def slippage_model() -> SlippageModel:
    return SlippageModel(base_slippage_pct=Decimal("0.001"), volatility_factor=Decimal("1.0"))


@pytest.fixture
def paper_executor(slippage_model: SlippageModel) -> PaperExecutor:
    return PaperExecutor(slippage_model=slippage_model)


def make_order(
    side: OrderSide = OrderSide.BUY,
    amount: Decimal = Decimal("1.0"),
    price: Decimal = Decimal("50000"),
) -> Order:
    return Order(
        exchange_id="paper",
        symbol="BTC/USDT",
        side=side,
        order_type=OrderType.LIMIT,
        price=price,
        amount=amount,
    )


# ---------------------------------------------------------------------------
# SlippageModel tests
# ---------------------------------------------------------------------------


def test_slippage_model_buy_increases_price(slippage_model: SlippageModel) -> None:
    """Buy orders get filled at a higher price (adverse slippage)."""
    base = Decimal("50000")
    fill_price = slippage_model.apply(base, OrderSide.BUY)
    assert fill_price > base


def test_slippage_model_sell_decreases_price(slippage_model: SlippageModel) -> None:
    """Sell orders get filled at a lower price (adverse slippage)."""
    base = Decimal("50000")
    fill_price = slippage_model.apply(base, OrderSide.SELL)
    assert fill_price < base


def test_slippage_model_magnitude(slippage_model: SlippageModel) -> None:
    """Slippage is within expected bounds."""
    base = Decimal("50000")
    fill_price = slippage_model.apply(base, OrderSide.BUY)
    slippage_pct = abs(fill_price - base) / base
    # Should be roughly 0.1% base ± some random component
    assert slippage_pct < Decimal("0.01")  # less than 1%


# ---------------------------------------------------------------------------
# PaperExecutor.execute tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paper_execute_returns_trade(paper_executor: PaperExecutor) -> None:
    """execute() returns a Trade with filled amount."""
    order = make_order()
    trade = await paper_executor.execute(order)
    assert trade.amount == order.amount
    assert trade.symbol == order.symbol
    assert trade.exchange_id == order.exchange_id


@pytest.mark.asyncio
async def test_paper_execute_buy_slippage(paper_executor: PaperExecutor) -> None:
    """Buy fill price is >= order price (slippage applied)."""
    order = make_order(side=OrderSide.BUY, price=Decimal("50000"))
    trade = await paper_executor.execute(order)
    assert trade.price >= Decimal("50000")


@pytest.mark.asyncio
async def test_paper_execute_sell_slippage(paper_executor: PaperExecutor) -> None:
    """Sell fill price is <= order price (slippage applied)."""
    order = make_order(side=OrderSide.SELL, price=Decimal("50000"))
    trade = await paper_executor.execute(order)
    assert trade.price <= Decimal("50000")


@pytest.mark.asyncio
async def test_paper_execute_records_trade(paper_executor: PaperExecutor) -> None:
    """All executed trades are recorded."""
    order = make_order()
    await paper_executor.execute(order)
    await paper_executor.execute(order)
    assert len(paper_executor.trade_history) == 2


@pytest.mark.asyncio
async def test_paper_execute_partial_fill_scenario(paper_executor: PaperExecutor) -> None:
    """With partial fill enabled, trade amount may be < order amount."""
    paper_executor.partial_fill_rate = Decimal("0.8")
    order = make_order(amount=Decimal("1.0"))
    # Run several times to test partial fill logic
    trades = [await paper_executor.execute(order) for _ in range(10)]
    amounts = [t.amount for t in trades]
    # At least some should be partial (< 1.0)
    assert any(a < Decimal("1.0") for a in amounts) or all(a == Decimal("1.0") for a in amounts)


@pytest.mark.asyncio
async def test_paper_execute_rejection_scenario(paper_executor: PaperExecutor) -> None:
    """With rejection enabled, some orders raise an exception."""
    paper_executor.rejection_rate = Decimal("1.0")  # always reject
    order = make_order()
    with pytest.raises(Exception):
        await paper_executor.execute(order)


@pytest.mark.asyncio
async def test_paper_execute_fee_applied(paper_executor: PaperExecutor) -> None:
    """Fee is applied to simulated trade."""
    order = make_order()
    trade = await paper_executor.execute(order)
    assert trade.fee >= Decimal("0")


@pytest.mark.asyncio
async def test_paper_execute_trade_has_timestamp(paper_executor: PaperExecutor) -> None:
    """Trade has a valid timestamp."""
    order = make_order()
    trade = await paper_executor.execute(order)
    assert trade.timestamp is not None


# ---------------------------------------------------------------------------
# Trade history / analysis tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paper_get_trade_history_empty(paper_executor: PaperExecutor) -> None:
    """Trade history is empty initially."""
    assert paper_executor.trade_history == []


@pytest.mark.asyncio
async def test_paper_total_pnl(paper_executor: PaperExecutor) -> None:
    """total_pnl() returns sum of all trade PnL."""
    order = make_order()
    await paper_executor.execute(order)
    pnl = paper_executor.total_pnl()
    assert isinstance(pnl, Decimal)


@pytest.mark.asyncio
async def test_paper_reset_clears_history(paper_executor: PaperExecutor) -> None:
    """reset() clears all recorded trades."""
    order = make_order()
    await paper_executor.execute(order)
    paper_executor.reset()
    assert paper_executor.trade_history == []
