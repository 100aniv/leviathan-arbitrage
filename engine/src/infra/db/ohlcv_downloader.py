"""OHLCV Downloader — Binance OHLCV → synthetic orderbook snapshots. US-362."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

_BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
_SYNTHETIC_SPREAD = Decimal("0.0005")  # ±0.05%


class OHLCVDownloader:
    """Downloads Binance OHLCV data and converts to synthetic orderbook snapshots.

    Synthetic orderbook: bid = mid * 0.9995, ask = mid * 1.0005, source='ohlcv_synthetic'
    ⚠️ triangular fee 0.06% > ±0.05% spread → triangular backtest trades=0 expected (architecture validation only)
    """

    def __init__(self, db_pool: Any | None = None) -> None:
        self._db_pool = db_pool

    async def download_and_store(
        self,
        exchange: str,
        symbol: str,
        start_date: str,
        end_date: str,
        interval: str = "1h",
    ) -> int:
        """Download OHLCV from Binance and store as synthetic orderbook snapshots.

        Returns: number of snapshots stored.
        """
        klines = await self._fetch_klines(symbol, start_date, end_date, interval)
        if not klines:
            logger.warning("ohlcv_downloader.no_data symbol=%s start=%s end=%s", symbol, start_date, end_date)
            return 0

        snapshots = self._convert_to_snapshots(exchange, symbol, klines)
        stored = await self._store_snapshots(snapshots)
        logger.info("ohlcv_downloader.stored symbol=%s count=%d", symbol, stored)
        return stored

    async def _fetch_klines(self, symbol: str, start_date: str, end_date: str, interval: str) -> list[list]:
        """Fetch kline data from Binance REST API."""
        binance_symbol = symbol.replace("/", "")
        start_ms = int(datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc).timestamp() * 1000)
        end_ms = int(datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc).timestamp() * 1000)

        all_klines: list[list] = []
        current_start = start_ms

        async with aiohttp.ClientSession() as session:
            while current_start < end_ms:
                params = {
                    "symbol": binance_symbol,
                    "interval": interval,
                    "startTime": current_start,
                    "endTime": end_ms,
                    "limit": 1000,
                }
                try:
                    async with session.get(
                        _BINANCE_KLINES_URL, params=params, timeout=aiohttp.ClientTimeout(total=30)
                    ) as resp:
                        if resp.status != 200:
                            logger.warning("ohlcv_downloader.fetch_error status=%d", resp.status)
                            break
                        data = await resp.json()
                        if not data:
                            break
                        all_klines.extend(data)
                        current_start = data[-1][6] + 1  # next candle after close_time
                        if len(data) < 1000:
                            break
                        await asyncio.sleep(0.1)  # rate limit
                except Exception as exc:
                    logger.warning("ohlcv_downloader.fetch_exception error=%s", exc)
                    break

        return all_klines

    def _convert_to_snapshots(self, exchange: str, symbol: str, klines: list[list]) -> list[dict]:
        """Convert klines to synthetic orderbook snapshots."""
        snapshots = []
        for k in klines:
            # Binance kline: [open_time, open, high, low, close, volume, close_time, ...]
            open_time_ms = k[0]
            close_price = Decimal(str(k[4]))
            volume = float(k[5])

            ts = datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc)
            bid = close_price * (1 - _SYNTHETIC_SPREAD)
            ask = close_price * (1 + _SYNTHETIC_SPREAD)

            snapshots.append({
                "timestamp": ts,
                "exchange": exchange,
                "symbol": symbol,
                "bids": [[float(bid), volume / 2]],
                "asks": [[float(ask), volume / 2]],
                "source": "ohlcv_synthetic",
            })
        return snapshots

    async def _store_snapshots(self, snapshots: list[dict]) -> int:
        """Store snapshots to TimescaleDB orderbook_snapshots table."""
        if self._db_pool is None:
            logger.warning("ohlcv_downloader.no_db_pool storing=%d skipped", len(snapshots))
            return 0

        stored = 0
        try:
            async with self._db_pool.acquire() as conn:
                import json as _json

                for snap in snapshots:
                    await conn.execute(
                        """
                        INSERT INTO orderbook_snapshots (timestamp, exchange, symbol, bids, asks, source)
                        VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6)
                        ON CONFLICT DO NOTHING
                        """,
                        snap["timestamp"],
                        snap["exchange"],
                        snap["symbol"],
                        _json.dumps(snap["bids"]),
                        _json.dumps(snap["asks"]),
                        snap["source"],
                    )
                    stored += 1
        except Exception as exc:
            logger.warning("ohlcv_downloader.store_error error=%s", exc)

        return stored
