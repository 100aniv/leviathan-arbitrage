"""Tests for CrossExchangeStrategy."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.core.models import OrderSide, Signal
from src.strategies.base import CostCalculator
from src.strategies.cross_exchange import CrossExchangeConfig, CrossExchangeStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_calculator(cost_per_leg: Decimal = Decimal("1")) -> CostCalculator:
    calc = MagicMock(spec=CostCalculator)
    calc.estimate_cost.return_value = cost_per_leg
    return calc


def make_signal(
    spread_pct: Decimal = Decimal("0.002"),  # 20 bps
    buy_price: Decimal = Decimal("50000"),
    sell_price: Decimal = Decimal("50100"),
    volume: Decimal = Decimal("0.5"),
) -> Signal:
    return Signal(
        strategy_id="cross_exchange_spot_v1",
        symbol="BTC/USDT",
        buy_exchange="binance",
        sell_exchange="okx",
        buy_price=buy_price,
        sell_price=sell_price,
        spread_pct=spread_pct,
        confidence=0.95,
        volume=volume,
        timestamp=datetime.utcnow(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signal_below_spread_threshold_returns_none():
    strategy = CrossExchangeStrategy(
        "cex_spot",
        make_calculator(),
        CrossExchangeConfig(min_spread_bps=Decimal("20")),
    )
    await strategy.start()
    signal = make_signal(spread_pct=Decimal("0.0015"))  # 15 bps < 20 bps
    result = await strategy.on_signal(signal)
    assert result is None
    assert strategy.metrics.signals_filtered == 1


@pytest.mark.asyncio
async def test_signal_above_threshold_generates_trade_request():
    """20 bps spread, 1 USDT cost per leg → net profit = (50100-50000)*0.5 - 2 = 48 USDT."""
    strategy = CrossExchangeStrategy(
        "cex_spot",
        make_calculator(Decimal("1")),
        CrossExchangeConfig(min_spread_bps=Decimal("10")),
    )
    await strategy.start()
    signal = make_signal(spread_pct=Decimal("0.002"), volume=Decimal("0.5"))
    result = await strategy.on_signal(signal)

    assert result is not None
    assert result.strategy_id == "cex_spot"
    assert len(result.legs) == 2
    assert result.expected_profit_usdt == Decimal("48")  # (50100-50000)*0.5 - 2


@pytest.mark.asyncio
async def test_legs_have_correct_exchanges_and_sides():
    strategy = CrossExchangeStrategy("cex_spot", make_calculator(), CrossExchangeConfig())
    await strategy.start()
    signal = make_signal()
    result = await strategy.on_signal(signal)
    assert result is not None

    buy_leg = next(l for l in result.legs if l.side == OrderSide.BUY)
    sell_leg = next(l for l in result.legs if l.side == OrderSide.SELL)
    assert buy_leg.exchange_id == "binance"
    assert sell_leg.exchange_id == "okx"
    assert buy_leg.symbol == "BTC/USDT"
    assert sell_leg.symbol == "BTC/USDT"


@pytest.mark.asyncio
async def test_size_capped_by_max_position_size():
    config = CrossExchangeConfig(min_spread_bps=Decimal("10"), max_position_size=Decimal("0.2"))
    strategy = CrossExchangeStrategy("cex_spot", make_calculator(), config)
    await strategy.start()
    signal = make_signal(volume=Decimal("1.0"))  # more than max
    result = await strategy.on_signal(signal)
    assert result is not None
    assert result.legs[0].size == Decimal("0.2")


@pytest.mark.asyncio
async def test_no_trade_when_costs_exceed_profit():
    """High cost per leg makes net profit negative → return None."""
    calc = make_calculator(Decimal("100"))  # 100 USDT per leg
    strategy = CrossExchangeStrategy("cex_spot", calc, CrossExchangeConfig(min_spread_bps=Decimal("10")))
    await strategy.start()
    # Gross profit = (50100 - 50000) * 0.5 = 50 USDT; total cost = 200 USDT
    signal = make_signal(volume=Decimal("0.5"))
    result = await strategy.on_signal(signal)
    assert result is None
    assert strategy.metrics.signals_filtered >= 1


@pytest.mark.asyncio
async def test_inactive_strategy_returns_none():
    strategy = CrossExchangeStrategy("cex_spot", make_calculator())
    # Not started — is_active == False
    signal = make_signal()
    result = await strategy.on_signal(signal)
    assert result is None


@pytest.mark.asyncio
async def test_metrics_track_correctly():
    strategy = CrossExchangeStrategy("cex_spot", make_calculator(Decimal("1")))
    await strategy.start()
    signal = make_signal()
    await strategy.on_signal(signal)
    await strategy.on_signal(signal)
    assert strategy.metrics.signals_received == 2
    assert strategy.metrics.trade_requests_generated == 2


@pytest.mark.asyncio
async def test_cost_calculator_called_for_both_legs():
    calc = make_calculator()
    strategy = CrossExchangeStrategy("cex_spot", calc, CrossExchangeConfig(min_spread_bps=Decimal("10")))
    await strategy.start()
    signal = make_signal()
    await strategy.on_signal(signal)
    assert calc.estimate_cost.call_count == 2
    calls = {call.kwargs["exchange_id"] for call in calc.estimate_cost.call_args_list}
    assert calls == {"binance", "okx"}
