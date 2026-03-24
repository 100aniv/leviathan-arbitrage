"""Tests for Shadow mode partial fill and order rejection tracking (US-058).

Covers:
- PaperExecutor initialized with partial_fill_rate=0.05 and rejection_rate=0.02
- SHADOW_PARTIAL_FILL_RATE / SHADOW_REJECTION_RATE env vars override defaults
- OrderRejectedError increments stats.trades_rejected, NOT trades_executed
- Partial fill increments stats.trades_partial_fill
- Sell order amount matches buy trade fill amount after partial fill
- Per-strategy rejection counter incremented on OrderRejectedError
- _execute_shadow_trade_request catches OrderRejectedError → trades_rejected
- _send_summary includes trades_rejected and trades_partial_fill in summary data
"""
from __future__ import annotations

import os
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import Order, OrderSide, OrderType, Signal, Trade
from src.execution.paper import OrderRejectedError, PaperExecutor
from src.modes.shadow import PowerLawSlippage, ShadowMode, ShadowStats, StrategyStats
from src.strategies.base import TradeLeg, TradeRequest


# ---------------------------------------------------------------------------
# Helpers — mirror make_shadow_mode() pattern from test_shadow_mode.py
# ---------------------------------------------------------------------------


def make_signal(
    buy_price: Decimal = Decimal("50000"),
    sell_price: Decimal = Decimal("50100"),
    volume: Decimal = Decimal("0.1"),
    strategy_id: str = "shadow_arb_v1",
) -> Signal:
    return Signal(
        strategy_id=strategy_id,
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
    fee: Decimal = Decimal("0.0"),
) -> Trade:
    return Trade(
        trade_id="test-trade-id",
        exchange_id="binance",
        symbol="BTC/USDT",
        side=side,
        price=price,
        amount=amount,
        fee=fee,
    )


def make_shadow_mode(
    paper_executor: PaperExecutor | None = None,
    telegram: MagicMock | None = None,
) -> ShadowMode:
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
        telegram=telegram,
        symbols=["BTC/USDT"],
    )


# ---------------------------------------------------------------------------
# PaperExecutor initialization in ShadowMode (default + env var)
# ---------------------------------------------------------------------------


class TestShadowModeDefaultRates:
    def test_shadow_default_partial_fill_rate(self) -> None:
        """ShadowMode default PaperExecutor has partial_fill_rate=0.05."""
        shadow = make_shadow_mode()
        assert shadow._paper_executor.partial_fill_rate == Decimal("0.05"), (
            f"Expected partial_fill_rate=0.05, got {shadow._paper_executor.partial_fill_rate}"
        )

    def test_shadow_default_rejection_rate(self) -> None:
        """ShadowMode default PaperExecutor has rejection_rate=0.02."""
        shadow = make_shadow_mode()
        assert shadow._paper_executor.rejection_rate == Decimal("0.02"), (
            f"Expected rejection_rate=0.02, got {shadow._paper_executor.rejection_rate}"
        )

    def test_shadow_env_var_partial_fill_rate(self) -> None:
        """SHADOW_PARTIAL_FILL_RATE env var overrides default partial_fill_rate."""
        with patch.dict(os.environ, {"SHADOW_PARTIAL_FILL_RATE": "0.10"}):
            shadow = make_shadow_mode()
        assert shadow._paper_executor.partial_fill_rate == Decimal("0.10"), (
            f"Expected partial_fill_rate=0.10, got {shadow._paper_executor.partial_fill_rate}"
        )

    def test_shadow_env_var_rejection_rate(self) -> None:
        """SHADOW_REJECTION_RATE env var overrides default rejection_rate."""
        with patch.dict(os.environ, {"SHADOW_REJECTION_RATE": "0.05"}):
            shadow = make_shadow_mode()
        assert shadow._paper_executor.rejection_rate == Decimal("0.05"), (
            f"Expected rejection_rate=0.05, got {shadow._paper_executor.rejection_rate}"
        )


# ---------------------------------------------------------------------------
# Rejection stats — _execute_shadow_trade
# ---------------------------------------------------------------------------


class TestRejectionStats:
    @pytest.mark.asyncio
    async def test_rejection_increments_stats(self) -> None:
        """OrderRejectedError increments stats.trades_rejected by 1."""
        mock_executor = MagicMock(spec=PaperExecutor)
        mock_executor.execute = AsyncMock(
            side_effect=OrderRejectedError("simulated rejection")
        )
        shadow = make_shadow_mode(paper_executor=mock_executor)

        await shadow._execute_shadow_trade(make_signal())

        assert shadow._stats.trades_rejected == 1, (
            f"Expected trades_rejected=1, got {shadow._stats.trades_rejected}"
        )

    @pytest.mark.asyncio
    async def test_rejection_does_not_count_as_trade(self) -> None:
        """OrderRejectedError does NOT increment trades_executed."""
        mock_executor = MagicMock(spec=PaperExecutor)
        mock_executor.execute = AsyncMock(
            side_effect=OrderRejectedError("simulated rejection")
        )
        shadow = make_shadow_mode(paper_executor=mock_executor)

        await shadow._execute_shadow_trade(make_signal())

        assert shadow._stats.trades_executed == 0, (
            f"Expected trades_executed=0 after rejection, got {shadow._stats.trades_executed}"
        )

    @pytest.mark.asyncio
    async def test_rejection_increments_strategy_stats(self) -> None:
        """OrderRejectedError increments per-strategy trades_rejected counter."""
        mock_executor = MagicMock(spec=PaperExecutor)
        mock_executor.execute = AsyncMock(
            side_effect=OrderRejectedError("simulated rejection")
        )
        shadow = make_shadow_mode(paper_executor=mock_executor)
        signal = make_signal(strategy_id="cross_exchange")

        await shadow._execute_shadow_trade(signal)

        ss = shadow._stats.by_strategy.get("cross_exchange")
        assert ss is not None, "StrategyStats entry must be created on rejection"
        assert ss.rejections == 1, (
            f"Expected strategy rejections=1, got {getattr(ss, 'rejections', 'MISSING')}"
        )


# ---------------------------------------------------------------------------
# Partial fill stats — _execute_shadow_trade
# ---------------------------------------------------------------------------


class TestPartialFillStats:
    @pytest.mark.asyncio
    async def test_partial_fill_increments_stats(self) -> None:
        """buy_trade.amount < signal.volume → stats.trades_partial_fill += 1."""
        volume = Decimal("0.1")
        partial_amount = Decimal("0.07")  # 70% fill

        buy_trade = make_trade(side=OrderSide.BUY, price=Decimal("50010"), amount=partial_amount)
        sell_trade = make_trade(side=OrderSide.SELL, price=Decimal("50090"), amount=partial_amount)

        mock_executor = MagicMock(spec=PaperExecutor)
        mock_executor.execute = AsyncMock(side_effect=[buy_trade, sell_trade])
        shadow = make_shadow_mode(paper_executor=mock_executor)

        await shadow._execute_shadow_trade(make_signal(volume=volume))

        assert shadow._stats.trades_partial_fill == 1, (
            f"Expected trades_partial_fill=1, got {shadow._stats.trades_partial_fill}"
        )

    @pytest.mark.asyncio
    async def test_partial_fill_sell_matches_buy_amount(self) -> None:
        """Sell order amount equals buy trade fill amount (not original signal.volume)."""
        volume = Decimal("0.1")
        partial_amount = Decimal("0.06")  # 60% partial fill

        buy_trade = make_trade(side=OrderSide.BUY, price=Decimal("50010"), amount=partial_amount)
        sell_trade = make_trade(side=OrderSide.SELL, price=Decimal("50090"), amount=partial_amount)

        mock_executor = MagicMock(spec=PaperExecutor)
        mock_executor.execute = AsyncMock(side_effect=[buy_trade, sell_trade])
        shadow = make_shadow_mode(paper_executor=mock_executor)

        await shadow._execute_shadow_trade(make_signal(volume=volume))

        calls = mock_executor.execute.call_args_list
        assert len(calls) == 2, f"Expected 2 execute calls (buy+sell), got {len(calls)}"
        sell_order: Order = calls[1][0][0]
        assert sell_order.amount == partial_amount, (
            f"Sell order amount must equal buy fill={partial_amount}, got {sell_order.amount}"
        )

    @pytest.mark.asyncio
    async def test_full_fill_does_not_increment_partial_fill_stats(self) -> None:
        """Full fill (buy_trade.amount == signal.volume) does not increment trades_partial_fill."""
        volume = Decimal("0.1")

        buy_trade = make_trade(side=OrderSide.BUY, price=Decimal("50010"), amount=volume)
        sell_trade = make_trade(side=OrderSide.SELL, price=Decimal("50090"), amount=volume)

        mock_executor = MagicMock(spec=PaperExecutor)
        mock_executor.execute = AsyncMock(side_effect=[buy_trade, sell_trade])
        shadow = make_shadow_mode(paper_executor=mock_executor)

        await shadow._execute_shadow_trade(make_signal(volume=volume))

        assert shadow._stats.trades_partial_fill == 0, (
            f"Expected trades_partial_fill=0 on full fill, got {shadow._stats.trades_partial_fill}"
        )


# ---------------------------------------------------------------------------
# _execute_shadow_trade_request — rejection handling
# ---------------------------------------------------------------------------


class TestTradeRequestRejectionHandling:
    @pytest.mark.asyncio
    async def test_trade_request_rejection_handling(self) -> None:
        """_execute_shadow_trade_request catches OrderRejectedError → trades_rejected += 1."""
        mock_executor = MagicMock(spec=PaperExecutor)
        mock_executor.execute = AsyncMock(
            side_effect=OrderRejectedError("leg rejected")
        )
        shadow = make_shadow_mode(paper_executor=mock_executor)

        trade_request = TradeRequest(
            strategy_id="cross_exchange",
            legs=[
                TradeLeg(
                    exchange_id="binance",
                    symbol="BTC/USDT",
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    size=Decimal("0.1"),
                    price=Decimal("50000"),
                ),
                TradeLeg(
                    exchange_id="coinone",
                    symbol="BTC/USDT",
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    size=Decimal("0.1"),
                    price=Decimal("50100"),
                ),
            ],
        )

        await shadow._execute_shadow_trade_request(trade_request)

        assert shadow._stats.trades_rejected == 1, (
            f"Expected trades_rejected=1 after request rejection, "
            f"got {shadow._stats.trades_rejected}"
        )
        assert shadow._stats.trades_executed == 0, (
            f"Expected trades_executed=0, got {shadow._stats.trades_executed}"
        )


# ---------------------------------------------------------------------------
# _send_summary includes rejection/partial_fill counts
# ---------------------------------------------------------------------------


class TestSummaryIncludesRejectionStats:
    @pytest.mark.asyncio
    async def test_summary_includes_rejection_stats(self) -> None:
        """_send_summary passes trades_rejected and trades_partial_fill in summary_data."""
        telegram = MagicMock()
        telegram.send_daily_summary = AsyncMock()
        telegram.send_alert_kr = AsyncMock()

        shadow = make_shadow_mode(telegram=telegram)
        shadow._stats.trades_rejected = 3
        shadow._stats.trades_partial_fill = 7
        shadow._stats.trades_executed = 10

        await shadow._send_summary()

        assert telegram.send_daily_summary.called, "send_daily_summary must be called"
        call_args = telegram.send_daily_summary.call_args[0][0]

        assert "trades_rejected" in call_args, (
            f"summary_data missing 'trades_rejected'. Keys: {sorted(call_args.keys())}"
        )
        assert "trades_partial_fill" in call_args, (
            f"summary_data missing 'trades_partial_fill'. Keys: {sorted(call_args.keys())}"
        )
        assert call_args["trades_rejected"] == 3, (
            f"Expected trades_rejected=3, got {call_args['trades_rejected']}"
        )
        assert call_args["trades_partial_fill"] == 7, (
            f"Expected trades_partial_fill=7, got {call_args['trades_partial_fill']}"
        )
