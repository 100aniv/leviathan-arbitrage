"""Unit tests for US-358: LiveMode._execute_trade_request → record_execution(mode='live').

Covers:
- record_execution called with mode='live' after successful execution
- record_execution strategy_id / symbol passed correctly
- No crash when _market_recorder is None
- On exception path: record_execution skipped OR called with mode='live_failed'
- No crash if record_execution itself raises
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.models import Order, OrderSide, OrderStatus, OrderType
from src.execution.executor import ExecutionStatus
from src.modes.live import LiveMode
from src.strategies.base import TradeRequest, TradeLeg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trade_request(
    strategy_id: str = "cross_exchange",
    buy_exchange: str = "binance",
    sell_exchange: str = "okx",
    symbol: str = "BTC/USDT",
    buy_price: Decimal = Decimal("50000"),
    sell_price: Decimal = Decimal("50100"),
    size: Decimal = Decimal("0.1"),
) -> TradeRequest:
    return TradeRequest(
        strategy_id=strategy_id,
        legs=[
            TradeLeg(
                exchange_id=buy_exchange,
                symbol=symbol,
                side=OrderSide.BUY,
                size=size,
                price=buy_price,
                order_type=OrderType.LIMIT,
            ),
            TradeLeg(
                exchange_id=sell_exchange,
                symbol=symbol,
                side=OrderSide.SELL,
                size=size,
                price=sell_price,
                order_type=OrderType.LIMIT,
            ),
        ],
        expected_profit_usdt=Decimal("5"),
    )


def _make_order(
    exchange_id: str = "binance",
    symbol: str = "BTC/USDT",
    side: OrderSide = OrderSide.BUY,
    price: Decimal = Decimal("50000"),
    amount: Decimal = Decimal("0.1"),
) -> Order:
    return Order(
        exchange_id=exchange_id,
        symbol=symbol,
        side=side,
        order_type=OrderType.LIMIT,
        price=price,
        amount=amount,
        status=OrderStatus.FILLED,
    )


def _make_success_result() -> MagicMock:
    result = MagicMock()
    result.status = ExecutionStatus.SUCCESS
    result.realized_pnl = Decimal("5.0")
    return result


def _make_live_mode(
    market_recorder: object | None = None,
    execution_mode: str = "live",
) -> LiveMode:
    mode = LiveMode(
        signal_generator=MagicMock(),
        execution_mode=execution_mode,
        market_recorder=market_recorder,
    )
    return mode


def _stub_execution(mode: LiveMode, orders: list[Order], exec_result: object) -> None:
    """Wire up the execution stubs needed for _execute_trade_request to reach record_execution."""
    mode._legs_to_orders = MagicMock(return_value=orders)
    mode._route_to_executor = AsyncMock(return_value=exec_result)
    mode._publish_trade_for_observability = AsyncMock()


# ---------------------------------------------------------------------------
# Success path: record_execution(mode='live') must be called
# ---------------------------------------------------------------------------


class TestRecordExecutionSuccessPath:
    async def test_record_execution_called_after_successful_trade(self):
        """After a successful trade, record_execution is called exactly once."""
        recorder = MagicMock()
        mode = _make_live_mode(market_recorder=recorder, execution_mode="live")
        orders = [_make_order()]
        _stub_execution(mode, orders, _make_success_result())

        await mode._execute_trade_request(_make_trade_request())

        recorder.record_execution.assert_called_once()

    async def test_record_execution_mode_is_live(self):
        """record_execution is called with mode='live'."""
        recorder = MagicMock()
        mode = _make_live_mode(market_recorder=recorder, execution_mode="live")
        orders = [_make_order()]
        _stub_execution(mode, orders, _make_success_result())

        await mode._execute_trade_request(_make_trade_request())

        call_kwargs = recorder.record_execution.call_args[1]
        assert call_kwargs.get("mode") == "live"

    async def test_record_execution_strategy_id_passed(self):
        """record_execution receives the correct strategy_id."""
        recorder = MagicMock()
        mode = _make_live_mode(market_recorder=recorder, execution_mode="live")
        orders = [_make_order()]
        _stub_execution(mode, orders, _make_success_result())

        await mode._execute_trade_request(_make_trade_request(strategy_id="triangular"))

        call_kwargs = recorder.record_execution.call_args[1]
        assert call_kwargs.get("strategy_id") == "triangular"

    async def test_record_execution_symbol_passed(self):
        """record_execution receives the correct symbol."""
        recorder = MagicMock()
        mode = _make_live_mode(market_recorder=recorder, execution_mode="live")
        orders = [_make_order(symbol="ETH/USDT")]
        _stub_execution(mode, orders, _make_success_result())

        await mode._execute_trade_request(_make_trade_request(symbol="ETH/USDT"))

        call_kwargs = recorder.record_execution.call_args[1]
        assert call_kwargs.get("symbol") == "ETH/USDT"

    async def test_record_execution_not_called_when_market_recorder_is_none(self):
        """No crash and no call attempted when _market_recorder is None."""
        mode = _make_live_mode(market_recorder=None, execution_mode="live")
        orders = [_make_order()]
        _stub_execution(mode, orders, _make_success_result())

        # Must not raise
        await mode._execute_trade_request(_make_trade_request())


# ---------------------------------------------------------------------------
# Failure path: graceful handling on executor exception
# ---------------------------------------------------------------------------


class TestRecordExecutionFailurePath:
    async def test_no_crash_when_executor_raises(self):
        """_execute_trade_request catches executor exceptions without propagating."""
        recorder = MagicMock()
        mode = _make_live_mode(market_recorder=recorder, execution_mode="live")
        orders = [_make_order()]
        mode._legs_to_orders = MagicMock(return_value=orders)
        mode._route_to_executor = AsyncMock(side_effect=RuntimeError("exchange unreachable"))
        mode._publish_trade_for_observability = AsyncMock()

        # Must not raise
        await mode._execute_trade_request(_make_trade_request())

    async def test_record_execution_on_failure_mode_is_valid(self):
        """If record_execution is called on the failure path, mode must be 'live_failed'."""
        recorder = MagicMock()
        mode = _make_live_mode(market_recorder=recorder, execution_mode="live")
        orders = [_make_order()]
        mode._legs_to_orders = MagicMock(return_value=orders)
        mode._route_to_executor = AsyncMock(side_effect=RuntimeError("network error"))
        mode._publish_trade_for_observability = AsyncMock()

        await mode._execute_trade_request(_make_trade_request())

        if recorder.record_execution.called:
            call_kwargs = recorder.record_execution.call_args[1]
            assert call_kwargs.get("mode") == "live_failed"

    async def test_no_crash_when_record_execution_itself_raises(self):
        """If record_execution throws, _execute_trade_request must NOT propagate it."""
        recorder = MagicMock()
        recorder.record_execution = MagicMock(side_effect=Exception("DB connection lost"))
        mode = _make_live_mode(market_recorder=recorder, execution_mode="live")
        orders = [_make_order()]
        _stub_execution(mode, orders, _make_success_result())

        # Must not raise even when the recorder itself fails
        await mode._execute_trade_request(_make_trade_request())
