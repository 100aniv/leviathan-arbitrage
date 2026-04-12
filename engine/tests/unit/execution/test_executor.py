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
    ex.get_lot_step = AsyncMock(return_value=Decimal("0.001"))
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
    # Use small amounts ($50 total) to stay within default $100k per-exchange budget
    leg1 = make_order("binance", OrderSide.BUY, amount=Decimal("0.001"))
    leg2 = make_order("binance", OrderSide.SELL, amount=Decimal("0.001"))
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
    exchange_a.place_order = AsyncMock(return_value=make_trade(amount=Decimal("0.00085")))
    leg1 = make_order("binance", OrderSide.BUY, amount=Decimal("0.001"))
    leg2 = make_order("binance", OrderSide.SELL, amount=Decimal("0.001"))
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
    exchange_a.place_order = AsyncMock(return_value=make_trade(amount=Decimal("0.0005")))
    leg1 = make_order("binance", OrderSide.BUY, amount=Decimal("0.001"))
    leg2 = make_order("binance", OrderSide.SELL, amount=Decimal("0.001"))
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
    leg1 = make_order("binance", OrderSide.BUY, amount=Decimal("0.001"))
    leg2 = make_order("binance", OrderSide.SELL, amount=Decimal("0.001"))
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
    """Rejects if either exchange health_score <= threshold (currently 0.7)."""
    exchange_b.health_score = 0.5  # below threshold
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
async def test_cross_exchange_exit_bypasses_margin_tracker(
    executor: AtomicExecutor, exchange_a: MagicMock, exchange_b: MagicMock
) -> None:
    """BUG-75: reduceOnly exit trades must succeed even when margin_tracker is exhausted.

    Entries are blocked when in_flight >= budget; exits must bypass this check
    so convergence exits don't thrash in a restore→block→restore loop.
    """
    import time as _time

    # Directly inject large in-flight reservation (bypasses check_and_reserve validation)
    # so net_available < required for a $50k entry but margin_tracker is "full"
    _large = Decimal("90000")
    _expires = _time.monotonic() + 60.0
    executor._margin_tracker._entries.append(("binance", _large, _expires))
    executor._margin_tracker._entries.append(("okx", _large, _expires))

    # Exit trade: both legs have reduceOnly=True — must bypass margin check
    leg1 = Order(
        exchange_id="binance", symbol="BTC/USDT", side=OrderSide.SELL,
        order_type=OrderType.LIMIT, price=Decimal("50000"), amount=Decimal("1.0"),
        metadata={"reduceOnly": True},
    )
    leg2 = Order(
        exchange_id="okx", symbol="BTC/USDT", side=OrderSide.BUY,
        order_type=OrderType.LIMIT, price=Decimal("50000"), amount=Decimal("1.0"),
        metadata={"reduceOnly": True},
    )
    result = await executor.execute_cross_exchange(
        leg1_order=leg1, leg2_order=leg2,
        strategy_id="strat_1", min_edge=Decimal("0.001"),
    )
    # Exit must not be blocked by exhausted margin
    assert result.status == ExecutionStatus.SUCCESS

    # Verify that an entry IS still blocked with same exhausted margin
    # (in_flight = 90000, net_available = 10000 < effective 57500 for $50k order)
    entry_leg1 = make_order("binance", OrderSide.BUY)
    entry_leg2 = make_order("okx", OrderSide.SELL)
    entry_result = await executor.execute_cross_exchange(
        leg1_order=entry_leg1, leg2_order=entry_leg2,
        strategy_id="strat_1", min_edge=Decimal("0.001"),
    )
    assert entry_result.status == ExecutionStatus.REJECTED
    assert entry_result.error == "margin_tracker_blocked"


@pytest.mark.asyncio
async def test_cross_exchange_exit_rollback_no_margin_leak(
    executor: AtomicExecutor, exchange_a: MagicMock, exchange_b: MagicMock
) -> None:
    """BUG-75 + BUG-42 interaction: exit trade where leg2 fails must not leak
    margin reservations. Since exits bypass margin_tracker, _entries must be
    unchanged after a ROLLED_BACK or ROLLBACK_FAILED result.
    """
    import time as _time

    # Pre-inject large in-flight so any accidental reservation would be detectable
    _large = Decimal("90000")
    _expires = _time.monotonic() + 60.0
    executor._margin_tracker._entries.append(("binance", _large, _expires))
    initial_entries_count = len(executor._margin_tracker._entries)

    # Make leg2 raise to trigger rollback path
    exchange_b.place_order = AsyncMock(side_effect=RuntimeError("exchange_b timeout"))

    leg1 = Order(
        exchange_id="binance", symbol="ETH/USDT", side=OrderSide.SELL,
        order_type=OrderType.LIMIT, price=Decimal("3000"), amount=Decimal("0.1"),
        metadata={"reduceOnly": True},
    )
    leg2 = Order(
        exchange_id="okx", symbol="ETH/USDT", side=OrderSide.BUY,
        order_type=OrderType.LIMIT, price=Decimal("3000"), amount=Decimal("0.1"),
        metadata={"reduceOnly": True},
    )
    result = await executor.execute_cross_exchange(
        leg1_order=leg1, leg2_order=leg2,
        strategy_id="strat_1", min_edge=Decimal("0.001"),
    )
    # Result must be ROLLED_BACK or ROLLBACK_FAILED (not SUCCESS, not REJECTED)
    assert result.status in (ExecutionStatus.ROLLED_BACK, ExecutionStatus.ROLLBACK_FAILED)
    # margin_tracker must be unchanged — no phantom reservations from exit bypass
    assert len(executor._margin_tracker._entries) == initial_entries_count


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
    """Leg 1 is rolled back (via opposing unwind order) if Leg 2 fails."""
    exchange_b.place_order = AsyncMock(side_effect=Exception("leg2 failed"))
    leg1 = make_order("binance", OrderSide.BUY)
    leg2 = make_order("okx", OrderSide.SELL)
    result = await executor.execute_cross_exchange(
        leg1_order=leg1,
        leg2_order=leg2,
        strategy_id="strat_1",
        min_edge=Decimal("0.001"),
    )
    # Filled leg1 is unwound via opposing market order (place_order called twice:
    # once for leg1 fill, once for unwind)
    assert exchange_a.place_order.call_count >= 2
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
    """RC-CROSS-2: Exchange health degrades below threshold during pre-validation."""
    exchange_a.health_score = 0.5
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


# ---------------------------------------------------------------------------
# _rollback_order — symbol propagation (newly fixed path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_order_passes_symbol_to_cancel_order(
    executor: AtomicExecutor, exchange_a: MagicMock
) -> None:
    """_rollback_order passes order.symbol as kwarg to cancel_order (Binance fix)."""
    order = make_order("binance", OrderSide.BUY)
    order.order_id = "ord_abc"
    await executor._rollback_order("binance", order)
    exchange_a.cancel_order.assert_called_once_with("ord_abc", symbol=order.symbol)


@pytest.mark.asyncio
async def test_rollback_order_falls_back_when_symbol_kwarg_raises_type_error(
    executor: AtomicExecutor, exchange_a: MagicMock
) -> None:
    """_rollback_order falls back to cancel_order(id) when symbol kwarg causes TypeError."""

    async def cancel_no_symbol(order_id: str) -> bool:
        return True

    # Simulates an adapter that doesn't accept the symbol kwarg
    async def cancel_raise_on_symbol(order_id: str, **kwargs: object) -> bool:
        if "symbol" in kwargs:
            raise TypeError("unexpected keyword argument 'symbol'")
        return True

    exchange_a.cancel_order = AsyncMock(side_effect=cancel_raise_on_symbol)
    order = make_order("binance", OrderSide.BUY)
    order.order_id = "ord_xyz"
    result, _ = await executor._rollback_order("binance", order)
    assert result is True


@pytest.mark.asyncio
async def test_rollback_order_returns_false_when_cancel_raises_runtime_error(
    executor: AtomicExecutor, exchange_a: MagicMock
) -> None:
    """_rollback_order returns False when cancel_order raises a non-TypeError exception."""
    exchange_a.cancel_order = AsyncMock(side_effect=RuntimeError("exchange error"))
    order = make_order("binance", OrderSide.BUY)
    order.order_id = "ord_fail"
    result, _ = await executor._rollback_order("binance", order)
    assert result is False


@pytest.mark.asyncio
async def test_rollback_order_returns_true_when_order_id_is_none(
    executor: AtomicExecutor, exchange_a: MagicMock
) -> None:
    """_rollback_order returns True without calling cancel_order when order_id is None."""
    order = make_order("binance", OrderSide.BUY)
    order.order_id = ""  # Empty string — no cancel needed
    result, _ = await executor._rollback_order("binance", order)
    assert result is True
    exchange_a.cancel_order.assert_not_called()


@pytest.mark.asyncio
async def test_rollback_order_uses_overridden_order_id(
    executor: AtomicExecutor, exchange_a: MagicMock
) -> None:
    """_rollback_order uses the provided order_id override instead of order.order_id."""
    order = make_order("binance", OrderSide.BUY)
    order.order_id = "original_id"
    await executor._rollback_order("binance", order, order_id="trade_fill_id")
    # Must cancel using the override id, not order.order_id
    call_args = exchange_a.cancel_order.call_args
    assert call_args[0][0] == "trade_fill_id"


@pytest.mark.asyncio
async def test_rollback_order_returns_false_for_unknown_exchange(
    executor: AtomicExecutor,
) -> None:
    """_rollback_order returns False immediately when exchange is not found."""
    order = make_order("unknown_exchange", OrderSide.BUY)
    order.order_id = "ord_001"
    result, _ = await executor._rollback_order("unknown_exchange", order)
    assert result is False


@pytest.mark.asyncio
async def test_do_rollback_cross_halts_engine_when_unwind_fails(
    executor: AtomicExecutor, exchange_a: MagicMock
) -> None:
    """_do_rollback_cross triggers halt when unwind order fails on Exchange A."""
    from src.risk.kill_switch import is_halted

    # Filled leg → rollback uses opposing market order (place_order), not cancel
    exchange_a.place_order = AsyncMock(side_effect=RuntimeError("unwind refused"))
    leg1_order = make_order("binance", OrderSide.BUY)
    leg1_order.order_id = "ord_001"
    leg1_trade = make_trade("binance")
    leg1_result = LegResult(order=leg1_order, trade=leg1_trade)
    leg2_result = LegResult(order=make_order("okx", OrderSide.SELL), error="timeout")

    result = await executor._do_rollback_cross(
        "binance", leg1_order, leg1_result, leg2_result, "strat_1", "Leg 2 timeout"
    )
    assert result.status == ExecutionStatus.ROLLBACK_FAILED
    assert is_halted()


@pytest.mark.asyncio
async def test_same_exchange_health_check_fails_below_threshold(
    executor: AtomicExecutor, exchange_a: MagicMock
) -> None:
    """Same-exchange rejects when exchange health_score is below threshold."""
    exchange_a.health_score = 0.5
    leg1 = make_order("binance", OrderSide.BUY)
    leg2 = make_order("binance", OrderSide.SELL)
    result = await executor.execute_same_exchange(
        exchange_id="binance",
        leg1_order=leg1,
        leg2_order=leg2,
        strategy_id="strat_1",
    )
    assert result.status == ExecutionStatus.REJECTED
    assert "health" in result.error.lower()


@pytest.mark.asyncio
async def test_rollback_order_filled_places_market_unwind(
    executor: AtomicExecutor, exchange_a: MagicMock
) -> None:
    """_rollback_order(filled=True) places opposing MARKET order, not cancel."""
    # Return Trade with amount=1.8 to simulate a fully-filled unwind (BUG-83: must match unwind_qty)
    unwind_trade = make_trade("binance", side=OrderSide.SELL, amount=Decimal("1.8"))
    exchange_a.place_order = AsyncMock(return_value=unwind_trade)

    order = make_order("binance", OrderSide.BUY, amount=Decimal("2.5"))
    # filled_amount < order.amount to prove unwind uses filled_amount
    result = await executor._rollback_order(
        "binance", order, filled=True, filled_amount=Decimal("1.8")
    )

    ok, _ = result
    assert ok is True
    exchange_a.place_order.assert_called_once()
    placed = exchange_a.place_order.call_args[0][0]
    assert placed.side == OrderSide.SELL  # opposite of BUY
    assert placed.order_type == OrderType.MARKET
    assert placed.price is None
    assert placed.amount == Decimal("1.8")  # uses filled_amount, not order.amount
    assert placed.order_id.startswith("unwind-")
    # cancel_order should NOT have been called
    exchange_a.cancel_order.assert_not_called()


@pytest.mark.asyncio
async def test_rollback_partial_fill_returns_false_and_registers_stranded(
    executor: AtomicExecutor, exchange_a: MagicMock
) -> None:
    """BUG-83: if unwind fills < 95% of requested, rollback returns False and registers stranded."""
    # Unwind trade only fills 1.0 out of 1.8 requested → partial fill
    partial_trade = make_trade("binance", side=OrderSide.SELL, amount=Decimal("1.0"))
    exchange_a.place_order = AsyncMock(return_value=partial_trade)

    order = make_order("binance", OrderSide.BUY, amount=Decimal("2.5"))
    result = await executor._rollback_order(
        "binance", order, filled=True, filled_amount=Decimal("1.8")
    )

    ok, msg = result
    assert ok is False, "Partial fill must return False (unhedged exposure)"
    assert "rollback_partial_fill" in msg
    assert "1.0" in msg  # filled amount shown in message


@pytest.mark.asyncio
async def test_same_exchange_filled_leg_uses_unwind_rollback(
    executor: AtomicExecutor, exchange_a: MagicMock
) -> None:
    """Same-exchange rollback detects filled legs and uses opposing market order."""
    leg1_trade = make_trade("binance", side=OrderSide.BUY, amount=Decimal("0.001"))
    # leg2 fails — triggers rollback of filled leg1
    exchange_a.place_order = AsyncMock(
        side_effect=[leg1_trade, RuntimeError("leg2 rejected"), leg1_trade]
    )

    leg1 = make_order("binance", OrderSide.BUY, amount=Decimal("0.001"))
    leg2 = make_order("binance", OrderSide.SELL, amount=Decimal("0.001"))

    result = await executor.execute_same_exchange(
        exchange_id="binance",
        leg1_order=leg1,
        leg2_order=leg2,
        strategy_id="strat_1",
    )

    assert result.status == ExecutionStatus.ROLLED_BACK
    # Third place_order call is the unwind for filled leg1
    assert exchange_a.place_order.call_count >= 3
    unwind_call = exchange_a.place_order.call_args_list[2][0][0]
    assert unwind_call.side == OrderSide.SELL  # opposite of leg1 BUY
    assert unwind_call.order_type == OrderType.MARKET
    assert unwind_call.price is None


@pytest.mark.asyncio
async def test_cross_exchange_leg1_partial_below_threshold_unwinds_filled(
    executor: AtomicExecutor, exchange_a: MagicMock, exchange_b: MagicMock
) -> None:
    """Cross-exchange leg1 partial fill <=80% uses market unwind, not cancel."""
    # leg1 returns 50% fill (below 80% threshold)
    partial_trade = make_trade("binance", amount=Decimal("0.5"), side=OrderSide.BUY)
    # First call: leg1 place (partial fill). Second call: unwind (succeeds).
    unwind_trade = make_trade("binance", amount=Decimal("0.5"), side=OrderSide.SELL)
    exchange_a.place_order = AsyncMock(side_effect=[partial_trade, unwind_trade])

    leg1 = make_order("binance", OrderSide.BUY, amount=Decimal("1.0"))
    leg2 = make_order("okx", OrderSide.SELL, amount=Decimal("1.0"))
    result = await executor.execute_cross_exchange(
        leg1_order=leg1, leg2_order=leg2,
        strategy_id="strat_1", min_edge=Decimal("0.001"),
    )

    assert result.status == ExecutionStatus.ROLLED_BACK
    # Leg2 never placed
    exchange_b.place_order.assert_not_called()
    # Second place_order is the MARKET unwind
    assert exchange_a.place_order.call_count == 2
    unwind_call = exchange_a.place_order.call_args_list[1][0][0]
    assert unwind_call.order_type == OrderType.MARKET
    assert unwind_call.price is None
    assert unwind_call.side == OrderSide.SELL


@pytest.mark.asyncio
async def test_cross_exchange_leg1_partial_unwind_failure_halts(
    executor: AtomicExecutor, exchange_a: MagicMock, exchange_b: MagicMock
) -> None:
    """Cross-exchange leg1 partial fill unwind failure triggers halt."""
    from src.risk.kill_switch import is_halted

    partial_trade = make_trade("binance", amount=Decimal("0.5"), side=OrderSide.BUY)
    # First call: leg1 (partial fill). Second call: unwind fails.
    exchange_a.place_order = AsyncMock(
        side_effect=[partial_trade, RuntimeError("unwind refused")]
    )

    leg1 = make_order("binance", OrderSide.BUY, amount=Decimal("1.0"))
    leg2 = make_order("okx", OrderSide.SELL, amount=Decimal("1.0"))
    result = await executor.execute_cross_exchange(
        leg1_order=leg1, leg2_order=leg2,
        strategy_id="strat_1", min_edge=Decimal("0.001"),
    )

    assert result.status == ExecutionStatus.ROLLBACK_FAILED
    assert is_halted()


# ---------------------------------------------------------------------------
# execute_multi_leg — N-leg sequential same-exchange execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_leg_3_legs_success(
    executor: AtomicExecutor, exchange_a: MagicMock
) -> None:
    """3-leg sequential execution: all legs succeed → SUCCESS, 3 LegResults."""
    orders = [
        make_order("binance", OrderSide.BUY),
        make_order("binance", OrderSide.SELL),
        make_order("binance", OrderSide.BUY),
    ]
    result = await executor.execute_multi_leg(
        exchange_id="binance", orders=orders, strategy_id="tri_1"
    )
    assert result.status == ExecutionStatus.SUCCESS
    assert len(result.legs) == 3


@pytest.mark.asyncio
async def test_multi_leg_halted_rejects(executor: AtomicExecutor) -> None:
    """Halted engine returns REJECTED with empty legs list."""
    from src.risk.kill_switch import halt_local
    halt_local()
    orders = [make_order("binance", OrderSide.BUY) for _ in range(3)]
    result = await executor.execute_multi_leg(
        exchange_id="binance", orders=orders, strategy_id="tri_1"
    )
    assert result.status == ExecutionStatus.REJECTED
    assert result.legs == []


@pytest.mark.asyncio
async def test_multi_leg_health_rejects(
    executor: AtomicExecutor, exchange_a: MagicMock
) -> None:
    """Exchange health below threshold returns REJECTED with empty legs."""
    exchange_a.health_score = 0.5
    orders = [make_order("binance", OrderSide.BUY) for _ in range(3)]
    result = await executor.execute_multi_leg(
        exchange_id="binance", orders=orders, strategy_id="tri_1"
    )
    assert result.status == ExecutionStatus.REJECTED
    assert result.legs == []


@pytest.mark.asyncio
async def test_multi_leg_leg2_fails_rollback_leg1(
    executor: AtomicExecutor, exchange_a: MagicMock
) -> None:
    """Leg 2 failure triggers reverse rollback: leg 1 unwind order is placed."""
    call_count = 0

    async def place_side_effect(order: Order) -> Trade:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("leg2 rejected")
        return make_trade("binance")

    exchange_a.place_order = AsyncMock(side_effect=place_side_effect)
    orders = [
        make_order("binance", OrderSide.BUY),
        make_order("binance", OrderSide.SELL),
        make_order("binance", OrderSide.BUY),
    ]
    result = await executor.execute_multi_leg(
        exchange_id="binance", orders=orders, strategy_id="tri_1"
    )
    assert result.status in (ExecutionStatus.ROLLED_BACK, ExecutionStatus.ROLLBACK_FAILED)
    # call1=leg1 fill, call2=leg2 fail, call3=unwind leg1
    assert exchange_a.place_order.call_count >= 2


@pytest.mark.asyncio
async def test_multi_leg_leg3_fails_rollback_legs_1_2(
    executor: AtomicExecutor, exchange_a: MagicMock
) -> None:
    """Leg 3 failure triggers reverse rollback of legs 2 then 1 (reverse order)."""
    call_count = 0

    async def place_side_effect(order: Order) -> Trade:
        nonlocal call_count
        call_count += 1
        if call_count == 3:
            raise RuntimeError("leg3 rejected")
        return make_trade("binance")

    exchange_a.place_order = AsyncMock(side_effect=place_side_effect)
    orders = [
        make_order("binance", OrderSide.BUY),
        make_order("binance", OrderSide.SELL),
        make_order("binance", OrderSide.BUY),
    ]
    result = await executor.execute_multi_leg(
        exchange_id="binance", orders=orders, strategy_id="tri_1"
    )
    assert result.status in (ExecutionStatus.ROLLED_BACK, ExecutionStatus.ROLLBACK_FAILED)
    # call1=leg1 fill, call2=leg2 fill, call3=leg3 fail, call4=unwind leg2, call5=unwind leg1
    assert exchange_a.place_order.call_count >= 4


@pytest.mark.asyncio
async def test_multi_leg_rollback_failure_halts(
    executor: AtomicExecutor, exchange_a: MagicMock
) -> None:
    """When leg 1 unwind fails after leg 2 error, engine is halted → ROLLBACK_FAILED."""
    from src.risk.kill_switch import is_halted
    call_count = 0

    async def place_side_effect(order: Order) -> Trade:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return make_trade("binance")  # leg1 succeeds
        raise RuntimeError("exchange unavailable")  # leg2 fail + unwind fail

    exchange_a.place_order = AsyncMock(side_effect=place_side_effect)
    orders = [
        make_order("binance", OrderSide.BUY),
        make_order("binance", OrderSide.SELL),
        make_order("binance", OrderSide.BUY),
    ]
    result = await executor.execute_multi_leg(
        exchange_id="binance", orders=orders, strategy_id="tri_1"
    )
    assert result.status == ExecutionStatus.ROLLBACK_FAILED
    assert is_halted()


@pytest.mark.asyncio
async def test_multi_leg_partial_below_threshold(
    executor: AtomicExecutor, exchange_a: MagicMock
) -> None:
    """Partial fill ≤80% on leg 2 triggers rollback of all completed legs."""
    call_count = 0

    async def place_side_effect(order: Order) -> Trade:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            return make_trade("binance", amount=Decimal("0.5"))  # 50% fill
        return make_trade("binance")

    exchange_a.place_order = AsyncMock(side_effect=place_side_effect)
    orders = [
        make_order("binance", OrderSide.BUY, amount=Decimal("1.0")),
        make_order("binance", OrderSide.SELL, amount=Decimal("1.0")),
        make_order("binance", OrderSide.BUY, amount=Decimal("1.0")),
    ]
    result = await executor.execute_multi_leg(
        exchange_id="binance", orders=orders, strategy_id="tri_1"
    )
    assert result.status in (ExecutionStatus.ROLLED_BACK, ExecutionStatus.ROLLBACK_FAILED)


@pytest.mark.asyncio
async def test_multi_leg_timeout_triggers_rollback(
    executor: AtomicExecutor, exchange_a: MagicMock
) -> None:
    """Timeout on leg 2 triggers reverse rollback of completed leg 1."""
    call_count = 0

    async def place_side_effect(order: Order) -> Trade:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise asyncio.TimeoutError()
        return make_trade("binance")

    exchange_a.place_order = AsyncMock(side_effect=place_side_effect)
    orders = [
        make_order("binance", OrderSide.BUY),
        make_order("binance", OrderSide.SELL),
        make_order("binance", OrderSide.BUY),
    ]
    result = await executor.execute_multi_leg(
        exchange_id="binance", orders=orders, strategy_id="tri_1"
    )
    assert result.status in (ExecutionStatus.ROLLED_BACK, ExecutionStatus.ROLLBACK_FAILED)


@pytest.mark.asyncio
async def test_multi_leg_lock_released_on_success(executor: AtomicExecutor) -> None:
    """Capital lock is released after successful multi-leg execution."""
    orders = [make_order("binance", OrderSide.BUY) for _ in range(3)]
    await executor.execute_multi_leg(
        exchange_id="binance", orders=orders, strategy_id="tri_1"
    )
    assert not executor.is_locked("binance")


@pytest.mark.asyncio
async def test_multi_leg_lock_released_on_failure(
    executor: AtomicExecutor, exchange_a: MagicMock
) -> None:
    """Capital lock is released even when all legs fail immediately."""
    exchange_a.place_order = AsyncMock(side_effect=RuntimeError("exchange down"))
    orders = [make_order("binance", OrderSide.BUY) for _ in range(3)]
    await executor.execute_multi_leg(
        exchange_id="binance", orders=orders, strategy_id="tri_1"
    )
    assert not executor.is_locked("binance")


def test_backward_compat_leg1_leg2_properties() -> None:
    """result.leg1 == result.legs[0] and result.leg2 == result.legs[1]."""
    order1 = make_order("binance", OrderSide.BUY)
    order2 = make_order("binance", OrderSide.SELL)
    lr1 = LegResult(order=order1)
    lr2 = LegResult(order=order2)
    result = ExecutionResult(status=ExecutionStatus.SUCCESS, legs=[lr1, lr2])
    assert result.leg1 is lr1
    assert result.leg2 is lr2
    assert result.leg1 == result.legs[0]
    assert result.leg2 == result.legs[1]


def test_backward_compat_empty_legs() -> None:
    """When legs=[], both leg1 and leg2 properties return None."""
    result = ExecutionResult(status=ExecutionStatus.REJECTED, legs=[])
    assert result.leg1 is None
    assert result.leg2 is None


# ---------------------------------------------------------------------------
# DeduplicationGate tests (PHOENIX v32 — Bug 26 fix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dedup_gate_blocks_duplicate_cross_exchange(
    executor: AtomicExecutor,
    exchange_a: MagicMock,
    exchange_b: MagicMock,
) -> None:
    """Second execute_cross_exchange call for same strategy+symbol within window is rejected."""
    order_a = make_order("binance", OrderSide.BUY)
    order_b = make_order("okx", OrderSide.SELL)
    # First call should succeed (dedup allows it)
    result1 = await executor.execute_cross_exchange(
        leg1_order=order_a,
        leg2_order=order_b,
        strategy_id="test_strategy",
        min_edge=Decimal("0.0001"),
    )
    # Second call with same strategy+symbol should be blocked by dedup gate
    result2 = await executor.execute_cross_exchange(
        leg1_order=order_a,
        leg2_order=order_b,
        strategy_id="test_strategy",
        min_edge=Decimal("0.0001"),
    )
    assert result2.status == ExecutionStatus.REJECTED
    assert result2.error == "dedup_gate_blocked"


@pytest.mark.asyncio
async def test_dedup_gate_allows_different_symbols(
    executor: AtomicExecutor,
) -> None:
    """Different symbols for same strategy are NOT blocked by dedup gate."""
    order_a = make_order("binance", OrderSide.BUY)
    order_b = make_order("okx", OrderSide.SELL)
    eth_order_a = Order(
        exchange_id="binance", symbol="ETH/USDT",
        side=OrderSide.BUY, order_type=OrderType.LIMIT,
        price=Decimal("3000"), amount=Decimal("1.0"),
    )
    eth_order_b = Order(
        exchange_id="okx", symbol="ETH/USDT",
        side=OrderSide.SELL, order_type=OrderType.LIMIT,
        price=Decimal("3001"), amount=Decimal("1.0"),
    )
    # Register BTC trade
    await executor.execute_cross_exchange(
        leg1_order=order_a, leg2_order=order_b,
        strategy_id="test_strategy", min_edge=Decimal("0.0001"),
    )
    # ETH trade should NOT be blocked
    result = await executor.execute_cross_exchange(
        leg1_order=eth_order_a, leg2_order=eth_order_b,
        strategy_id="test_strategy", min_edge=Decimal("0.0001"),
    )
    assert result.error != "dedup_gate_blocked"


# ---------------------------------------------------------------------------
# BUG-42: exit leg1 filled + leg2 failed — no incorrect unwind, correct stranded tracking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_do_rollback_cross_exit_leg1_filled_leg2_failed_no_unwind(
    executor: AtomicExecutor, exchange_a: MagicMock
) -> None:
    """BUG-42: When exit leg1 (reduceOnly) fills and leg2 fails,
    _do_rollback_cross must NOT place an unwind on ex_a (position already closed).
    It must register stranded on ex_b and return ROLLBACK_FAILED.
    place_order on exchange_a must NOT be called (no unwind attempt)."""
    from src.risk.kill_switch import is_halted

    # exit leg1 = SELL reduceOnly (closes long on binance_futures)
    leg1_order = make_order("binance", OrderSide.SELL)
    leg1_order.order_id = "exit_ord_001"
    leg1_order.metadata = {"reduceOnly": True, "leg_type": "futures_close"}

    leg1_trade = make_trade("binance", side=OrderSide.SELL, amount=Decimal("1.0"))
    leg1_result = LegResult(order=leg1_order, trade=leg1_trade)

    # leg2 failed (BUY reduceOnly on okx — closes short, timed out)
    leg2_order = make_order("okx", OrderSide.BUY)
    leg2_order.metadata = {"reduceOnly": True, "leg_type": "futures_close"}
    leg2_result = LegResult(order=leg2_order, error="timeout")

    result = await executor._do_rollback_cross(
        "binance", leg1_order, leg1_result, leg2_result, "ff_strat", "Leg 2 timeout"
    )

    # Must NOT try to unwind on ex_a (long already closed by exit SELL)
    exchange_a.place_order.assert_not_called()
    # Must return ROLLBACK_FAILED (stranded short on okx is unresolved)
    assert result.status == ExecutionStatus.ROLLBACK_FAILED
    assert "stranded" in result.error.lower()


@pytest.mark.asyncio
async def test_do_rollback_cross_entry_leg1_filled_leg2_failed_does_unwind(
    executor: AtomicExecutor, exchange_a: MagicMock
) -> None:
    """BUG-42 regression: non-exit (entry) leg1 filled + leg2 failed MUST still unwind on ex_a."""
    unwind_trade = make_trade("binance", side=OrderSide.SELL)
    exchange_a.place_order = AsyncMock(return_value=unwind_trade)

    # entry leg1 = BUY (no reduceOnly) — normal entry
    leg1_order = make_order("binance", OrderSide.BUY)
    leg1_order.order_id = "entry_ord_001"
    leg1_order.metadata = {"leg_type": "futures"}  # no reduceOnly

    leg1_trade = make_trade("binance", side=OrderSide.BUY, amount=Decimal("1.0"))
    leg1_result = LegResult(order=leg1_order, trade=leg1_trade)

    leg2_result = LegResult(order=make_order("okx", OrderSide.SELL), error="timeout")

    await executor._do_rollback_cross(
        "binance", leg1_order, leg1_result, leg2_result, "ff_strat", "Leg 2 timeout"
    )

    # Entry leg1 filled → unwind (opposing MARKET order) MUST be attempted on ex_a
    exchange_a.place_order.assert_called_once()
    placed = exchange_a.place_order.call_args[0][0]
    assert placed.side == OrderSide.SELL  # opposite of BUY entry
