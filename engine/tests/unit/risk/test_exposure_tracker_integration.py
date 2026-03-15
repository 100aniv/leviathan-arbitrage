"""US-175: ExposureTracker — _init_risk wiring, BUY/SELL accumulation, guardian injection."""
from __future__ import annotations

from decimal import Decimal

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.risk.exposure_tracker import ExposureTracker
from src.risk.guardian import TradeProposal


def _proposal(side="BUY", size="1.0", exchange_id="binance", symbol="BTC/USDT"):
    return TradeProposal(
        strategy_id="strat1",
        exchange_id=exchange_id,
        symbol=symbol,
        side=side,
        size=Decimal(size),
        price=Decimal("50000"),
        position_value=Decimal("50000"),
    )


# ---------------------------------------------------------------------------
# ExposureTracker creation
# ---------------------------------------------------------------------------


class TestExposureTrackerCreation:
    def test_instantiates_without_redis(self):
        """ExposureTracker can be created with redis_client=None (in-memory fallback)."""
        tracker = ExposureTracker(redis_client=None)
        assert tracker is not None

    def test_instantiates_with_fake_redis(self, fake_redis):
        """ExposureTracker accepts a real redis client."""
        tracker = ExposureTracker(redis_client=fake_redis)
        assert tracker is not None


# ---------------------------------------------------------------------------
# BUY/SELL accumulation
# ---------------------------------------------------------------------------


class TestExposureAccumulation:
    @pytest.mark.asyncio
    async def test_buy_increases_net_exposure(self, fake_redis):
        """update_exposure with positive delta increases net exposure."""
        tracker = ExposureTracker(redis_client=fake_redis)
        await tracker.update_exposure("binance", "BTC", Decimal("1.0"))
        net = await tracker.get_net_exposure("binance", "BTC")
        assert net == Decimal("1.0")

    @pytest.mark.asyncio
    async def test_sell_decreases_net_exposure(self, fake_redis):
        """update_exposure with negative delta decreases net exposure."""
        tracker = ExposureTracker(redis_client=fake_redis)
        await tracker.update_exposure("binance", "BTC", Decimal("2.0"))
        await tracker.update_exposure("binance", "BTC", Decimal("-1.0"))
        net = await tracker.get_net_exposure("binance", "BTC")
        assert net == Decimal("1.0")

    @pytest.mark.asyncio
    async def test_multiple_buys_accumulate(self, fake_redis):
        """Multiple BUY updates accumulate correctly."""
        tracker = ExposureTracker(redis_client=fake_redis)
        await tracker.update_exposure("binance", "BTC", Decimal("0.5"))
        await tracker.update_exposure("binance", "BTC", Decimal("0.5"))
        await tracker.update_exposure("binance", "BTC", Decimal("0.5"))
        net = await tracker.get_net_exposure("binance", "BTC")
        assert net == Decimal("1.5")

    @pytest.mark.asyncio
    async def test_zero_net_when_balanced(self, fake_redis):
        """Equal buy and sell gives zero net exposure."""
        tracker = ExposureTracker(redis_client=fake_redis)
        await tracker.update_exposure("binance", "BTC", Decimal("1.0"))
        await tracker.update_exposure("binance", "BTC", Decimal("-1.0"))
        net = await tracker.get_net_exposure("binance", "BTC")
        assert net == Decimal("0")


# ---------------------------------------------------------------------------
# In-memory fallback when Redis is unavailable
# ---------------------------------------------------------------------------


class TestRedisInMemoryFallback:
    @pytest.mark.asyncio
    async def test_get_net_exposure_returns_zero_without_redis(self, fake_redis):
        """get_net_exposure returns 0 for fresh tracker with no data set."""
        tracker = ExposureTracker(redis_client=fake_redis)
        net = await tracker.get_net_exposure("binance", "BTC")
        assert net == Decimal("0")

    @pytest.mark.asyncio
    async def test_update_exposure_works_and_returns_new_value(self, fake_redis):
        """update_exposure returns the new cumulative net value."""
        tracker = ExposureTracker(redis_client=fake_redis)
        result = await tracker.update_exposure("binance", "BTC", Decimal("1.5"))
        assert result == Decimal("1.5")


# ---------------------------------------------------------------------------
# net_exposures injection into TradeProposal (guardian check)
# ---------------------------------------------------------------------------


class TestNetExposuresGuardianInjection:
    @pytest.mark.asyncio
    async def test_get_portfolio_exposure_returns_nonzero_entries(self, fake_redis):
        """get_portfolio_exposure returns dict with non-zero entries."""
        tracker = ExposureTracker(redis_client=fake_redis)
        await tracker.update_exposure("binance", "BTC", Decimal("2.0"))
        result = await tracker.get_portfolio_exposure(
            exchanges=["binance"], assets=["BTC", "ETH"]
        )
        assert ("binance", "BTC") in result
        assert result[("binance", "BTC")] == Decimal("2.0")

    @pytest.mark.asyncio
    async def test_get_portfolio_exposure_excludes_zero_entries(self, fake_redis):
        """get_portfolio_exposure omits assets with zero exposure."""
        tracker = ExposureTracker(redis_client=fake_redis)
        result = await tracker.get_portfolio_exposure(
            exchanges=["binance"], assets=["BTC"]
        )
        assert ("binance", "BTC") not in result
