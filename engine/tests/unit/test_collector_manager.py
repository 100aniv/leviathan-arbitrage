"""Tests for engine/src/collectors/manager.py (CollectorManager).

Covers: default exchanges list, start creates collectors for each exchange,
stop cancels tasks, stats aggregation, connected_count,
unknown exchange skipped, custom symbols/exchanges.

Individual collectors are mocked with AsyncMock.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.manager import CollectorManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_collector(connected: bool = False, message_count: int = 0) -> MagicMock:
    """Return a mock that satisfies the BaseCollector interface."""
    collector = MagicMock()
    collector.start = AsyncMock()
    collector.stop = AsyncMock()
    collector.is_connected = connected
    collector.stats = {
        "exchange": "mock",
        "connected": connected,
        "message_count": message_count,
        "last_message_age_s": None,
    }
    return collector


# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------


class TestDefaultConfiguration:
    def test_default_exchanges_list_contains_thirteen_exchanges(self):
        """10→12→13 거래소 (MEXC + Gate.io + Bitget Futures 추가)."""
        assert "mexc" in CollectorManager.DEFAULT_EXCHANGES
        assert "gateio" in CollectorManager.DEFAULT_EXCHANGES
        assert "bitget_futures" in CollectorManager.DEFAULT_EXCHANGES
        assert len(CollectorManager.DEFAULT_EXCHANGES) == 13

    def test_default_symbols_is_btc_usdt(self):
        manager = CollectorManager()
        assert manager.symbols == ["BTC/USDT"]

    def test_default_exchange_ids_matches_class_constant(self):
        manager = CollectorManager()
        assert manager._exchange_ids == CollectorManager.DEFAULT_EXCHANGES

    def test_custom_symbols_are_stored(self):
        manager = CollectorManager(symbols=["ETH/USDT", "SOL/USDT"])
        assert manager.symbols == ["ETH/USDT", "SOL/USDT"]

    def test_custom_exchanges_override_defaults(self):
        manager = CollectorManager(exchanges=["binance", "okx"])
        assert manager._exchange_ids == ["binance", "okx"]


# ---------------------------------------------------------------------------
# start() — collector creation
# ---------------------------------------------------------------------------


class TestStart:
    async def test_start_creates_one_collector_per_exchange(self):
        manager = CollectorManager(exchanges=["binance", "bybit", "okx", "bitget"])
        mock_collector = _make_mock_collector()

        with patch.object(manager, "_create_collector", return_value=mock_collector):
            await manager.start()

        assert len(manager._collectors) == 4
        await manager.stop()

    async def test_start_creates_background_task_per_collector(self):
        manager = CollectorManager(exchanges=["binance", "okx"])
        mock_collector = _make_mock_collector()

        with patch.object(manager, "_create_collector", return_value=mock_collector):
            await manager.start()

        assert len(manager._tasks) == 2
        await manager.stop()

    async def test_start_skips_unknown_exchange(self):
        manager = CollectorManager(exchanges=["binance", "unknown_exchange"])

        # Only patch the real factory for the unknown path; let _create_collector run
        await manager.start()
        # "unknown_exchange" should not appear in collectors
        assert "unknown_exchange" not in manager._collectors
        await manager.stop()

    async def test_start_with_single_exchange_creates_one_task(self):
        manager = CollectorManager(exchanges=["binance"])
        mock_collector = _make_mock_collector()

        with patch.object(manager, "_create_collector", return_value=mock_collector):
            await manager.start()

        assert len(manager._tasks) == 1
        await manager.stop()


# ---------------------------------------------------------------------------
# _create_collector factory
# ---------------------------------------------------------------------------


class TestCreateCollector:
    def test_known_exchange_returns_collector_instance(self):
        manager = CollectorManager(symbols=["BTC/USDT"])
        for exchange in ["binance", "bybit", "okx", "bitget", "coinone"]:
            collector = manager._create_collector(exchange)
            assert collector is not None

    def test_unknown_exchange_returns_none(self):
        manager = CollectorManager()
        result = manager._create_collector("kraken")
        assert result is None

    def test_created_collector_receives_correct_symbols(self):
        manager = CollectorManager(symbols=["ETH/USDT", "BTC/USDT"])
        collector = manager._create_collector("binance")
        assert collector is not None
        assert "ETH/USDT" in collector.symbols
        assert "BTC/USDT" in collector.symbols


# ---------------------------------------------------------------------------
# stop() — task cancellation
# ---------------------------------------------------------------------------


class TestStop:
    async def test_stop_calls_stop_on_every_collector(self):
        manager = CollectorManager(exchanges=["binance", "okx"])
        mock_a = _make_mock_collector()
        mock_b = _make_mock_collector()

        with patch.object(manager, "_create_collector", side_effect=[mock_a, mock_b]):
            await manager.start()

        await manager.stop()

        mock_a.stop.assert_called_once()
        mock_b.stop.assert_called_once()

    async def test_stop_clears_collectors_dict(self):
        manager = CollectorManager(exchanges=["binance"])
        mock_collector = _make_mock_collector()

        with patch.object(manager, "_create_collector", return_value=mock_collector):
            await manager.start()

        await manager.stop()
        assert len(manager._collectors) == 0

    async def test_stop_clears_tasks_dict(self):
        manager = CollectorManager(exchanges=["binance"])
        mock_collector = _make_mock_collector()

        with patch.object(manager, "_create_collector", return_value=mock_collector):
            await manager.start()

        await manager.stop()
        assert len(manager._tasks) == 0

    async def test_stop_cancels_running_tasks(self):
        manager = CollectorManager(exchanges=["binance"])
        mock_collector = _make_mock_collector()

        # Make start() hang so the task is still running when stop() is called
        async def hang_forever():
            await asyncio.sleep(3600)

        mock_collector.start = hang_forever

        with patch.object(manager, "_create_collector", return_value=mock_collector):
            await manager.start()

        # Task should be running
        assert not list(manager._tasks.values())[0].done()

        await manager.stop()
        # After stop, tasks dict is cleared
        assert len(manager._tasks) == 0


# ---------------------------------------------------------------------------
# stats aggregation
# ---------------------------------------------------------------------------


class TestStatsAggregation:
    async def test_stats_returns_dict_keyed_by_exchange_id(self):
        manager = CollectorManager(exchanges=["binance", "okx"])
        binance_mock = _make_mock_collector(connected=True, message_count=100)
        binance_mock.stats = {"exchange": "binance", "connected": True, "message_count": 100, "last_message_age_s": 1.0}
        okx_mock = _make_mock_collector(connected=False, message_count=50)
        okx_mock.stats = {"exchange": "okx", "connected": False, "message_count": 50, "last_message_age_s": None}

        with patch.object(manager, "_create_collector", side_effect=[binance_mock, okx_mock]):
            await manager.start()

        s = manager.stats
        assert "binance" in s
        assert "okx" in s
        assert s["binance"]["message_count"] == 100
        assert s["okx"]["message_count"] == 50

        await manager.stop()

    async def test_stats_is_empty_before_start(self):
        manager = CollectorManager()
        assert manager.stats == {}


# ---------------------------------------------------------------------------
# connected_count
# ---------------------------------------------------------------------------


class TestConnectedCount:
    async def test_connected_count_is_zero_before_start(self):
        manager = CollectorManager()
        assert manager.connected_count == 0

    async def test_connected_count_reflects_connected_collectors(self):
        manager = CollectorManager(exchanges=["binance", "bybit", "okx"])
        connected_mock = _make_mock_collector(connected=True)
        disconnected_mock = _make_mock_collector(connected=False)

        with patch.object(
            manager,
            "_create_collector",
            side_effect=[connected_mock, disconnected_mock, connected_mock],
        ):
            await manager.start()

        assert manager.connected_count == 2
        await manager.stop()

    async def test_connected_count_is_zero_when_all_disconnected(self):
        manager = CollectorManager(exchanges=["binance", "okx"])
        mock = _make_mock_collector(connected=False)

        with patch.object(manager, "_create_collector", return_value=mock):
            await manager.start()

        assert manager.connected_count == 0
        await manager.stop()
