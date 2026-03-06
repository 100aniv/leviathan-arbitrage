"""Redis-backed orderbook state manager.

Stores L2 orderbook state in Redis sorted sets (price levels) with hash
metadata. Supports snapshot + delta reconstruction, Binance CRC32 checksum
validation, stale-data detection, and the Price Hub pattern.
"""
from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Optional

from .client import RedisClient
from ...core.order_book import OrderBook

logger = logging.getLogger(__name__)

DEFAULT_STALE_THRESHOLD_SECONDS = 5.0


class OrderbookManager:
    """
    Stores and retrieves orderbook state in Redis.

    Redis key layout for exchange="binance", symbol="BTC/USDT":
        leviathan:orderbook:binance:BTC-USDT:bids       → sorted set (score=price)
        leviathan:orderbook:binance:BTC-USDT:bids:qty   → hash (price → quantity)
        leviathan:orderbook:binance:BTC-USDT:asks       → sorted set (score=price)
        leviathan:orderbook:binance:BTC-USDT:asks:qty   → hash (price → quantity)
        leviathan:orderbook:binance:BTC-USDT:meta       → hash (exchange, symbol, last_update, is_stale)

    Price Hub (global best bid/ask):
        leviathan:pricehub:BTC-USDT  → hash ({exchange}:bid, {exchange}:ask, {exchange}:ts)
    """

    def __init__(
        self,
        client: RedisClient,
        stale_threshold_seconds: float = DEFAULT_STALE_THRESHOLD_SECONDS,
    ) -> None:
        self._client = client
        self._stale_threshold = stale_threshold_seconds

    # ── Key helpers ────────────────────────────────────────────────────────────

    def _sym(self, symbol: str) -> str:
        return symbol.replace("/", "-")

    def _base_key(self, exchange: str, symbol: str) -> str:
        return f"leviathan:orderbook:{exchange}:{self._sym(symbol)}"

    def _bids_key(self, exchange: str, symbol: str) -> str:
        return f"{self._base_key(exchange, symbol)}:bids"

    def _asks_key(self, exchange: str, symbol: str) -> str:
        return f"{self._base_key(exchange, symbol)}:asks"

    def _meta_key(self, exchange: str, symbol: str) -> str:
        return f"{self._base_key(exchange, symbol)}:meta"

    def _price_hub_key(self, symbol: str) -> str:
        return f"leviathan:pricehub:{self._sym(symbol)}"

    # ── Snapshot ───────────────────────────────────────────────────────────────

    async def store_snapshot(
        self,
        exchange: str,
        symbol: str,
        bids: list[tuple[str, str]],
        asks: list[tuple[str, str]],
    ) -> None:
        """
        Store full orderbook snapshot, replacing any existing state.

        Bids/asks stored as sorted sets keyed by price (float score) for
        range queries. Quantities stored in companion hash for exact Decimal
        retrieval.
        """
        bids_key = self._bids_key(exchange, symbol)
        asks_key = self._asks_key(exchange, symbol)

        # Clear existing state
        await self._client.delete(
            bids_key,
            f"{bids_key}:qty",
            asks_key,
            f"{asks_key}:qty",
        )

        if bids:
            bid_scores = {price: float(price) for price, _ in bids}
            bid_qtys = {price: qty for price, qty in bids}
            await self._client.zadd(bids_key, bid_scores)
            await self._client.hset(f"{bids_key}:qty", mapping=bid_qtys)

        if asks:
            ask_scores = {price: float(price) for price, _ in asks}
            ask_qtys = {price: qty for price, qty in asks}
            await self._client.zadd(asks_key, ask_scores)
            await self._client.hset(f"{asks_key}:qty", mapping=ask_qtys)

        await self._client.hset(self._meta_key(exchange, symbol), mapping={
            "exchange": exchange,
            "symbol": symbol,
            "last_update": str(time.time()),
            "is_stale": "0",
        })

    # ── Delta ──────────────────────────────────────────────────────────────────

    async def apply_delta(
        self,
        exchange: str,
        symbol: str,
        bid_updates: list[tuple[str, str]],
        ask_updates: list[tuple[str, str]],
    ) -> None:
        """
        Apply incremental orderbook updates.
        quantity == "0" removes the price level.
        """
        bids_key = self._bids_key(exchange, symbol)
        asks_key = self._asks_key(exchange, symbol)
        r = self._client.redis

        for price, qty in bid_updates:
            if Decimal(qty) == Decimal("0"):
                await r.zrem(bids_key, price)
                await r.hdel(f"{bids_key}:qty", price)
            else:
                await self._client.zadd(bids_key, {price: float(price)})
                await r.hset(f"{bids_key}:qty", price, qty)

        for price, qty in ask_updates:
            if Decimal(qty) == Decimal("0"):
                await r.zrem(asks_key, price)
                await r.hdel(f"{asks_key}:qty", price)
            else:
                await self._client.zadd(asks_key, {price: float(price)})
                await r.hset(f"{asks_key}:qty", price, qty)

        await r.hset(self._meta_key(exchange, symbol), "last_update", str(time.time()))

    # ── Retrieval ──────────────────────────────────────────────────────────────

    async def get_orderbook(self, exchange: str, symbol: str) -> Optional[OrderBook]:
        """
        Reconstruct an OrderBook instance from Redis state.
        Returns None if no data exists for this exchange/symbol.
        """
        bids_key = self._bids_key(exchange, symbol)
        asks_key = self._asks_key(exchange, symbol)
        r = self._client.redis

        bid_prices = await r.zrangebyscore(bids_key, "-inf", "+inf")
        ask_prices = await r.zrangebyscore(asks_key, "-inf", "+inf")

        if not bid_prices and not ask_prices:
            return None

        book = OrderBook(symbol=symbol, exchange=exchange)

        bids: list[tuple[str, str]] = []
        for price_b in bid_prices:
            price = price_b.decode() if isinstance(price_b, bytes) else price_b
            qty_b = await r.hget(f"{bids_key}:qty", price)
            if qty_b:
                qty = qty_b.decode() if isinstance(qty_b, bytes) else qty_b
                bids.append((price, qty))

        asks: list[tuple[str, str]] = []
        for price_b in ask_prices:
            price = price_b.decode() if isinstance(price_b, bytes) else price_b
            qty_b = await r.hget(f"{asks_key}:qty", price)
            if qty_b:
                qty = qty_b.decode() if isinstance(qty_b, bytes) else qty_b
                asks.append((price, qty))

        book.apply_snapshot(bids, asks)
        return book

    # ── Stale detection ────────────────────────────────────────────────────────

    async def is_stale(self, exchange: str, symbol: str) -> bool:
        """
        True if the orderbook hasn't been updated within stale_threshold_seconds,
        or if no data exists at all.
        """
        raw = await self._client.redis.hget(self._meta_key(exchange, symbol), "last_update")
        if raw is None:
            return True
        last_update = float(raw.decode() if isinstance(raw, bytes) else raw)
        return (time.time() - last_update) > self._stale_threshold

    async def mark_stale(self, exchange: str, symbol: str) -> None:
        """Explicitly flag orderbook as stale."""
        await self._client.redis.hset(self._meta_key(exchange, symbol), "is_stale", "1")

    # ── Price Hub ──────────────────────────────────────────────────────────────

    async def update_price_hub(
        self,
        exchange: str,
        symbol: str,
        best_bid: Decimal,
        best_ask: Decimal,
    ) -> None:
        """
        Update global best bid/ask for this exchange in the Price Hub hash.
        Key: leviathan:pricehub:{symbol}
        """
        await self._client.hset(self._price_hub_key(symbol), mapping={
            f"{exchange}:bid": str(best_bid),
            f"{exchange}:ask": str(best_ask),
            f"{exchange}:ts": str(time.time()),
        })

    async def get_global_best(self, symbol: str) -> dict[str, dict[str, Decimal]]:
        """
        Return best bid/ask for all exchanges tracking this symbol.

        Returns: {"binance": {"bid": Decimal(...), "ask": Decimal(...)}, ...}
        """
        raw = await self._client.hgetall(self._price_hub_key(symbol))
        result: dict[str, dict[str, Decimal]] = {}
        for key_b, val_b in raw.items():
            key = key_b.decode() if isinstance(key_b, bytes) else key_b
            val = val_b.decode() if isinstance(val_b, bytes) else val_b
            if ":" not in key:
                continue
            exchange, field = key.rsplit(":", 1)
            if field in ("bid", "ask"):
                result.setdefault(exchange, {})[field] = Decimal(val)
        return result

    # ── Checksum ───────────────────────────────────────────────────────────────

    async def validate_checksum(
        self, exchange: str, symbol: str, checksum: int
    ) -> bool:
        """
        Validate orderbook CRC32 checksum by reconstructing the book from Redis.
        Returns False if no orderbook data exists.
        """
        book = await self.get_orderbook(exchange, symbol)
        if book is None:
            return False
        return book.validate_checksum(checksum)
