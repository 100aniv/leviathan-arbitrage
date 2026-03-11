"""Bithumb public orderbook collector via native WebSocket."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Callable, Awaitable

import httpx
import structlog

from src.collectors.base_collector import BaseCollector

logger = structlog.get_logger(__name__)

_REST_BASE = "https://api.bithumb.com"
_SNAPSHOT_DEPTH = 30


def _normalize_symbol(symbol: str) -> str:
    """Convert 'BTC/KRW' -> 'BTC_KRW' (Bithumb format)."""
    return symbol.replace("/", "_")


def _denormalize_symbol(bithumb_sym: str) -> str:
    """Convert 'BTC_KRW' -> 'BTC/KRW'."""
    return bithumb_sym.replace("_", "/")


def _coin_from_symbol(symbol: str) -> str:
    """Extract coin from 'BTC/KRW' or 'BTC_KRW'."""
    sep = "/" if "/" in symbol else "_"
    return symbol.split(sep)[0]


class BithumbCollector(BaseCollector):
    """Collects Bithumb orderbook snapshots via the public WebSocket.

    Connects to: wss://pubwss.bithumb.com/pub/ws
    Subscription: JSON with type=orderbookdepth + symbols + tickTypes.
    No API key is required for public orderbook data.

    Enhancement: Fetches REST snapshots before WS stream starts to avoid
    stale data on small-cap coins (NOM, SXP, etc.).
    """

    _WS_URL = "wss://pubwss.bithumb.com/pub/ws"

    def __init__(
        self,
        symbols: list[str],
        on_orderbook: Callable[[str, str, list, list], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(exchange_id="bithumb", symbols=symbols, on_orderbook=on_orderbook)
        self._last_update: dict[str, float] = {}
        self._snapshot_fetched = False
        # Accumulated in-memory book: {symbol: {"bids": {price: qty}, "asks": {price: qty}}}
        self._books: dict[str, dict[str, dict[str, str]]] = {}
        self._stale_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start with REST snapshots, then WS stream + per-symbol stale watcher."""
        self._running = True
        await self._fetch_initial_snapshots()
        self._stale_task = asyncio.create_task(self._stale_watch_loop())
        await super().start()

    async def stop(self) -> None:
        """Stop the collector, cancelling the stale watch loop."""
        if self._stale_task is not None:
            self._stale_task.cancel()
            try:
                await self._stale_task
            except asyncio.CancelledError:
                pass
            self._stale_task = None
        await super().stop()

    async def _fetch_initial_snapshots(self) -> None:
        """Fetch REST orderbook snapshots for all symbols before WS starts."""
        if self._snapshot_fetched:
            return
        logger.info("bithumb_rest_snapshot_start", symbols=len(self.symbols))
        fetched = 0
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                for symbol in self.symbols:
                    try:
                        coin = _coin_from_symbol(symbol)
                        resp = await client.get(
                            f"{_REST_BASE}/public/orderbook/{coin}_KRW",
                            params={"count": _SNAPSHOT_DEPTH},
                        )
                        data = resp.json()
                        if data.get("status") != "0000":
                            logger.warning("bithumb_rest_snapshot_error",
                                         symbol=symbol, status=data.get("status"))
                            continue

                        ob_data = data.get("data", {})
                        bids = [[str(item["price"]), str(item["quantity"])]
                                for item in ob_data.get("bids", [])]
                        asks = [[str(item["price"]), str(item["quantity"])]
                                for item in ob_data.get("asks", [])]

                        if not bids or not asks:
                            continue

                        # Price sanity check: top bid should not be > 10x top ask
                        top_bid = float(bids[0][0]) if bids else 0
                        top_ask = float(asks[0][0]) if asks else 0
                        if top_ask > 0 and (top_bid / top_ask > 10 or top_bid / top_ask < 0.1):
                            logger.warning("bithumb_rest_snapshot_price_insane",
                                         symbol=symbol, bid=top_bid, ask=top_ask)
                            continue

                        # Sort: bids descending, asks ascending
                        bids.sort(key=lambda x: float(x[0]), reverse=True)
                        asks.sort(key=lambda x: float(x[0]))

                        # Initialize accumulated book state from snapshot
                        self._books[symbol] = {
                            "bids": {b[0]: b[1] for b in bids},
                            "asks": {a[0]: a[1] for a in asks},
                        }

                        if self._on_orderbook:
                            await self._on_orderbook(self.exchange_id, symbol, bids, asks, is_snapshot=True)
                        self._last_update[symbol] = time.monotonic()
                        fetched += 1

                        # Rate limit: ~5 req/s for Bithumb public API
                        await asyncio.sleep(0.25)

                    except Exception as exc:
                        logger.warning("bithumb_rest_snapshot_symbol_error",
                                     symbol=symbol, error=str(exc))
                        continue

        except Exception as exc:
            logger.error("bithumb_rest_snapshot_failed", error=str(exc))

        self._snapshot_fetched = True
        logger.info("bithumb_rest_snapshot_done", fetched=fetched, total=len(self.symbols))

    def _ws_url(self) -> str:
        return self._WS_URL

    def _subscribe_all_messages(self) -> list[str | dict] | None:
        """Bithumb requires all symbols in a single subscription message."""
        syms = [_normalize_symbol(s) for s in self.symbols]
        return [{"type": "orderbookdepth", "symbols": syms, "tickTypes": ["1H"]}]

    def _subscribe_message(self, symbol: str) -> str | dict:
        """Fallback per-symbol subscription (not used when batch is available)."""
        bithumb_sym = _normalize_symbol(symbol)
        return {
            "type": "orderbookdepth",
            "symbols": [bithumb_sym],
            "tickTypes": ["1H"],
        }

    def _parse_message(self, data: dict) -> tuple[str, list, list] | None:
        """Parse Bithumb orderbookdepth delta and apply to accumulated in-memory book.

        Bithumb format:
        {
            "type": "orderbookdepth",
            "content": {
                "list": [
                    {"symbol": "BTC_KRW", "orderType": "ask",
                     "price": "50000000", "quantity": "0.1"},
                    {"symbol": "BTC_KRW", "orderType": "bid",
                     "price": "49990000", "quantity": "0"},  # qty=0 means delete
                    ...
                ]
            }
        }
        """
        msg_type = data.get("type")
        if msg_type != "orderbookdepth":
            return None

        content = data.get("content", {})
        entries = content.get("list", [])

        if not entries:
            return None

        # Determine symbol from first entry
        raw_sym = entries[0].get("symbol", "")
        symbol = _denormalize_symbol(raw_sym)

        # Ensure accumulated book exists (WS delta may arrive before REST snapshot)
        if symbol not in self._books:
            self._books[symbol] = {"bids": {}, "asks": {}}

        book = self._books[symbol]

        for entry in entries:
            price = str(entry.get("price", "0"))
            qty = str(entry.get("quantity", "0"))
            order_type = entry.get("orderType", "")
            side = "bids" if order_type == "bid" else "asks"

            if float(qty) == 0:
                book[side].pop(price, None)  # Delete level
            else:
                book[side][price] = qty  # Upsert level

        # Return sorted top-N from accumulated state
        bids = sorted(
            [[p, q] for p, q in book["bids"].items()],
            key=lambda x: float(x[0]),
            reverse=True,
        )[:_SNAPSHOT_DEPTH]
        asks = sorted(
            [[p, q] for p, q in book["asks"].items()],
            key=lambda x: float(x[0]),
        )[:_SNAPSHOT_DEPTH]

        self._last_update[symbol] = time.monotonic()
        return symbol, bids, asks

    async def refresh_symbols(self, symbols: list[str]) -> int:
        """Re-fetch REST snapshots for a specific set of symbols in parallel (max 5 concurrent).

        Reinitializes self._books[symbol] for each symbol fetched.
        Returns count of successfully refreshed symbols.
        """
        sem = asyncio.Semaphore(5)

        async def _fetch_one(client: httpx.AsyncClient, symbol: str) -> bool:
            async with sem:
                try:
                    coin = _coin_from_symbol(symbol)
                    resp = await client.get(
                        f"{_REST_BASE}/public/orderbook/{coin}_KRW",
                        params={"count": _SNAPSHOT_DEPTH},
                    )
                    data = resp.json()
                    if data.get("status") != "0000":
                        return False

                    ob_data = data.get("data", {})
                    bids = [[str(item["price"]), str(item["quantity"])]
                            for item in ob_data.get("bids", [])]
                    asks = [[str(item["price"]), str(item["quantity"])]
                            for item in ob_data.get("asks", [])]

                    if not bids or not asks:
                        return False

                    bids.sort(key=lambda x: float(x[0]), reverse=True)
                    asks.sort(key=lambda x: float(x[0]))

                    self._books[symbol] = {
                        "bids": {b[0]: b[1] for b in bids},
                        "asks": {a[0]: a[1] for a in asks},
                    }
                    self._last_update[symbol] = time.monotonic()

                    if self._on_orderbook:
                        await self._on_orderbook(self.exchange_id, symbol, bids, asks, is_snapshot=True)

                    return True
                except Exception as exc:
                    logger.warning("bithumb_refresh_symbol_error", symbol=symbol, error=str(exc))
                    return False

        async with httpx.AsyncClient(timeout=15.0) as client:
            results = await asyncio.gather(*[_fetch_one(client, s) for s in symbols])

        return sum(1 for r in results if r)

    async def refresh_snapshots(self) -> int:
        """Re-fetch REST snapshots for all symbols to re-anchor accumulated deltas.

        Returns count of successfully refreshed symbols.
        Called periodically by ShadowMode._delta_refresh_loop (US-066).
        """
        return await self.refresh_symbols(self.symbols)

    async def _stale_watch_loop(
        self, check_interval_s: float = 1.0, stale_threshold_s: float = 5.0
    ) -> None:
        """Check per-symbol staleness every second; re-sync stale symbols immediately."""
        while self._running:
            await asyncio.sleep(check_interval_s)
            stale = [s for s in self.symbols if self.is_symbol_stale(s, max_age_s=stale_threshold_s)]
            if stale:
                logger.info("bithumb_stale_resync", count=len(stale))
                await self.refresh_symbols(stale)

    def is_symbol_stale(self, symbol: str, max_age_s: float = 300.0) -> bool:
        """Check if a symbol's orderbook data is stale (no update in max_age_s seconds)."""
        last = self._last_update.get(symbol)
        if last is None:
            return True
        return (time.monotonic() - last) > max_age_s
