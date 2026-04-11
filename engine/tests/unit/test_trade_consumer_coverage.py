"""Coverage tests for TradeRequestConsumer — targeting uncovered lines 132-154, 190-263, 298-305."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import OrderSide, OrderType
from src.execution.executor import ExecutionResult, ExecutionStatus
from src.execution.trade_consumer import (
    CONSUMER_GROUP,
    CONSUMER_NAME,
    TRADE_REQUEST_STREAM,
    TradeRequestConsumer,
    _default_risk_check,
    _leg_to_order,
)
from src.strategies.base import TradeLeg, TradeRequest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_trade_request(
    strategy_id: str = "test_strat",
    n_legs: int = 2,
    same_exchange: bool = False,
) -> TradeRequest:
    legs = [
        TradeLeg(
            exchange_id="binance",
            symbol="BTC/USDT",
            side=OrderSide.BUY if i == 0 else OrderSide.SELL,
            size=Decimal("0.1"),
            order_type=OrderType.MARKET,
            price=None,
        )
        if same_exchange else
        TradeLeg(
            exchange_id="binance" if i == 0 else "okx",
            symbol="BTC/USDT",
            side=OrderSide.BUY if i == 0 else OrderSide.SELL,
            size=Decimal("0.1"),
            order_type=OrderType.MARKET,
        )
        for i in range(n_legs)
    ]
    return TradeRequest(
        strategy_id=strategy_id,
        legs=legs,
        expected_profit_usdt=Decimal("5.0"),
        confidence=0.95,
    )


def _make_result(status: ExecutionStatus, strategy_id: str = "test_strat") -> ExecutionResult:
    return ExecutionResult(
        status=status,
        leg1=None,
        leg2=None,
        strategy_id=strategy_id,
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def event_bus():
    bus = MagicMock()
    bus.create_consumer_group = AsyncMock()
    bus.subscribe = AsyncMock(return_value=[])
    bus.ack_message = AsyncMock()
    return bus


@pytest.fixture
def executor():
    ex = MagicMock()
    ex.execute_same_exchange = AsyncMock(return_value=_make_result(ExecutionStatus.SUCCESS))
    ex.execute_cross_exchange = AsyncMock(return_value=_make_result(ExecutionStatus.SUCCESS))
    return ex


@pytest.fixture
def consumer(event_bus, executor):
    return TradeRequestConsumer(event_bus, executor)


# ── _default_risk_check ───────────────────────────────────────────────────────

class TestDefaultRiskCheck:
    def test_always_approves_any_trade_request(self):
        """Default risk check approves all trade requests."""
        tr = _make_trade_request()
        approved, reason = _default_risk_check(tr)
        assert approved is True
        assert reason == ""


# ── _leg_to_order ─────────────────────────────────────────────────────────────

class TestLegToOrder:
    def test_converts_leg_fields_to_order(self):
        """_leg_to_order maps TradeLeg fields correctly to Order."""
        leg = TradeLeg(
            exchange_id="binance",
            symbol="ETH/USDT",
            side=OrderSide.BUY,
            size=Decimal("1.0"),
            order_type=OrderType.LIMIT,
            price=Decimal("2000.0"),
        )
        order = _leg_to_order(leg, "strat_001")
        assert order.exchange_id == "binance"
        assert order.symbol == "ETH/USDT"
        assert order.side == OrderSide.BUY
        assert order.amount == Decimal("1.0")
        assert order.price == Decimal("2000.0")
        assert order.order_id  # non-empty UUID

    def test_generates_unique_order_ids(self):
        """_leg_to_order produces unique order_id for each call."""
        leg = TradeLeg(exchange_id="binance", symbol="BTC/USDT",
                       side=OrderSide.BUY, size=Decimal("0.1"))
        o1 = _leg_to_order(leg, "s")
        o2 = _leg_to_order(leg, "s")
        assert o1.order_id != o2.order_id

    def test_client_order_id_contains_strategy_id(self):
        """_leg_to_order includes strategy_id prefix in client_order_id."""
        leg = TradeLeg(exchange_id="okx", symbol="ETH/USDT",
                       side=OrderSide.SELL, size=Decimal("0.5"))
        order = _leg_to_order(leg, "my_strategy")
        assert order.client_order_id.startswith("my_strategy_")


# ── start() / stop() lifecycle (lines 129-163) ────────────────────────────────

class TestTradeConsumerLifecycle:
    async def test_start_creates_consumer_group(self, consumer, event_bus):
        """start() creates consumer group before starting the loop."""
        await consumer.start()
        event_bus.create_consumer_group.assert_called_once_with(
            TRADE_REQUEST_STREAM, CONSUMER_GROUP
        )
        consumer._running = False
        if consumer._task:
            consumer._task.cancel()
            try:
                await consumer._task
            except asyncio.CancelledError:
                pass

    async def test_start_sets_running_true(self, consumer):
        """start() sets _running to True."""
        await consumer.start()
        assert consumer._running is True
        await consumer.stop()

    async def test_start_when_already_running_skips_group_creation(self, consumer, event_bus):
        """start() when already running logs warning and does not re-create group."""
        consumer._running = True
        await consumer.start()
        event_bus.create_consumer_group.assert_not_called()
        consumer._running = False

    async def test_stop_sets_running_false(self, consumer):
        """stop() sets _running to False."""
        await consumer.start()
        await consumer.stop()
        assert consumer._running is False

    async def test_stop_clears_task(self, consumer):
        """stop() sets _task to None after cancelling."""
        await consumer.start()
        await consumer.stop()
        assert consumer._task is None

    async def test_stop_when_not_started_is_safe(self, consumer):
        """stop() when never started does not raise."""
        await consumer.stop()
        assert consumer._task is None

    async def test_stop_logs_final_counters(self, consumer):
        """stop() completes without error after processing messages."""
        await consumer.start()
        consumer.processed_count = 10
        consumer.execution_success_count = 8
        await consumer.stop()  # must not raise


# ── _consume_loop (lines 165-193) ────────────────────────────────────────────

class TestConsumeLoop:
    async def test_pauses_when_kill_switch_is_halted(self, consumer, event_bus):
        """_consume_loop sleeps without consuming when is_halted() is True."""
        iteration = 0

        async def fast_sleep(delay):
            nonlocal iteration
            iteration += 1
            if iteration >= 2:
                consumer._running = False

        with patch("src.execution.trade_consumer.is_halted", return_value=True), \
             patch("asyncio.sleep", side_effect=fast_sleep):
            consumer._running = True
            await consumer._consume_loop()

        event_bus.subscribe.assert_not_called()

    async def test_subscribes_for_messages_when_not_halted(self, consumer, event_bus):
        """_consume_loop calls subscribe when kill switch is not active."""
        call_count = 0

        async def subscribe_once(**kwargs):
            nonlocal call_count
            call_count += 1
            consumer._running = False
            return []

        event_bus.subscribe = AsyncMock(side_effect=subscribe_once)

        with patch("src.execution.trade_consumer.is_halted", return_value=False):
            consumer._running = True
            await consumer._consume_loop()

        assert call_count == 1

    async def test_processes_each_received_message(self, consumer, event_bus):
        """_consume_loop calls _process_message for every message returned."""
        tr = _make_trade_request()
        msg = tr.model_dump(mode="json")

        call_count = 0

        async def subscribe_with_message(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [msg]
            consumer._running = False
            return []

        event_bus.subscribe = AsyncMock(side_effect=subscribe_with_message)

        consumer._process_message = AsyncMock()

        with patch("src.execution.trade_consumer.is_halted", return_value=False):
            consumer._running = True
            await consumer._consume_loop()

        consumer._process_message.assert_called_once_with(msg)

    async def test_increments_error_count_on_unexpected_exception(self, consumer, event_bus):
        """_consume_loop catches unexpected exceptions and increments error_count."""
        call_count = 0

        async def subscribe_raises(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("unexpected redis error")
            consumer._running = False
            return []

        event_bus.subscribe = AsyncMock(side_effect=subscribe_raises)

        with patch("src.execution.trade_consumer.is_halted", return_value=False), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            consumer._running = True
            await consumer._consume_loop()

        assert consumer.error_count >= 1


# ── _process_message (lines 195-265) ─────────────────────────────────────────

class TestProcessMessage:
    async def test_invalid_data_increments_error_count(self, consumer):
        """_process_message increments error_count for unparseable data."""
        await consumer._process_message({"invalid_field": "not_a_trade_request"})
        assert consumer.error_count == 1

    async def test_valid_request_increments_processed_count(self, consumer):
        """_process_message increments processed_count for valid trade request."""
        tr = _make_trade_request()
        msg = tr.model_dump(mode="json")

        with patch("src.execution.trade_consumer.is_halted", return_value=False):
            await consumer._process_message(msg)

        assert consumer.processed_count == 1

    async def test_skips_execution_when_kill_switch_halted_after_parse(self, consumer, executor):
        """_process_message skips trade execution when kill switch is active."""
        tr = _make_trade_request()
        msg = tr.model_dump(mode="json")

        with patch("src.execution.trade_consumer.is_halted", return_value=True):
            await consumer._process_message(msg)

        assert consumer.processed_count == 1
        executor.execute_cross_exchange.assert_not_called()
        executor.execute_same_exchange.assert_not_called()

    async def test_risk_rejected_increments_risk_count(self, consumer, executor):
        """_process_message increments risk_rejected_count when check fails."""
        consumer._risk_check = MagicMock(return_value=(False, "exceeds drawdown limit"))
        tr = _make_trade_request()
        msg = tr.model_dump(mode="json")

        with patch("src.execution.trade_consumer.is_halted", return_value=False):
            await consumer._process_message(msg)

        assert consumer.risk_rejected_count == 1

    async def test_raw_redis_format_parsed_correctly(self, consumer):
        """BUG-65: _process_message handles raw {"id": ..., "fields": {b"data": ...}} format."""
        import json
        tr = _make_trade_request()
        payload_bytes = json.dumps(tr.model_dump(mode="json")).encode()
        raw_msg = {"id": b"1234567890-0", "fields": {b"data": payload_bytes}}

        with patch("src.execution.trade_consumer.is_halted", return_value=False):
            await consumer._process_message(raw_msg)

        assert consumer.processed_count == 1

    async def test_raw_redis_format_with_string_data_key(self, consumer):
        """BUG-65: raw format also handles string 'data' key (decoded redis)."""
        import json
        tr = _make_trade_request()
        payload_str = json.dumps(tr.model_dump(mode="json"))
        raw_msg = {"id": "1234567890-0", "fields": {"data": payload_str}}

        with patch("src.execution.trade_consumer.is_halted", return_value=False):
            await consumer._process_message(raw_msg)

        assert consumer.processed_count == 1

    async def test_raw_redis_malformed_data_increments_error_count(self, consumer):
        """BUG-65: raw format with malformed JSON in data field increments error_count."""
        raw_msg = {"id": b"1234-0", "fields": {b"data": b"not-json"}}
        await consumer._process_message(raw_msg)
        assert consumer.error_count == 1
        assert consumer.execution_success_count == 0

    async def test_risk_check_exception_increments_error_count(self, consumer):
        """_process_message handles risk check exception gracefully."""
        consumer._risk_check = MagicMock(side_effect=RuntimeError("guardian failed"))
        tr = _make_trade_request()
        msg = tr.model_dump(mode="json")

        with patch("src.execution.trade_consumer.is_halted", return_value=False):
            await consumer._process_message(msg)

        assert consumer.error_count == 1

    async def test_fewer_than_2_legs_increments_error_count(self, consumer):
        """_process_message rejects trade request with fewer than 2 legs."""
        tr = _make_trade_request(n_legs=1)
        msg = tr.model_dump(mode="json")

        with patch("src.execution.trade_consumer.is_halted", return_value=False):
            await consumer._process_message(msg)

        assert consumer.error_count == 1

    async def test_success_increments_success_count(self, consumer, executor):
        """_process_message increments execution_success_count on SUCCESS result."""
        executor.execute_cross_exchange = AsyncMock(
            return_value=_make_result(ExecutionStatus.SUCCESS)
        )
        tr = _make_trade_request()
        msg = tr.model_dump(mode="json")

        with patch("src.execution.trade_consumer.is_halted", return_value=False):
            await consumer._process_message(msg)

        assert consumer.execution_success_count == 1

    async def test_failure_increments_failure_count(self, consumer, executor):
        """_process_message increments execution_failure_count on non-SUCCESS result."""
        executor.execute_cross_exchange = AsyncMock(
            return_value=_make_result(ExecutionStatus.REJECTED)
        )
        tr = _make_trade_request()
        msg = tr.model_dump(mode="json")

        with patch("src.execution.trade_consumer.is_halted", return_value=False):
            await consumer._process_message(msg)

        assert consumer.execution_failure_count == 1

    async def test_on_result_callback_is_called_after_execution(self, consumer, executor):
        """_process_message invokes on_result callback with request and result."""
        callback = MagicMock()
        consumer._on_result = callback
        executor.execute_cross_exchange = AsyncMock(
            return_value=_make_result(ExecutionStatus.SUCCESS)
        )
        tr = _make_trade_request()
        msg = tr.model_dump(mode="json")

        with patch("src.execution.trade_consumer.is_halted", return_value=False):
            await consumer._process_message(msg)

        assert callback.call_count == 1
        call_args = callback.call_args[0]
        assert isinstance(call_args[0], TradeRequest)
        assert isinstance(call_args[1], ExecutionResult)

    async def test_on_result_callback_exception_is_caught(self, consumer, executor):
        """_process_message handles on_result callback exception without propagating."""
        consumer._on_result = MagicMock(side_effect=RuntimeError("callback blew up"))
        executor.execute_cross_exchange = AsyncMock(
            return_value=_make_result(ExecutionStatus.SUCCESS)
        )
        tr = _make_trade_request()
        msg = tr.model_dump(mode="json")

        with patch("src.execution.trade_consumer.is_halted", return_value=False):
            await consumer._process_message(msg)  # must not raise


# ── _execute routing (lines 267-311) ─────────────────────────────────────────

class TestExecuteRouting:
    async def test_routes_to_same_exchange_when_legs_share_exchange(self, consumer, executor):
        """_execute calls execute_same_exchange when both legs share an exchange."""
        tr = _make_trade_request(same_exchange=True)
        orders = [_leg_to_order(leg, tr.strategy_id) for leg in tr.legs]

        result = await consumer._execute(tr, orders)

        executor.execute_same_exchange.assert_called_once()
        executor.execute_cross_exchange.assert_not_called()
        assert result.status == ExecutionStatus.SUCCESS

    async def test_routes_to_cross_exchange_when_legs_differ(self, consumer, executor):
        """_execute calls execute_cross_exchange when legs are on different exchanges."""
        tr = _make_trade_request(same_exchange=False)
        orders = [_leg_to_order(leg, tr.strategy_id) for leg in tr.legs]

        result = await consumer._execute(tr, orders)

        executor.execute_cross_exchange.assert_called_once()
        executor.execute_same_exchange.assert_not_called()
        assert result.status == ExecutionStatus.SUCCESS

    async def test_cross_exchange_passes_min_edge(self, consumer, executor):
        """_execute passes min_edge parameter to execute_cross_exchange."""
        consumer._min_edge = Decimal("0.0005")
        tr = _make_trade_request(same_exchange=False)
        orders = [_leg_to_order(leg, tr.strategy_id) for leg in tr.legs]

        await consumer._execute(tr, orders)

        call_kwargs = executor.execute_cross_exchange.call_args[1]
        assert call_kwargs["min_edge"] == Decimal("0.0005")

    async def test_exception_returns_rejected_result(self, consumer, executor):
        """_execute returns REJECTED ExecutionResult when execution raises (lines 298-305)."""
        executor.execute_cross_exchange = AsyncMock(side_effect=RuntimeError("boom"))
        tr = _make_trade_request(same_exchange=False)
        orders = [_leg_to_order(leg, tr.strategy_id) for leg in tr.legs]

        result = await consumer._execute(tr, orders)

        assert result.status == ExecutionStatus.REJECTED
        assert "boom" in result.error
        assert result.strategy_id == tr.strategy_id
        assert consumer.error_count == 1

    async def test_same_exchange_exception_returns_rejected_result(self, consumer, executor):
        """_execute returns REJECTED result when same-exchange execution raises."""
        executor.execute_same_exchange = AsyncMock(side_effect=ConnectionError("timeout"))
        tr = _make_trade_request(same_exchange=True)
        orders = [_leg_to_order(leg, tr.strategy_id) for leg in tr.legs]

        result = await consumer._execute(tr, orders)

        assert result.status == ExecutionStatus.REJECTED
        assert consumer.error_count == 1

    async def test_exception_result_has_null_legs(self, consumer, executor):
        """_execute returns leg1=None, leg2=None in exception result."""
        executor.execute_cross_exchange = AsyncMock(side_effect=RuntimeError("err"))
        tr = _make_trade_request()
        orders = [_leg_to_order(leg, tr.strategy_id) for leg in tr.legs]

        result = await consumer._execute(tr, orders)

        assert result.leg1 is None
        assert result.leg2 is None
