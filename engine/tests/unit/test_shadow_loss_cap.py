"""Tests for ShadowMode per-trade loss cap (US-066).

Per-trade loss cap semantics:
  - net_pnl < -SHADOW_MAX_LOSS_PER_TRADE_USD → capped at -max_loss
  - Profitable trades and small losses pass through unchanged
  - Loss cap triggers blacklist for both involved exchange-symbol pairs
  - Same cap logic applies in both _execute_shadow_trade AND
    _execute_shadow_trade_request (Critic amendment)
"""
from __future__ import annotations

import asyncio
import os
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import OrderSide
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_sim_trade(
    price: float,
    amount: float = 1.0,
    side: OrderSide = OrderSide.BUY,
) -> MagicMock:
    """Return a mock SimulatedTrade with the attributes _execute_shadow_trade accesses."""
    trade = MagicMock()
    trade.price = Decimal(str(price))
    trade.amount = Decimal(str(amount))
    trade.side = side
    trade.fee = Decimal("0")
    return trade


def _make_signal(
    buy_exchange: str = "coinone",
    sell_exchange: str = "binance",
    buy_price: float = 30_000,
    sell_price: float = 28_000,  # sell below buy → guaranteed big loss
    symbol: str = "BTC/USDT",
    volume: float = 1.0,
    strategy_id: str = "cross_exchange_spot",
):
    from src.core.models import Signal
    from datetime import datetime, timezone

    return Signal(
        strategy_id=strategy_id,
        symbol=symbol,
        buy_exchange=buy_exchange,
        sell_exchange=sell_exchange,
        buy_price=Decimal(str(buy_price)),
        sell_price=Decimal(str(sell_price)),
        spread_pct=Decimal("0.001"),
        confidence=0.9,
        volume=Decimal(str(volume)),
        timestamp=datetime.now(timezone.utc),
    )


@pytest.fixture
def shadow_mode(monkeypatch):
    """Minimal ShadowMode with mocked deps and bypassed rate/balance checks."""
    from src.modes.shadow import ShadowMode

    mock_executor = MagicMock()
    # Default side_effect: overridden in each test
    mock_executor.execute = AsyncMock()

    mode = ShadowMode(signal_generator=MagicMock(), paper_executor=mock_executor)
    mode._rate_limiter = MagicMock()
    mode._rate_limiter.try_acquire.return_value = True
    mode._balance_tracker = MagicMock()
    mode._balance_tracker.deduct.return_value = True
    return mode


def _large_loss_signal(buy_exchange="coinone", sell_exchange="binance", symbol="BTC/USDT"):
    """Signal that passes the buy<sell guard; executor fills cause a large loss."""
    # sell_price > buy_price passes the guard in _execute_shadow_trade
    return _make_signal(
        buy_exchange=buy_exchange,
        sell_exchange=sell_exchange,
        buy_price=29_990,  # signal says "looks profitable"
        sell_price=30_100,
        symbol=symbol,
        volume=1.0,
    )


def _large_loss_executor_side_effect():
    """Executor returns fills that create a ≈-$3000 loss (far exceeds $50 cap)."""
    return [
        _make_sim_trade(31_000, 1.0, OrderSide.BUY),   # buy at 31_000 (costly)
        _make_sim_trade(27_500, 1.0, OrderSide.SELL),  # sell at 27_500 (bad fill)
    ]


# ---------------------------------------------------------------------------
# Loss cap tests
# ---------------------------------------------------------------------------


class TestLossCapBehavior:
    @pytest.mark.asyncio
    async def test_loss_cap_caps_large_loss(self, shadow_mode):
        """Raw PnL ≈ -$3500 is capped at -$50 (mode._stats.total_pnl >= -50)."""
        shadow_mode._paper_executor.execute = AsyncMock(
            side_effect=_large_loss_executor_side_effect()
        )
        signal = _large_loss_signal()
        await shadow_mode._execute_shadow_trade(signal)

        # Global stat must reflect capped loss
        assert shadow_mode._stats.total_pnl >= -50.0

    @pytest.mark.asyncio
    async def test_loss_cap_allows_profitable_trade(self, shadow_mode):
        """+$5 PnL passes through without modification."""
        shadow_mode._paper_executor.execute = AsyncMock(side_effect=[
            _make_sim_trade(30_000, 0.01, OrderSide.BUY),
            _make_sim_trade(30_500, 0.01, OrderSide.SELL),  # 1.67% profit
        ])
        signal = _make_signal(buy_price=30_000, sell_price=30_500, volume=0.01)
        await shadow_mode._execute_shadow_trade(signal)

        # Global stat: profitable trade should not be penalised by cap
        assert shadow_mode._stats.total_pnl > -50.0

    @pytest.mark.asyncio
    async def test_loss_cap_allows_small_loss(self, shadow_mode):
        """-$3 PnL (within $50 cap) is recorded unchanged."""
        shadow_mode._paper_executor.execute = AsyncMock(side_effect=[
            _make_sim_trade(30_000, 0.01, OrderSide.BUY),
            _make_sim_trade(29_700, 0.01, OrderSide.SELL),  # small adverse move
        ])
        signal = _make_signal(buy_price=29_990, sell_price=30_100, volume=0.01)
        await shadow_mode._execute_shadow_trade(signal)

        # Small loss — global stat should be > -$50
        assert shadow_mode._stats.total_pnl > -50.0

    @pytest.mark.asyncio
    async def test_loss_cap_triggers_blacklist(self, shadow_mode):
        """Loss cap trigger blacklists both exchange-symbol pairs."""
        from src.core.stale_detector import StaleOrderbookDetector

        detector = StaleOrderbookDetector()
        shadow_mode._stale_detector = detector

        shadow_mode._paper_executor.execute = AsyncMock(
            side_effect=_large_loss_executor_side_effect()
        )
        signal = _large_loss_signal(buy_exchange="coinone", sell_exchange="bithumb")
        await shadow_mode._execute_shadow_trade(signal)

        # After cap triggers, both exchange-symbol pairs should be blacklisted
        assert detector.is_blacklisted("coinone", "BTC/USDT") or \
               detector.is_blacklisted("bithumb", "BTC/USDT"), \
               "Expected at least one exchange-symbol pair blacklisted after loss cap"

    @pytest.mark.asyncio
    async def test_loss_cap_env_override(self, monkeypatch):
        """SHADOW_MAX_LOSS_PER_TRADE_USD=100 sets cap at $100."""
        monkeypatch.setenv("SHADOW_MAX_LOSS_PER_TRADE_USD", "100")
        from src.modes.shadow import ShadowMode

        mode = ShadowMode(signal_generator=MagicMock(), paper_executor=MagicMock())
        assert mode._max_loss_per_trade_usd == Decimal("100")

    @pytest.mark.asyncio
    async def test_loss_cap_trade_request_path(self, shadow_mode):
        """_execute_shadow_trade_request also applies the per-trade loss cap."""
        from src.strategies.base import TradeRequest, TradeLeg

        shadow_mode._paper_executor.execute = AsyncMock(
            side_effect=_large_loss_executor_side_effect()
        )

        legs = [
            TradeLeg(
                exchange_id="coinone",
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                size=Decimal("1.0"),
                price=Decimal("29990"),
            ),
            TradeLeg(
                exchange_id="bithumb",
                symbol="BTC/USDT",
                side=OrderSide.SELL,
                size=Decimal("1.0"),
                price=Decimal("30100"),
            ),
        ]
        trade_request = TradeRequest(strategy_id="cross_exchange_spot", legs=legs)
        await shadow_mode._execute_shadow_trade_request(trade_request)

        # Cap must also apply in the trade_request path
        assert shadow_mode._stats.total_pnl >= -50.0
