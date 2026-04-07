"""Tests for VirtualBalanceTracker and Depth-Based Sizing (US-061).

TDD test suite defining expected behaviour for:
1. VirtualBalanceTracker — per-exchange virtual balance management in shadow mode
2. compute_depth_trade_size — min(L1_ask, L1_bid) * fraction, clamped [0.001, 10]
3. ShadowMode._execute_shadow_trade integration with balance deduct/credit

Run after implementation:
    cd engine && python -m pytest tests/test_virtual_balance.py -x --tb=short -v
"""
import os
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog.testing

from src.modes.shadow import VirtualBalanceTracker, ShadowMode
from src.core.signal import compute_depth_trade_size
from src.core.order_book import OrderBook
from src.core.models import OrderSide, Signal, Trade


# ---------------------------------------------------------------------------
# VirtualBalanceTracker — unit tests
# ---------------------------------------------------------------------------


def test_initial_balance_default():
    """get_balance returns the env-var default (10000000) per exchange when no custom amount given."""
    tracker = VirtualBalanceTracker()
    assert tracker.get_balance("binance") == Decimal(
        os.getenv("SHADOW_INITIAL_BALANCE_USDT", "10000000")
    )


def test_initial_balance_custom():
    """Custom initial_balance_usdt overrides the env-var default."""
    tracker = VirtualBalanceTracker(Decimal("5000"))
    assert tracker.get_balance("binance") == Decimal("5000")


def test_deduct_success():
    """deduct returns True and reduces exchange balance by the requested amount."""
    tracker = VirtualBalanceTracker(Decimal("10000"))
    result = tracker.deduct("binance", Decimal("1000"))
    assert result is True
    assert tracker.get_balance("binance") == Decimal("9000")


def test_deduct_insufficient():
    """deduct returns False and leaves balance unchanged when funds are insufficient."""
    tracker = VirtualBalanceTracker(Decimal("100"))
    result = tracker.deduct("binance", Decimal("200"))
    assert result is False
    assert tracker.get_balance("binance") == Decimal("100")


def test_credit():
    """credit adds the given amount to the exchange balance."""
    tracker = VirtualBalanceTracker(Decimal("10000"))
    tracker.credit("binance", Decimal("500"))
    assert tracker.get_balance("binance") == Decimal("10500")


def test_rebalance_warning():
    """deduct logs shadow_mode.rebalance_needed when balance falls below 10% of initial."""
    with structlog.testing.capture_logs() as cap_logs:
        tracker = VirtualBalanceTracker(Decimal("10000"))
        # Deplete to 500 — below the 10% threshold (1000)
        tracker.deduct("binance", Decimal("9500"))

    events = [e["event"] for e in cap_logs]
    assert "paper_mode.rebalance_needed" in events


def test_reset():
    """reset() clears all per-exchange balances so the next call returns the initial value."""
    tracker = VirtualBalanceTracker(Decimal("10000"))
    tracker.deduct("binance", Decimal("4000"))
    tracker.reset()
    assert tracker.get_balance("binance") == Decimal("10000")


def test_summary():
    """summary() returns a str-keyed dict with current balance strings for all used exchanges."""
    tracker = VirtualBalanceTracker(Decimal("10000"))
    tracker.get_balance("binance")
    tracker.get_balance("upbit")
    tracker.deduct("binance", Decimal("1000"))

    result = tracker.summary()
    assert isinstance(result, dict)
    assert "binance" in result and "upbit" in result
    assert result["binance"] == str(Decimal("9000"))
    assert result["upbit"] == str(Decimal("10000"))


def test_multi_exchange_isolation():
    """Deducting from binance does not affect the upbit balance."""
    tracker = VirtualBalanceTracker(Decimal("10000"))
    tracker.deduct("binance", Decimal("3000"))
    assert tracker.get_balance("upbit") == Decimal("10000")


# ---------------------------------------------------------------------------
# Depth-Based Sizing — unit tests for compute_depth_trade_size()
# ---------------------------------------------------------------------------


def test_depth_based_sizing_basic():
    """min(buy_depth, sell_depth) * fraction with explicit params."""
    # min(10, 20) * 0.10 = 1.0
    size = compute_depth_trade_size(Decimal("10"), Decimal("20"), depth_fraction=Decimal("0.10"), max_trade=Decimal("10"))
    assert size == Decimal("1.0")


def test_depth_based_sizing_clamp_max():
    """Computed size is clamped to max_trade when depth is very large."""
    size = compute_depth_trade_size(Decimal("1000"), Decimal("2000"), depth_fraction=Decimal("0.10"), max_trade=Decimal("10"))
    assert size == Decimal("10")


def test_depth_based_sizing_clamp_min():
    """Computed size is clamped to the minimum of 0.001 when depth is very small."""
    size = compute_depth_trade_size(Decimal("0.005"), Decimal("0.005"), depth_fraction=Decimal("0.10"), max_trade=Decimal("10"))
    assert size == Decimal("0.001")


def test_depth_based_sizing_zero_depth():
    """Zero depth on either side falls back to the minimum trade size of 0.001."""
    size = compute_depth_trade_size(Decimal("0"), Decimal("100"))
    assert size == Decimal("0.001")


# ---------------------------------------------------------------------------
# Integration tests — ShadowMode._execute_shadow_trade with balance tracking
# ---------------------------------------------------------------------------


def _make_shadow_with_tracker(initial_usdt: Decimal = Decimal("10000")) -> ShadowMode:
    """Build a ShadowMode with mocked IO dependencies and a fresh balance tracker."""
    mock_executor = MagicMock()
    mock_executor.slippage_model = MagicMock(spec=[])  # no set_context attribute
    shadow = ShadowMode(
        signal_generator=MagicMock(),
        paper_executor=mock_executor,
    )
    shadow._balance_tracker = VirtualBalanceTracker(initial_usdt)
    return shadow


def _make_signal(buy_price: Decimal, sell_price: Decimal, volume: Decimal) -> Signal:
    return Signal(
        strategy_id="test_arb",
        symbol="BTC/USDT",
        buy_exchange="binance",
        sell_exchange="upbit",
        buy_price=buy_price,
        sell_price=sell_price,
        spread_pct=Decimal("0.01"),
        confidence=0.9,
        volume=volume,
    )


@pytest.mark.asyncio
async def test_execute_shadow_trade_deducts_balance():
    """_execute_shadow_trade deducts buy_price * volume from the buy exchange after success."""
    shadow = _make_shadow_with_tracker(Decimal("10000"))

    buy_trade = Trade(
        trade_id="t-buy", order_id="o1", exchange_id="binance",
        symbol="BTC/USDT", side=OrderSide.BUY,
        price=Decimal("50000"), amount=Decimal("0.1"),
    )
    sell_trade = Trade(
        trade_id="t-sell", order_id="o2", exchange_id="upbit",
        symbol="BTC/USDT", side=OrderSide.SELL,
        price=Decimal("50500"), amount=Decimal("0.1"),
    )
    shadow._paper_executor.execute = AsyncMock(side_effect=[buy_trade, sell_trade])

    signal = _make_signal(Decimal("50000"), Decimal("50500"), Decimal("0.1"))
    await shadow._execute_shadow_trade(signal)

    # binance should have been debited 50000 * 0.1 = 5000
    assert shadow._balance_tracker.get_balance("binance") == Decimal("10000") - Decimal("50000") * Decimal("0.1")


@pytest.mark.asyncio
async def test_execute_shadow_trade_skips_on_insufficient():
    """_execute_shadow_trade skips paper execution when buy exchange balance is insufficient."""
    shadow = _make_shadow_with_tracker(Decimal("100"))  # far below 50000 * 0.1 = 5000
    shadow._paper_executor.execute = AsyncMock()

    signal = _make_signal(Decimal("50000"), Decimal("50500"), Decimal("0.1"))
    await shadow._execute_shadow_trade(signal)

    shadow._paper_executor.execute.assert_not_called()
