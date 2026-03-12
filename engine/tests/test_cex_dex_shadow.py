"""US-090: CEX-DEX Shadow mode validation tests."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.strategies.cex_dex import (
    AMMSlippageModel,
    CexDexConfig,
    CexDexStrategy,
)
from src.core.models import OrderSide, Signal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dex_mock(
    pool_price=Decimal("50100"),
    gas=Decimal("1"),
    reserves=(Decimal("1000"), Decimal("50000000")),
):
    mock = AsyncMock()
    mock.get_pool_price = AsyncMock(return_value=pool_price)
    mock.estimate_gas = AsyncMock(return_value=gas)
    mock.get_pool_reserves = AsyncMock(return_value=reserves)
    mock.pool_address = "0x1234567890abcdef"
    mock.dex_id = "uniswap_v3"
    return mock


def _make_signal(
    buy_price=Decimal("50000"),
    sell_price=Decimal("50000"),
    volume=Decimal("0.1"),
):
    return Signal(
        strategy_id="cex_dex_shadow_test",
        symbol="BTC/USDT",
        buy_exchange="binance",
        sell_exchange="uniswap_v3",
        buy_price=buy_price,
        sell_price=sell_price,
        spread_pct=Decimal("0.002"),
        confidence=0.8,
        volume=volume,
    )


def _make_strategy(
    dex_mock=None,
    dex_cost=None,
    min_edge_bps=Decimal("5"),
    friction_cost_pct=Decimal("0.001"),
):
    dex = dex_mock or _make_dex_mock()
    config = CexDexConfig(
        min_edge_bps=min_edge_bps,
        friction_cost_pct=friction_cost_pct,
        max_position_size=Decimal("1.0"),
    )
    return CexDexStrategy(
        strategy_id="cex_dex_shadow_test",
        cost_calculator=MagicMock(),
        dex_adapter=dex,
        cex_exchange_id="binance",
        symbol="BTC/USDT",
        config=config,
        dex_cost_calculator=dex_cost,
    )


# ---------------------------------------------------------------------------
# Shadow Compatibility Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shadow_signal_produces_trade_request():
    """Sufficient spread → TradeRequest generated."""
    strategy = _make_strategy()
    strategy._is_active = True
    signal = _make_signal()
    result = await strategy.on_signal(signal)
    assert result is not None
    assert len(result.legs) == 2


@pytest.mark.asyncio
async def test_shadow_pnl_tracking():
    """TradeRequest has positive expected_profit_usdt."""
    strategy = _make_strategy()
    strategy._is_active = True
    signal = _make_signal()
    result = await strategy.on_signal(signal)
    assert result is not None
    assert result.expected_profit_usdt > 0


@pytest.mark.asyncio
async def test_shadow_gas_cost_in_metadata():
    """Gas cost appears in DEX leg metadata."""
    strategy = _make_strategy()
    strategy._is_active = True
    signal = _make_signal()
    result = await strategy.on_signal(signal)
    assert result is not None
    dex_leg = result.legs[1]
    assert "gas_cost_usd" in dex_leg.metadata


@pytest.mark.asyncio
async def test_shadow_dex_cost_in_trade():
    """DEXCostCalculator total_cost_bps used when available."""
    mock_dex_cost = MagicMock()
    mock_result = MagicMock()
    mock_result.total_cost_bps = Decimal("5")  # 5bps cost, spread ~20bps → tradeable
    mock_dex_cost.calculate.return_value = mock_result

    dex = _make_dex_mock(pool_price=Decimal("50100"), gas=Decimal("0.5"))
    strategy = _make_strategy(dex_mock=dex, dex_cost=mock_dex_cost, min_edge_bps=Decimal("3"))
    strategy._is_active = True
    signal = _make_signal()
    result = await strategy.on_signal(signal)
    # DEXCostCalculator.calculate was called
    mock_dex_cost.calculate.assert_called_once()
    assert result is not None


@pytest.mark.asyncio
async def test_shadow_on_fill_no_crash():
    """on_fill handles trade without error."""
    strategy = _make_strategy()
    strategy._is_active = True
    mock_trade = MagicMock()
    mock_trade.metadata = {"realized_pnl": "5.0"}
    mock_trade.fee = Decimal("0.1")
    mock_trade.symbol = "BTC/USDT"
    # Should not raise
    await strategy.on_fill(mock_trade)


# ---------------------------------------------------------------------------
# Strategy Direction Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_direction_buy_cex_sell_dex():
    """CEX cheaper than DEX → buy on CEX, sell on DEX."""
    dex = _make_dex_mock(pool_price=Decimal("50100"))
    strategy = _make_strategy(dex_mock=dex)
    strategy._is_active = True
    signal = _make_signal(buy_price=Decimal("50000"), sell_price=Decimal("50000"))
    result = await strategy.on_signal(signal)
    assert result is not None
    assert result.legs[0].side == OrderSide.BUY  # CEX leg
    assert result.legs[1].side == OrderSide.SELL  # DEX leg


@pytest.mark.asyncio
async def test_direction_buy_dex_sell_cex():
    """DEX cheaper than CEX → buy on DEX, sell on CEX."""
    dex = _make_dex_mock(pool_price=Decimal("49900"))
    strategy = _make_strategy(dex_mock=dex)
    strategy._is_active = True
    signal = _make_signal(buy_price=Decimal("50000"), sell_price=Decimal("50000"))
    result = await strategy.on_signal(signal)
    assert result is not None
    assert result.legs[0].side == OrderSide.SELL  # CEX leg
    assert result.legs[1].side == OrderSide.BUY  # DEX leg


@pytest.mark.asyncio
async def test_insufficient_spread_filtered():
    """Spread < min_edge → filtered (None)."""
    dex = _make_dex_mock(pool_price=Decimal("50001"))  # ~0.002% spread
    strategy = _make_strategy(dex_mock=dex, min_edge_bps=Decimal("100"))
    strategy._is_active = True
    signal = _make_signal()
    result = await strategy.on_signal(signal)
    assert result is None


@pytest.mark.asyncio
async def test_dex_price_error_filtered():
    """DEX price fetch failure → filtered, no crash."""
    dex = _make_dex_mock()
    dex.get_pool_price = AsyncMock(side_effect=Exception("RPC timeout"))
    strategy = _make_strategy(dex_mock=dex)
    strategy._is_active = True
    signal = _make_signal()
    result = await strategy.on_signal(signal)
    assert result is None


# ---------------------------------------------------------------------------
# Engine Stability Tests
# ---------------------------------------------------------------------------


def test_engine_no_crash_without_dex():
    """_build_dex_adapter returns None when DEX_RPC_URL not set."""
    with patch.dict("os.environ", {}, clear=False):
        import os
        os.environ.pop("DEX_RPC_URL", None)
        os.environ.pop("DEX_POOL_ADDRESS", None)
        from src.main import Engine
        engine = Engine.__new__(Engine)
        result = engine._build_dex_adapter()
        assert result is None


@pytest.mark.asyncio
async def test_strategy_inactive_filters_all():
    """Inactive strategy filters all signals."""
    strategy = _make_strategy()
    strategy._is_active = False
    signal = _make_signal()
    result = await strategy.on_signal(signal)
    assert result is None


@pytest.mark.asyncio
async def test_scan_spread_shadow_compatible():
    """scan_spread returns shadow-compatible dict."""
    strategy = _make_strategy()
    result = await strategy.scan_spread("BTC/USDT", Decimal("50000"))
    assert isinstance(result, dict)
    assert "raw_spread_bps" in result
    assert "net_spread_bps" in result
    assert "direction" in result
    assert "tradeable" in result
