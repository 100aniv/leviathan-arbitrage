"""Unit tests for TradeRequestConsumer position collision detection (US-195)."""
from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.models import OrderSide, OrderType
from src.execution.executor import ExecutionResult, ExecutionStatus
from src.execution.trade_consumer import TradeRequestConsumer
from src.strategies.base import TradeLeg, TradeRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_trade_request(
    symbol: str = "BTC/USDT",
    buy_exchange: str = "binance",
    sell_exchange: str = "okx",
    strategy_id: str = "cross_exchange",
) -> TradeRequest:
    return TradeRequest(
        strategy_id=strategy_id,
        legs=[
            TradeLeg(
                exchange_id=buy_exchange,
                symbol=symbol,
                side=OrderSide.BUY,
                size=Decimal("0.01"),
                order_type=OrderType.MARKET,
            ),
            TradeLeg(
                exchange_id=sell_exchange,
                symbol=symbol,
                side=OrderSide.SELL,
                size=Decimal("0.01"),
                order_type=OrderType.MARKET,
            ),
        ],
        expected_profit_usdt=Decimal("1.0"),
    )


def make_consumer() -> tuple[TradeRequestConsumer, MagicMock]:
    executor = MagicMock()
    success_result = ExecutionResult(
        status=ExecutionStatus.SUCCESS,
        legs=[],
        strategy_id="cross_exchange",
    )
    executor.execute_cross_exchange = AsyncMock(return_value=success_result)
    executor.execute_same_exchange = AsyncMock(return_value=success_result)

    event_bus = MagicMock()
    consumer = TradeRequestConsumer(event_bus=event_bus, executor=executor)
    return consumer, executor


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collision_blocked_within_window():
    """Same (symbol, exchange_pair) within 10s: second request is rejected."""
    consumer, executor = make_consumer()
    req = make_trade_request()

    msg = req.model_dump()
    await consumer._process_message(msg)
    await consumer._process_message(msg)

    assert consumer.processed_count == 2
    # Executor called only once — second was blocked
    assert executor.execute_cross_exchange.call_count == 1


@pytest.mark.asyncio
async def test_different_pair_allowed():
    """Different symbol pairs are allowed simultaneously."""
    consumer, executor = make_consumer()
    req_btc = make_trade_request(symbol="BTC/USDT")
    req_eth = make_trade_request(symbol="ETH/USDT")

    await consumer._process_message(req_btc.model_dump())
    await consumer._process_message(req_eth.model_dump())

    assert executor.execute_cross_exchange.call_count == 2


@pytest.mark.asyncio
async def test_window_expiry(monkeypatch):
    """After 10s window expires, same pair is allowed again."""
    consumer, executor = make_consumer()
    req = make_trade_request()

    # First trade at t=0
    t0 = 1000.0
    call_times = [t0, t0 + 11.0]  # second call is 11s later
    call_index = [0]

    original_monotonic = time.monotonic

    def mock_monotonic() -> float:
        val = call_times[min(call_index[0], len(call_times) - 1)]
        call_index[0] += 1
        return val

    monkeypatch.setattr(
        "src.execution.trade_consumer.time.monotonic", mock_monotonic
    )

    await consumer._process_message(req.model_dump())
    await consumer._process_message(req.model_dump())

    # Both should execute because window expired
    assert executor.execute_cross_exchange.call_count == 2


@pytest.mark.asyncio
async def test_direction_independent():
    """(binance, okx) and (okx, binance) resolve to same frozenset key — blocked."""
    consumer, executor = make_consumer()

    req_forward = make_trade_request(buy_exchange="binance", sell_exchange="okx")
    req_reverse = make_trade_request(buy_exchange="okx", sell_exchange="binance")

    await consumer._process_message(req_forward.model_dump())
    await consumer._process_message(req_reverse.model_dump())

    # Second should be blocked — same frozenset
    assert executor.execute_cross_exchange.call_count == 1
