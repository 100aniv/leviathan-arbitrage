#!/usr/bin/env python3
"""ohlcv_to_orderbook.py — CSV/JSON OHLCV → orderbook_snapshots converter (US-378)

Converts OHLCV candle data (from CSV file or piped input) into synthetic orderbook
snapshots and inserts them into the orderbook_snapshots table.

Usage:
    cd /Users/100aniv/Development/arbitrage_OMC/engine
    python scripts/ohlcv_to_orderbook.py --file data.csv --exchange bybit --symbol BTC/USDT
    python scripts/ohlcv_to_orderbook.py --exchange bybit --verify  # DB count check only
    cat data.csv | python scripts/ohlcv_to_orderbook.py --exchange mexc --symbol ETH/USDT

CSV format (header required):
    timestamp_ms,open,high,low,close,volume
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import logging
import os
import pathlib
import sys
from datetime import datetime, timezone
from typing import Iterator

_ENGINE_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ENGINE_ROOT))

_ROOT_ENV = _ENGINE_ROOT.parent / ".env"
if _ROOT_ENV.exists():
    from dotenv import load_dotenv
    load_dotenv(str(_ROOT_ENV), override=False)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ohlcv_to_orderbook")

# ─── Spread Configuration ─────────────────────────────────────────────────────

SPREAD_BPS: dict[str, float] = {
    "bybit": 5.0,
    "bybit_futures": 5.0,
    "okx": 3.0,
    "okx_futures": 3.0,
    "mexc": 10.0,
    "bingx": 10.0,
    "lbank": 20.0,
    "gateio": 8.0,
    "bitget": 8.0,
    "coinone": 15.0,
}

# ─── DB SQL ───────────────────────────────────────────────────────────────────

_INSERT_SQL = """
    INSERT INTO orderbook_snapshots
        (ts, exchange, symbol, bids_json, asks_json, best_bid, best_ask, spread_bps, mid_price)
    VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7, $8, $9)
    ON CONFLICT DO NOTHING
"""

# ─── Conversion Logic ─────────────────────────────────────────────────────────

def ohlcv_to_levels(exchange: str, close_price: float) -> tuple:
    """Convert single OHLCV close to 5-level synthetic orderbook.

    Returns: (bids, asks, best_bid, best_ask, spread_bps, mid_price)
    bids/asks are [[price, qty], ...] arrays (5 levels each).
    """
    mid = float(close_price)
    if mid <= 0:
        raise ValueError(f"Invalid price: {close_price}")

    sbps = SPREAD_BPS.get(exchange, 10.0)
    half_spread = mid * (sbps / 20000.0)
    best_bid = mid - half_spread
    best_ask = mid + half_spread
    actual_sbps = (best_ask - best_bid) / mid * 10000.0

    bids = [[round(best_bid * (1.0 - 0.001 * i), 8), round(1000.0 / (best_bid * (1.0 - 0.001 * i)), 8)] for i in range(5)]
    asks = [[round(best_ask * (1.0 + 0.001 * i), 8), round(1000.0 / (best_ask * (1.0 + 0.001 * i)), 8)] for i in range(5)]

    return bids, asks, best_bid, best_ask, actual_sbps, mid


def parse_csv_rows(file_obj) -> Iterator[list]:
    """Parse CSV rows into [ts_ms, open, high, low, close, volume] lists."""
    reader = csv.DictReader(file_obj)
    for row in reader:
        try:
            # Support various timestamp column names
            ts_raw = row.get("timestamp_ms") or row.get("timestamp") or row.get("ts") or row.get("time")
            close_raw = row.get("close") or row.get("Close")
            open_raw = row.get("open") or row.get("Open") or close_raw
            high_raw = row.get("high") or row.get("High") or close_raw
            low_raw = row.get("low") or row.get("Low") or close_raw
            vol_raw = row.get("volume") or row.get("Volume") or "0"

            ts_ms = int(float(ts_raw))
            # Handle unix seconds vs milliseconds
            if ts_ms < 1e12:
                ts_ms = ts_ms * 1000

            yield [ts_ms, float(open_raw), float(high_raw), float(low_raw), float(close_raw), float(vol_raw)]
        except (ValueError, TypeError, KeyError) as exc:
            logger.debug("Skipping malformed row: %s — %s", row, exc)
            continue


def candles_to_db_rows(exchange: str, symbol: str, candles: list[list]) -> list[tuple]:
    """Convert candle list to DB insert tuples."""
    rows = []
    for c in candles:
        ts_ms, open_, high, low, close, vol = c[0], c[1], c[2], c[3], c[4], c[5] if len(c) > 5 else 0.0
        if close <= 0:
            continue
        try:
            bids, asks, best_bid, best_ask, sbps, mid = ohlcv_to_levels(exchange, close)
        except ValueError as exc:
            logger.debug("Skip row ts=%d: %s", ts_ms, exc)
            continue

        ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        rows.append((
            ts,
            exchange,
            symbol,
            json.dumps(bids),
            json.dumps(asks),
            best_bid,
            best_ask,
            sbps,
            mid,
        ))
    return rows


# ─── DB helpers ───────────────────────────────────────────────────────────────

async def get_pool():
    import asyncpg
    dsn = os.environ.get("DATABASE_URL", "postgresql://leviathan:leviathan@localhost:5432/leviathan")
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    return await asyncpg.create_pool(dsn, min_size=1, max_size=3)


async def verify_counts(exchanges: list[str]) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT exchange, COUNT(*) as cnt, MIN(ts) as first_ts, MAX(ts) as last_ts "
            "FROM orderbook_snapshots "
            "WHERE exchange = ANY($1) "
            "GROUP BY exchange ORDER BY exchange",
            exchanges,
        )
    await pool.close()

    if not rows:
        print(f"No data found for exchanges: {exchanges}")
    else:
        print(f"\n{'Exchange':<20} {'Count':>10} {'First':>25} {'Last':>25}")
        print("-" * 80)
        for r in rows:
            print(f"{r['exchange']:<20} {r['cnt']:>10} {str(r['first_ts']):>25} {str(r['last_ts']):>25}")


async def insert_rows(pool, rows: list[tuple]) -> int:
    if not rows:
        return 0
    async with pool.acquire() as conn:
        await conn.executemany(_INSERT_SQL, rows)
    return len(rows)


# ─── Main ─────────────────────────────────────────────────────────────────────

async def main(
    exchange: str,
    symbol: str,
    file_path: Optional[str],
    verify: bool,
    dry_run: bool,
) -> None:
    if verify:
        await verify_counts([exchange])
        return

    # Read candles from file or stdin
    if file_path:
        with open(file_path, newline="") as f:
            candles = list(parse_csv_rows(f))
        logger.info("Read %d candles from %s", len(candles), file_path)
    else:
        logger.info("Reading from stdin (CSV format expected)...")
        candles = list(parse_csv_rows(sys.stdin))
        logger.info("Read %d candles from stdin", len(candles))

    if not candles:
        logger.warning("No valid candles found — nothing to insert")
        return

    rows = candles_to_db_rows(exchange, symbol, candles)
    logger.info("Converted %d candles → %d DB rows", len(candles), len(rows))

    if dry_run:
        logger.info("[DRY-RUN] Would insert %d rows for %s %s", len(rows), exchange, symbol)
        if rows:
            ts_first = rows[0][0]
            ts_last = rows[-1][0]
            logger.info("[DRY-RUN] Time range: %s ~ %s", ts_first, ts_last)
        return

    pool = await get_pool()
    try:
        inserted = await insert_rows(pool, rows)
        logger.info("Inserted %d rows for %s %s", inserted, exchange, symbol)
    finally:
        await pool.close()


if __name__ == "__main__":
    from typing import Optional

    parser = argparse.ArgumentParser(description="Convert OHLCV CSV to synthetic orderbook snapshots")
    parser.add_argument("--exchange", required=True, help="Exchange ID (e.g. bybit, mexc)")
    parser.add_argument("--symbol", default="BTC/USDT", help="Symbol in CCXT format (e.g. BTC/USDT)")
    parser.add_argument("--file", dest="file_path", default=None, help="Input CSV file path (default: stdin)")
    parser.add_argument("--verify", action="store_true", help="Show DB counts for this exchange and exit")
    parser.add_argument("--dry-run", action="store_true", help="Parse and convert without inserting")
    args = parser.parse_args()

    asyncio.run(main(
        exchange=args.exchange,
        symbol=args.symbol,
        file_path=args.file_path,
        verify=args.verify,
        dry_run=args.dry_run,
    ))
