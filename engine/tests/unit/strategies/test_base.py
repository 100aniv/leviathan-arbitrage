"""Tests for BaseStrategy and supporting types."""
from __future__ import annotations

from decimal import Decimal
from typing import Optional
from unittest.mock import MagicMock

import pytest

from src.core.models import OrderSide, OrderType, Signal, Trade
from src.strategies.base import (
    BaseStrategy,
    CostCalculator,
    StrategyMetrics,
    TradeLeg,
    TradeRequest,
)


# ---------------------------------------------------------------------------
# Concrete test implementation
# ---------------------------------------------------------------------------


class _ConcreteStrategy(BaseStrategy):
    async def on_signal(self, signal: Signal) -> Optional[TradeRequest]:
        self._metrics.signals_received += 1
        return None


def make_cost_calculator(cost: Decimal = Decimal("1")) -> CostCalculator:
    calc = MagicMock(spec=CostCalculator)
    calc.estimate_cost.return_value = cost
    return calc


# ---------------------------------------------------------------------------
# TradeLeg
# ---------------------------------------------------------------------------


def test_trade_leg_defaults():
    leg = TradeLeg(
        exchange_id="binance",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        size=Decimal("0.1"),
    )
    assert leg.order_type == OrderType.MARKET
    assert leg.price is None
    assert leg.metadata == {}


def test_trade_leg_with_price():
    leg = TradeLeg(
        exchange_id="okx",
        symbol="BTC/USDT",
        side=OrderSide.SELL,
        size=Decimal("0.5"),
        order_type=OrderType.LIMIT,
        price=Decimal("50000"),
    )
    assert leg.price == Decimal("50000")
    assert leg.order_type == OrderType.LIMIT


# ---------------------------------------------------------------------------
# TradeRequest
# ---------------------------------------------------------------------------


def test_trade_request_creation():
    legs = [
        TradeLeg(exchange_id="binance", symbol="BTC/USDT", side=OrderSide.BUY, size=Decimal("0.1")),
        TradeLeg(exchange_id="okx", symbol="BTC/USDT", side=OrderSide.SELL, size=Decimal("0.1")),
    ]
    req = TradeRequest(
        strategy_id="test_strategy",
        legs=legs,
        expected_profit_usdt=Decimal("10"),
        confidence=0.9,
    )
    assert req.strategy_id == "test_strategy"
    assert len(req.legs) == 2
    assert req.expected_profit_usdt == Decimal("10")
    assert req.confidence == 0.9


# ---------------------------------------------------------------------------
# StrategyMetrics
# ---------------------------------------------------------------------------


def test_metrics_defaults():
    m = StrategyMetrics()
    assert m.signals_received == 0
    assert m.trade_requests_generated == 0
    assert m.fills_received == 0
    assert m.total_realized_pnl_usdt == Decimal("0")
    assert m.signals_filtered == 0


# ---------------------------------------------------------------------------
# BaseStrategy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_strategy_starts_inactive():
    s = _ConcreteStrategy("test_id", make_cost_calculator())
    assert not s.is_active
    assert s.strategy_id == "test_id"


@pytest.mark.asyncio
async def test_strategy_start_stop():
    s = _ConcreteStrategy("test_id", make_cost_calculator())
    await s.start()
    assert s.is_active
    await s.stop()
    assert not s.is_active


@pytest.mark.asyncio
async def test_on_fill_increments_counter():
    s = _ConcreteStrategy("test_id", make_cost_calculator())
    trade = Trade(
        trade_id="t1",
        exchange_id="binance",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        price=Decimal("50000"),
        amount=Decimal("0.1"),
        fee=Decimal("5"),
    )
    await s.on_fill(trade)
    assert s.metrics.fills_received == 1


@pytest.mark.asyncio
async def test_cost_calculator_protocol():
    """CostCalculator is a structural protocol — any class with estimate_cost qualifies."""

    class SimpleCost:
        def estimate_cost(self, exchange_id, symbol, side, size, price):
            return Decimal("1")

    calc = SimpleCost()
    assert isinstance(calc, CostCalculator)
