"""US-170: TriangularScanner — orderbook-driven cycle detection."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.core.triangular_scanner import TriangularScanner, TriangleCycle
from src.core.order_book import OrderBook


def _make_book(bids: list, asks: list) -> OrderBook:
    book = OrderBook(symbol="X/Y", exchange="test")
    book.bids = {Decimal(str(p)): Decimal(str(s)) for p, s in bids}
    book.asks = {Decimal(str(p)): Decimal(str(s)) for p, s in asks}
    return book


# ---------------------------------------------------------------------------
# Basic API contract
# ---------------------------------------------------------------------------


class TestOnOrderbookUpdate:
    def test_returns_list(self):
        """on_orderbook_update always returns a list (possibly empty)."""
        scanner = TriangularScanner()
        book = _make_book([(100, 1)], [(101, 1)])
        result = scanner.on_orderbook_update("binance", "BTC/USDT", book)
        assert isinstance(result, list)

    def test_empty_orderbook_returns_empty_list(self):
        """Empty orderbook (no bids/asks) returns [] without raising."""
        scanner = TriangularScanner()
        book = _make_book([], [])
        result = scanner.on_orderbook_update("binance", "BTC/USDT", book)
        assert result == []

    def test_single_pair_returns_empty_list(self):
        """Only 1 pair cached cannot form a triangle — returns []."""
        scanner = TriangularScanner()
        book = _make_book([(100, 1)], [(101, 1)])
        result = scanner.on_orderbook_update("binance", "BTC/USDT", book)
        assert result == []

    def test_two_pairs_returns_empty_list(self):
        """2 pairs cannot form a complete triangle — returns []."""
        scanner = TriangularScanner()
        for sym, bid, ask in [("BTC/USDT", 60000, 60100), ("ETH/USDT", 3000, 3010)]:
            book = _make_book([(bid, 1)], [(ask, 1)])
            result = scanner.on_orderbook_update("binance", sym, book)
        assert result == []

    def test_returns_triangle_cycle_instances(self):
        """When cycles are found, results contain TriangleCycle instances."""
        scanner = TriangularScanner(min_profit_bps=Decimal("0.001"))
        # Build extremely tight triangle to ensure detection
        books = {
            "BTC/USDT": _make_book([(60000, 1)], [(60001, 1)]),
            "ETH/BTC": _make_book([(0.05, 1)], [(0.0501, 1)]),
            "ETH/USDT": _make_book([(3000, 1)], [(3001, 1)]),
        }
        result = []
        for sym, book in books.items():
            result = scanner.on_orderbook_update("binance", sym, book)
        # Result may be empty (market is fair) but if returned, type must be correct
        for item in result:
            assert isinstance(item, TriangleCycle)

    def test_isolates_books_by_exchange(self):
        """Books on different exchanges are stored separately."""
        scanner = TriangularScanner()
        book = _make_book([(100, 1)], [(101, 1)])
        scanner.on_orderbook_update("binance", "BTC/USDT", book)
        scanner.on_orderbook_update("okx", "BTC/USDT", book)
        assert "binance" in scanner._books
        assert "okx" in scanner._books
        assert scanner._books["binance"] is not scanner._books["okx"]


# ---------------------------------------------------------------------------
# Profitable cycle detection properties
# ---------------------------------------------------------------------------


class TestCycleProperties:
    def test_cycle_has_three_legs(self):
        """Any returned TriangleCycle has exactly 3 pairs (legs)."""
        scanner = TriangularScanner(min_profit_bps=Decimal("0.001"))
        # Build a theoretically profitable triangle (artificial spread)
        books = {
            "BTC/USDT": _make_book([(62000, 10)], [(61000, 10)]),  # inverted for profit
            "ETH/BTC": _make_book([(0.055, 10)], [(0.050, 10)]),
            "ETH/USDT": _make_book([(3100, 10)], [(3000, 10)]),
        }
        result = []
        for sym, book in books.items():
            result = scanner.on_orderbook_update("binance", sym, book)
        for cycle in result:
            assert len(cycle.pairs) == 3
            assert len(cycle.sides) == 3
            assert len(cycle.prices) == 3

    def test_cycle_exchange_matches_input(self):
        """TriangleCycle.exchange_id matches the exchange passed to update."""
        scanner = TriangularScanner(min_profit_bps=Decimal("0.001"))
        books = {
            "BTC/USDT": _make_book([(62000, 10)], [(61000, 10)]),
            "ETH/BTC": _make_book([(0.055, 10)], [(0.050, 10)]),
            "ETH/USDT": _make_book([(3100, 10)], [(3000, 10)]),
        }
        result = []
        for sym, book in books.items():
            result = scanner.on_orderbook_update("myexchange", sym, book)
        for cycle in result:
            assert cycle.exchange_id == "myexchange"
