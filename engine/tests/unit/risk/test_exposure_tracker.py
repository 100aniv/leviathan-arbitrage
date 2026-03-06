"""Tests for engine/src/risk/exposure_tracker.py — TDD first."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.risk.exposure_tracker import ExposureTracker
from src.risk.guardian import TradeProposal


@pytest.fixture
def tracker(fake_redis):
    return ExposureTracker(redis_client=fake_redis)


def make_proposal(
    side: str = "BUY",
    size: Decimal = Decimal("1.0"),
    symbol: str = "BTC/USDT",
    exchange_id: str = "binance",
) -> TradeProposal:
    return TradeProposal(
        strategy_id="strat1",
        exchange_id=exchange_id,
        symbol=symbol,
        side=side,
        size=size,
        price=Decimal("50000"),
        position_value=Decimal("50000"),
    )


# ---------------------------------------------------------------------------
# get_net_exposure
# ---------------------------------------------------------------------------


class TestGetNetExposure:
    async def test_zero_when_no_data(self, tracker):
        net = await tracker.get_net_exposure("binance", "BTC")
        assert net == Decimal("0")

    async def test_returns_stored_value(self, tracker, fake_redis):
        await fake_redis.set("leviathan:exposure:binance:BTC", "1.5")
        net = await tracker.get_net_exposure("binance", "BTC")
        assert net == Decimal("1.5")

    async def test_returns_negative_value(self, tracker, fake_redis):
        await fake_redis.set("leviathan:exposure:binance:BTC", "-2.3")
        net = await tracker.get_net_exposure("binance", "BTC")
        assert net == Decimal("-2.3")

    async def test_isolates_by_asset(self, tracker, fake_redis):
        await fake_redis.set("leviathan:exposure:binance:BTC", "1.0")
        eth_net = await tracker.get_net_exposure("binance", "ETH")
        assert eth_net == Decimal("0")

    async def test_isolates_by_exchange(self, tracker, fake_redis):
        await fake_redis.set("leviathan:exposure:binance:BTC", "1.0")
        okx_net = await tracker.get_net_exposure("okx", "BTC")
        assert okx_net == Decimal("0")


# ---------------------------------------------------------------------------
# update_exposure
# ---------------------------------------------------------------------------


class TestUpdateExposure:
    async def test_update_increases_exposure(self, tracker):
        result = await tracker.update_exposure("binance", "BTC", Decimal("1.0"))
        assert result == Decimal("1.0")
        net = await tracker.get_net_exposure("binance", "BTC")
        assert net == Decimal("1.0")

    async def test_update_accumulates(self, tracker):
        await tracker.update_exposure("binance", "BTC", Decimal("1.0"))
        await tracker.update_exposure("binance", "BTC", Decimal("0.5"))
        net = await tracker.get_net_exposure("binance", "BTC")
        assert net == Decimal("1.5")

    async def test_negative_delta_decreases_exposure(self, tracker):
        await tracker.update_exposure("binance", "BTC", Decimal("2.0"))
        await tracker.update_exposure("binance", "BTC", Decimal("-1.0"))
        net = await tracker.get_net_exposure("binance", "BTC")
        assert net == Decimal("1.0")

    async def test_returns_new_net_value(self, tracker):
        await tracker.update_exposure("binance", "BTC", Decimal("1.0"))
        result = await tracker.update_exposure("binance", "BTC", Decimal("0.5"))
        assert result == Decimal("1.5")

    async def test_different_assets_independent(self, tracker):
        await tracker.update_exposure("binance", "BTC", Decimal("1.0"))
        await tracker.update_exposure("binance", "ETH", Decimal("5.0"))
        assert await tracker.get_net_exposure("binance", "BTC") == Decimal("1.0")
        assert await tracker.get_net_exposure("binance", "ETH") == Decimal("5.0")


# ---------------------------------------------------------------------------
# check_correlation (Amendment 7 Scenario 5)
# ---------------------------------------------------------------------------


class TestCheckCorrelation:
    async def test_safe_when_net_within_limit(self, tracker):
        await tracker.update_exposure("binance", "BTC", Decimal("0.5"))
        proposal = make_proposal(side="BUY", size=Decimal("0.4"))
        is_safe = await tracker.check_correlation(proposal, max_net_exposure=Decimal("1.0"))
        assert is_safe is True  # |0.5 + 0.4| = 0.9 ≤ 1.0

    async def test_unsafe_when_net_exceeds_limit(self, tracker):
        await tracker.update_exposure("binance", "BTC", Decimal("0.8"))
        proposal = make_proposal(side="BUY", size=Decimal("0.5"))
        is_safe = await tracker.check_correlation(proposal, max_net_exposure=Decimal("1.0"))
        assert is_safe is False  # |0.8 + 0.5| = 1.3 > 1.0

    async def test_sell_reduces_net_exposure(self, tracker):
        await tracker.update_exposure("binance", "BTC", Decimal("2.0"))
        proposal = make_proposal(side="SELL", size=Decimal("1.0"))
        is_safe = await tracker.check_correlation(proposal, max_net_exposure=Decimal("1.0"))
        assert is_safe is True  # |2.0 - 1.0| = 1.0 ≤ 1.0

    async def test_short_side_detected(self, tracker):
        """Amendment 7: strategy A long, strategy B short creates basis position."""
        await tracker.update_exposure("binance", "BTC", Decimal("-0.8"))
        proposal = make_proposal(side="SELL", size=Decimal("0.5"))
        is_safe = await tracker.check_correlation(proposal, max_net_exposure=Decimal("1.0"))
        assert is_safe is False  # |-0.8 - 0.5| = 1.3 > 1.0

    async def test_exactly_at_limit_is_safe(self, tracker):
        await tracker.update_exposure("binance", "BTC", Decimal("0.5"))
        proposal = make_proposal(side="BUY", size=Decimal("0.5"))
        is_safe = await tracker.check_correlation(proposal, max_net_exposure=Decimal("1.0"))
        assert is_safe is True  # |0.5 + 0.5| = 1.0 ≤ 1.0 (boundary)

    async def test_zero_existing_exposure_small_trade_safe(self, tracker):
        proposal = make_proposal(side="BUY", size=Decimal("0.9"))
        is_safe = await tracker.check_correlation(proposal, max_net_exposure=Decimal("1.0"))
        assert is_safe is True

    async def test_symbol_without_slash_returns_safe(self, tracker):
        """Graceful handling of non-pair symbols."""
        proposal = make_proposal(symbol="BTCUSDT")  # no slash
        is_safe = await tracker.check_correlation(proposal, max_net_exposure=Decimal("1.0"))
        assert is_safe is True

    async def test_different_exchange_not_counted(self, tracker):
        """Net exposure on OKX doesn't affect Binance check."""
        await tracker.update_exposure("okx", "BTC", Decimal("5.0"))
        proposal = make_proposal(side="BUY", size=Decimal("0.9"), exchange_id="binance")
        is_safe = await tracker.check_correlation(proposal, max_net_exposure=Decimal("1.0"))
        assert is_safe is True  # Binance has no exposure yet


# ---------------------------------------------------------------------------
# get_portfolio_exposure
# ---------------------------------------------------------------------------


class TestGetPortfolioExposure:
    async def test_returns_nonzero_exposures(self, tracker):
        await tracker.update_exposure("binance", "BTC", Decimal("1.5"))
        await tracker.update_exposure("binance", "ETH", Decimal("-0.5"))
        result = await tracker.get_portfolio_exposure(
            exchanges=["binance"], assets=["BTC", "ETH", "SOL"]
        )
        assert result[("binance", "BTC")] == Decimal("1.5")
        assert result[("binance", "ETH")] == Decimal("-0.5")
        assert ("binance", "SOL") not in result  # zero exposure excluded

    async def test_empty_when_no_positions(self, tracker):
        result = await tracker.get_portfolio_exposure(
            exchanges=["binance"], assets=["BTC"]
        )
        assert result == {}
