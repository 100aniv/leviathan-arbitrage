"""Tests for SpotFuturesStrategy — US-270 (OU basis filter) + US-271 (holding timeout)."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.core.models import Signal
from src.strategies.base import CostCalculator
from src.strategies.spot_futures import OpenPosition, SpotFuturesConfig, SpotFuturesStrategy


def _make_calc(cost: Decimal = Decimal("0")) -> CostCalculator:
    calc = MagicMock(spec=CostCalculator)
    calc.estimate_cost.return_value = cost
    return calc


def _make_signal(
    basis_bps: float = 30.0,
    exchange: str = "binance",
) -> Signal:
    return Signal(
        strategy_id="spot_futures_basis",
        symbol="BTC/USDT:USDT",
        buy_exchange=exchange,
        sell_exchange=exchange,
        buy_price=Decimal("50000"),
        sell_price=Decimal("50150"),
        spread_pct=Decimal(str(basis_bps / 10000)),
        confidence=0.8,
        volume=Decimal("0.1"),
        timestamp=datetime.now(timezone.utc),
        metadata={
            "basis_bps": str(basis_bps),
            "spot_symbol": "BTC/USDT",
            "futures_symbol": "BTC/USDT:USDT",
            "funding_rate": "0.0001",
        },
    )


@pytest.mark.asyncio
async def test_ou_basis_filter_slow_reversion():
    """OU half_life > max_basis_halflife_h → signal filtered."""
    config = SpotFuturesConfig(
        min_basis_bps=Decimal("10"),
        enable_basis_ou_filter=True,
        max_basis_halflife_h=24.0,
    )
    strategy = SpotFuturesStrategy("sf_test1", _make_calc(), config)
    await strategy.start()

    # Inject a mock OU process with very slow reversion (half_life = 48h = 172800s)
    ou_mock = MagicMock()
    ou_mock.is_mean_reverting = True
    ou_mock.half_life = 172800.0  # 48 hours in seconds
    strategy._ou_basis = ou_mock

    signal = _make_signal(basis_bps=30.0)
    result = await strategy.on_signal(signal)
    assert result is None


@pytest.mark.asyncio
async def test_ou_basis_no_abs():
    """basis_bps is passed as signed value (no abs()) — negative basis still filtered by abs check."""
    config = SpotFuturesConfig(
        min_basis_bps=Decimal("10"),
        enable_basis_ou_filter=False,
    )
    strategy = SpotFuturesStrategy("sf_test2", _make_calc(), config)
    await strategy.start()

    # basis_bps = -5 → abs = 5 < min=10 → should be filtered
    signal = _make_signal(basis_bps=-5.0)
    result = await strategy.on_signal(signal)
    assert result is None


@pytest.mark.asyncio
async def test_holding_timeout_expired():
    """Position held > max_holding_hours returns a closing TradeRequest."""
    config = SpotFuturesConfig(
        min_basis_bps=Decimal("10"),
        max_holding_hours=8.0,
        enable_basis_ou_filter=False,
    )
    strategy = SpotFuturesStrategy("sf_test3", _make_calc(), config)
    await strategy.start()
    strategy._holding_timeout_enabled = True

    # Inject expired position (entry 9 hours ago)
    expired_entry = time.monotonic() - 9 * 3600
    strategy._open_positions["BTC/USDT:USDT"] = OpenPosition(
        symbol="BTC/USDT:USDT",
        entry_time=expired_entry,
        entry_price=Decimal("50000"),
        size=Decimal("0.1"),
        side="contango",
        exchange_id="binance",
    )

    signal = _make_signal(basis_bps=30.0)
    await strategy.on_signal(signal)  # queues close in _pending_timeout_requests

    # BUG-CRITICAL fix: on_signal() no longer returns timeout close inline.
    # pop_exit_requests() is the sole consumer — prevents duplicate exits.
    exits = strategy.pop_exit_requests()
    assert len(exits) == 1, f"Expected 1 exit request, got {len(exits)}"
    result = exits[0]
    assert result.metadata.get("reason") == "holding_timeout"
    # Both spot + futures legs must be closed
    assert len(result.legs) == 2
    leg_types = {leg.metadata.get("leg_type") for leg in result.legs}
    assert "timeout_close_spot" in leg_types
    assert "timeout_close_futures" in leg_types


@pytest.mark.asyncio
async def test_holding_timeout_not_expired():
    """Position held < max_holding_hours → timeout not triggered, normal flow continues."""
    config = SpotFuturesConfig(
        min_basis_bps=Decimal("10"),
        max_holding_hours=8.0,
        enable_basis_ou_filter=False,
    )
    strategy = SpotFuturesStrategy("sf_test4", _make_calc(), config)
    await strategy.start()
    strategy._holding_timeout_enabled = True

    # Inject fresh position (1 hour ago)
    recent_entry = time.monotonic() - 1 * 3600
    strategy._open_positions["BTC/USDT:USDT"] = OpenPosition(
        symbol="BTC/USDT:USDT",
        entry_time=recent_entry,
        entry_price=Decimal("50000"),
        size=Decimal("0.1"),
        side="contango",
        exchange_id="binance",
    )

    signal = _make_signal(basis_bps=30.0)
    result = await strategy.on_signal(signal)
    # Not a timeout close — may return TradeRequest or None depending on other filters
    if result is not None:
        assert result.metadata.get("reason") != "holding_timeout"


def test_open_position_full_state():
    """OpenPosition dataclass carries entry_price, side, and exchange_id fields."""
    pos = OpenPosition(
        symbol="ETH/USDT:USDT",
        entry_time=time.monotonic(),
        entry_price=Decimal("3000"),
        size=Decimal("1.0"),
        side="backwardation",
        exchange_id="bybit",
    )
    assert pos.entry_price == Decimal("3000")
    assert pos.side == "backwardation"
    assert pos.exchange_id == "bybit"
