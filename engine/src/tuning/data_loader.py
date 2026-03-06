"""TimescaleDB OHLCV and spread data loader with in-memory caching."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np


@dataclass
class OHLCVWindow:
    """Windowed OHLCV data backed by numpy arrays."""

    times: np.ndarray
    opens: np.ndarray
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray
    volumes: np.ndarray

    @property
    def length(self) -> int:
        return len(self.closes)


@dataclass
class SpreadRecord:
    """Single spread observation from TimescaleDB."""

    time: datetime
    strategy: str
    exchange_pair: str
    gross_spread: float
    net_spread: float


class DataLoader:
    """
    Async loader for OHLCV and spread data from TimescaleDB.

    Supports windowed access for walk-forward optimization.
    Provides an LRU-style in-memory cache keyed by query parameters.
    """

    def __init__(self, dsn: str, cache_max_entries: int = 100) -> None:
        self._dsn = dsn
        self._cache: dict[str, Any] = {}
        self._cache_max_entries = cache_max_entries
        self._conn: Any = None  # asyncpg.Connection

    async def connect(self) -> None:
        """Open a persistent asyncpg connection."""
        import asyncpg  # deferred import — asyncpg only needed at runtime

        self._conn = await asyncpg.connect(self._dsn)

    async def close(self) -> None:
        """Close the asyncpg connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> "DataLoader":
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _cache_key(self, *args: Any) -> str:
        return hashlib.md5(str(args).encode()).hexdigest()

    def _evict_if_needed(self) -> None:
        if len(self._cache) >= self._cache_max_entries:
            oldest = next(iter(self._cache))
            del self._cache[oldest]

    def clear_cache(self) -> None:
        self._cache.clear()

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    async def load_ohlcv(
        self,
        exchange: str,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> OHLCVWindow:
        """Load OHLCV candles into a numpy-backed OHLCVWindow."""
        key = self._cache_key("ohlcv", exchange, symbol, start, end)
        if key in self._cache:
            return self._cache[key]

        assert self._conn is not None, "Call connect() first"

        rows = await self._conn.fetch(
            """
            SELECT time, open, high, low, close, volume
            FROM ohlcv
            WHERE exchange = $1 AND symbol = $2
              AND time >= $3 AND time < $4
            ORDER BY time ASC
            """,
            exchange,
            symbol,
            start,
            end,
        )

        if not rows:
            window = OHLCVWindow(
                times=np.array([], dtype="datetime64[ms]"),
                opens=np.array([], dtype=float),
                highs=np.array([], dtype=float),
                lows=np.array([], dtype=float),
                closes=np.array([], dtype=float),
                volumes=np.array([], dtype=float),
            )
        else:
            window = OHLCVWindow(
                times=np.array([r["time"] for r in rows]),
                opens=np.array([float(r["open"]) for r in rows]),
                highs=np.array([float(r["high"]) for r in rows]),
                lows=np.array([float(r["low"]) for r in rows]),
                closes=np.array([float(r["close"]) for r in rows]),
                volumes=np.array([float(r["volume"]) for r in rows]),
            )

        self._evict_if_needed()
        self._cache[key] = window
        return window

    async def load_spreads(
        self,
        strategy: str,
        start: datetime,
        end: datetime,
    ) -> list[SpreadRecord]:
        """Load spread observations for a strategy over a time window."""
        key = self._cache_key("spreads", strategy, start, end)
        if key in self._cache:
            return self._cache[key]

        assert self._conn is not None, "Call connect() first"

        rows = await self._conn.fetch(
            """
            SELECT time, strategy, exchange_pair, gross_spread, net_spread
            FROM spreads
            WHERE strategy = $1 AND time >= $2 AND time < $3
            ORDER BY time ASC
            """,
            strategy,
            start,
            end,
        )

        records = [
            SpreadRecord(
                time=r["time"],
                strategy=r["strategy"],
                exchange_pair=r["exchange_pair"],
                gross_spread=float(r["gross_spread"]),
                net_spread=float(r["net_spread"]),
            )
            for r in rows
        ]

        self._evict_if_needed()
        self._cache[key] = records
        return records

    # ------------------------------------------------------------------
    # Windowing
    # ------------------------------------------------------------------

    def slice_window(self, window: OHLCVWindow, start_idx: int, end_idx: int) -> OHLCVWindow:
        """Return a sub-slice of an OHLCVWindow (used in walk-forward)."""
        return OHLCVWindow(
            times=window.times[start_idx:end_idx],
            opens=window.opens[start_idx:end_idx],
            highs=window.highs[start_idx:end_idx],
            lows=window.lows[start_idx:end_idx],
            closes=window.closes[start_idx:end_idx],
            volumes=window.volumes[start_idx:end_idx],
        )
