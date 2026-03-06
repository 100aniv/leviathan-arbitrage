"""Tests for engine/src/risk/position_manager.py — TDD first."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.risk.position_manager import PositionManager, PositionRecord


@pytest.fixture
def mock_dual_writer():
    writer = MagicMock()
    writer.write_position = AsyncMock(return_value=42)
    return writer


@pytest.fixture
def position_manager(mock_dual_writer, fake_redis):
    return PositionManager(dual_writer=mock_dual_writer, redis_client=fake_redis)


# ---------------------------------------------------------------------------
# PositionRecord unit tests
# ---------------------------------------------------------------------------


class TestPositionRecord:
    def test_long_unrealized_pnl(self):
        record = PositionRecord(
            strategy_id="s1",
            exchange_id="binance",
            symbol="BTC/USDT",
            side="LONG",
            quantity=Decimal("2.0"),
            entry_price=Decimal("50000"),
            mark_price=Decimal("51000"),
        )
        assert record.unrealized_pnl == Decimal("2000")

    def test_short_unrealized_pnl(self):
        record = PositionRecord(
            strategy_id="s1",
            exchange_id="binance",
            symbol="BTC/USDT",
            side="SHORT",
            quantity=Decimal("2.0"),
            entry_price=Decimal("50000"),
            mark_price=Decimal("49000"),
        )
        assert record.unrealized_pnl == Decimal("2000")

    def test_no_mark_price_zero_pnl(self):
        record = PositionRecord(
            strategy_id="s1",
            exchange_id="binance",
            symbol="BTC/USDT",
            side="LONG",
            quantity=Decimal("1.0"),
            entry_price=Decimal("50000"),
        )
        assert record.unrealized_pnl == Decimal("0")

    def test_long_position_value_uses_mark_price(self):
        record = PositionRecord(
            strategy_id="s1",
            exchange_id="binance",
            symbol="BTC/USDT",
            side="LONG",
            quantity=Decimal("2.0"),
            entry_price=Decimal("50000"),
            mark_price=Decimal("51000"),
        )
        assert record.position_value == Decimal("102000")

    def test_position_value_uses_entry_when_no_mark(self):
        record = PositionRecord(
            strategy_id="s1",
            exchange_id="binance",
            symbol="BTC/USDT",
            side="LONG",
            quantity=Decimal("1.0"),
            entry_price=Decimal("50000"),
        )
        assert record.position_value == Decimal("50000")


# ---------------------------------------------------------------------------
# open_position
# ---------------------------------------------------------------------------


class TestOpenPosition:
    async def test_open_creates_position_record(self, position_manager):
        wal_id = await position_manager.open_position(
            strategy_id="strat1",
            exchange_id="binance",
            symbol="BTC/USDT",
            side="LONG",
            quantity=Decimal("0.1"),
            entry_price=Decimal("50000"),
        )
        assert wal_id == 42
        positions = position_manager.get_positions()
        assert ("strat1", "binance", "BTC/USDT") in positions

    async def test_open_calls_dual_writer_with_open_event(self, position_manager, mock_dual_writer):
        await position_manager.open_position(
            strategy_id="strat1",
            exchange_id="binance",
            symbol="BTC/USDT",
            side="LONG",
            quantity=Decimal("0.1"),
            entry_price=Decimal("50000"),
        )
        mock_dual_writer.write_position.assert_awaited_once()
        kwargs = mock_dual_writer.write_position.call_args.kwargs
        assert kwargs["event_type"] == "OPEN"
        assert kwargs["side"] == "LONG"
        assert kwargs["strategy_id"] == "strat1"

    async def test_open_long_increases_net_exposure(self, position_manager):
        await position_manager.open_position(
            strategy_id="strat1",
            exchange_id="binance",
            symbol="BTC/USDT",
            side="LONG",
            quantity=Decimal("1.0"),
            entry_price=Decimal("50000"),
        )
        net = await position_manager.get_net_exposure("binance", "BTC")
        assert net == Decimal("1.0")

    async def test_open_short_decreases_net_exposure(self, position_manager):
        await position_manager.open_position(
            strategy_id="strat1",
            exchange_id="binance",
            symbol="BTC/USDT",
            side="SHORT",
            quantity=Decimal("0.5"),
            entry_price=Decimal("50000"),
        )
        net = await position_manager.get_net_exposure("binance", "BTC")
        assert net == Decimal("-0.5")

    async def test_two_strategies_accumulate_exposure(self, position_manager):
        await position_manager.open_position(
            strategy_id="strat1",
            exchange_id="binance",
            symbol="BTC/USDT",
            side="LONG",
            quantity=Decimal("1.0"),
            entry_price=Decimal("50000"),
        )
        await position_manager.open_position(
            strategy_id="strat2",
            exchange_id="binance",
            symbol="BTC/USDT",
            side="SHORT",
            quantity=Decimal("0.3"),
            entry_price=Decimal("50100"),
        )
        net = await position_manager.get_net_exposure("binance", "BTC")
        assert net == Decimal("0.7")  # 1.0 - 0.3


# ---------------------------------------------------------------------------
# update_position
# ---------------------------------------------------------------------------


class TestUpdatePosition:
    async def test_update_mark_price_and_pnl(self, position_manager):
        await position_manager.open_position(
            strategy_id="strat1",
            exchange_id="binance",
            symbol="BTC/USDT",
            side="LONG",
            quantity=Decimal("1.0"),
            entry_price=Decimal("50000"),
        )
        await position_manager.update_position(
            strategy_id="strat1",
            exchange_id="binance",
            symbol="BTC/USDT",
            mark_price=Decimal("51000"),
        )
        record = position_manager.get_positions()[("strat1", "binance", "BTC/USDT")]
        assert record.mark_price == Decimal("51000")
        assert record.unrealized_pnl == Decimal("1000")

    async def test_update_nonexistent_is_noop(self, position_manager):
        # Must not raise
        await position_manager.update_position(
            strategy_id="nonexistent",
            exchange_id="binance",
            symbol="BTC/USDT",
            mark_price=Decimal("50000"),
        )

    async def test_update_calls_dual_writer_with_update_event(
        self, position_manager, mock_dual_writer
    ):
        await position_manager.open_position(
            strategy_id="strat1",
            exchange_id="binance",
            symbol="BTC/USDT",
            side="LONG",
            quantity=Decimal("1.0"),
            entry_price=Decimal("50000"),
        )
        mock_dual_writer.write_position.reset_mock()
        await position_manager.update_position(
            strategy_id="strat1",
            exchange_id="binance",
            symbol="BTC/USDT",
            mark_price=Decimal("51000"),
        )
        mock_dual_writer.write_position.assert_awaited_once()
        kwargs = mock_dual_writer.write_position.call_args.kwargs
        assert kwargs["event_type"] == "UPDATE"


# ---------------------------------------------------------------------------
# close_position
# ---------------------------------------------------------------------------


class TestClosePosition:
    async def test_close_long_returns_positive_pnl(self, position_manager):
        await position_manager.open_position(
            strategy_id="strat1",
            exchange_id="binance",
            symbol="BTC/USDT",
            side="LONG",
            quantity=Decimal("1.0"),
            entry_price=Decimal("50000"),
        )
        pnl = await position_manager.close_position(
            strategy_id="strat1",
            exchange_id="binance",
            symbol="BTC/USDT",
            close_price=Decimal("51000"),
        )
        assert pnl == Decimal("1000")

    async def test_close_short_returns_positive_pnl(self, position_manager):
        await position_manager.open_position(
            strategy_id="strat1",
            exchange_id="binance",
            symbol="BTC/USDT",
            side="SHORT",
            quantity=Decimal("1.0"),
            entry_price=Decimal("50000"),
        )
        pnl = await position_manager.close_position(
            strategy_id="strat1",
            exchange_id="binance",
            symbol="BTC/USDT",
            close_price=Decimal("49000"),
        )
        assert pnl == Decimal("1000")

    async def test_close_removes_position(self, position_manager):
        await position_manager.open_position(
            strategy_id="strat1",
            exchange_id="binance",
            symbol="BTC/USDT",
            side="LONG",
            quantity=Decimal("1.0"),
            entry_price=Decimal("50000"),
        )
        await position_manager.close_position(
            strategy_id="strat1",
            exchange_id="binance",
            symbol="BTC/USDT",
            close_price=Decimal("51000"),
        )
        assert ("strat1", "binance", "BTC/USDT") not in position_manager.get_positions()

    async def test_close_reverses_net_exposure(self, position_manager):
        await position_manager.open_position(
            strategy_id="strat1",
            exchange_id="binance",
            symbol="BTC/USDT",
            side="LONG",
            quantity=Decimal("1.0"),
            entry_price=Decimal("50000"),
        )
        await position_manager.close_position(
            strategy_id="strat1",
            exchange_id="binance",
            symbol="BTC/USDT",
            close_price=Decimal("51000"),
        )
        net = await position_manager.get_net_exposure("binance", "BTC")
        assert net == Decimal("0")

    async def test_close_calls_dual_writer_with_close_event(
        self, position_manager, mock_dual_writer
    ):
        await position_manager.open_position(
            strategy_id="strat1",
            exchange_id="binance",
            symbol="BTC/USDT",
            side="LONG",
            quantity=Decimal("1.0"),
            entry_price=Decimal("50000"),
        )
        mock_dual_writer.write_position.reset_mock()
        await position_manager.close_position(
            strategy_id="strat1",
            exchange_id="binance",
            symbol="BTC/USDT",
            close_price=Decimal("51000"),
        )
        mock_dual_writer.write_position.assert_awaited_once()
        kwargs = mock_dual_writer.write_position.call_args.kwargs
        assert kwargs["event_type"] == "CLOSE"

    async def test_close_nonexistent_returns_zero(self, position_manager):
        pnl = await position_manager.close_position(
            strategy_id="nonexistent",
            exchange_id="binance",
            symbol="BTC/USDT",
            close_price=Decimal("50000"),
        )
        assert pnl == Decimal("0")


# ---------------------------------------------------------------------------
# get_net_exposure
# ---------------------------------------------------------------------------


class TestGetNetExposure:
    async def test_zero_when_no_positions(self, position_manager):
        net = await position_manager.get_net_exposure("binance", "BTC")
        assert net == Decimal("0")

    async def test_isolates_by_exchange(self, position_manager):
        await position_manager.open_position(
            strategy_id="strat1",
            exchange_id="binance",
            symbol="BTC/USDT",
            side="LONG",
            quantity=Decimal("1.0"),
            entry_price=Decimal("50000"),
        )
        # OKX should have zero exposure
        net = await position_manager.get_net_exposure("okx", "BTC")
        assert net == Decimal("0")
