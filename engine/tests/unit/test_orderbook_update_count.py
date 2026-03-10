"""Tests for OrderBook.update_count — incremental update tracking.

update_count semantics (Critic-amended):
  - apply_snapshot() RESETS update_count to 0 (re-anchor from ground truth)
  - apply_delta()   INCREMENTS update_count by 1
  - min_delta_update_count gate uses this to reject delta exchanges
    that have not yet accumulated sufficient depth (default: 3 updates).
"""
from __future__ import annotations

from src.core.order_book import OrderBook


class TestUpdateCountOnSnapshot:
    def test_update_count_zero_on_snapshot(self):
        """apply_snapshot() resets update_count to 0 (fresh re-anchor)."""
        book = OrderBook(symbol="BTC/USDT", exchange="bithumb")
        book.apply_snapshot([("30000", "1.0")], [("30001", "1.0")])
        assert book.update_count == 0

    def test_snapshot_resets_count(self):
        """Existing count=5 becomes 0 after apply_snapshot."""
        book = OrderBook(symbol="BTC/USDT", exchange="bithumb")
        # Accumulate 5 delta updates
        book.apply_snapshot([("30000", "1.0")], [("30001", "1.0")])
        for _ in range(5):
            book.apply_delta([("30000", "1.1")], [])
        assert book.update_count == 5

        # Snapshot re-anchors → count resets
        book.apply_snapshot([("31000", "2.0")], [("31001", "2.0")])
        assert book.update_count == 0


class TestUpdateCountOnDelta:
    def test_update_count_increments_on_delta(self):
        """apply_delta() increments update_count by 1."""
        book = OrderBook(symbol="ETH/USDT", exchange="bithumb")
        book.apply_snapshot([("2000", "1.0")], [("2001", "1.0")])
        assert book.update_count == 0

        book.apply_delta([("2000", "1.5")], [])
        assert book.update_count == 1

    def test_update_count_accumulates(self):
        """snapshot(→0) + 3 deltas = update_count of 3."""
        book = OrderBook(symbol="XRP/USDT", exchange="bithumb")
        book.apply_snapshot([("0.50", "100.0")], [("0.51", "100.0")])
        assert book.update_count == 0

        book.apply_delta([("0.50", "110.0")], [])
        book.apply_delta([("0.50", "120.0")], [])
        book.apply_delta([("0.50", "130.0")], [])
        assert book.update_count == 3
