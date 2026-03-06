"""OrderBook domain logic — pure Python, no Redis dependency.

Uses Decimal for all price/quantity arithmetic to avoid floating-point errors.
Implements Binance CRC32 checksum validation.
"""
from __future__ import annotations

import zlib
from decimal import Decimal
from typing import Optional


class OrderBook:
    """
    L2 orderbook with sorted price levels.

    Bids: highest price = best (sorted descending).
    Asks: lowest price = best (sorted ascending).
    """

    def __init__(self, symbol: str, exchange: str) -> None:
        self.symbol = symbol
        self.exchange = exchange
        self.bids: dict[Decimal, Decimal] = {}  # price -> quantity
        self.asks: dict[Decimal, Decimal] = {}  # price -> quantity

    def apply_snapshot(
        self,
        bids: list[tuple[str, str]],
        asks: list[tuple[str, str]],
    ) -> None:
        """Replace entire orderbook with snapshot data. Zero-qty levels are ignored."""
        self.bids = {}
        self.asks = {}
        for price, qty in bids:
            p, q = Decimal(price), Decimal(qty)
            if q > 0:
                self.bids[p] = q
        for price, qty in asks:
            p, q = Decimal(price), Decimal(qty)
            if q > 0:
                self.asks[p] = q

    def apply_delta(
        self,
        bid_updates: list[tuple[str, str]],
        ask_updates: list[tuple[str, str]],
    ) -> None:
        """Apply incremental updates. qty == 0 removes the price level."""
        for price, qty in bid_updates:
            p, q = Decimal(price), Decimal(qty)
            if q == 0:
                self.bids.pop(p, None)
            else:
                self.bids[p] = q
        for price, qty in ask_updates:
            p, q = Decimal(price), Decimal(qty)
            if q == 0:
                self.asks.pop(p, None)
            else:
                self.asks[p] = q

    def best_bid(self) -> Optional[Decimal]:
        """Highest bid price, or None if empty."""
        return max(self.bids.keys()) if self.bids else None

    def best_ask(self) -> Optional[Decimal]:
        """Lowest ask price, or None if empty."""
        return min(self.asks.keys()) if self.asks else None

    def spread(self) -> Optional[Decimal]:
        """Absolute bid-ask spread, or None if either side is empty."""
        bid = self.best_bid()
        ask = self.best_ask()
        if bid is None or ask is None:
            return None
        return ask - bid

    def spread_pct(self) -> Optional[Decimal]:
        """Relative spread as fraction of best bid, or None if empty."""
        bid = self.best_bid()
        ask = self.best_ask()
        if bid is None or ask is None or bid == 0:
            return None
        return (ask - bid) / bid

    def depth_weighted_mid_price(self, depth: int = 5) -> Decimal:
        """
        Depth-weighted mid price across top N levels on each side.

        Formula: average of (VWAP of top-N bids, VWAP of top-N asks).
        VWAP = sum(price_i * qty_i) / sum(qty_i).

        Raises ValueError if either side is empty.
        """
        sorted_bids = sorted(self.bids.keys(), reverse=True)[:depth]
        sorted_asks = sorted(self.asks.keys())[:depth]
        if not sorted_bids or not sorted_asks:
            raise ValueError("OrderBook is empty — cannot compute mid price")

        bid_qty_total = sum(self.bids[p] for p in sorted_bids)
        ask_qty_total = sum(self.asks[p] for p in sorted_asks)
        if bid_qty_total == 0 or ask_qty_total == 0:
            raise ValueError("Zero total quantity in orderbook levels")

        bid_vwap = sum(p * self.bids[p] for p in sorted_bids) / bid_qty_total
        ask_vwap = sum(p * self.asks[p] for p in sorted_asks) / ask_qty_total
        return (bid_vwap + ask_vwap) / 2

    def volume_at_price(self, price: Decimal, side: str) -> Decimal:
        """Return quantity at a specific price level. Returns Decimal('0') if absent."""
        if side == "bid":
            return self.bids.get(price, Decimal("0"))
        if side == "ask":
            return self.asks.get(price, Decimal("0"))
        raise ValueError(f"Invalid side '{side}': must be 'bid' or 'ask'")

    def compute_checksum(self) -> int:
        """
        Compute Binance-style CRC32 checksum.

        Format: top-5 bids (descending) and top-5 asks (ascending),
        each formatted as "price@qty", joined by "|".
        Returns unsigned 32-bit integer.
        """
        sorted_bids = sorted(self.bids.keys(), reverse=True)[:5]
        sorted_asks = sorted(self.asks.keys())[:5]
        parts: list[str] = []
        for p in sorted_bids:
            parts.append(f"{p}@{self.bids[p]}")
        for p in sorted_asks:
            parts.append(f"{p}@{self.asks[p]}")
        payload = "|".join(parts)
        return zlib.crc32(payload.encode()) & 0xFFFFFFFF

    def validate_checksum(self, expected: int) -> bool:
        """Validate orderbook integrity against expected CRC32 checksum."""
        return self.compute_checksum() == expected
