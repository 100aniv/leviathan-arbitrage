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
async def test_adverse_funding_rate_filters_signal():
    """Adverse funding direction should filter the signal.

    Contango (basis > 0): we sell futures (short). Negative funding means
    shorts PAY, which is adverse. Should be filtered.
    """
    config = SpotFuturesConfig(
        min_basis_bps=Decimal("10"),
        funding_rate_threshold=Decimal("0.0005"),
    )
    strategy = SpotFuturesStrategy("sf_basis", make_calculator(), config)
    await strategy.start()
    # Contango + negative funding = adverse (shorts pay)
    signal = make_signal(basis_bps=Decimal("20"), funding_rate=Decimal("-0.002"))
    result = await strategy.on_signal(signal)
    assert result is None


@pytest.mark.asyncio
async def test_beneficial_funding_rate_allows_signal():
    """Beneficial funding direction should NOT filter the signal.

    Contango (basis > 0): we sell futures (short). Positive funding means
    shorts RECEIVE, which is beneficial. Should be allowed.
    """
    config = SpotFuturesConfig(
        min_basis_bps=Decimal("10"),
        funding_rate_threshold=Decimal("0.0005"),
    )
    strategy = SpotFuturesStrategy("sf_basis", make_calculator(Decimal("0.5")), config)
    await strategy.start()
    # Contango + positive funding = beneficial (shorts receive)
    signal = make_signal(basis_bps=Decimal("20"), funding_rate=Decimal("0.002"))
    result = await strategy.on_signal(signal)
    assert result is not None


@pytest.mark.asyncio
async def test_backwardation_adverse_funding_filters():
    """Backwardation with positive funding (longs pay) should filter."""
    config = SpotFuturesConfig(
        min_basis_bps=Decimal("10"),
        funding_rate_threshold=Decimal("0.0005"),
    )
    strategy = SpotFuturesStrategy("sf_basis", make_calculator(), config)
    await strategy.start()
    # Backwardation + positive funding = adverse (longs pay)
    signal = make_signal(
        basis_bps=Decimal("-20"),
        funding_rate=Decimal("0.002"),
        buy_price=Decimal("50100"),
        sell_price=Decimal("50000"),
    )
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


# ---------------------------------------------------------------------------
# US-322: on_fill futures symbol reverse lookup
# ---------------------------------------------------------------------------

from src.core.models import Trade
from src.strategies.spot_futures import OpenPosition
import time


@pytest.mark.asyncio
async def test_on_fill_resolves_futures_symbol():
    """US-322: on_fill with futures symbol correctly resolves to spot symbol."""
    config = SpotFuturesConfig(min_basis_bps=Decimal("10"))
    strategy = SpotFuturesStrategy("sf_basis", make_calculator(), config)
    await strategy.start()
    strategy._holding_timeout_enabled = True

    # Manually add a position keyed by spot symbol
    strategy._open_positions["BTC/USDT"] = OpenPosition(
        symbol="BTC/USDT",
        entry_time=time.monotonic(),
        entry_price=Decimal("50000"),
        size=Decimal("0.5"),
        side="contango",
        exchange_id="binance",
        futures_symbol="BTC/USDT:USDT",
        futures_exchange="binance",
    )
    assert "BTC/USDT" in strategy._open_positions

    # Simulate fill with FUTURES symbol (not spot)
    trade = MagicMock(spec=Trade)
    trade.symbol = "BTC/USDT:USDT"  # futures symbol
    trade.metadata = {"leg_type": "timeout_close_futures"}
    trade.fee = Decimal("0")
    trade.side = OrderSide.SELL
    trade.size = Decimal("0.5")
    trade.price = Decimal("50000")
    await strategy.on_fill(trade)

    # Position should be removed via reverse lookup
    assert "BTC/USDT" not in strategy._open_positions


@pytest.mark.asyncio
async def test_on_fill_resolves_spot_symbol_directly():
    """US-322: on_fill with spot symbol still works (backward compat)."""
    config = SpotFuturesConfig(min_basis_bps=Decimal("10"))
    strategy = SpotFuturesStrategy("sf_basis", make_calculator(), config)
    await strategy.start()
    strategy._holding_timeout_enabled = True

    strategy._open_positions["ETH/USDT"] = OpenPosition(
        symbol="ETH/USDT",
        entry_time=time.monotonic(),
        entry_price=Decimal("3000"),
        size=Decimal("1.0"),
        side="backwardation",
        exchange_id="binance",
        futures_symbol="ETH/USDT:USDT",
    )

    trade = MagicMock(spec=Trade)
    trade.symbol = "ETH/USDT"  # spot symbol directly
    trade.metadata = {"leg_type": "timeout_close_spot"}
    trade.fee = Decimal("0")
    trade.side = OrderSide.BUY
    trade.size = Decimal("1.0")
    trade.price = Decimal("3000")
    await strategy.on_fill(trade)

    assert "ETH/USDT" not in strategy._open_positions
