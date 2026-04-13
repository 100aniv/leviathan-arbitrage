"""Tests for FundingRateStrategy."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.core.models import OrderSide, Signal
from src.strategies.base import CostCalculator
from src.strategies.funding_rate import FundingRateConfig, FundingRateStrategy


def make_calculator(cost: Decimal = Decimal("0.5")) -> CostCalculator:
    calc = MagicMock(spec=CostCalculator)
    calc.estimate_cost.return_value = cost
    return calc


def make_signal(
    funding_rate_sell: Decimal = Decimal("0.001"),   # 10 bps
    funding_rate_buy: Decimal = Decimal("-0.0003"),  # -3 bps
    buy_price: Decimal = Decimal("50000"),
    sell_price: Decimal = Decimal("50010"),
    volume: Decimal = Decimal("0.5"),
) -> Signal:
    return Signal(
        strategy_id="funding_rate_arb_v1",
        symbol="BTC/USDT:USDT",
        buy_exchange="bybit",
        sell_exchange="binance",
        buy_price=buy_price,
        sell_price=sell_price,
        spread_pct=(sell_price - buy_price) / buy_price,
        confidence=0.85,
        volume=volume,
        timestamp=datetime.now(timezone.utc),
        metadata={
            "funding_rate_sell": str(funding_rate_sell),
            "funding_rate_buy": str(funding_rate_buy),
        },
    )


@pytest.mark.asyncio
async def test_funding_diff_below_threshold_returns_none():
    config = FundingRateConfig(min_funding_diff_bps=Decimal("20"))
    strategy = FundingRateStrategy("fr_arb", make_calculator(), config)
    await strategy.start()

    # diff = 0.001 - (-0.0003) = 0.0013 = 13 bps < 20 bps
    signal = make_signal(
        funding_rate_sell=Decimal("0.001"),
        funding_rate_buy=Decimal("-0.0003"),
    )
    result = await strategy.on_signal(signal)
    assert result is None
    assert strategy.metrics.signals_filtered == 1


@pytest.mark.asyncio
async def test_sufficient_funding_diff_generates_trade():
    """diff = 0.003 - (-0.001) = 0.004 = 40 bps > 5 bps threshold."""
    config = FundingRateConfig(min_funding_diff_bps=Decimal("5"), max_holding_periods=3)
    strategy = FundingRateStrategy("fr_arb", make_calculator(Decimal("0.5")), config)
    await strategy.start()

    signal = make_signal(
        funding_rate_sell=Decimal("0.003"),
        funding_rate_buy=Decimal("-0.001"),
    )
    result = await strategy.on_signal(signal)
    assert result is not None
    assert result.strategy_id == "fr_arb"
    assert len(result.legs) == 2


@pytest.mark.asyncio
async def test_short_leg_on_sell_exchange():
    """Short where funding_rate_sell is high (shorts receive funding)."""
    strategy = FundingRateStrategy(
        "fr_arb", make_calculator(), FundingRateConfig(min_funding_diff_bps=Decimal("5"))
    )
    await strategy.start()
    signal = make_signal(funding_rate_sell=Decimal("0.003"), funding_rate_buy=Decimal("-0.001"))
    result = await strategy.on_signal(signal)
    assert result is not None

    short_leg = next(l for l in result.legs if l.metadata.get("leg_type") == "short")
    long_leg = next(l for l in result.legs if l.metadata.get("leg_type") == "long")
    assert short_leg.exchange_id == "binance"
    assert short_leg.side == OrderSide.SELL
    assert long_leg.exchange_id == "bybit"
    assert long_leg.side == OrderSide.BUY


@pytest.mark.asyncio
async def test_funding_rate_stored_in_leg_metadata():
    strategy = FundingRateStrategy(
        "fr_arb", make_calculator(), FundingRateConfig(min_funding_diff_bps=Decimal("5"))
    )
    await strategy.start()
    signal = make_signal(funding_rate_sell=Decimal("0.003"), funding_rate_buy=Decimal("-0.001"))
    result = await strategy.on_signal(signal)
    assert result is not None

    short_leg = next(l for l in result.legs if l.metadata.get("leg_type") == "short")
    long_leg = next(l for l in result.legs if l.metadata.get("leg_type") == "long")
    assert short_leg.metadata["funding_rate"] == "0.003"
    assert long_leg.metadata["funding_rate"] == "-0.001"


@pytest.mark.asyncio
async def test_hedge_ratio_applied_to_long_leg():
    config = FundingRateConfig(
        min_funding_diff_bps=Decimal("5"),
        max_position_size=Decimal("50005"),  # USD notional: $50005 / avg($50005) = 1.0 BTC cap
        hedge_ratio=Decimal("0.95"),
    )
    strategy = FundingRateStrategy("fr_arb", make_calculator(), config)
    await strategy.start()
    signal = make_signal(
        funding_rate_sell=Decimal("0.003"),
        funding_rate_buy=Decimal("-0.001"),
        volume=Decimal("1.0"),
    )
    result = await strategy.on_signal(signal)
    assert result is not None

    short_leg = next(l for l in result.legs if l.metadata.get("leg_type") == "short")
    long_leg = next(l for l in result.legs if l.metadata.get("leg_type") == "long")
    assert short_leg.size == Decimal("1.0")
    assert long_leg.size == Decimal("0.95000000")


@pytest.mark.asyncio
async def test_funding_income_metadata_present():
    config = FundingRateConfig(min_funding_diff_bps=Decimal("5"), max_holding_periods=3)
    strategy = FundingRateStrategy("fr_arb", make_calculator(), config)
    await strategy.start()
    signal = make_signal(funding_rate_sell=Decimal("0.003"), funding_rate_buy=Decimal("-0.001"))
    result = await strategy.on_signal(signal)
    assert result is not None
    assert "funding_diff_bps" in result.metadata
    assert "max_holding_periods" in result.metadata
    assert result.metadata["max_holding_periods"] == "3"


@pytest.mark.asyncio
async def test_high_entry_cost_kills_profitability():
    """If costs exceed expected funding income, no trade."""
    config = FundingRateConfig(
        min_funding_diff_bps=Decimal("5"),
        max_holding_periods=1,
    )
    strategy = FundingRateStrategy("fr_arb", make_calculator(Decimal("1000")), config)
    await strategy.start()
    signal = make_signal(
        funding_rate_sell=Decimal("0.0001"),
        funding_rate_buy=Decimal("0.00005"),
        volume=Decimal("0.01"),
    )
    result = await strategy.on_signal(signal)
    assert result is None


@pytest.mark.asyncio
async def test_inactive_strategy_returns_none():
    strategy = FundingRateStrategy("fr_arb", make_calculator())
    signal = make_signal()
    result = await strategy.on_signal(signal)
    assert result is None


@pytest.mark.asyncio
async def test_bug77_settlement_cooldown_blocks_new_entries():
    """BUG-77: After settlement fires, new entries are blocked for _SETTLEMENT_COOLDOWN_S."""
    import time
    from unittest.mock import patch

    config = FundingRateConfig(
        min_funding_diff_bps=Decimal("5"),
        settlement_window_minutes=0,  # disable window filter
    )
    strategy = FundingRateStrategy("fr_arb", make_calculator(Decimal("0.1")), config)
    await strategy.start()

    # 1. Open a position first
    sig1 = make_signal(funding_rate_sell=Decimal("0.003"), funding_rate_buy=Decimal("-0.001"))
    result = await strategy.on_signal(sig1)
    assert result is not None, "First entry should succeed"

    # 2. Trigger settlement release
    base_time = time.monotonic()
    with patch("time.monotonic", return_value=base_time):
        strategy._last_settlement_hour = -1  # force trigger
        # Simulate settlement hour
        from datetime import datetime as dt, timezone as tz
        with patch("src.strategies.funding_rate.datetime") as mock_dt:
            mock_dt.now.return_value = dt(2026, 4, 13, 8, 0, 0, tzinfo=tz.utc)
            mock_dt.side_effect = lambda *a, **kw: dt(*a, **kw)
            strategy._check_settlement_release()

    assert len(strategy._pending_settlement_positions) > 0, "Positions moved to pending"
    assert strategy._settlement_cooldown_until > 0, "Cooldown should be set"

    # 3. During cooldown: new signal should be BLOCKED
    with patch("time.monotonic", return_value=base_time + 60):  # 60s into cooldown
        sig2 = make_signal(
            funding_rate_sell=Decimal("0.003"),
            funding_rate_buy=Decimal("-0.001"),
        )
        sig2 = Signal(
            strategy_id="fr_arb",
            symbol="ETH/USDT:USDT",  # different symbol
            buy_exchange="bybit",
            sell_exchange="binance",
            buy_price=Decimal("3000"),
            sell_price=Decimal("3001"),
            spread_pct=Decimal("0.0003"),
            confidence=0.85,
            volume=Decimal("1"),
            timestamp=dt(2026, 4, 13, 8, 1, 0, tzinfo=tz.utc),
            metadata={
                "funding_rate_sell": "0.003",
                "funding_rate_buy": "-0.001",
            },
        )
        # Drain any pending exit requests first
        strategy._pending_exit_requests.clear()
        result = await strategy.on_signal(sig2)
        assert result is None, "Should be blocked by settlement cooldown"

    # 4. After cooldown expires: new signal should be ACCEPTED
    with patch("time.monotonic", return_value=base_time + 130):  # 130s > 120s cooldown
        strategy._pending_settlement_positions.clear()  # simulate confirmed settlement
        result = await strategy.on_signal(sig2)
        assert result is not None, "Should accept after cooldown expires"
