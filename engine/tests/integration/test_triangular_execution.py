"""Integration tests for 3-leg triangular arbitrage execution pipeline.

Tests the TradeRequest → TradeRequestConsumer → execute_multi_leg path
using MockExchangeAdapter (no external dependencies).
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.models import Order, OrderSide, OrderType, Trade
from src.execution.executor import (
    AtomicExecutor,
    ExecutionConfig,
    ExecutionResult,
    ExecutionStatus,
)
from src.execution.trade_consumer import TradeRequestConsumer, _leg_to_order
from src.risk.kill_switch import clear_halt
from src.strategies.base import TradeLeg, TradeRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_trade(
    exchange_id: str = "binance",
    amount: Decimal = Decimal("1.0"),
    side: OrderSide = OrderSide.BUY,
    order_id: str = "ord_001",
) -> Trade:
    price = Decimal("50000")
    return Trade(
        trade_id=f"trade_{order_id}",
        order_id=order_id,
        exchange_id=exchange_id,
        symbol="BTC/USDT",
        side=side,
        price=price,
        amount=amount,
        fee=price * amount * Decimal("0.001"),
    )


def make_mock_exchange(exchange_id: str = "binance", health: float = 1.0) -> MagicMock:
    ex = MagicMock()
    ex.exchange_id = exchange_id
    ex.health_score = health
    ex.place_order = AsyncMock(return_value=make_trade(exchange_id=exchange_id))
    ex.cancel_order = AsyncMock(return_value=True)
    ex.get_balances = AsyncMock(return_value={})
    ex.get_orderbook_snapshot = AsyncMock(
        return_value=MagicMock(best_ask=Decimal("50001"), best_bid=Decimal("49999"))
    )
    ex.get_positions = AsyncMock(return_value=[])
    return ex


def make_3leg_trade_request(exchange_id: str = "binance") -> TradeRequest:
    """3-leg same-exchange TradeRequest (triangular arb: BTC/USDT → ETH/BTC → ETH/USDT)."""
    return TradeRequest(
        strategy_id="triangular",
        legs=[
            TradeLeg(
                exchange_id=exchange_id, symbol="BTC/USDT",
                side=OrderSide.BUY, size=Decimal("0.01"), price=Decimal("50000"),
            ),
            TradeLeg(
                exchange_id=exchange_id, symbol="ETH/BTC",
                side=OrderSide.SELL, size=Decimal("0.01"), price=Decimal("0.06"),
            ),
            TradeLeg(
                exchange_id=exchange_id, symbol="ETH/USDT",
                side=OrderSide.BUY, size=Decimal("0.01"), price=Decimal("3000"),
            ),
        ],
        expected_profit_usdt=Decimal("1.0"),
    )


def make_2leg_trade_request() -> TradeRequest:
    """2-leg same-exchange TradeRequest (regression: must still use execute_same_exchange)."""
    return TradeRequest(
        strategy_id="same_exchange_2leg",
        legs=[
            TradeLeg(
                exchange_id="binance", symbol="BTC/USDT",
                side=OrderSide.BUY, size=Decimal("0.01"), price=Decimal("50000"),
            ),
            TradeLeg(
                exchange_id="binance", symbol="BTC/USDT",
                side=OrderSide.SELL, size=Decimal("0.01"), price=Decimal("50100"),
            ),
        ],
        expected_profit_usdt=Decimal("0.5"),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_kill_switch():
    clear_halt()
    yield
    clear_halt()


@pytest.fixture
def mock_exchange() -> MagicMock:
    return make_mock_exchange("binance")


@pytest.fixture
def executor(mock_exchange: MagicMock) -> AtomicExecutor:
    return AtomicExecutor(
        exchanges={"binance": mock_exchange},
        config=ExecutionConfig(timeout_ms=500, post_reconcile_delay_s=0.01),
    )


@pytest.fixture
def consumer(executor: AtomicExecutor) -> TradeRequestConsumer:
    return TradeRequestConsumer(event_bus=MagicMock(), executor=executor)


# ---------------------------------------------------------------------------
# Step 4: 3-leg triangular integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_triangular_3_leg_full_execution(
    consumer: TradeRequestConsumer,
) -> None:
    """3-leg same-exchange TradeRequest executes via execute_multi_leg → SUCCESS, 3 legs."""
    trade_req = make_3leg_trade_request("binance")
    orders = [_leg_to_order(leg, trade_req.strategy_id) for leg in trade_req.legs]

    result = await consumer._execute(trade_req, orders)

    assert result.status == ExecutionStatus.SUCCESS
    assert len(result.legs) == 3


@pytest.mark.asyncio
async def test_triangular_leg3_fails_rollback(
    consumer: TradeRequestConsumer, mock_exchange: MagicMock
) -> None:
    """3rd leg failure triggers reverse rollback: legs 2 and 1 unwound in order."""
    call_count = 0

    async def place_side_effect(order: Order) -> Trade:
        nonlocal call_count
        call_count += 1
        if call_count == 3:
            raise RuntimeError("leg3 temporarily unavailable")
        return make_trade(order.exchange_id)

    mock_exchange.place_order = AsyncMock(side_effect=place_side_effect)

    trade_req = make_3leg_trade_request("binance")
    orders = [_leg_to_order(leg, trade_req.strategy_id) for leg in trade_req.legs]

    result = await consumer._execute(trade_req, orders)

    assert result.status in (ExecutionStatus.ROLLED_BACK, ExecutionStatus.ROLLBACK_FAILED)
    # call1=leg1 fill, call2=leg2 fill, call3=leg3 fail,
    # call4=unwind leg2, call5=unwind leg1 → ≥4 total place_order calls
    assert mock_exchange.place_order.call_count >= 4


@pytest.mark.asyncio
async def test_trade_consumer_routes_3_leg_to_multi_leg(
    executor: AtomicExecutor,
) -> None:
    """TradeRequestConsumer._execute routes 3-leg same-exchange request to execute_multi_leg."""
    multi_leg_calls: list[dict] = []
    original = executor.execute_multi_leg

    async def capturing_multi_leg(
        exchange_id: str, orders: list[Order], strategy_id: str
    ) -> ExecutionResult:
        multi_leg_calls.append({"exchange_id": exchange_id, "n_orders": len(orders)})
        return await original(
            exchange_id=exchange_id, orders=orders, strategy_id=strategy_id
        )

    executor.execute_multi_leg = capturing_multi_leg  # type: ignore[method-assign]

    consumer = TradeRequestConsumer(event_bus=MagicMock(), executor=executor)
    trade_req = make_3leg_trade_request("binance")
    orders = [_leg_to_order(leg, trade_req.strategy_id) for leg in trade_req.legs]

    await consumer._execute(trade_req, orders)

    assert len(multi_leg_calls) == 1
    assert multi_leg_calls[0]["exchange_id"] == "binance"
    assert multi_leg_calls[0]["n_orders"] == 3


@pytest.mark.asyncio
async def test_2_leg_still_uses_same_exchange(
    executor: AtomicExecutor,
) -> None:
    """2-leg same-exchange request still routes to execute_same_exchange (regression guard)."""
    same_exchange_calls: list[bool] = []
    original = executor.execute_same_exchange

    async def capturing_same_exchange(
        exchange_id: str, leg1_order: Order, leg2_order: Order, strategy_id: str
    ) -> ExecutionResult:
        same_exchange_calls.append(True)
        return await original(
            exchange_id=exchange_id,
            leg1_order=leg1_order,
            leg2_order=leg2_order,
            strategy_id=strategy_id,
        )

    executor.execute_same_exchange = capturing_same_exchange  # type: ignore[method-assign]

    consumer = TradeRequestConsumer(event_bus=MagicMock(), executor=executor)
    trade_req = make_2leg_trade_request()
    orders = [_leg_to_order(leg, trade_req.strategy_id) for leg in trade_req.legs]

    await consumer._execute(trade_req, orders)

    assert len(same_exchange_calls) == 1
    assert same_exchange_calls[0] is True
