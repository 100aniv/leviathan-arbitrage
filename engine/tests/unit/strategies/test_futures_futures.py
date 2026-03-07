"""Tests for FuturesFuturesStrategy."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.core.models import OrderSide, Signal
from src.strategies.base import CostCalculator
from src.strategies.futures_futures import FuturesFuturesConfig, FuturesFuturesStrategy


def make_calculator(cost: Decimal = Decimal("1")) -> CostCalculator:
    calc = MagicMock(spec=CostCalculator)
    calc.estimate_cost.return_value = cost
    return calc


def make_signal(
    spread_pct: Decimal = Decimal("0.002"),
    buy_price: Decimal = Decimal("50000"),
    sell_price: Decimal = Decimal("50100"),
    volume: Decimal = Decimal("0.5"),
    margin_available: Decimal | None = None,
) -> Signal:
    metadata = {}
    if margin_available is not None:
        metadata["margin_available"] = str(margin_available)
    return Signal(
        strategy_id="futures_futures_cross_v1",
        symbol="BTC/USDT:USDT",
        buy_exchange="binance",
        sell_exchange="bybit",
        buy_price=buy_price,
        sell_price=sell_price,
        spread_pct=spread_pct,
        confidence=0.9,
        volume=volume,
        timestamp=datetime.now(timezone.utc),
        metadata=metadata,
    )


@pytest.mark.asyncio
async def test_spread_below_threshold_returns_none():
    config = FuturesFuturesConfig(min_spread_bps=Decimal("20"))
    strategy = FuturesFuturesStrategy("ff_cross", make_calculator(), config)
    await strategy.start()
    signal = make_signal(spread_pct=Decimal("0.0015"))  # 15 bps < 20 bps
    result = await strategy.on_signal(signal)
    assert result is None
    assert strategy.metrics.signals_filtered == 1


@pytest.mark.asyncio
async def test_profitable_signal_generates_two_legs():
    # gross = (50100-50000)*0.5 = 50; cost = 1*2 = 2; net = 48
    strategy = FuturesFuturesStrategy(
        "ff_cross", make_calculator(Decimal("1")), FuturesFuturesConfig(min_spread_bps=Decimal("8"))
    )
    await strategy.start()
    signal = make_signal(spread_pct=Decimal("0.002"))
    result = await strategy.on_signal(signal)

    assert result is not None
    assert len(result.legs) == 2
    assert result.expected_profit_usdt == Decimal("48")


@pytest.mark.asyncio
async def test_legs_have_correct_exchanges_and_sides():
    strategy = FuturesFuturesStrategy(
        "ff_cross", make_calculator(), FuturesFuturesConfig(min_spread_bps=Decimal("8"))
    )
    await strategy.start()
    signal = make_signal()
    result = await strategy.on_signal(signal)
    assert result is not None

    buy_leg = next(l for l in result.legs if l.side == OrderSide.BUY)
    sell_leg = next(l for l in result.legs if l.side == OrderSide.SELL)
    assert buy_leg.exchange_id == "binance"
    assert sell_leg.exchange_id == "bybit"


@pytest.mark.asyncio
async def test_legs_contain_leverage_metadata():
    config = FuturesFuturesConfig(min_spread_bps=Decimal("8"), max_leverage=3)
    strategy = FuturesFuturesStrategy("ff_cross", make_calculator(), config)
    await strategy.start()
    signal = make_signal()
    result = await strategy.on_signal(signal)
    assert result is not None
    for leg in result.legs:
        assert leg.metadata["leverage"] == "3"
        assert leg.metadata["leg_type"] == "futures"


@pytest.mark.asyncio
async def test_margin_safety_check_rejects_oversized_trade():
    """Required margin exceeds available * (1 - safety_pct) → filter."""
    config = FuturesFuturesConfig(
        min_spread_bps=Decimal("8"),
        max_leverage=2,
        margin_safety_pct=Decimal("0.20"),
    )
    strategy = FuturesFuturesStrategy("ff_cross", make_calculator(), config)
    await strategy.start()

    # required margin = 50000 * 1.0 / 2 = 25000
    # max allowed = 1000 * (1 - 0.20) = 800 < 25000 → reject
    signal = make_signal(volume=Decimal("1.0"), margin_available=Decimal("1000"))
    result = await strategy.on_signal(signal)
    assert result is None


@pytest.mark.asyncio
async def test_margin_check_passes_with_sufficient_margin():
    config = FuturesFuturesConfig(
        min_spread_bps=Decimal("8"),
        max_leverage=5,
        margin_safety_pct=Decimal("0.20"),
    )
    strategy = FuturesFuturesStrategy("ff_cross", make_calculator(Decimal("1")), config)
    await strategy.start()

    # required margin = 50000 * 0.1 / 5 = 1000
    # max allowed = 10000 * 0.80 = 8000 > 1000 → pass
    signal = make_signal(volume=Decimal("0.1"), margin_available=Decimal("10000"))
    result = await strategy.on_signal(signal)
    assert result is not None


@pytest.mark.asyncio
async def test_high_cost_no_trade():
    """When costs exceed gross profit, return None."""
    strategy = FuturesFuturesStrategy(
        "ff_cross",
        make_calculator(Decimal("200")),  # 200 USDT per leg
        FuturesFuturesConfig(min_spread_bps=Decimal("8")),
    )
    await strategy.start()
    signal = make_signal(volume=Decimal("0.5"))  # gross = 50 USDT; cost = 400 USDT
    result = await strategy.on_signal(signal)
    assert result is None


@pytest.mark.asyncio
async def test_inactive_strategy_returns_none():
    strategy = FuturesFuturesStrategy("ff_cross", make_calculator())
    signal = make_signal()
    result = await strategy.on_signal(signal)
    assert result is None
