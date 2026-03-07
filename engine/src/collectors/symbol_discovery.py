"""Auto-discover common trading symbols across exchanges."""
from __future__ import annotations

import asyncio
from typing import Sequence

import httpx
import structlog

logger = structlog.get_logger(__name__)

# Stablecoins and wrapped tokens to exclude (no arbitrage value)
_EXCLUDE = {"USDC", "USDT", "USDE", "USD1", "BUSD", "TUSD", "DAI", "FDUSD", "WBTC", "WETH"}


async def _fetch_binance_bases(client: httpx.AsyncClient) -> set[str]:
    """Fetch base assets of active USDT pairs on Binance."""
    r = await client.get("https://api.binance.com/api/v3/exchangeInfo")
    r.raise_for_status()
    return {
        s["baseAsset"]
        for s in r.json()["symbols"]
        if s["quoteAsset"] == "USDT" and s["status"] == "TRADING"
    }


async def _fetch_upbit_bases(client: httpx.AsyncClient) -> set[str]:
    """Fetch base assets of KRW pairs on Upbit."""
    r = await client.get("https://api.upbit.com/v1/market/all")
    r.raise_for_status()
    return {
        m["market"].split("-")[1]
        for m in r.json()
        if m["market"].startswith("KRW-")
    }


async def _fetch_bithumb_bases(client: httpx.AsyncClient) -> set[str]:
    """Fetch base assets of KRW pairs on Bithumb."""
    r = await client.get("https://api.bithumb.com/public/ticker/ALL_KRW")
    r.raise_for_status()
    data = r.json().get("data", {})
    return {k for k in data.keys() if k != "date"}


_EXCHANGE_FETCHERS = {
    "binance": _fetch_binance_bases,
    "upbit": _fetch_upbit_bases,
    "bithumb": _fetch_bithumb_bases,
}


async def discover_common_symbols(
    exchanges: Sequence[str] = ("binance", "upbit", "bithumb"),
    exclude: set[str] | None = None,
    min_exchanges: int = 2,
) -> list[str]:
    """Discover symbols common to at least `min_exchanges` of the given exchanges.

    Returns symbols in 'BASE/USDT' format, sorted alphabetically.
    """
    if exclude is None:
        exclude = _EXCLUDE

    async with httpx.AsyncClient(timeout=15.0) as client:
        tasks = {}
        for ex in exchanges:
            fetcher = _EXCHANGE_FETCHERS.get(ex)
            if fetcher:
                tasks[ex] = fetcher(client)

        results: dict[str, set[str]] = {}
        for ex, coro in tasks.items():
            try:
                results[ex] = await coro
                logger.info("symbol_discovery", exchange=ex, count=len(results[ex]))
            except Exception as exc:
                logger.warning("symbol_discovery_failed", exchange=ex, error=str(exc))
                results[ex] = set()

    if not results:
        return ["BTC/USDT", "ETH/USDT", "XRP/USDT"]

    # Count how many exchanges list each base asset
    from collections import Counter
    counter: Counter[str] = Counter()
    for bases in results.values():
        for base in bases:
            counter[base] += 1

    # Keep symbols listed on >= min_exchanges
    common = {
        base for base, count in counter.items()
        if count >= min_exchanges and base not in exclude
    }

    symbols = sorted(f"{base}/USDT" for base in common)
    logger.info("symbol_discovery_complete", common=len(symbols), min_exchanges=min_exchanges)
    return symbols


if __name__ == "__main__":
    syms = asyncio.run(discover_common_symbols())
    print(f"Found {len(syms)} common symbols:")
    for s in syms:
        print(f"  {s}")
