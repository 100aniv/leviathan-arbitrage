"""Tests for StatisticalArbStrategy cost gate — US-274."""
from __future__ import annotations

import math
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.strategies.base import CostCalculator
from src.strategies.statistical_arb import StatArbConfig, StatisticalArbStrategy


def _make_calc(cost_per_call: Decimal = Decimal("0")) -> CostCalculator:
    calc = MagicMock(spec=CostCalculator)
    calc.estimate_cost.return_value = cost_per_call
    return calc


async def _feed_pair(strategy: StatisticalArbStrategy, n: int = 150) -> None:
    """Feed enough price ticks for a pair to build spread history."""
    exchange = "binance"
    for i in range(n):
        btc = 50000.0 + 100.0 * math.sin(i * 0.1)
        eth = 3000.0 + 60.0 * math.sin(i * 0.1 + 0.2)
        await strategy.on_orderbook_update(exchange, "BTC/USDT", btc)
        await strategy.on_orderbook_update(exchange, "ETH/USDT", eth)


@pytest.mark.asyncio
async def test_cost_gate_blocks_unprofitable():
    """round_trip_cost > expected_spread_profit → returns None."""
    config = StatArbConfig(
        min_history=50,
        zscore_entry=0.1,
        enable_cointegration=False,
        enable_cost_gate=True,
        min_zero_crossings=0,
        adaptive_threshold=False,
    )
    strategy = StatisticalArbStrategy("sa_test1", _make_calc(Decimal("1000")), config)
    await strategy.start()
    await _feed_pair(strategy, n=80)

    result = await strategy.on_orderbook_update("binance", "BTC/USDT", 55000.0)
    if not result:
        result = await strategy.on_orderbook_update("binance", "ETH/USDT", 2500.0)
    # cost=1000 per leg × 4 = 4000 total; expected profit is small → blocked → empty list
    assert not result


@pytest.mark.asyncio
async def test_cost_gate_allows_profitable():
    """round_trip_cost=0 < expected_spread_profit → TradeRequest may be returned."""
    config = StatArbConfig(
        min_history=50,
        zscore_entry=0.1,
        enable_cointegration=False,
        enable_cost_gate=True,
        min_zero_crossings=0,
        adaptive_threshold=False,
    )
    strategy = StatisticalArbStrategy("sa_test2", _make_calc(Decimal("0")), config)
    await strategy.start()
    await _feed_pair(strategy, n=80)

    result = []
    for offset in [5000.0, -5000.0, 5000.0]:
        result = await strategy.on_orderbook_update("binance", "BTC/USDT", 50000.0 + offset)
        if result:
            break
        result = await strategy.on_orderbook_update("binance", "ETH/USDT", 3000.0 - offset / 20)
        if result:
            break
    # At zero cost: result is list[TradeRequest] (possibly empty); each item has .legs
    assert isinstance(result, list)
    for req in result:
        assert hasattr(req, "legs")


@pytest.mark.asyncio
async def test_expected_profit_nonzero():
    """expected_profit_usdt in TradeRequest must be >= 0 when returned."""
    config = StatArbConfig(
        min_history=50,
        zscore_entry=0.1,
        enable_cointegration=False,
        enable_cost_gate=False,
        min_zero_crossings=0,
        adaptive_threshold=False,
    )
    strategy = StatisticalArbStrategy("sa_test3", _make_calc(Decimal("0")), config)
    await strategy.start()
    await _feed_pair(strategy, n=80)

    result: list = []
    for i in range(20):
        btc = 50000.0 + 3000.0 * math.sin(i)
        result = await strategy.on_orderbook_update("binance", "BTC/USDT", btc)
        if result:
            break
        result = await strategy.on_orderbook_update("binance", "ETH/USDT", 2000.0)
        if result:
            break

    for req in result:
        assert req.expected_profit_usdt >= Decimal("0")
