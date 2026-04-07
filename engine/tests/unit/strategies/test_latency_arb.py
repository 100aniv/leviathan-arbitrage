"""Tests for latency_boost mode in CrossExchangeStrategy (US-194 migration).

Previously tested LatencyArbStrategy; now tests CrossExchangeStrategy with
latency_boost=True which absorbed the latency-arbitrage logic.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.core.latency_tracker import LatencyTracker
from src.core.models import OrderSide, Signal
from src.strategies.base import CostCalculator
from src.strategies.cross_exchange import CrossExchangeConfig, CrossExchangeStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_calculator(cost: Decimal = Decimal("1")) -> CostCalculator:
    calc = MagicMock(spec=CostCalculator)
    calc.estimate_cost.return_value = cost
    return calc


def make_signal(
    buy_price: Decimal = Decimal("50000"),
    sell_price: Decimal = Decimal("50100"),
    volume: Decimal = Decimal("0.5"),
    spread_pct: Decimal = Decimal("0.002"),
    buy_exchange: str = "binance",
    sell_exchange: str = "okx",
    net_profit: str | None = None,
) -> Signal:
    metadata: dict = {}
    if net_profit is not None:
        metadata["net_profit"] = net_profit
    return Signal(
        strategy_id="cross_exchange_v1",
        symbol="BTC/USDT",
        buy_exchange=buy_exchange,
        sell_exchange=sell_exchange,
        buy_price=buy_price,
        sell_price=sell_price,
        spread_pct=spread_pct,
        confidence=0.9,
        volume=volume,
        timestamp=datetime.now(timezone.utc),
        metadata=metadata,
    )


def make_tracker(
    fast: str = "binance",
    slow: str = "okx",
    fast_ms: float = 2.0,
    slow_ms: float = 20.0,
) -> LatencyTracker:
    tracker = LatencyTracker(window_size=5)
    tracker.record_latency(fast, fast_ms)
    tracker.record_latency(slow, slow_ms)
    return tracker


def make_strategy(
    tracker: LatencyTracker,
    min_latency_advantage_ms: float = 5.0,
    max_position_size: Decimal = Decimal("1.0"),
    strategy_id: str = "lat_arb",
    cost: Decimal = Decimal("1"),
) -> CrossExchangeStrategy:
    config = CrossExchangeConfig(
        min_spread_bps=Decimal("0"),
        max_position_size=max_position_size,
        latency_boost=True,
        min_latency_advantage_ms=min_latency_advantage_ms,
    )
    return CrossExchangeStrategy(strategy_id, make_calculator(cost), config=config, latency_tracker=tracker)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generates_trade_when_latency_advantage_sufficient():
    """18ms advantage (2ms vs 20ms) > 5ms threshold → should trade."""
    strategy = make_strategy(make_tracker(fast_ms=2.0, slow_ms=20.0))
    await strategy.start()

    result = await strategy.on_signal(make_signal())

    assert result is not None
    assert result.strategy_id == "lat_arb"
    assert result.expected_profit_usdt > Decimal("0")


@pytest.mark.asyncio
async def test_filtered_when_advantage_below_threshold():
    """2ms advantage (10ms vs 12ms) < 5ms threshold → should be filtered."""
    strategy = make_strategy(make_tracker(fast_ms=10.0, slow_ms=12.0))
    await strategy.start()

    result = await strategy.on_signal(make_signal())

    assert result is None
    assert strategy.metrics.signals_filtered == 1


@pytest.mark.asyncio
async def test_auto_disable_when_no_latency_data():
    """If tracker has no data for the signal's exchanges → filtered."""
    tracker = LatencyTracker(window_size=5)  # empty
    strategy = make_strategy(tracker)
    await strategy.start()

    result = await strategy.on_signal(make_signal())

    assert result is None
    assert strategy.metrics.signals_filtered == 1


@pytest.mark.asyncio
async def test_inactive_strategy_returns_none():
    strategy = make_strategy(make_tracker())
    # Not started — is_active == False
    result = await strategy.on_signal(make_signal())
    assert result is None


@pytest.mark.asyncio
async def test_no_trade_when_costs_exceed_profit():
    """High friction costs eat all profit → return None."""
    tracker = make_tracker(fast_ms=1.0, slow_ms=50.0)
    strategy = make_strategy(tracker, cost=Decimal("100"))

    await strategy.start()

    # S22: SignalGenerator pre-computes net_profit (gross 10 - cost 200 = -190)
    result = await strategy.on_signal(make_signal(volume=Decimal("0.1"), net_profit="-190"))

    assert result is None
    assert strategy.metrics.signals_filtered >= 1


@pytest.mark.asyncio
async def test_size_capped_by_max_position_size():
    # max_position_size is USD notional: $10010 / avg_price($50050) = 0.2 BTC cap
    strategy = make_strategy(make_tracker(), max_position_size=Decimal("10010"))
    await strategy.start()

    result = await strategy.on_signal(make_signal(volume=Decimal("1.0")))

    assert result is not None
    assert result.legs[0].size == Decimal("0.2")


@pytest.mark.asyncio
async def test_trade_has_buy_and_sell_legs():
    strategy = make_strategy(make_tracker())
    await strategy.start()

    result = await strategy.on_signal(make_signal())

    assert result is not None
    assert len(result.legs) == 2
    sides = {leg.side for leg in result.legs}
    assert OrderSide.BUY in sides
    assert OrderSide.SELL in sides


@pytest.mark.asyncio
async def test_legs_assigned_to_correct_exchanges():
    strategy = make_strategy(make_tracker(fast="binance", slow="okx"))
    await strategy.start()

    result = await strategy.on_signal(make_signal(buy_exchange="binance", sell_exchange="okx"))

    assert result is not None
    buy_leg = next(l for l in result.legs if l.side == OrderSide.BUY)
    sell_leg = next(l for l in result.legs if l.side == OrderSide.SELL)
    assert buy_leg.exchange_id == "binance"
    assert sell_leg.exchange_id == "okx"


@pytest.mark.asyncio
async def test_metadata_includes_latency_advantage_and_mode():
    """latency_boost mode tags metadata with mode=latency_boost and latency_advantage_ms."""
    strategy = make_strategy(make_tracker(fast_ms=2.0, slow_ms=20.0))
    await strategy.start()

    result = await strategy.on_signal(make_signal())

    assert result is not None
    assert result.metadata.get("mode") == "latency_boost"
    assert "latency_advantage_ms" in result.metadata
    assert float(result.metadata["latency_advantage_ms"]) == pytest.approx(18.0, rel=1e-3)


@pytest.mark.asyncio
async def test_metrics_track_signals_and_requests():
    strategy = make_strategy(make_tracker())
    await strategy.start()

    await strategy.on_signal(make_signal())
    await strategy.on_signal(make_signal())

    assert strategy.metrics.signals_received == 2
    assert strategy.metrics.trade_requests_generated == 2
