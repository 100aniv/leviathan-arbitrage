"""Tests for StaleOrderbookDetector — cross-exchange validation and blacklist."""
from __future__ import annotations

import time
from decimal import Decimal

import pytest

from src.core.order_book import OrderBook
from src.core.stale_detector import StaleOrderbookDetector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_book(exchange: str, bid: float, ask: float, symbol: str = "BTC/USDT") -> OrderBook:
    book = OrderBook(symbol=symbol, exchange=exchange)
    book.apply_snapshot([(str(bid), "1.0")], [(str(ask), "1.0")])
    return book


def _all_books(*books: OrderBook) -> dict[str, dict[str, OrderBook]]:
    """Returns symbol → exchange → OrderBook (matches ShadowMode._books layout)."""
    result: dict[str, dict[str, OrderBook]] = {}
    for b in books:
        result.setdefault(b.symbol, {})[b.exchange] = b
    return result


# ---------------------------------------------------------------------------
# Cross-exchange validation
# ---------------------------------------------------------------------------


class TestCrossExchangeValidation:
    def test_cross_exchange_deviation_detected(self):
        """mid-price 5x off non-Korean median returns False (stale)."""
        symbol = "BTC/USDT"
        stale = _make_book("bithumb", 49_999, 50_001, symbol)
        # Non-Korean exchanges at ~10_000
        books = _all_books(
            stale,
            _make_book("binance", 9_999, 10_001, symbol),
            _make_book("okx", 9_998, 10_002, symbol),
        )
        detector = StaleOrderbookDetector(deviation_pct=0.10, min_comparison_exchanges=2)
        assert detector.check_cross_exchange("bithumb", symbol, stale, books) is False

    def test_cross_exchange_deviation_normal(self):
        """0.5% deviation from median returns True (passes validation)."""
        symbol = "BTC/USDT"
        book = _make_book("binance", 49_999, 50_001, symbol)
        books = _all_books(
            book,
            _make_book("okx", 50_100, 50_300, symbol),
            _make_book("bybit", 50_050, 50_200, symbol),
        )
        detector = StaleOrderbookDetector(deviation_pct=0.10, min_comparison_exchanges=2)
        assert detector.check_cross_exchange("binance", symbol, book, books) is True

    def test_korean_exchange_uses_non_korean_median(self):
        """When validating Korean book, median computed from non-Korean exchanges only."""
        symbol = "BTC/USDT"
        bithumb_book = _make_book("bithumb", 49_999, 50_001, symbol)
        books = _all_books(
            bithumb_book,
            # Korean (same price as bithumb — should be excluded from median)
            _make_book("upbit", 49_998, 50_002, symbol),
            _make_book("coinone", 49_997, 50_003, symbol),
            # Non-Korean at ~10_000
            _make_book("binance", 9_999, 10_001, symbol),
            _make_book("okx", 9_998, 10_002, symbol),
        )
        detector = StaleOrderbookDetector(deviation_pct=0.10, min_comparison_exchanges=2)
        # Non-Korean median ~10_000; bithumb mid ~50_000 → 400% → stale
        assert detector.check_cross_exchange("bithumb", symbol, bithumb_book, books) is False

    def test_insufficient_exchanges_skips_validation(self):
        """Fewer than min_comparison_exchanges returns True (skip, not reject)."""
        symbol = "BTC/USDT"
        book = _make_book("binance", 49_999, 50_001, symbol)
        # Only 1 comparison exchange; need min 2 → skip
        books = _all_books(book, _make_book("okx", 50_100, 50_200, symbol))
        detector = StaleOrderbookDetector(deviation_pct=0.10, min_comparison_exchanges=2)
        assert detector.check_cross_exchange("binance", symbol, book, books) is True


# ---------------------------------------------------------------------------
# Blacklist management
# ---------------------------------------------------------------------------


class TestBlacklist:
    def test_blacklist_add_and_check(self):
        """Blacklisted (exchange, symbol) returns True from is_blacklisted."""
        detector = StaleOrderbookDetector()
        detector.add_blacklist("bithumb", "BTC/USDT")
        assert detector.is_blacklisted("bithumb", "BTC/USDT") is True

    def test_blacklist_expiry(self):
        """Blacklist entry expires after TTL and is_blacklisted returns False."""
        detector = StaleOrderbookDetector(blacklist_ttl_s=0.02)  # 20ms TTL
        detector.add_blacklist("bithumb", "ETH/USDT")
        assert detector.is_blacklisted("bithumb", "ETH/USDT") is True
        time.sleep(0.05)
        assert detector.is_blacklisted("bithumb", "ETH/USDT") is False

    def test_blacklist_env_override(self):
        """Custom TTL via constructor parameter is respected."""
        detector = StaleOrderbookDetector(blacklist_ttl_s=600.0)
        detector.add_blacklist("upbit", "BTC/USDT")
        # Should still be blacklisted (600s TTL far in the future)
        assert detector.is_blacklisted("upbit", "BTC/USDT") is True

    def test_cleanup_expired(self):
        """cleanup_expired removes all expired entries and count returns 0."""
        detector = StaleOrderbookDetector(blacklist_ttl_s=0.02)
        detector.add_blacklist("bithumb", "XRP/USDT")
        detector.add_blacklist("bithumb", "ETH/USDT")
        time.sleep(0.05)
        detector.cleanup_expired()
        assert detector.blacklist_count() == 0
