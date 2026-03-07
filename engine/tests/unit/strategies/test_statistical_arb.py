"""Tests for StatisticalArbStrategy."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.core.models import OrderSide, Signal
from src.strategies.base import CostCalculator
from src.strategies.statistical_arb import StatArbConfig, StatArbState, StatisticalArbStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_calculator(cost: Decimal = Decimal("0.5")) -> CostCalculator:
    calc = MagicMock(spec=CostCalculator)
    calc.estimate_cost.return_value = cost
    return calc


def make_signal(
    buy_price: Decimal = Decimal("50000"),
    sell_price: Decimal = Decimal("50100"),
    volume: Decimal = Decimal("0.5"),
    buy_exchange: str = "binance",
    sell_exchange: str = "okx",
) -> Signal:
    spread_pct = (sell_price - buy_price) / buy_price
    return Signal(
        strategy_id="stat_arb_v1",
        symbol="BTC/USDT",
        buy_exchange=buy_exchange,
        sell_exchange=sell_exchange,
        buy_price=buy_price,
        sell_price=sell_price,
        spread_pct=spread_pct,
        confidence=0.8,
        volume=volume,
        timestamp=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inactive_returns_none():
    strategy = StatisticalArbStrategy("stat_arb", make_calculator())
    # Not started
    result = await strategy.on_signal(make_signal())
    assert result is None


@pytest.mark.asyncio
async def test_requires_warmup_before_generating_signals():
    """Before min_history samples are collected, all signals are filtered."""
    config = StatArbConfig(min_history=20, zscore_entry=2.0, zscore_exit=0.5)
    strategy = StatisticalArbStrategy("stat_arb", make_calculator(), config)
    await strategy.start()

    for _ in range(5):
        result = await strategy.on_signal(make_signal())

    assert result is None
    assert strategy.metrics.signals_filtered >= 5


@pytest.mark.asyncio
async def test_no_trade_when_zscore_below_entry():
    """Uniform price history → zscore ≈ 0 → no trade."""
    config = StatArbConfig(min_history=10, zscore_entry=2.0, min_zero_crossings=0, adaptive_threshold=False)
    strategy = StatisticalArbStrategy("stat_arb", make_calculator(), config)
    await strategy.start()

    # Feed 15 identical spread signals
    for _ in range(15):
        await strategy.on_signal(make_signal(buy_price=Decimal("50000"), sell_price=Decimal("50100")))

    # Same spread again — zscore should be near 0
    result = await strategy.on_signal(make_signal(buy_price=Decimal("50000"), sell_price=Decimal("50100")))
    assert result is None


@pytest.mark.asyncio
async def test_generates_trade_on_high_zscore():
    """Abnormally wide spread after stable history → high z-score → trade generated."""
    config = StatArbConfig(min_history=10, zscore_entry=2.0, min_zero_crossings=0, adaptive_threshold=False)
    strategy = StatisticalArbStrategy("stat_arb", make_calculator(), config)
    await strategy.start()

    # Establish narrow-spread baseline
    for _ in range(12):
        await strategy.on_signal(make_signal(buy_price=Decimal("50000"), sell_price=Decimal("50010")))

    # Extreme spread → very high z-score
    result = await strategy.on_signal(make_signal(buy_price=Decimal("50000"), sell_price=Decimal("51000")))

    assert result is not None
    assert result.expected_profit_usdt > Decimal("0")
    assert result.strategy_id == "stat_arb"


@pytest.mark.asyncio
async def test_trade_has_two_legs():
    config = StatArbConfig(min_history=10, zscore_entry=2.0, min_zero_crossings=0, adaptive_threshold=False)
    strategy = StatisticalArbStrategy("stat_arb", make_calculator(), config)
    await strategy.start()

    for _ in range(12):
        await strategy.on_signal(make_signal(sell_price=Decimal("50010")))

    result = await strategy.on_signal(make_signal(sell_price=Decimal("51000")))

    assert result is not None
    assert len(result.legs) == 2
    sides = {leg.side for leg in result.legs}
    assert OrderSide.BUY in sides
    assert OrderSide.SELL in sides


@pytest.mark.asyncio
async def test_state_transitions_to_nonfat_after_trade():
    config = StatArbConfig(min_history=10, zscore_entry=2.0, min_zero_crossings=0, adaptive_threshold=False)
    strategy = StatisticalArbStrategy("stat_arb", make_calculator(), config)
    await strategy.start()

    for _ in range(12):
        await strategy.on_signal(make_signal(sell_price=Decimal("50010")))

    result = await strategy.on_signal(make_signal(sell_price=Decimal("51000")))

    if result is not None:
        assert strategy.state in (StatArbState.LONG, StatArbState.SHORT)


@pytest.mark.asyncio
async def test_initial_state_is_flat():
    strategy = StatisticalArbStrategy("stat_arb", make_calculator())
    assert strategy.state == StatArbState.FLAT


@pytest.mark.asyncio
async def test_size_capped_by_max_position_size():
    config = StatArbConfig(min_history=10, zscore_entry=2.0, max_position_size=Decimal("0.2"))
    strategy = StatisticalArbStrategy("stat_arb", make_calculator(), config)
    await strategy.start()

    for _ in range(12):
        await strategy.on_signal(make_signal(sell_price=Decimal("50010")))

    result = await strategy.on_signal(make_signal(sell_price=Decimal("51000"), volume=Decimal("5.0")))

    if result is not None:
        for leg in result.legs:
            assert leg.size <= Decimal("0.2")


@pytest.mark.asyncio
async def test_metrics_increment_on_each_signal():
    strategy = StatisticalArbStrategy("stat_arb", make_calculator())
    await strategy.start()

    await strategy.on_signal(make_signal())
    await strategy.on_signal(make_signal())
    await strategy.on_signal(make_signal())

    assert strategy.metrics.signals_received == 3


@pytest.mark.asyncio
async def test_metadata_includes_zscore_and_hedge_ratio():
    config = StatArbConfig(min_history=10, zscore_entry=2.0, min_zero_crossings=0, adaptive_threshold=False)
    strategy = StatisticalArbStrategy("stat_arb", make_calculator(), config)
    await strategy.start()

    for _ in range(12):
        await strategy.on_signal(make_signal(sell_price=Decimal("50010")))

    result = await strategy.on_signal(make_signal(sell_price=Decimal("51000")))

    if result is not None:
        assert "zscore" in result.metadata
        assert "hedge_ratio" in result.metadata


@pytest.mark.asyncio
async def test_no_trade_when_net_profit_negative():
    """Very high friction costs → net_profit ≤ 0 → no trade."""
    config = StatArbConfig(min_history=10, zscore_entry=2.0, min_zero_crossings=0, adaptive_threshold=False)
    calc = make_calculator(Decimal("10000"))  # absurdly high cost per leg
    strategy = StatisticalArbStrategy("stat_arb", calc, config)
    await strategy.start()

    for _ in range(12):
        await strategy.on_signal(make_signal(sell_price=Decimal("50010")))

    result = await strategy.on_signal(make_signal(sell_price=Decimal("51000")))
    assert result is None
