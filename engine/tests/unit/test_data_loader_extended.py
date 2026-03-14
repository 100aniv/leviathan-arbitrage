"""Extended tests for src/tuning/data_loader.py — covers connect, cache, load_ohlcv, load_spreads."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from src.tuning.data_loader import DataLoader, OHLCVWindow, SpreadRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_loader(conn: AsyncMock | None = None, cache_max: int = 100) -> DataLoader:
    loader = DataLoader(dsn="postgresql://user:pass@localhost/testdb", cache_max_entries=cache_max)
    if conn is not None:
        loader._conn = conn
    return loader


def _fake_ohlcv_rows(count: int = 3) -> list[dict]:
    base = datetime(2024, 1, 1, 0, 0, 0)
    return [
        {
            "time": base,
            "open": 50000.0 + i,
            "high": 51000.0 + i,
            "low": 49000.0 + i,
            "close": 50500.0 + i,
            "volume": 100.0 + i * 10,
        }
        for i in range(count)
    ]


def _fake_spread_rows(count: int = 3) -> list[dict]:
    base = datetime(2024, 1, 1, 0, 0, 0)
    return [
        {
            "time": base,
            "strategy": "cross_exchange_v1",
            "exchange_pair": "binance-okx",
            "gross_spread": 0.005 + i * 0.001,
            "net_spread": 0.003 + i * 0.001,
        }
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# connect / close / context manager
# ---------------------------------------------------------------------------

class TestDataLoaderConnect:
    @pytest.mark.asyncio
    async def test_connect_calls_asyncpg_connect(self):
        loader = _make_loader()
        mock_conn = AsyncMock()
        with patch("asyncpg.connect", return_value=mock_conn) as mock_connect:
            await loader.connect()
        mock_connect.assert_called_once_with("postgresql://user:pass@localhost/testdb")
        assert loader._conn is mock_conn

    @pytest.mark.asyncio
    async def test_close_closes_and_clears_conn(self):
        mock_conn = AsyncMock()
        loader = _make_loader(conn=mock_conn)
        await loader.close()
        mock_conn.close.assert_called_once()
        assert loader._conn is None

    @pytest.mark.asyncio
    async def test_close_noop_when_already_none(self):
        loader = _make_loader()
        await loader.close()  # must not raise

    @pytest.mark.asyncio
    async def test_context_manager_connects_and_closes(self):
        loader = _make_loader()
        mock_conn = AsyncMock()
        with patch("asyncpg.connect", return_value=mock_conn):
            async with loader as dl:
                assert dl is loader
                assert loader._conn is mock_conn
        assert loader._conn is None

    @pytest.mark.asyncio
    async def test_context_manager_closes_on_exit(self):
        loader = _make_loader()
        mock_conn = AsyncMock()
        with patch("asyncpg.connect", return_value=mock_conn):
            async with loader:
                pass
        mock_conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

class TestDataLoaderCache:
    def test_cache_key_is_deterministic(self):
        loader = _make_loader()
        k1 = loader._cache_key("ohlcv", "binance", "BTC/USDT", "2024-01-01")
        k2 = loader._cache_key("ohlcv", "binance", "BTC/USDT", "2024-01-01")
        assert k1 == k2

    def test_cache_key_differs_for_different_args(self):
        loader = _make_loader()
        k1 = loader._cache_key("ohlcv", "binance", "BTC/USDT")
        k2 = loader._cache_key("ohlcv", "binance", "ETH/USDT")
        assert k1 != k2

    def test_evict_removes_oldest_when_at_capacity(self):
        loader = _make_loader(cache_max=2)
        loader._cache["k1"] = "v1"
        loader._cache["k2"] = "v2"
        loader._evict_if_needed()  # at capacity → evict oldest
        assert "k1" not in loader._cache
        assert "k2" in loader._cache

    def test_evict_is_noop_when_below_capacity(self):
        loader = _make_loader(cache_max=10)
        loader._cache["k1"] = "v1"
        loader._evict_if_needed()
        assert "k1" in loader._cache  # not evicted

    def test_clear_cache_empties_all_entries(self):
        loader = _make_loader()
        loader._cache["a"] = 1
        loader._cache["b"] = 2
        loader.clear_cache()
        assert loader._cache == {}


# ---------------------------------------------------------------------------
# load_ohlcv
# ---------------------------------------------------------------------------

class TestDataLoaderLoadOHLCV:
    _start = datetime(2024, 1, 1)
    _end = datetime(2024, 1, 2)

    @pytest.mark.asyncio
    async def test_load_ohlcv_returns_window_with_data(self):
        conn = AsyncMock()
        conn.fetch.return_value = _fake_ohlcv_rows(3)
        loader = _make_loader(conn=conn)

        window = await loader.load_ohlcv("binance", "BTC/USDT", self._start, self._end)

        assert isinstance(window, OHLCVWindow)
        assert window.length == 3
        assert window.closes[0] == pytest.approx(50500.0)
        assert window.volumes[2] == pytest.approx(120.0)

    @pytest.mark.asyncio
    async def test_load_ohlcv_empty_result_returns_empty_window(self):
        conn = AsyncMock()
        conn.fetch.return_value = []
        loader = _make_loader(conn=conn)

        window = await loader.load_ohlcv("binance", "ETH/USDT", self._start, self._end)

        assert window.length == 0
        assert len(window.closes) == 0
        assert window.closes.dtype == float

    @pytest.mark.asyncio
    async def test_load_ohlcv_caches_result(self):
        conn = AsyncMock()
        conn.fetch.return_value = _fake_ohlcv_rows(2)
        loader = _make_loader(conn=conn)

        w1 = await loader.load_ohlcv("binance", "BTC/USDT", self._start, self._end)
        w2 = await loader.load_ohlcv("binance", "BTC/USDT", self._start, self._end)

        assert conn.fetch.call_count == 1  # only one DB query
        assert w1 is w2

    @pytest.mark.asyncio
    async def test_load_ohlcv_requires_connection(self):
        loader = _make_loader()  # no conn
        with pytest.raises((AssertionError, RuntimeError)):
            await loader.load_ohlcv("binance", "BTC/USDT", self._start, self._end)

    @pytest.mark.asyncio
    async def test_load_ohlcv_passes_correct_query_params(self):
        conn = AsyncMock()
        conn.fetch.return_value = []
        loader = _make_loader(conn=conn)

        await loader.load_ohlcv("okx", "ETH/USDT", self._start, self._end)

        call_args = conn.fetch.call_args
        args = call_args[0]
        assert "okx" in args
        assert "ETH/USDT" in args
        assert self._start in args
        assert self._end in args

    @pytest.mark.asyncio
    async def test_load_ohlcv_evicts_oldest_when_cache_full(self):
        conn = AsyncMock()
        conn.fetch.return_value = _fake_ohlcv_rows(1)
        loader = _make_loader(conn=conn, cache_max=1)

        start1 = datetime(2024, 1, 1)
        end1 = datetime(2024, 1, 2)
        start2 = datetime(2024, 1, 3)
        end2 = datetime(2024, 1, 4)

        await loader.load_ohlcv("binance", "BTC/USDT", start1, end1)
        await loader.load_ohlcv("binance", "BTC/USDT", start2, end2)

        assert len(loader._cache) == 1  # oldest evicted

    @pytest.mark.asyncio
    async def test_load_ohlcv_numpy_dtypes(self):
        conn = AsyncMock()
        conn.fetch.return_value = _fake_ohlcv_rows(2)
        loader = _make_loader(conn=conn)

        window = await loader.load_ohlcv("binance", "BTC/USDT", self._start, self._end)

        assert window.opens.dtype == float
        assert window.highs.dtype == float
        assert window.lows.dtype == float
        assert window.closes.dtype == float
        assert window.volumes.dtype == float


# ---------------------------------------------------------------------------
# load_spreads
# ---------------------------------------------------------------------------

class TestDataLoaderLoadSpreads:
    _start = datetime(2024, 1, 1)
    _end = datetime(2024, 1, 2)

    @pytest.mark.asyncio
    async def test_load_spreads_returns_records(self):
        conn = AsyncMock()
        conn.fetch.return_value = _fake_spread_rows(3)
        loader = _make_loader(conn=conn)

        records = await loader.load_spreads("cross_exchange_v1", self._start, self._end)

        assert len(records) == 3
        assert all(isinstance(r, SpreadRecord) for r in records)
        assert records[0].strategy == "cross_exchange_v1"
        assert records[0].exchange_pair == "binance-okx"

    @pytest.mark.asyncio
    async def test_load_spreads_empty_result(self):
        conn = AsyncMock()
        conn.fetch.return_value = []
        loader = _make_loader(conn=conn)

        records = await loader.load_spreads("strat", self._start, self._end)
        assert records == []

    @pytest.mark.asyncio
    async def test_load_spreads_caches_result(self):
        conn = AsyncMock()
        conn.fetch.return_value = _fake_spread_rows(2)
        loader = _make_loader(conn=conn)

        r1 = await loader.load_spreads("strat", self._start, self._end)
        r2 = await loader.load_spreads("strat", self._start, self._end)

        assert conn.fetch.call_count == 1
        assert r1 is r2

    @pytest.mark.asyncio
    async def test_load_spreads_requires_connection(self):
        loader = _make_loader()
        with pytest.raises((AssertionError, RuntimeError)):
            await loader.load_spreads("strat", self._start, self._end)

    @pytest.mark.asyncio
    async def test_load_spreads_field_values(self):
        conn = AsyncMock()
        conn.fetch.return_value = [
            {
                "time": datetime(2024, 6, 15),
                "strategy": "arb_v2",
                "exchange_pair": "bybit-okx",
                "gross_spread": 0.0123,
                "net_spread": 0.0087,
            }
        ]
        loader = _make_loader(conn=conn)
        records = await loader.load_spreads("arb_v2", self._start, self._end)

        r = records[0]
        assert r.gross_spread == pytest.approx(0.0123)
        assert r.net_spread == pytest.approx(0.0087)
        assert r.time == datetime(2024, 6, 15)

    @pytest.mark.asyncio
    async def test_load_spreads_passes_strategy_to_query(self):
        conn = AsyncMock()
        conn.fetch.return_value = []
        loader = _make_loader(conn=conn)

        await loader.load_spreads("my_strategy", self._start, self._end)

        call_args = conn.fetch.call_args[0]
        assert "my_strategy" in call_args

    @pytest.mark.asyncio
    async def test_load_spreads_different_strategies_cached_separately(self):
        conn = AsyncMock()
        conn.fetch.return_value = _fake_spread_rows(1)
        loader = _make_loader(conn=conn)

        await loader.load_spreads("strat_a", self._start, self._end)
        await loader.load_spreads("strat_b", self._start, self._end)

        assert conn.fetch.call_count == 2  # different cache keys


# ---------------------------------------------------------------------------
# slice_window
# ---------------------------------------------------------------------------

class TestDataLoaderSliceWindow:
    def _make_window(self, n: int = 5) -> OHLCVWindow:
        return OHLCVWindow(
            times=np.arange(n, dtype=float),
            opens=np.arange(n, dtype=float) * 1.0,
            highs=np.arange(n, dtype=float) * 1.1,
            lows=np.arange(n, dtype=float) * 0.9,
            closes=np.arange(n, dtype=float) * 1.05,
            volumes=np.arange(n, dtype=float) * 100.0,
        )

    def test_slice_window_middle(self):
        loader = _make_loader()
        window = self._make_window(5)
        sliced = loader.slice_window(window, 1, 4)
        assert sliced.length == 3
        assert sliced.closes[0] == pytest.approx(1 * 1.05)
        assert sliced.closes[2] == pytest.approx(3 * 1.05)

    def test_slice_window_full(self):
        loader = _make_loader()
        window = self._make_window(3)
        sliced = loader.slice_window(window, 0, 3)
        assert sliced.length == 3

    def test_slice_window_single_element(self):
        loader = _make_loader()
        window = self._make_window(5)
        sliced = loader.slice_window(window, 2, 3)
        assert sliced.length == 1

    def test_slice_window_preserves_all_arrays(self):
        loader = _make_loader()
        window = self._make_window(4)
        sliced = loader.slice_window(window, 0, 2)
        assert len(sliced.times) == 2
        assert len(sliced.opens) == 2
        assert len(sliced.highs) == 2
        assert len(sliced.lows) == 2
        assert len(sliced.closes) == 2
        assert len(sliced.volumes) == 2
