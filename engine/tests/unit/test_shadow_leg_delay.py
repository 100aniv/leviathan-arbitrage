"""Tests for Shadow mode inter-leg execution delay (US-059).

Covers:
- ShadowMode._leg_delay_min_ms == 50 (default)
- ShadowMode._leg_delay_max_ms == 300 (default)
- SHADOW_LEG_DELAY_MIN_MS / SHADOW_LEG_DELAY_MAX_MS env vars override defaults
- _execute_shadow_trade calls asyncio.sleep once after buy leg
- sleep argument is in [0.05, 0.30] seconds (50–300ms)
- _execute_shadow_trade_request calls sleep N-1 times for N legs
- No sleep after the last leg
- delay_max_ms=0 skips sleep entirely
"""
from __future__ import annotations

import os
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import OrderSide, OrderType, Signal, Trade
from src.execution.paper import PaperExecutor
from src.modes.shadow import ShadowMode
from src.strategies.base import TradeLeg, TradeRequest


# ---------------------------------------------------------------------------
# Helpers — mirror make_shadow_mode() pattern from test_shadow_partial_fill_rejection.py
# ---------------------------------------------------------------------------


def make_signal(
    buy_price: Decimal = Decimal("50000"),
    sell_price: Decimal = Decimal("50100"),
    volume: Decimal = Decimal("0.1"),
) -> Signal:
    return Signal(
        strategy_id="shadow_arb_v1",
        symbol="BTC/USDT",
        buy_exchange="binance",
        sell_exchange="coinone",
        buy_price=buy_price,
        sell_price=sell_price,
        spread_pct=Decimal("0.002"),
        confidence=0.9,
        volume=volume,
    )


def make_trade(
    side: OrderSide = OrderSide.BUY,
    price: Decimal = Decimal("50010"),
    amount: Decimal = Decimal("0.1"),
) -> Trade:
    return Trade(
        trade_id="test-trade-id",
        exchange_id="binance",
        symbol="BTC/USDT",
        side=side,
        price=price,
        amount=amount,
        fee=Decimal("0.0"),
    )


def make_shadow_mode(paper_executor: PaperExecutor | None = None) -> ShadowMode:
    """Create ShadowMode with all external dependencies mocked."""
    signal_generator = MagicMock()
    signal_generator.on_orderbook_update = AsyncMock(return_value=None)

    collector_manager = MagicMock()
    collector_manager.start = AsyncMock()
    collector_manager.stop = AsyncMock()

    return ShadowMode(
        signal_generator=signal_generator,
        paper_executor=paper_executor,
        collector_manager=collector_manager,
        symbols=["BTC/USDT"],
    )


def make_trade_request(n_legs: int = 2) -> TradeRequest:
    """Create a TradeRequest with n_legs alternating BUY/SELL legs."""
    legs = []
    for i in range(n_legs):
        side = OrderSide.BUY if i % 2 == 0 else OrderSide.SELL
        legs.append(
            TradeLeg(
                exchange_id="binance" if i % 2 == 0 else "coinone",
                symbol="BTC/USDT",
                side=side,
                order_type=OrderType.MARKET,
                size=Decimal("0.1"),
                price=Decimal("50000") if side == OrderSide.BUY else Decimal("50100"),
            )
        )
    return TradeRequest(strategy_id="cross_exchange", legs=legs)


# ---------------------------------------------------------------------------
# Default delay attribute values
# ---------------------------------------------------------------------------


class TestDefaultLegDelayValues:
    def test_default_leg_delay_min_ms(self) -> None:
        """ShadowMode._leg_delay_min_ms defaults to 50."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SHADOW_LEG_DELAY_MIN_MS", None)
            shadow = make_shadow_mode()
        assert shadow._leg_delay_min_ms == 50.0, (
            f"Expected _leg_delay_min_ms=50.0, got {shadow._leg_delay_min_ms}"
        )

    def test_default_leg_delay_max_ms(self) -> None:
        """ShadowMode._leg_delay_max_ms defaults to 300."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SHADOW_LEG_DELAY_MAX_MS", None)
            shadow = make_shadow_mode()
        assert shadow._leg_delay_max_ms == 300.0, (
            f"Expected _leg_delay_max_ms=300.0, got {shadow._leg_delay_max_ms}"
        )

    def test_env_var_leg_delay(self) -> None:
        """SHADOW_LEG_DELAY_MIN_MS and MAX_MS env vars override defaults."""
        with patch.dict(os.environ, {
            "PAPER_LEG_DELAY_MIN_MS": "100",
            "PAPER_LEG_DELAY_MAX_MS": "500",
        }):
            shadow = make_shadow_mode()
        assert shadow._leg_delay_min_ms == 100.0, (
            f"Expected _leg_delay_min_ms=100.0, got {shadow._leg_delay_min_ms}"
        )
        assert shadow._leg_delay_max_ms == 500.0, (
            f"Expected _leg_delay_max_ms=500.0, got {shadow._leg_delay_max_ms}"
        )


# ---------------------------------------------------------------------------
# _execute_shadow_trade: sleep called once after buy leg
# ---------------------------------------------------------------------------


class TestExecuteShadowTradeSleep:
    @pytest.mark.asyncio
    async def test_execute_shadow_trade_calls_sleep(self) -> None:
        """_execute_shadow_trade calls asyncio.sleep exactly once after buy leg."""
        buy_trade = make_trade(side=OrderSide.BUY)
        sell_trade = make_trade(side=OrderSide.SELL, price=Decimal("50090"))

        mock_executor = MagicMock(spec=PaperExecutor)
        mock_executor.execute = AsyncMock(side_effect=[buy_trade, sell_trade])
        shadow = make_shadow_mode(paper_executor=mock_executor)

        with patch("src.modes.shadow.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await shadow._execute_shadow_trade(make_signal())

        mock_sleep.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_shadow_trade_sleep_range(self) -> None:
        """asyncio.sleep argument is in [0.05, 0.30] seconds (50–300ms default range)."""
        buy_trade = make_trade(side=OrderSide.BUY)
        sell_trade = make_trade(side=OrderSide.SELL, price=Decimal("50090"))

        mock_executor = MagicMock(spec=PaperExecutor)
        mock_executor.execute = AsyncMock(side_effect=[buy_trade, sell_trade])
        shadow = make_shadow_mode(paper_executor=mock_executor)

        with patch("src.modes.shadow.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await shadow._execute_shadow_trade(make_signal())

        actual_delay = mock_sleep.call_args[0][0]
        assert 0.05 <= actual_delay <= 0.30, (
            f"Expected sleep in [0.05, 0.30]s, got {actual_delay:.4f}s"
        )


# ---------------------------------------------------------------------------
# _execute_shadow_trade_request: inter-leg sleep
# ---------------------------------------------------------------------------


class TestTradeRequestLegDelay:
    @pytest.mark.asyncio
    async def test_trade_request_calls_sleep_between_legs(self) -> None:
        """_execute_shadow_trade_request calls sleep N-1 times for N legs."""
        n_legs = 3
        trades = [
            make_trade(side=OrderSide.BUY if i % 2 == 0 else OrderSide.SELL)
            for i in range(n_legs)
        ]
        mock_executor = MagicMock(spec=PaperExecutor)
        mock_executor.execute = AsyncMock(side_effect=trades)
        shadow = make_shadow_mode(paper_executor=mock_executor)

        with patch("src.modes.shadow.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await shadow._execute_shadow_trade_request(make_trade_request(n_legs=n_legs))

        assert mock_sleep.call_count == n_legs - 1, (
            f"Expected sleep called {n_legs - 1} times for {n_legs} legs, "
            f"got {mock_sleep.call_count}"
        )

    @pytest.mark.asyncio
    async def test_trade_request_no_sleep_after_last_leg(self) -> None:
        """_execute_shadow_trade_request does NOT sleep after the last leg."""
        trades = [
            make_trade(side=OrderSide.BUY),
            make_trade(side=OrderSide.SELL, price=Decimal("50090")),
        ]
        mock_executor = MagicMock(spec=PaperExecutor)
        mock_executor.execute = AsyncMock(side_effect=trades)
        shadow = make_shadow_mode(paper_executor=mock_executor)

        with patch("src.modes.shadow.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await shadow._execute_shadow_trade_request(make_trade_request(n_legs=2))

        # 2 legs → exactly 1 sleep (between leg 0 and leg 1, none after leg 1)
        assert mock_sleep.call_count == 1, (
            f"Expected sleep called 1 time for 2-leg request (not after last leg), "
            f"got {mock_sleep.call_count}"
        )

    @pytest.mark.asyncio
    async def test_zero_delay_skips_sleep(self) -> None:
        """delay_max_ms=0 causes asyncio.sleep to be skipped entirely."""
        buy_trade = make_trade(side=OrderSide.BUY)
        sell_trade = make_trade(side=OrderSide.SELL, price=Decimal("50090"))

        mock_executor = MagicMock(spec=PaperExecutor)
        mock_executor.execute = AsyncMock(side_effect=[buy_trade, sell_trade])
        shadow = make_shadow_mode(paper_executor=mock_executor)
        shadow._leg_delay_max_ms = 0.0  # Disable delay

        with patch("src.modes.shadow.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await shadow._execute_shadow_trade(make_signal())

        mock_sleep.assert_not_called()
