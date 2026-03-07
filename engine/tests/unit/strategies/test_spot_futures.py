"""Tests for SpotFuturesStrategy."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.core.models import OrderSide, Signal
from src.strategies.base import CostCalculator
from src.strategies.spot_futures import SpotFuturesConfig, SpotFuturesStrategy


def make_calculator(cost: Decimal = Decimal("0.5")) -> CostCalculator:
    calc = MagicMock(spec=CostCalculator)
    calc.estimate_cost.return_value = cost
    return calc


def make_signal(
    basis_bps: Decimal = Decimal("20"),
    funding_rate: Decimal = Decimal("0"),
    spot_symbol: str = "BTC/USDT",
    futures_symbol: str = "BTC/USDT:USDT",
    buy_price: Decimal = Decimal("50000"),
    sell_price: Decimal = Decimal("50100"),
    volume: Decimal = Decimal("0.5"),
) -> Signal:
    # For contango: basis_bps > 0: sell_exchange = sell futures (expensive), buy spot
    # buy_exchange == sell_exchange for same-exchange
    return Signal(
        strategy_id="spot_futures_basis_v1",
        symbol="BTC/USDT",
        buy_exchange="binance",
        sell_exchange="binance",
        buy_price=buy_price,
        sell_price=sell_price,
        spread_pct=abs(sell_price - buy_price) / buy_price,
        confidence=0.9,
        volume=volume,
        timestamp=datetime.now(timezone.utc),
        metadata={
            "basis_bps": str(basis_bps),
            "spot_symbol": spot_symbol,
            "futures_symbol": futures_symbol,
            "funding_rate": str(funding_rate),
        },
    )


@pytest.mark.asyncio
async def test_signal_below_basis_threshold_returns_none():
    strategy = SpotFuturesStrategy(
        "sf_basis", make_calculator(), SpotFuturesConfig(min_basis_bps=Decimal("30"))
    )
    await strategy.start()
    signal = make_signal(basis_bps=Decimal("10"))  # 10 < 30
    result = await strategy.on_signal(signal)
    assert result is None
    assert strategy.metrics.signals_filtered == 1


@pytest.mark.asyncio
async def test_contango_generates_sell_futures_buy_spot():
    """basis_bps > 0 → sell futures (expensive), buy spot (cheap)."""
    strategy = SpotFuturesStrategy(
        "sf_basis", make_calculator(Decimal("0.5")), SpotFuturesConfig(min_basis_bps=Decimal("10"))
    )
    await strategy.start()
    signal = make_signal(basis_bps=Decimal("20"))
    result = await strategy.on_signal(signal)
    assert result is not None

    spot_leg = next(l for l in result.legs if l.metadata.get("leg_type") == "spot")
    futures_leg = next(l for l in result.legs if l.metadata.get("leg_type") == "futures")
    assert spot_leg.side == OrderSide.BUY
    assert futures_leg.side == OrderSide.SELL


@pytest.mark.asyncio
async def test_backwardation_generates_buy_futures_sell_spot():
    """basis_bps < 0 → buy futures (cheap), sell spot (expensive)."""
    strategy = SpotFuturesStrategy(
        "sf_basis", make_calculator(Decimal("0.5")), SpotFuturesConfig(min_basis_bps=Decimal("10"))
    )
    await strategy.start()
    # For backwardation: swap prices so sell_price < buy_price
    signal = make_signal(
        basis_bps=Decimal("-20"),
        buy_price=Decimal("50100"),
        sell_price=Decimal("50000"),
    )
    result = await strategy.on_signal(signal)
    assert result is not None

    spot_leg = next(l for l in result.legs if l.metadata.get("leg_type") == "spot")
    futures_leg = next(l for l in result.legs if l.metadata.get("leg_type") == "futures")
    assert spot_leg.side == OrderSide.SELL
    assert futures_leg.side == OrderSide.BUY


@pytest.mark.asyncio
async def test_cross_exchange_signal_rejected():
    """Spot-futures must be same exchange."""
    strategy = SpotFuturesStrategy("sf_basis", make_calculator())
    await strategy.start()
    signal = Signal(
        strategy_id="spot_futures_basis_v1",
        symbol="BTC/USDT",
        buy_exchange="binance",
        sell_exchange="okx",  # different exchange!
        buy_price=Decimal("50000"),
        sell_price=Decimal("50100"),
        spread_pct=Decimal("0.002"),
        confidence=0.9,
        volume=Decimal("0.5"),
        metadata={"basis_bps": "20"},
    )
    result = await strategy.on_signal(signal)
    assert result is None


@pytest.mark.asyncio
async def test_high_funding_rate_filters_signal():
    """Funding rate above threshold should filter the signal."""
    config = SpotFuturesConfig(
        min_basis_bps=Decimal("10"),
        funding_rate_threshold=Decimal("0.0005"),
    )
    strategy = SpotFuturesStrategy("sf_basis", make_calculator(), config)
    await strategy.start()
    signal = make_signal(basis_bps=Decimal("20"), funding_rate=Decimal("0.002"))  # > 0.0005
    result = await strategy.on_signal(signal)
    assert result is None


@pytest.mark.asyncio
async def test_legs_on_same_exchange():
    strategy = SpotFuturesStrategy(
        "sf_basis", make_calculator(Decimal("0.5")), SpotFuturesConfig(min_basis_bps=Decimal("10"))
    )
    await strategy.start()
    signal = make_signal(basis_bps=Decimal("20"))
    result = await strategy.on_signal(signal)
    assert result is not None
    assert all(l.exchange_id == "binance" for l in result.legs)


@pytest.mark.asyncio
async def test_net_profit_positive():
    # gross = (50100-50000)*0.5 = 50; cost = 0.5*2 = 1; net = 49
    strategy = SpotFuturesStrategy(
        "sf_basis", make_calculator(Decimal("0.5")), SpotFuturesConfig(min_basis_bps=Decimal("10"))
    )
    await strategy.start()
    signal = make_signal(basis_bps=Decimal("20"))
    result = await strategy.on_signal(signal)
    assert result is not None
    assert result.expected_profit_usdt == Decimal("49")
