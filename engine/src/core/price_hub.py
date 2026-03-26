"""PriceHub — global best bid/ask aggregator across all exchanges per symbol."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from src.core.order_book import OrderBook


@dataclass
class BestPrice:
    price: Decimal
    exchange: str
    qty: Decimal


class PriceHub:
    """
    Maintains the global best bid/ask for each symbol across all registered exchanges.

    Call update() whenever an OrderBook snapshot or delta is applied.
    best_bid() returns the highest bid price across all exchanges (with attribution).
    best_ask() returns the lowest ask price across all exchanges (with attribution).

    Uses per-symbol index for O(E) lookup where E = exchanges per symbol (typically 2-5),
    instead of O(N) where N = total books (500+). Critical for 72H Shadow stability.
    """

    def __init__(self) -> None:
        # (symbol, exchange) -> OrderBook
        self._books: dict[tuple[str, str], OrderBook] = {}
        # symbol -> set of exchanges (per-symbol index for O(E) lookup)
        self._symbol_exchanges: dict[str, set[str]] = defaultdict(set)

    def update(self, book: OrderBook) -> None:
        """Register or replace the orderbook for (symbol, exchange)."""
        self._books[(book.symbol, book.exchange)] = book
        self._symbol_exchanges[book.symbol].add(book.exchange)

    def best_bid(self, symbol: str) -> Optional[BestPrice]:
        """Highest bid price across all exchanges for symbol, with source attribution."""
        best: Optional[BestPrice] = None
        for exch in self._symbol_exchanges.get(symbol, ()):
            book = self._books.get((symbol, exch))
            if book is None:
                continue
            bid_price = book.best_bid()
            if bid_price is None:
                continue
            if best is None or bid_price > best.price:
                best = BestPrice(
                    price=bid_price,
                    exchange=exch,
                    qty=book.bids[bid_price],
                )
        return best

    def best_ask(self, symbol: str) -> Optional[BestPrice]:
        """Lowest ask price across all exchanges for symbol, with source attribution."""
        best: Optional[BestPrice] = None
        for exch in self._symbol_exchanges.get(symbol, ()):
            book = self._books.get((symbol, exch))
            if book is None:
                continue
            ask_price = book.best_ask()
            if ask_price is None:
                continue
            if best is None or ask_price < best.price:
                best = BestPrice(
                    price=ask_price,
                    exchange=exch,
                    qty=book.asks[ask_price],
                )
        return best

    def exchanges_for(self, symbol: str) -> list[str]:
        """Return all exchanges that have an orderbook registered for symbol."""
        return list(self._symbol_exchanges.get(symbol, set()))
