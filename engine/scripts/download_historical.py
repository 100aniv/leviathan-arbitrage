#!/usr/bin/env python3
"""download_historical.py — OHLCV Historical Data Download + Synthetic Orderbook Insert (US-377, US-387)

Downloads OHLCV candles from exchange REST APIs and inserts them as synthetic orderbook
snapshots into the orderbook_snapshots table.

Supported exchanges: binance, binance_futures, bybit, bybit_futures, okx, okx_futures,
                     mexc, gateio, bingx, lbank, bitget, upbit, bithumb, coinone

Usage:
    cd /Users/100aniv/Development/arbitrage_OMC/engine
    python scripts/download_historical.py [--exchanges binance,bybit] [--symbols BTC/USDT,ETH/USDT]
    python scripts/download_historical.py --dry-run
    python scripts/download_historical.py --exchanges binance --symbols BTC/USDT --start 2024-01-10 --end 2024-03-31 --interval 1h
    python scripts/download_historical.py --exchanges upbit,bithumb --symbols BTC/KRW,ETH/KRW --interval 1h
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import pathlib
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import aiohttp

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
logger = logging.getLogger("download_historical")

# ─── Configuration ───────────────────────────────────────────────────────────

# Default period for K-BT downloads
DEFAULT_START = "2024-01-10"
DEFAULT_END = "2024-03-31"
DEFAULT_INTERVAL = "1h"

# Exchange-specific typical spread in basis points
SPREAD_BPS: dict[str, float] = {
    "binance": 2.0,
    "binance_futures": 1.5,
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
    "upbit": 12.0,
    "bithumb": 15.0,
}

# Interval code mapping per exchange
# Keys: "1h", "5m"
INTERVAL_MAP: dict[str, dict[str, str]] = {
    "binance":          {"1h": "1h",     "5m": "5m"},
    "binance_futures":  {"1h": "1h",     "5m": "5m"},
    "bybit":            {"1h": "60",     "5m": "5"},
    "bybit_futures":    {"1h": "60",     "5m": "5"},
    "okx":              {"1h": "1H",     "5m": "5m"},
    "okx_futures":      {"1h": "1H",     "5m": "5m"},
    "mexc":             {"1h": "60m",    "5m": "5m"},
    "gateio":           {"1h": "1h",     "5m": "5m"},
    "bingx":            {"1h": "1h",     "5m": "5m"},
    "lbank":            {"1h": "hour1",  "5m": "5min"},
    "bitget":           {"1h": "1h",     "5m": "5min"},
    "bitget_futures":   {"1h": "1H",     "5m": "5m"},
    "upbit":            {"1h": "60",     "5m": "5"},   # minutes endpoint
    "bithumb":          {"1h": "1h",     "5m": "5m"},
    "coinone":          {"1h": "1h",     "5m": "5m"},
}

# Candles per request per exchange (for time window calculation)
PAGE_SIZE: dict[str, int] = {
    "binance":         1000,
    "binance_futures": 1000,
    "bybit":            200,
    "bybit_futures":    200,
    "okx":              300,
    "okx_futures":      300,
    "mexc":             500,  # MEXC klines API caps at 500 per request
    "gateio":           500,
    "bingx":           1000,
    "lbank":             60,
    "bitget":          1000,
    "upbit":            200,
    "bithumb":        1440,
    "coinone":          500,  # Coinone chart API max per request
}

# Symbol mapping per exchange (CCXT-style BTC/USDT → exchange-specific)
SYMBOL_MAP: dict[str, dict[str, str]] = {
    "binance": {
        "BTC/USDT": "BTCUSDT", "ETH/USDT": "ETHUSDT", "SOL/USDT": "SOLUSDT",
        "BNB/USDT": "BNBUSDT", "XRP/USDT": "XRPUSDT",
        "ETH/BTC": "ETHBTC", "SOL/BTC": "SOLBTC",
    },
    "binance_futures": {
        "BTC/USDT": "BTCUSDT", "ETH/USDT": "ETHUSDT", "SOL/USDT": "SOLUSDT",
        "BNB/USDT": "BNBUSDT", "XRP/USDT": "XRPUSDT",
    },
    "bybit": {
        "BTC/USDT": "BTCUSDT", "ETH/USDT": "ETHUSDT", "SOL/USDT": "SOLUSDT",
        "BNB/USDT": "BNBUSDT", "ETH/BTC": "ETHBTC", "SOL/BTC": "SOLBTC",
    },
    "bybit_futures": {
        "BTC/USDT": "BTCUSDT", "ETH/USDT": "ETHUSDT", "SOL/USDT": "SOLUSDT",
    },
    "mexc": {
        "BTC/USDT": "BTCUSDT", "ETH/USDT": "ETHUSDT", "SOL/USDT": "SOLUSDT",
        "ETH/BTC": "ETHBTC", "SOL/BTC": "SOLBTC",
    },
    "bingx": {
        "BTC/USDT": "BTC-USDT", "ETH/USDT": "ETH-USDT", "SOL/USDT": "SOL-USDT",
    },
    "lbank": {
        "BTC/USDT": "btc_usdt", "ETH/USDT": "eth_usdt", "SOL/USDT": "sol_usdt",
    },
    "gateio": {
        "BTC/USDT": "BTC_USDT", "ETH/USDT": "ETH_USDT", "SOL/USDT": "SOL_USDT",
        "ETH/BTC": "ETH_BTC", "SOL/BTC": "SOL_BTC",
    },
    "okx": {
        "BTC/USDT": "BTC-USDT", "ETH/USDT": "ETH-USDT", "SOL/USDT": "SOL-USDT",
        "ETH/BTC": "ETH-BTC", "SOL/BTC": "SOL-BTC",
    },
    "okx_futures": {
        "BTC/USDT": "BTC-USDT-SWAP", "ETH/USDT": "ETH-USDT-SWAP", "SOL/USDT": "SOL-USDT-SWAP",
    },
    # Bitget cross-quote pairs for triangular arb (US-384: US-369 re-run)
    "bitget": {
        "BTC/USDT": "BTCUSDT", "ETH/USDT": "ETHUSDT", "SOL/USDT": "SOLUSDT",
        "ETH/BTC": "ETHBTC", "SOL/BTC": "SOLBTC",
    },
    "bitget_futures": {
        "BTC/USDT": "BTCUSDT", "ETH/USDT": "ETHUSDT", "SOL/USDT": "SOLUSDT",
    },
    "coinone": {
        "BTC/KRW": "BTC", "ETH/KRW": "ETH", "SOL/KRW": "SOL", "XRP/KRW": "XRP",
    },
    # KRW exchanges
    "upbit": {
        "BTC/KRW": "KRW-BTC", "ETH/KRW": "KRW-ETH", "SOL/KRW": "KRW-SOL",
        "XRP/KRW": "KRW-XRP", "BNB/KRW": "KRW-BNB",
    },
    "bithumb": {
        "BTC/KRW": "BTC", "ETH/KRW": "ETH", "SOL/KRW": "SOL",
        "XRP/KRW": "XRP",
    },
}

# ─── Synthetic Orderbook Conversion ─────────────────────────────────────────

def ohlcv_to_levels(exchange: str, close_price: float) -> tuple:
    """Convert OHLCV close price to 5-level synthetic orderbook.

    Returns: (bids_json, asks_json, best_bid, best_ask, spread_bps, mid_price)
    """
    mid = float(close_price)
    if mid <= 0:
        raise ValueError(f"Invalid close price: {close_price}")

    sbps = SPREAD_BPS.get(exchange, 10.0)
    half_spread = mid * (sbps / 20000.0)

    best_bid = mid - half_spread
    best_ask = mid + half_spread
    actual_spread_bps = (best_ask - best_bid) / mid * 10000.0

    bids = []
    asks = []
    for i in range(5):
        bp = best_bid * (1.0 - 0.001 * i)
        ap = best_ask * (1.0 + 0.001 * i)
        bids.append([round(bp, 8), round(1000.0 / bp, 8)])
        asks.append([round(ap, 8), round(1000.0 / ap, 8)])

    return bids, asks, best_bid, best_ask, actual_spread_bps, mid


# ─── REST API Fetchers ────────────────────────────────────────────────────────

async def fetch_binance(
    session: aiohttp.ClientSession,
    symbol: str,
    start_ms: int,
    end_ms: int,
    interval: str = "1h",
) -> list[list]:
    """Fetch Binance Spot OHLCV (GET /api/v3/klines).
    Returns [[ts_ms, open, high, low, close, vol], ...]
    """
    all_candles = []
    cursor_ms = start_ms
    interval_ms = _interval_to_ms(interval)
    page = PAGE_SIZE.get("binance", 1000)

    while cursor_ms < end_ms:
        url = "https://api.binance.com/api/v3/klines"
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": str(cursor_ms),
            "endTime": str(min(cursor_ms + page * interval_ms - 1, end_ms)),
            "limit": str(page),
        }
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.warning("Binance %s HTTP %d", symbol, resp.status)
                    break
                candles = await resp.json()
                if not candles or not isinstance(candles, list):
                    break
                for c in candles:
                    ts = int(c[0])
                    if start_ms <= ts < end_ms:
                        all_candles.append([ts, float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])])
                if len(candles) < page:
                    break
                last_ts = int(candles[-1][0])
                if last_ts <= cursor_ms:
                    break
                cursor_ms = last_ts + interval_ms
                await asyncio.sleep(0.1)
        except Exception as exc:
            logger.warning("Binance fetch error %s: %s", symbol, exc)
            break

    return all_candles


async def fetch_binance_futures(
    session: aiohttp.ClientSession,
    symbol: str,
    start_ms: int,
    end_ms: int,
    interval: str = "1h",
) -> list[list]:
    """Fetch Binance Futures OHLCV (GET /fapi/v1/klines).
    Returns [[ts_ms, open, high, low, close, vol], ...]
    """
    all_candles = []
    cursor_ms = start_ms
    interval_ms = _interval_to_ms(interval)
    page = PAGE_SIZE.get("binance_futures", 1000)

    while cursor_ms < end_ms:
        url = "https://fapi.binance.com/fapi/v1/klines"
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": str(cursor_ms),
            "endTime": str(min(cursor_ms + page * interval_ms - 1, end_ms)),
            "limit": str(page),
        }
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.warning("BinanceFutures %s HTTP %d", symbol, resp.status)
                    break
                candles = await resp.json()
                if not candles or not isinstance(candles, list):
                    break
                for c in candles:
                    ts = int(c[0])
                    if start_ms <= ts < end_ms:
                        all_candles.append([ts, float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])])
                if len(candles) < page:
                    break
                last_ts = int(candles[-1][0])
                if last_ts <= cursor_ms:
                    break
                cursor_ms = last_ts + interval_ms
                await asyncio.sleep(0.1)
        except Exception as exc:
            logger.warning("BinanceFutures fetch error %s: %s", symbol, exc)
            break

    return all_candles


async def fetch_upbit(
    session: aiohttp.ClientSession,
    symbol: str,
    start_ms: int,
    end_ms: int,
    interval: str = "1h",
) -> list[list]:
    """Fetch Upbit KRW OHLCV (GET /v1/candles/minutes/60).
    symbol: KRW-BTC format.
    Upbit paginates backwards (newest first), max 200/request.
    Returns [[ts_ms, open, high, low, close, vol], ...]
    """
    all_candles = []
    # Upbit uses 'to' param (ISO8601 UTC exclusive upper bound), paginate backwards from end
    from datetime import timezone as _tz
    interval_minutes = _interval_to_minutes(interval)
    url = f"https://api.upbit.com/v1/candles/minutes/{interval_minutes}"
    page = PAGE_SIZE.get("upbit", 200)

    # Start from end, walk backwards
    cursor_end_ms = end_ms

    while cursor_end_ms > start_ms:
        to_str = datetime.fromtimestamp(cursor_end_ms / 1000.0, tz=_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {
            "market": symbol,
            "to": to_str,
            "count": str(page),
        }
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning("Upbit %s HTTP %d: %s", symbol, resp.status, body[:200])
                    break
                candles = await resp.json()
                if not candles or not isinstance(candles, list):
                    break
                # Upbit returns newest first; each item has candle_date_time_utc, opening_price, high_price, low_price, trade_price, candle_acc_trade_volume
                for c in candles:
                    # candle_date_time_utc: "2024-01-10T00:00:00"
                    ts_str = c.get("candle_date_time_utc", "")
                    if not ts_str:
                        continue
                    try:
                        ts_dt = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=_tz.utc)
                        ts_ms_val = int(ts_dt.timestamp() * 1000)
                    except ValueError:
                        continue
                    if start_ms <= ts_ms_val < end_ms:
                        all_candles.append([
                            ts_ms_val,
                            float(c.get("opening_price", 0)),
                            float(c.get("high_price", 0)),
                            float(c.get("low_price", 0)),
                            float(c.get("trade_price", 0)),
                            float(c.get("candle_acc_trade_volume", 0)),
                        ])
                # Oldest candle is last in response
                oldest = candles[-1]
                oldest_str = oldest.get("candle_date_time_utc", "")
                if not oldest_str:
                    break
                oldest_dt = datetime.strptime(oldest_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=_tz.utc)
                oldest_ms = int(oldest_dt.timestamp() * 1000)
                if oldest_ms <= start_ms:
                    break
                # Move cursor back by one interval from the oldest candle
                interval_ms = interval_minutes * 60 * 1000
                cursor_end_ms = oldest_ms - interval_ms
                await asyncio.sleep(0.1)
        except Exception as exc:
            logger.warning("Upbit fetch error %s: %s", symbol, exc)
            break

    # Sort chronologically
    all_candles.sort(key=lambda c: c[0])
    return all_candles


async def fetch_bithumb(
    session: aiohttp.ClientSession,
    symbol: str,
    start_ms: int,
    end_ms: int,
    interval: str = "1h",
) -> list[list]:
    """Fetch Bithumb KRW OHLCV (GET /public/candlestick/{symbol}_KRW/1h).
    symbol: BTC (not BTC_KRW, not KRW-BTC).
    Returns up to 1440 candles per request (60 days at 1H).
    Returns [[ts_ms, open, high, low, close, vol], ...]
    """
    all_candles = []
    interval_code = INTERVAL_MAP.get("bithumb", {}).get(interval, "1h")
    url = f"https://api.bithumb.com/public/candlestick/{symbol}_KRW/{interval_code}"

    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                logger.warning("Bithumb %s HTTP %d", symbol, resp.status)
                return []
            body = await resp.json()
            if body.get("status") != "0000":
                logger.warning("Bithumb %s error: %s", symbol, body.get("message", ""))
                return []
            candles_raw = body.get("data", [])
            if not candles_raw or not isinstance(candles_raw, list):
                return []
            # Bithumb: [timestamp_ms, open, close, high, low, vol]  (note: open/close order!)
            for c in candles_raw:
                if not isinstance(c, list) or len(c) < 6:
                    continue
                try:
                    ts_ms_val = int(c[0])
                    # Handle seconds vs ms
                    if ts_ms_val < 1e12:
                        ts_ms_val = ts_ms_val * 1000
                    if start_ms <= ts_ms_val < end_ms:
                        open_  = float(c[1])
                        close_ = float(c[2])
                        high   = float(c[3])
                        low    = float(c[4])
                        vol    = float(c[5])
                        all_candles.append([ts_ms_val, open_, high, low, close_, vol])
                except (ValueError, IndexError):
                    continue
    except Exception as exc:
        logger.warning("Bithumb fetch error %s: %s", symbol, exc)

    all_candles.sort(key=lambda c: c[0])
    return all_candles


def _interval_to_ms(interval: str) -> int:
    """Convert interval string to milliseconds."""
    mapping = {
        "1m":    60_000,
        "5m":    300_000,
        "15m":   900_000,
        "30m":   1_800_000,
        "1h":    3_600_000,
        "4h":    14_400_000,
        "1d":    86_400_000,
        # Bybit-style
        "1":     60_000,
        "5":     300_000,
        "60":    3_600_000,
        "240":   14_400_000,
        # LBank-style
        "5min":  300_000,
        "hour1": 3_600_000,
        # OKX-style
        "1H":    3_600_000,
        "4H":    14_400_000,
    }
    return mapping.get(interval, 3_600_000)


def _interval_to_minutes(interval: str) -> int:
    """Convert interval string to minutes (for Upbit URL path)."""
    return _interval_to_ms(interval) // 60_000


async def fetch_bybit(
    session: aiohttp.ClientSession,
    symbol: str,
    start_ms: int,
    end_ms: int,
    category: str = "spot",
    interval: str = "1h",
) -> list[list]:
    """Fetch Bybit OHLCV. Returns [[ts_ms, open, high, low, close, vol], ...]"""
    all_candles = []
    cursor_ms = start_ms
    exchange_key = "bybit" if category == "spot" else "bybit_futures"
    interval_code = INTERVAL_MAP.get(exchange_key, {}).get(interval, "60")
    interval_ms = _interval_to_ms(interval)
    page = PAGE_SIZE.get(exchange_key, 200)

    while cursor_ms < end_ms:
        url = "https://api.bybit.com/v5/market/kline"
        params = {
            "category": category,
            "symbol": symbol,
            "interval": interval_code,
            "start": str(cursor_ms),
            "end": str(min(cursor_ms + page * interval_ms, end_ms)),
            "limit": str(page),
        }
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.warning("Bybit %s HTTP %d", symbol, resp.status)
                    break
                data = await resp.json()
                candles = data.get("result", {}).get("list", [])
                if not candles:
                    break
                # Bybit returns newest first, reverse to chronological
                candles = list(reversed(candles))
                for c in candles:
                    ts = int(c[0])
                    if start_ms <= ts < end_ms:
                        all_candles.append([ts, float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])])
                last_ts = int(candles[-1][0])
                if last_ts <= cursor_ms:
                    break
                cursor_ms = last_ts + interval_ms
                await asyncio.sleep(0.1)
        except Exception as exc:
            logger.warning("Bybit fetch error %s: %s", symbol, exc)
            break

    return all_candles


async def fetch_mexc(
    session: aiohttp.ClientSession,
    symbol: str,
    start_ms: int,
    end_ms: int,
    interval: str = "1h",
) -> list[list]:
    """Fetch MEXC OHLCV."""
    all_candles = []
    cursor_ms = start_ms
    interval_code = INTERVAL_MAP.get("mexc", {}).get(interval, "1h")
    interval_ms = _interval_to_ms(interval)
    page = PAGE_SIZE.get("mexc", 1000)

    while cursor_ms < end_ms:
        url = "https://api.mexc.com/api/v3/klines"
        params = {
            "symbol": symbol,
            "interval": interval_code,
            "startTime": str(cursor_ms),
            "endTime": str(min(cursor_ms + page * interval_ms, end_ms)),
            "limit": str(page),
        }
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.warning("MEXC %s HTTP %d", symbol, resp.status)
                    break
                candles = await resp.json()
                if not candles or not isinstance(candles, list):
                    break
                for c in candles:
                    ts = int(c[0])
                    if start_ms <= ts < end_ms:
                        all_candles.append([ts, float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])])
                if len(candles) < page:
                    break
                last_ts = int(candles[-1][0])
                if last_ts <= cursor_ms:
                    break
                cursor_ms = last_ts + interval_ms
                await asyncio.sleep(0.1)
        except Exception as exc:
            logger.warning("MEXC fetch error %s: %s", symbol, exc)
            break

    return all_candles


async def fetch_gateio(
    session: aiohttp.ClientSession,
    symbol: str,
    start_ms: int,
    end_ms: int,
    interval: str = "1h",
) -> list[list]:
    """Fetch Gate.io OHLCV."""
    all_candles = []
    cursor_s = start_ms // 1000
    interval_code = INTERVAL_MAP.get("gateio", {}).get(interval, "1h")
    interval_s = _interval_to_ms(interval) // 1000
    page = PAGE_SIZE.get("gateio", 500)

    while cursor_s * 1000 < end_ms:
        url = "https://api.gateio.ws/api/v4/spot/candlesticks"
        batch_end_s = min(cursor_s + page * interval_s, end_ms // 1000)
        params = {
            "currency_pair": symbol,
            "interval": interval_code,
            "from": str(cursor_s),
            "to": str(batch_end_s),
            # NOTE: do NOT add 'limit' — Gate.io returns 400 when limit is combined with from/to
        }
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.warning("Gate.io %s HTTP %d", symbol, resp.status)
                    break
                candles = await resp.json()
                if not candles or not isinstance(candles, list):
                    break
                # Gate.io: ["unix_ts", "vol", "close", "high", "low", "open", "base_vol", bool]
                for c in candles:
                    ts_ms = int(c[0]) * 1000
                    if start_ms <= ts_ms < end_ms:
                        all_candles.append([ts_ms, float(c[5]), float(c[3]), float(c[4]), float(c[2]), float(c[1])])
                if batch_end_s >= end_ms // 1000:
                    break
                cursor_s = batch_end_s + interval_s
                await asyncio.sleep(0.1)
        except Exception as exc:
            logger.warning("Gate.io fetch error %s: %s", symbol, exc)
            break

    return all_candles


async def fetch_bingx(
    session: aiohttp.ClientSession,
    symbol: str,
    start_ms: int,
    end_ms: int,
    interval: str = "1h",
) -> list[list]:
    """Fetch BingX OHLCV (graceful skip on API changes)."""
    all_candles = []
    cursor_ms = start_ms
    interval_code = INTERVAL_MAP.get("bingx", {}).get(interval, "1h")
    interval_ms = _interval_to_ms(interval)
    page = PAGE_SIZE.get("bingx", 1000)

    while cursor_ms < end_ms:
        url = "https://open-api.bingx.com/openApi/spot/v1/market/kline"
        params = {
            "symbol": symbol,
            "interval": interval_code,
            "startTime": str(cursor_ms),
            "endTime": str(min(cursor_ms + page * interval_ms, end_ms)),
            "limit": str(page),
        }
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.warning("BingX %s HTTP %d — skipping", symbol, resp.status)
                    break
                body = await resp.json()
                # BingX response formats vary — handle both known structures
                candles_raw = None
                if isinstance(body, dict):
                    d = body.get("data", {})
                    if isinstance(d, dict):
                        candles_raw = d.get("klines", [])
                    elif isinstance(d, list):
                        candles_raw = d
                elif isinstance(body, list):
                    candles_raw = body

                if not candles_raw:
                    break

                for c in candles_raw:
                    try:
                        if isinstance(c, dict):
                            ts = int(c.get("time", c.get("openTime", 0)))
                            close = float(c.get("close", 0))
                            open_ = float(c.get("open", close))
                            high = float(c.get("high", close))
                            low = float(c.get("low", close))
                            vol = float(c.get("volume", 0))
                        elif isinstance(c, list) and len(c) >= 5:
                            ts = int(c[0])
                            open_, high, low, close, vol = float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5]) if len(c) > 5 else 0.0
                        else:
                            continue
                        if start_ms <= ts < end_ms and close > 0:
                            all_candles.append([ts, open_, high, low, close, vol])
                    except (ValueError, KeyError, IndexError):
                        continue

                if len(candles_raw) < 100:
                    break
                last_ts = all_candles[-1][0] if all_candles else cursor_ms
                if last_ts <= cursor_ms:
                    break
                cursor_ms = last_ts + interval_ms
                await asyncio.sleep(0.15)
        except Exception as exc:
            logger.warning("BingX fetch error %s: %s — skipping gracefully", symbol, exc)
            break

    return all_candles


async def fetch_lbank(
    session: aiohttp.ClientSession,
    symbol: str,
    start_ms: int,
    end_ms: int,
    interval: str = "1h",
) -> list[list]:
    """Fetch LBank OHLCV."""
    all_candles = []
    cursor_s = start_ms // 1000
    end_s = end_ms // 1000
    interval_code = INTERVAL_MAP.get("lbank", {}).get(interval, "hour1")
    interval_s = _interval_to_ms(interval) // 1000
    page = PAGE_SIZE.get("lbank", 60)

    while cursor_s < end_s:
        url = "https://api.lbkex.com/v2/kline.do"
        params = {
            "symbol": symbol,
            "size": str(page),
            "type": interval_code,
            "time": str(cursor_s),
        }
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.warning("LBank %s HTTP %d", symbol, resp.status)
                    break
                body = await resp.json()
                candles = body.get("data", []) if isinstance(body, dict) else body
                if not candles or not isinstance(candles, list):
                    break
                for c in candles:
                    ts_ms = int(c[0]) if int(c[0]) > 1e10 else int(c[0]) * 1000
                    if start_ms <= ts_ms < end_ms:
                        all_candles.append([ts_ms, float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])])
                if len(candles) < page:
                    break
                last_ts_s = all_candles[-1][0] // 1000 if all_candles else cursor_s
                if last_ts_s <= cursor_s:
                    break
                cursor_s = last_ts_s + interval_s
                await asyncio.sleep(0.15)
        except Exception as exc:
            logger.warning("LBank fetch error %s: %s", symbol, exc)
            break

    return all_candles


# ─── DB Insert ───────────────────────────────────────────────────────────────

_INSERT_SQL = """
    INSERT INTO orderbook_snapshots
        (ts, exchange, symbol, bids_json, asks_json, best_bid, best_ask, spread_bps, mid_price)
    VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7, $8, $9)
    ON CONFLICT DO NOTHING
"""


async def insert_candles(
    pool,
    exchange: str,
    unified_symbol: str,
    candles: list[list],
    dry_run: bool = False,
) -> int:
    """Convert OHLCV candles to synthetic orderbook rows and insert into DB."""
    if not candles:
        return 0

    rows = []
    for c in candles:
        ts_ms, open_, high, low, close, vol = c[0], c[1], c[2], c[3], c[4], c[5] if len(c) > 5 else 0.0
        if close <= 0:
            continue
        try:
            bids, asks, best_bid, best_ask, sbps, mid = ohlcv_to_levels(exchange, close)
        except ValueError:
            continue

        ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        rows.append((
            ts,
            exchange,
            unified_symbol,
            json.dumps(bids),
            json.dumps(asks),
            best_bid,
            best_ask,
            sbps,
            mid,
        ))

    if dry_run:
        logger.info("[DRY-RUN] %s %s: would insert %d rows", exchange, unified_symbol, len(rows))
        return len(rows)

    if not rows:
        return 0

    async with pool.acquire() as conn:
        await conn.executemany(_INSERT_SQL, rows)

    return len(rows)


async def fetch_okx(
    session: aiohttp.ClientSession,
    symbol: str,
    start_ms: int,
    end_ms: int,
    inst_type: str = "SPOT",
    interval: str = "1h",
) -> list[list]:
    """Fetch OKX OHLCV. Paginates backwards from end_ms to start_ms."""
    all_candles = []
    # OKX: after=ts means return candles with ts < after (exclusive upper bound)
    after_ts = str(end_ms + 1)
    exchange_key = "okx" if inst_type == "SPOT" else "okx_futures"
    interval_code = INTERVAL_MAP.get(exchange_key, {}).get(interval, "1H")
    page = PAGE_SIZE.get(exchange_key, 300)

    # Use history-candles for data older than ~3 months; candles for recent data
    _three_months_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - 90 * 24 * 3600 * 1000
    use_history = end_ms < _three_months_ms

    while True:
        url = (
            "https://www.okx.com/api/v5/market/history-candles"
            if use_history
            else "https://www.okx.com/api/v5/market/candles"
        )
        params = {
            "instId": symbol,
            "bar": interval_code,
            "limit": str(page),
            "after": after_ts,
        }
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.warning("OKX %s HTTP %d", symbol, resp.status)
                    break
                body = await resp.json()
                if body.get("code") != "0":
                    logger.warning("OKX %s error: %s", symbol, body.get("msg", ""))
                    break
                candles = body.get("data", [])
                if not candles:
                    break
                # OKX returns newest first — each item: [ts_ms, open, high, low, close, vol, ...]
                for c in candles:
                    ts_ms = int(c[0])
                    if start_ms <= ts_ms < end_ms:
                        all_candles.append([ts_ms, float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])])
                # Oldest candle in this batch
                oldest_ts = int(candles[-1][0])
                if oldest_ts <= start_ms:
                    break
                after_ts = str(oldest_ts)
                await asyncio.sleep(0.1)
        except Exception as exc:
            logger.warning("OKX fetch error %s: %s", symbol, exc)
            break

    # Sort chronologically
    all_candles.sort(key=lambda c: c[0])
    return all_candles


async def fetch_bitget(
    session: aiohttp.ClientSession,
    symbol: str,
    start_ms: int,
    end_ms: int,
    interval: str = "1h",
    market_type: str = "spot",
) -> list[list]:
    """Fetch Bitget OHLCV (V2 API). market_type='spot' or 'futures'."""
    all_candles = []
    cursor_ms = start_ms
    _map_key = "bitget_futures" if market_type == "futures" else "bitget"
    interval_code = INTERVAL_MAP.get(_map_key, {}).get(interval, "1h")
    interval_ms = _interval_to_ms(interval)
    page = PAGE_SIZE.get("bitget", 1000)

    # Use history-candles for data older than ~3 months
    # history-candles does NOT accept 'limit' param; max 100 candles per request
    _three_months_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - 90 * 24 * 3600 * 1000
    use_history = end_ms < _three_months_ms
    _hist_page = 100  # history-candles max per request

    while cursor_ms < end_ms:
        if market_type == "futures":
            url = (
                "https://api.bitget.com/api/v2/mix/market/history-candles"
                if use_history
                else "https://api.bitget.com/api/v2/mix/market/candles"
            )
        else:
            url = (
                "https://api.bitget.com/api/v2/spot/market/history-candles"
                if use_history
                else "https://api.bitget.com/api/v2/spot/market/candles"
            )
        effective_page = _hist_page if use_history else page
        params: dict = {
            "symbol": symbol,
            "granularity": interval_code,
            "startTime": str(cursor_ms),
            "endTime": str(min(cursor_ms + effective_page * interval_ms, end_ms)),
        }
        if market_type == "futures":
            params["productType"] = "usdt-futures"
        if not use_history:
            params["limit"] = str(page)
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.warning("Bitget %s HTTP %d", symbol, resp.status)
                    break
                body = await resp.json()
                if body.get("code") != "00000":
                    logger.warning("Bitget %s error: %s", symbol, body.get("msg", ""))
                    break
                candles = body.get("data", [])
                if not candles:
                    break
                # Bitget: [ts_ms, open, high, low, close, baseVol, quoteVol, usdtVol]
                for c in candles:
                    ts_ms = int(c[0])
                    if start_ms <= ts_ms < end_ms:
                        all_candles.append([ts_ms, float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])])
                if len(candles) < effective_page:
                    break
                last_ts = int(candles[-1][0])
                if last_ts <= cursor_ms:
                    break
                cursor_ms = last_ts + interval_ms
                await asyncio.sleep(0.1)
        except Exception as exc:
            logger.warning("Bitget fetch error %s: %s", symbol, exc)
            break

    return all_candles


async def fetch_coinone(
    session: aiohttp.ClientSession,
    symbol: str,
    start_ms: int,
    end_ms: int,
    interval: str = "1h",
) -> list[list]:
    """Fetch Coinone KRW OHLCV (GET /public/v2/chart/{quote_currency}/{target_currency}).
    Correct URL: KRW/{symbol} (quote first, then target — NOT {symbol}/KRW).
    Pagination: backwards via 'timestamp' cursor (ms). is_last=true stops iteration.
    symbol: BTC (coinone SYMBOL_MAP maps BTC/KRW -> BTC).
    Returns [[ts_ms, open, high, low, close, vol], ...]
    """
    all_candles: list[list] = []
    interval_code = INTERVAL_MAP.get("coinone", {}).get(interval, "1h")
    page = PAGE_SIZE.get("coinone", 500)

    # Coinone uses backwards pagination: timestamp cursor returns data BEFORE that ms
    cursor_ms: int | None = end_ms
    while True:
        url = f"https://api.coinone.co.kr/public/v2/chart/KRW/{symbol}"
        params: dict = {
            "interval": interval_code,
            "size": str(page),
        }
        if cursor_ms is not None:
            params["timestamp"] = str(cursor_ms)
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.warning("Coinone %s HTTP %d", symbol, resp.status)
                    break
                body = await resp.json()
                if body.get("result") != "success":
                    logger.warning("Coinone %s error: %s", symbol, body.get("error_code", ""))
                    break
                candles_raw = body.get("chart", [])
                if not candles_raw or not isinstance(candles_raw, list):
                    break
                # Coinone: {"timestamp": unix_ms, "open": str, "high": str, "low": str, "close": str, "target_volume": str}
                oldest_ts = None
                for c in candles_raw:
                    try:
                        ts_val = int(c.get("timestamp", 0))
                        ts_ms_val = ts_val * 1000 if ts_val < 1_000_000_000_000 else ts_val
                        if oldest_ts is None or ts_ms_val < oldest_ts:
                            oldest_ts = ts_ms_val
                        if start_ms <= ts_ms_val < end_ms:
                            all_candles.append([
                                ts_ms_val,
                                float(c.get("open", 0)),
                                float(c.get("high", 0)),
                                float(c.get("low", 0)),
                                float(c.get("close", 0)),
                                float(c.get("target_volume", 0)),
                            ])
                    except (ValueError, KeyError):
                        continue
                is_last = body.get("is_last", False)
                if is_last or oldest_ts is None or oldest_ts <= start_ms:
                    break
                cursor_ms = oldest_ts  # next page = data before oldest seen
                await asyncio.sleep(0.15)
        except Exception as exc:
            logger.warning("Coinone fetch error %s: %s", symbol, exc)
            break

    all_candles.sort(key=lambda c: c[0])
    return all_candles


# ─── Main ─────────────────────────────────────────────────────────────────────

def _make_fetchers(interval: str) -> dict:
    """Build fetcher lambdas bound to the given interval string."""
    return {
        "binance":         lambda sess, sym, s, e: fetch_binance(sess, sym, s, e, interval=interval),
        "binance_futures": lambda sess, sym, s, e: fetch_binance_futures(sess, sym, s, e, interval=interval),
        "bybit":           lambda sess, sym, s, e: fetch_bybit(sess, sym, s, e, category="spot", interval=interval),
        "bybit_futures":   lambda sess, sym, s, e: fetch_bybit(sess, sym, s, e, category="linear", interval=interval),
        "okx":             lambda sess, sym, s, e: fetch_okx(sess, sym, s, e, inst_type="SPOT", interval=interval),
        "okx_futures":     lambda sess, sym, s, e: fetch_okx(sess, sym, s, e, inst_type="SWAP", interval=interval),
        "mexc":            lambda sess, sym, s, e: fetch_mexc(sess, sym, s, e, interval=interval),
        "gateio":          lambda sess, sym, s, e: fetch_gateio(sess, sym, s, e, interval=interval),
        "bingx":           lambda sess, sym, s, e: fetch_bingx(sess, sym, s, e, interval=interval),
        "lbank":           lambda sess, sym, s, e: fetch_lbank(sess, sym, s, e, interval=interval),
        "bitget":          lambda sess, sym, s, e: fetch_bitget(sess, sym, s, e, interval=interval),
        "bitget_futures":  lambda sess, sym, s, e: fetch_bitget(sess, sym, s, e, interval=interval, market_type="futures"),
        "upbit":           lambda sess, sym, s, e: fetch_upbit(sess, sym, s, e, interval=interval),
        "bithumb":         lambda sess, sym, s, e: fetch_bithumb(sess, sym, s, e, interval=interval),
        "coinone":         lambda sess, sym, s, e: fetch_coinone(sess, sym, s, e, interval=interval),
    }


DEFAULT_EXCHANGES = ["binance", "bybit", "mexc", "gateio", "bingx", "lbank"]
DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]


async def main(
    exchanges: list[str],
    unified_symbols: list[str],
    start_date: str,
    end_date: str,
    dry_run: bool,
    interval: str = "1h",
) -> None:
    # Parse date range
    start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    # end_date is inclusive — shift end to end of that day
    end_dt = end_dt.replace(hour=23, minute=59, second=59)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    logger.info(
        "Period: %s ~ %s | interval: %s (%d ms ~ %d ms)",
        start_date, end_date, interval, start_ms, end_ms
    )

    fetchers = _make_fetchers(interval)

    pool = None
    if not dry_run:
        import asyncpg
        dsn = os.environ.get("DATABASE_URL", "postgresql://leviathan:leviathan@localhost:5432/leviathan")
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
        pool = await asyncpg.create_pool(dsn, min_size=2, max_size=5)
        logger.info("DB pool connected: %s", dsn.split("@")[-1])

    total_inserted = 0

    async with aiohttp.ClientSession(
        headers={
            "User-Agent": "LeviathanBacktest/1.0",
            # Disable Brotli (br) — aiohttp doesn't support it without the brotli extra package
            "Accept-Encoding": "gzip, deflate",
        },
        connector=aiohttp.TCPConnector(limit=10),
    ) as session:
        for exchange in exchanges:
            fetcher = fetchers.get(exchange)
            if not fetcher:
                logger.warning("No fetcher for exchange: %s — skipping", exchange)
                continue

            sym_map = SYMBOL_MAP.get(exchange, {})
            for unified_sym in unified_symbols:
                exchange_sym = sym_map.get(unified_sym)
                if not exchange_sym:
                    logger.debug("%s %s: no symbol mapping — skipping", exchange, unified_sym)
                    continue

                logger.info("Fetching %s %s (%s) interval=%s...", exchange, unified_sym, exchange_sym, interval)
                try:
                    candles = await fetcher(session, exchange_sym, start_ms, end_ms)
                    logger.info(
                        "%s %s: fetched %d candles", exchange, unified_sym, len(candles)
                    )
                    if candles:
                        inserted = await insert_candles(pool, exchange, unified_sym, candles, dry_run)
                        total_inserted += inserted
                        logger.info(
                            "%s %s: inserted %d rows", exchange, unified_sym, inserted
                        )
                except Exception as exc:
                    logger.error("%s %s: unexpected error: %s", exchange, unified_sym, exc)
                    continue

                await asyncio.sleep(0.2)

    if pool and not dry_run:
        await pool.close()

        # Final count
        import asyncpg
        dsn = os.environ.get("DATABASE_URL", "postgresql://leviathan:leviathan@localhost:5432/leviathan")
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(dsn)
        rows = await conn.fetch(
            "SELECT exchange, COUNT(*) as cnt FROM orderbook_snapshots "
            "WHERE exchange = ANY($1) GROUP BY exchange ORDER BY exchange",
            exchanges,
        )
        await conn.close()
        logger.info("=== DB counts for downloaded exchanges ===")
        for r in rows:
            logger.info("  %s: %d snapshots", r["exchange"], r["cnt"])

    logger.info("Done. Total rows inserted/would-insert: %d", total_inserted)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download OHLCV data and insert as synthetic orderbook snapshots")
    parser.add_argument(
        "--exchanges",
        type=str,
        default=",".join(DEFAULT_EXCHANGES),
        help=f"Comma-separated exchange IDs (default: {','.join(DEFAULT_EXCHANGES)})",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default=",".join(DEFAULT_SYMBOLS),
        help=f"Comma-separated symbols in CCXT format (default: {','.join(DEFAULT_SYMBOLS)})",
    )
    parser.add_argument(
        "--start",
        type=str,
        default=DEFAULT_START,
        help=f"Start date YYYY-MM-DD (default: {DEFAULT_START})",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=DEFAULT_END,
        help=f"End date YYYY-MM-DD (default: {DEFAULT_END})",
    )
    parser.add_argument(
        "--interval",
        type=str,
        default=DEFAULT_INTERVAL,
        help=f"Candle interval: 1h, 5m, etc. (default: {DEFAULT_INTERVAL})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count rows without inserting",
    )
    args = parser.parse_args()

    exchanges = [e.strip() for e in args.exchanges.split(",") if e.strip()]
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    asyncio.run(main(
        exchanges=exchanges,
        unified_symbols=symbols,
        start_date=args.start,
        end_date=args.end,
        dry_run=args.dry_run,
        interval=args.interval,
    ))
