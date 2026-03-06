"""Unit tests for atomic execution engine (same-exchange + cross-exchange)."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from src.core.models import Order, OrderSide, OrderStatus, OrderType, Trade
from src.execution.executor import (
    AtomicExecutor,
    ExecutionConfig,
    ExecutionResult,
    ExecutionStatus,
    LegResult,
)
from src.risk.kill_switch import clear_halt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_trade(
    exchange_id: str = "binance",
    amount: Decimal = Decimal("1.0"),
    price: Decimal = Decimal("50000"),
    side: OrderSide = OrderSide.BUY,
    order_id: str = "ord_001",
) -> Trade:
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


def make_order(
    exchange_id: str = "binance",
    side: OrderSide = OrderSide.BUY,
    amount: Decimal = Decimal("1.0"),
    price: Decimal = Decimal("50000"),
) -> Order:
    return Order(
        exchange_id=exchange_id,
        symbol="BTC/USDT",
        side=side,
        order_type=OrderType.LIMIT,
        price=price,
        amount=amount,
    )


def make_exchange(
    exchange_id: str = "binance",
    health: float = 1.0,
    fill_status: OrderStatus = OrderStatus.FILLED,
    fill_amount: Decimal = Decimal("1.0"),
) -> MagicMock:
    ex = MagicMock()
    ex.exchange_id = exchange_id
    ex.health_score = health
    trade = make_trade(
        exchange_id=exchange_id,
        amount=fill_amount,
    )
    ex.place_order = AsyncMock(return_value=trade)
    ex.cancel_order = AsyncMock(return_value=True)
    ex.get_balances = AsyncMock(return_value={
        "USDT": MagicMock(free=Decimal("10000"), total=Decimal("10000")),
        "BTC": MagicMock(free=Decimal("1.0"), total=Decimal("1.0")),
    })
    ex.get_orderbook_snapshot = AsyncMock(return_value=MagicMock(
        best_ask=Decimal("50001"),
        best_bid=Decimal("49999"),
    ))
    ex.get_positions = AsyncMock(return_value=[])
    return ex


@pytest.fixture(autouse=True)
def clear_kill_switch():
    """Always clear halt flag before each test."""
    clear_halt()
    yield
    clear_halt()


@pytest.fixture
def config() -> ExecutionConfig:
    return ExecutionConfig(
        timeout_ms=500,
        partial_fill_threshold=Decimal("0.8"),
        post_reconcile_delay_s=0.01,  # short for tests
    )


@pytest.fixture
def exchange_a() -> MagicMock:
    return make_exchange("binance", health=1.0)


@pytest.fixture
def exchange_b() -> MagicMock:
    return make_exchange("okx", health=1.0)


@pytest.fixture
def executor(config: ExecutionConfig, exchange_a: MagicMock, exchange_b: MagicMock) -> AtomicExecutor:
    return AtomicExecutor(
        exchanges={"binance": exchange_a, "okx": exchange_b},
        config=config,
    )


# ---------------------------------------------------------------------------
# ExecutionResult tests
# ---------------------------------------------------------------------------


def test_execution_result_success() -> None:
    r = ExecutionResult(status=ExecutionStatus.SUCCESS, leg1=None, leg2=None)
    assert r.status == ExecutionStatus.SUCCESS


def test_execution_result_rolled_back() -> None:
    r = ExecutionResult(status=ExecutionStatus.ROLLED_BACK, leg1=None, leg2=None)
    assert r.status == ExecutionStatus.ROLLED_BACK


# ---------------------------------------------------------------------------
# Same-exchange atomic execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_exchange_success(executor: AtomicExecutor, exchange_a: MagicMock) -> None:
    """Both legs on same exchange succeed in parallel."""
    leg1 = make_order("binance", OrderSide.BUY)
    leg2 = make_order("binance", OrderSide.SELL)
    result = await executor.execute_same_exchange(
        exchange_id="binance",
        leg1_order=leg1,
        leg2_order=leg2,
        strategy_id="strat_1",
    )
    assert result.status == ExecutionStatus.SUCCESS


@pytest.mark.asyncio
async def test_same_exchange_halted_engine(executor: AtomicExecutor) -> None:
    """Halted engine rejects execution."""
    from src.risk.kill_switch import halt_local
    halt_local()
    leg1 = make_order("binance", OrderSide.BUY)
    leg2 = make_order("binance", OrderSide.SELL)
    result = await executor.execute_same_exchange(
        exchange_id="binance",
        leg1_order=leg1,
        leg2_order=leg2,
        strategy_id="strat_1",
    )
    assert result.status == ExecutionStatus.REJECTED


@pytest.mark.asyncio
async def test_same_exchange_partial_above_threshold(
    executor: AtomicExecutor, exchange_a: MagicMock
) -> None:
    """Partial fill >80% is accepted."""
    exchange_a.place_order = AsyncMock(return_value=make_trade(amount=Decimal("0.85")))
    leg1 = make_order("binance", OrderSide.BUY, amount=Decimal("1.0"))
    leg2 = make_order("binance", OrderSide.SELL, amount=Decimal("1.0"))
    result = await executor.execute_same_exchange(
        exchange_id="binance",
        leg1_order=leg1,
        leg2_order=leg2,
        strategy_id="strat_1",
    )
    assert result.status == ExecutionStatus.SUCCESS


@pytest.mark.asyncio
async def test_same_exchange_partial_below_threshold_rollback(
    executor: AtomicExecutor, exchange_a: MagicMock
) -> None:
    """Partial fill ≤80% triggers cancel+rollback."""
    exchange_a.place_order = AsyncMock(return_value=make_trade(amount=Decimal("0.5")))
    leg1 = make_order("binance", OrderSide.BUY, amount=Decimal("1.0"))
    leg2 = make_order("binance", OrderSide.SELL, amount=Decimal("1.0"))
    result = await executor.execute_same_exchange(
        exchange_id="binance",
        leg1_order=leg1,
        leg2_order=leg2,
        strategy_id="strat_1",
    )
    assert result.status in (ExecutionStatus.ROLLED_BACK, ExecutionStatus.ROLLBACK_FAILED)


@pytest.mark.asyncio
async def test_same_exchange_leg_failure_rollback(
    executor: AtomicExecutor, exchange_a: MagicMock
) -> None:
    """Exception on any leg triggers rollback."""
    exchange_a.place_order = AsyncMock(side_effect=Exception("exchange error"))
    leg1 = make_order("binance", OrderSide.BUY)
    leg2 = make_order("binance", OrderSide.SELL)
    result = await executor.execute_same_exchange(
        exchange_id="binance",
        leg1_order=leg1,
        leg2_order=leg2,
        strategy_id="strat_1",
    )
    assert result.status in (ExecutionStatus.ROLLED_BACK, ExecutionStatus.ROLLBACK_FAILED)


# ---------------------------------------------------------------------------
# Cross-exchange atomic execution — PRE-VALIDATION phase
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_exchange_health_check_fails(
    executor: AtomicExecutor, exchange_a: MagicMock, exchange_b: MagicMock
) -> None:
    """Rejects if either exchange health_score <= 0.9."""
    exchange_b.health_score = 0.8  # below threshold
    leg1 = make_order("binance", OrderSide.BUY)
    leg2 = make_order("okx", OrderSide.SELL)
    result = await executor.execute_cross_exchange(
        leg1_order=leg1,
        leg2_order=leg2,
        strategy_id="strat_1",
        min_edge=Decimal("0.001"),
    )
    assert result.status == ExecutionStatus.REJECTED


@pytest.mark.asyncio
async def test_cross_exchange_success(
    executor: AtomicExecutor, exchange_a: MagicMock, exchange_b: MagicMock
) -> None:
    """Full success: both legs fill, no rollback needed."""
    leg1 = make_order("binance", OrderSide.BUY)
    leg2 = make_order("okx", OrderSide.SELL)
    result = await executor.execute_cross_exchange(
        leg1_order=leg1,
        leg2_order=leg2,
        strategy_id="strat_1",
        min_edge=Decimal("0.001"),
    )
    assert result.status == ExecutionStatus.SUCCESS


@pytest.mark.asyncio
async def test_cross_exchange_sequential_submission(
    executor: AtomicExecutor, exchange_a: MagicMock, exchange_b: MagicMock
) -> None:
    """Legs are submitted sequentially (leg1 before leg2)."""
    call_order = []

    async def leg1_place(order: Order) -> Trade:
        call_order.append("leg1")
        return make_trade("binance")

    async def leg2_place(order: Order) -> Trade:
        call_order.append("leg2")
        return make_trade("okx")

    exchange_a.place_order = leg1_place
    exchange_b.place_order = leg2_place

    leg1 = make_order("binance", OrderSide.BUY)
    leg2 = make_order("okx", OrderSide.SELL)
    await executor.execute_cross_exchange(
        leg1_order=leg1,
        leg2_order=leg2,
        strategy_id="strat_1",
        min_edge=Decimal("0.001"),
    )
    assert call_order == ["leg1", "leg2"]


@pytest.mark.asyncio
async def test_cross_exchange_leg1_failure_no_leg2(
    executor: AtomicExecutor, exchange_a: MagicMock, exchange_b: MagicMock
) -> None:
    """Leg 2 never submitted if Leg 1 fails."""
    exchange_a.place_order = AsyncMock(side_effect=Exception("leg1 failed"))
    leg1 = make_order("binance", OrderSide.BUY)
    leg2 = make_order("okx", OrderSide.SELL)
    result = await executor.execute_cross_exchange(
        leg1_order=leg1,
        leg2_order=leg2,
        strategy_id="strat_1",
        min_edge=Decimal("0.001"),
    )
    exchange_b.place_order.assert_not_called()
    assert result.status in (ExecutionStatus.ROLLED_BACK, ExecutionStatus.ROLLBACK_FAILED)


@pytest.mark.asyncio
async def test_cross_exchange_leg2_failure_rollback_leg1(
    executor: AtomicExecutor, exchange_a: MagicMock, exchange_b: MagicMock
) -> None:
    """Leg 1 is rolled back if Leg 2 fails."""
    exchange_b.place_order = AsyncMock(side_effect=Exception("leg2 failed"))
    leg1 = make_order("binance", OrderSide.BUY)
    leg2 = make_order("okx", OrderSide.SELL)
    result = await executor.execute_cross_exchange(
        leg1_order=leg1,
        leg2_order=leg2,
        strategy_id="strat_1",
        min_edge=Decimal("0.001"),
    )
    exchange_a.cancel_order.assert_called()
    assert result.status in (ExecutionStatus.ROLLED_BACK, ExecutionStatus.ROLLBACK_FAILED)


@pytest.mark.asyncio
async def test_cross_exchange_leg1_partial_above_threshold_adjusts_leg2(
    executor: AtomicExecutor, exchange_a: MagicMock, exchange_b: MagicMock
) -> None:
    """Partial leg1 fill >80%: adjust leg2 size to match."""
    exchange_a.place_order = AsyncMock(return_value=make_trade("binance", amount=Decimal("0.85")))
    exchange_b.place_order = AsyncMock(return_value=make_trade("okx", amount=Decimal("0.85")))

    leg1 = make_order("binance", OrderSide.BUY, amount=Decimal("1.0"))
    leg2 = make_order("okx", OrderSide.SELL, amount=Decimal("1.0"))
    result = await executor.execute_cross_exchange(
        leg1_order=leg1,
        leg2_order=leg2,
        strategy_id="strat_1",
        min_edge=Decimal("0.001"),
    )
    assert result.status == ExecutionStatus.SUCCESS
    # leg2 order amount should have been adjusted down
    submitted_leg2 = exchange_b.place_order.call_args[0][0]
    assert submitted_leg2.amount == Decimal("0.85")


@pytest.mark.asyncio
async def test_cross_exchange_leg1_partial_below_threshold_rollback(
    executor: AtomicExecutor, exchange_a: MagicMock, exchange_b: MagicMock
) -> None:
    """Partial leg1 fill ≤80%: cancel+rollback, no leg2."""
    exchange_a.place_order = AsyncMock(return_value=make_trade("binance", amount=Decimal("0.5")))
    leg1 = make_order("binance", OrderSide.BUY, amount=Decimal("1.0"))
    leg2 = make_order("okx", OrderSide.SELL, amount=Decimal("1.0"))
    result = await executor.execute_cross_exchange(
        leg1_order=leg1,
        leg2_order=leg2,
        strategy_id="strat_1",
        min_edge=Decimal("0.001"),
    )
    exchange_b.place_order.assert_not_called()
    assert result.status in (ExecutionStatus.ROLLED_BACK, ExecutionStatus.ROLLBACK_FAILED)


# ---------------------------------------------------------------------------
# Race conditions — Cross-exchange (Amendments 4 & 5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_race_halted_engine_rejects(executor: AtomicExecutor) -> None:
    """RC-CROSS-1: Halt flag set before execution → REJECTED."""
    from src.risk.kill_switch import halt_local
    halt_local()
    leg1 = make_order("binance", OrderSide.BUY)
    leg2 = make_order("okx", OrderSide.SELL)
    result = await executor.execute_cross_exchange(
        leg1_order=leg1, leg2_order=leg2, strategy_id="strat_1", min_edge=Decimal("0.001")
    )
    assert result.status == ExecutionStatus.REJECTED


@pytest.mark.asyncio
async def test_race_exchange_health_degraded_during_validation(
    executor: AtomicExecutor, exchange_a: MagicMock
) -> None:
    """RC-CROSS-2: Exchange health degrades to 0.8 during pre-validation."""
    exchange_a.health_score = 0.8
    leg1 = make_order("binance", OrderSide.BUY)
    leg2 = make_order("okx", OrderSide.SELL)
    result = await executor.execute_cross_exchange(
        leg1_order=leg1, leg2_order=leg2, strategy_id="strat_1", min_edge=Decimal("0.001")
    )
    assert result.status == ExecutionStatus.REJECTED


# ---------------------------------------------------------------------------
# Capital lock tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capital_lock_released_on_success(executor: AtomicExecutor) -> None:
    """Capital lock is released after successful execution."""
    leg1 = make_order("binance", OrderSide.BUY)
    leg2 = make_order("okx", OrderSide.SELL)
    await executor.execute_cross_exchange(
        leg1_order=leg1, leg2_order=leg2, strategy_id="strat_1", min_edge=Decimal("0.001")
    )
    # No lock should remain held
    assert not executor.is_locked("binance")
    assert not executor.is_locked("okx")


@pytest.mark.asyncio
async def test_capital_lock_released_on_rollback(
    executor: AtomicExecutor, exchange_b: MagicMock
) -> None:
    """Capital lock released even after rollback."""
    exchange_b.place_order = AsyncMock(side_effect=Exception("fail"))
    leg1 = make_order("binance", OrderSide.BUY)
    leg2 = make_order("okx", OrderSide.SELL)
    await executor.execute_cross_exchange(
        leg1_order=leg1, leg2_order=leg2, strategy_id="strat_1", min_edge=Decimal("0.001")
    )
    assert not executor.is_locked("binance")
    assert not executor.is_locked("okx")
