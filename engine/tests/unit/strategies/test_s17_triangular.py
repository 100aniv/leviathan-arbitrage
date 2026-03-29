"""Tests for TriangularStrategy — US-266 (Bellman-Ford depth/fee) + US-267 (latency budget)."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.core.models import Signal
from src.strategies.base import CostCalculator
from src.strategies.triangular import TriangularConfig, TriangularStrategy


def _make_calc(cost: Decimal = Decimal("0")) -> CostCalculator:
    calc = MagicMock(spec=CostCalculator)
    calc.estimate_cost.return_value = cost
    return calc


def _make_signal(
    profit_bps: float = 15.0,
    signal_timestamp_ms: float | None = None,
    volume: Decimal = Decimal("1000"),
) -> Signal:
    meta: dict = {
        "path": ["USDT", "BTC", "ETH"],
        "pairs": ["BTC/USDT", "ETH/BTC", "ETH/USDT"],
        "sides": ["buy", "sell", "sell"],
        "prices": ["50000", "0.06", "3000"],
        "exchange_id": "binance",
        "profit_bps": profit_bps,
    }
    if signal_timestamp_ms is not None:
        meta["signal_timestamp_ms"] = signal_timestamp_ms
    return Signal(
        strategy_id="triangular_arb",
        symbol="BTC/USDT",
        buy_exchange="binance",
        sell_exchange="binance",
        buy_price=Decimal("50000"),
        sell_price=Decimal("50100"),
        spread_pct=Decimal(str(profit_bps / 10000)),
        confidence=0.85,
        volume=volume,
        timestamp=datetime.now(timezone.utc),
        metadata=meta,
    )


@pytest.mark.asyncio
async def test_bellman_ford_depth_prune():
    """Profit below min_profit_bps (depth-pruned edge) is rejected."""
    config = TriangularConfig(min_profit_bps=Decimal("10"))
    strategy = TriangularStrategy("tri_test1", _make_calc(), config)
    await strategy.start()
    # profit_bps=5 < min=10
    signal = _make_signal(profit_bps=5.0)
    result = await strategy.on_signal(signal)
    assert result is None


@pytest.mark.asyncio
async def test_bellman_ford_fee_integration():
    """Cost higher than gross profit → net negative → filtered."""
    config = TriangularConfig(min_profit_bps=Decimal("8"), max_position_usdt=Decimal("1000"))
    # cost per leg = 5 USDT → total cost = 15 USDT > small profit
    calc = MagicMock(spec=CostCalculator)
    calc.estimate_cost.return_value = Decimal("5")
    strategy = TriangularStrategy("tri_test2", calc, config)
    await strategy.start()
    # profit_bps=8 on 1000 USDT = $0.80 gross; cost = 3*5 = $15 → net negative
    signal = _make_signal(profit_bps=8.0, volume=Decimal("0.02"))
    result = await strategy.on_signal(signal)
    assert result is None


@pytest.mark.asyncio
async def test_latency_budget_exceed():
    """Signal older than max_latency_ms=500ms is rejected when ENABLE_LATENCY_BUDGET=true."""
    config = TriangularConfig(min_profit_bps=Decimal("5"), max_latency_ms=500.0)
    strategy = TriangularStrategy("tri_test3", _make_calc(), config)
    await strategy.start()
    # Timestamp 1 second in the past
    old_ts_ms = (time.time() - 1.0) * 1000
    signal = _make_signal(profit_bps=20.0, signal_timestamp_ms=old_ts_ms)
    with patch("src.strategies.triangular._ENABLE_LATENCY_BUDGET", True):
        result = await strategy.on_signal(signal)
    assert result is None


@pytest.mark.asyncio
async def test_latency_budget_within():
    """Signal 200ms old passes when max_latency_ms=500ms."""
    config = TriangularConfig(min_profit_bps=Decimal("5"), max_latency_ms=500.0)
    strategy = TriangularStrategy("tri_test4", _make_calc(), config)
    await strategy.start()
    recent_ts_ms = (time.time() - 0.2) * 1000
    signal = _make_signal(profit_bps=20.0, signal_timestamp_ms=recent_ts_ms)
    with patch("src.strategies.triangular._ENABLE_LATENCY_BUDGET", True):
        result = await strategy.on_signal(signal)
    assert result is not None
