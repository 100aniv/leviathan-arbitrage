"""Tests for FuturesFuturesStrategy — US-272 (funding convergence) + US-273 (stale guard)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.core.models import Signal
from src.strategies.base import CostCalculator
from src.strategies.futures_futures import FuturesFuturesConfig, FuturesFuturesStrategy


def _make_calc(cost: Decimal = Decimal("0")) -> CostCalculator:
    calc = MagicMock(spec=CostCalculator)
    calc.estimate_cost.return_value = cost
    return calc


def _make_signal(
    spread_pct: Decimal = Decimal("0.005"),
    funding_diff_bps: float = 0.0,
    book_age_ms: float | None = 100.0,
    margin_available: Decimal = Decimal("100000"),
) -> Signal:
    metadata: dict = {
        # BUG-115: always inject margin_available so the guard doesn't block test signals
        "margin_available": str(margin_available),
    }
    if book_age_ms is not None:
        metadata["book_age_ms"] = book_age_ms
    if funding_diff_bps:
        metadata["funding_diff_bps"] = funding_diff_bps
    return Signal(
        strategy_id="futures_futures_cross_v1",
        symbol="BTC/USDT:USDT",
        buy_exchange="binance",
        sell_exchange="bybit",
        buy_price=Decimal("50000"),
        sell_price=Decimal("50250"),
        spread_pct=spread_pct,
        confidence=0.9,
        volume=Decimal("0.1"),
        timestamp=datetime.now(timezone.utc),
        metadata=metadata,
    )


@pytest.mark.asyncio
async def test_funding_convergence_combined_score():
    """spread_bps + weight*funding_diff_bps raises combined score above min threshold."""
    # spread_pct=0.001 → 10 bps; funding_diff=20 bps; weight=0.3 → combined=10+6=16 > 15
    config = FuturesFuturesConfig(
        min_spread_bps=Decimal("15"),
        enable_funding_convergence=True,
        funding_convergence_weight=Decimal("0.3"),
        enable_stale_guard=False,
        max_notional_usd=None,
    )
    strategy = FuturesFuturesStrategy("ff_test", _make_calc(), config)
    await strategy.start()
    signal = _make_signal(spread_pct=Decimal("0.001"), funding_diff_bps=20.0, book_age_ms=None)
    result = await strategy.on_signal(signal)
    assert result is not None


@pytest.mark.asyncio
async def test_funding_convergence_zero_weight():
    """weight=0 → combined_score equals raw spread_bps, same as baseline behaviour."""
    config = FuturesFuturesConfig(
        min_spread_bps=Decimal("8"),
        enable_funding_convergence=True,
        funding_convergence_weight=Decimal("0"),
        enable_stale_guard=False,
        max_notional_usd=None,
    )
    strategy = FuturesFuturesStrategy("ff_test2", _make_calc(), config)
    await strategy.start()
    # spread_pct=0.002 → 20 bps > 8 bps min → should pass regardless of funding_diff
    signal = _make_signal(spread_pct=Decimal("0.002"), funding_diff_bps=1000.0, book_age_ms=None)
    result = await strategy.on_signal(signal)
    assert result is not None


@pytest.mark.asyncio
async def test_stale_guard_missing_book_age():
    """book_age_ms absent → stale guard returns None (fail-closed)."""
    config = FuturesFuturesConfig(
        min_spread_bps=Decimal("5"),
        enable_stale_guard=True,
        max_book_age_seconds=5.0,
        max_notional_usd=None,
    )
    strategy = FuturesFuturesStrategy("ff_test3", _make_calc(), config)
    await strategy.start()
    signal = _make_signal(spread_pct=Decimal("0.01"), book_age_ms=None)
    result = await strategy.on_signal(signal)
    assert result is None


@pytest.mark.asyncio
async def test_stale_guard_stale_signal():
    """book_age_ms=6000ms > max_book_age_seconds=5s → filtered."""
    config = FuturesFuturesConfig(
        min_spread_bps=Decimal("5"),
        enable_stale_guard=True,
        max_book_age_seconds=5.0,
        max_notional_usd=None,
    )
    strategy = FuturesFuturesStrategy("ff_test4", _make_calc(), config)
    await strategy.start()
    signal = _make_signal(spread_pct=Decimal("0.01"), book_age_ms=6000.0)
    result = await strategy.on_signal(signal)
    assert result is None


@pytest.mark.asyncio
async def test_stale_guard_fresh_signal():
    """book_age_ms=2000ms < max_book_age_seconds=5s → signal proceeds normally."""
    config = FuturesFuturesConfig(
        min_spread_bps=Decimal("5"),
        enable_stale_guard=True,
        max_book_age_seconds=5.0,
        enable_funding_convergence=False,
        max_notional_usd=None,
    )
    strategy = FuturesFuturesStrategy("ff_test5", _make_calc(), config)
    await strategy.start()
    signal = _make_signal(spread_pct=Decimal("0.01"), book_age_ms=2000.0)
    result = await strategy.on_signal(signal)
    assert result is not None
