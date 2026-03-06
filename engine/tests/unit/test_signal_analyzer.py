"""Tests for signal_analyzer — offline orderbook replay analysis."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.analysis.signal_analyzer import SignalAnalyzer, SignalStats


# ---------------------------------------------------------------------------
# SignalStats dataclass tests
# ---------------------------------------------------------------------------

class TestSignalStats:
    def test_defaults(self):
        stats = SignalStats()
        assert stats.total_updates == 0
        assert stats.signals_generated == 0
        assert stats.avg_spread_bps == 0.0
        assert stats.exchange_pairs == {}

    def test_exchange_pairs_tracking(self):
        stats = SignalStats()
        stats.exchange_pairs["binance→okx"] = 5
        stats.exchange_pairs["bybit→bitget"] = 3
        assert stats.exchange_pairs["binance→okx"] == 5
        assert len(stats.exchange_pairs) == 2


# ---------------------------------------------------------------------------
# SignalAnalyzer tests
# ---------------------------------------------------------------------------

class _AsyncCM:
    """Helper to create a proper async context manager from a value."""
    def __init__(self, value):
        self._value = value
    async def __aenter__(self):
        return self._value
    async def __aexit__(self, *args):
        pass


def _make_pool_with_cursor(rows: list[dict]):
    """Build a mock asyncpg pool that returns rows from cursor iteration."""
    row_iter = iter(rows)

    mock_cursor = MagicMock()
    mock_cursor.__aiter__ = lambda self: self

    async def _anext(self_inner=None):
        try:
            return next(row_iter)
        except StopIteration:
            raise StopAsyncIteration

    mock_cursor.__anext__ = _anext

    mock_conn = MagicMock()
    mock_conn.cursor = MagicMock(return_value=mock_cursor)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCM(mock_conn))
    return pool


class TestSignalAnalyzer:
    @pytest.fixture
    def mock_pool(self):
        return _make_pool_with_cursor([])

    def test_init(self, mock_pool):
        analyzer = SignalAnalyzer(mock_pool)
        assert analyzer._pool is mock_pool

    def test_init_with_custom_config(self, mock_pool):
        from src.core.signal import SignalConfig
        config = SignalConfig(min_edge=Decimal("0.001"))
        analyzer = SignalAnalyzer(mock_pool, signal_config=config)
        assert analyzer._signal_config.min_edge == Decimal("0.001")

    @pytest.mark.asyncio
    async def test_analyze_empty_data(self):
        """Empty DB should return zero stats."""
        pool = _make_pool_with_cursor([])
        analyzer = SignalAnalyzer(pool)
        stats = await analyzer.analyze(symbol="BTC/USDT")

        assert stats.total_updates == 0
        assert stats.signals_generated == 0
        assert stats.signals_per_hour == 0.0

    @pytest.mark.asyncio
    async def test_analyze_default_time_range(self):
        """Default time range should be 72 hours."""
        pool = _make_pool_with_cursor([])
        analyzer = SignalAnalyzer(pool)
        stats = await analyzer.analyze()
        assert isinstance(stats, SignalStats)

    @pytest.mark.asyncio
    async def test_analyze_processes_orderbook_rows(self):
        """Verify rows are processed and stats updated."""
        now = datetime.now(timezone.utc)
        rows = [
            {
                "ts": now - timedelta(hours=2),
                "exchange": "binance",
                "symbol": "BTC/USDT",
                "bids_json": [["50000", "1.0"], ["49999", "2.0"]],
                "asks_json": [["50001", "1.0"], ["50002", "2.0"]],
            },
            {
                "ts": now - timedelta(hours=1),
                "exchange": "okx",
                "symbol": "BTC/USDT",
                "bids_json": [["50010", "1.0"], ["50009", "2.0"]],
                "asks_json": [["50011", "1.0"], ["50012", "2.0"]],
            },
        ]

        pool = _make_pool_with_cursor(rows)
        analyzer = SignalAnalyzer(pool)
        stats = await analyzer.analyze(
            symbol="BTC/USDT",
            start=now - timedelta(hours=3),
            end=now,
        )

        assert stats.total_updates == 2
        assert stats.time_range_hours > 0
