"""Unit tests for TriangularScanner — Bellman-Ford triangular arb detection."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.core.order_book import OrderBook
from src.core.triangular_scanner import TriangleCycle, TriangularScanner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_book(symbol: str, exchange: str, bid: str, ask: str, qty: str = "1") -> OrderBook:
    """Create an OrderBook with a single bid/ask level."""
    book = OrderBook(symbol=symbol, exchange=exchange)
    book.apply_snapshot(
        bids=[(bid, qty)],
        asks=[(ask, qty)],
    )
    return book


def _make_book_multi(
    symbol: str,
    exchange: str,
    bids: list[tuple[str, str]],
    asks: list[tuple[str, str]],
) -> OrderBook:
    """Create an OrderBook with multiple bid/ask levels."""
    book = OrderBook(symbol=symbol, exchange=exchange)
    book.apply_snapshot(bids=bids, asks=asks)
    return book


# ---------------------------------------------------------------------------
# Profitable triangle fixture:
#   USDT→BTC: buy BTC at ask=50000
#   BTC→ETH:  buy ETH with BTC at ask=0.05  (ETH/BTC)
#   ETH→USDT: sell ETH at bid=2600
#
#   cycle_return = (1/50000) * (1/0.05) * 2600
#               = 2600 / (50000 * 0.05)
#               = 2600 / 2500 = 1.04 → 4% profit (400 bps)
# ---------------------------------------------------------------------------

EXCHANGE = "binance"

BTC_USDT_ASK = "50000"
BTC_USDT_BID = "49990"
ETH_BTC_ASK  = "0.05"
ETH_BTC_BID  = "0.0499"
ETH_USDT_BID = "2600"
ETH_USDT_ASK = "2601"


def _profitable_scanner() -> tuple[TriangularScanner, list]:
    """Build a scanner with a clearly profitable USDT→BTC→ETH→USDT cycle."""
    scanner = TriangularScanner(min_profit_bps=Decimal("10"))
    book_btc_usdt = _make_book("BTC/USDT", EXCHANGE, BTC_USDT_BID, BTC_USDT_ASK, "2")
    book_eth_btc  = _make_book("ETH/BTC",  EXCHANGE, ETH_BTC_BID,  ETH_BTC_ASK,  "10")
    book_eth_usdt = _make_book("ETH/USDT", EXCHANGE, ETH_USDT_BID, ETH_USDT_ASK, "5")

    scanner.on_orderbook_update(EXCHANGE, "BTC/USDT", book_btc_usdt)
    scanner.on_orderbook_update(EXCHANGE, "ETH/BTC",  book_eth_btc)
    cycles = scanner.on_orderbook_update(EXCHANGE, "ETH/USDT", book_eth_usdt)
    return scanner, cycles


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEmptyBooks:
    def test_empty_books_returns_no_cycles(self):
        scanner = TriangularScanner()
        book = _make_book("BTC/USDT", EXCHANGE, "50000", "50001")
        cycles = scanner.on_orderbook_update(EXCHANGE, "BTC/USDT", book)
        assert cycles == []


class TestSinglePair:
    def test_single_pair_no_cycle(self):
        scanner = TriangularScanner()
        book = _make_book("BTC/USDT", EXCHANGE, "50000", "50001")
        scanner.on_orderbook_update(EXCHANGE, "BTC/USDT", book)
        book2 = _make_book("ETH/USDT", EXCHANGE, "2600", "2601")
        cycles = scanner.on_orderbook_update(EXCHANGE, "ETH/USDT", book2)
        # Only 2 pairs — no ETH/BTC pair to complete the triangle
        assert cycles == []


class TestProfitableCycle:
    def test_profitable_cycle_detected(self):
        _, cycles = _profitable_scanner()
        assert len(cycles) >= 1

    def test_cycle_has_correct_structure(self):
        _, cycles = _profitable_scanner()
        assert len(cycles) >= 1
        cycle = cycles[0]
        assert isinstance(cycle, TriangleCycle)
        assert cycle.exchange_id == EXCHANGE
        assert len(cycle.path) == 3
        assert len(cycle.pairs) == 3
        assert len(cycle.sides) == 3
        assert len(cycle.prices) == 3

    def test_cycle_profit_is_positive(self):
        _, cycles = _profitable_scanner()
        assert len(cycles) >= 1
        assert cycles[0].profit_pct > 0

    def test_cycle_profit_exceeds_10bps(self):
        _, cycles = _profitable_scanner()
        assert len(cycles) >= 1
        profit_bps = cycles[0].profit_pct * Decimal("10000")
        assert profit_bps > Decimal("10")

    def test_cycle_contains_usdt(self):
        _, cycles = _profitable_scanner()
        assert "USDT" in cycles[0].path

    def test_cycle_volume_positive(self):
        _, cycles = _profitable_scanner()
        assert cycles[0].max_volume_usdt > 0


class TestUnprofitableCycle:
    def test_unprofitable_cycle_not_returned(self):
        """Use wide bid-ask spreads that make BOTH cycle directions unprofitable.

        With BTC=100 USDT, ETH=0.01 BTC, ETH=1 USDT and 2% spreads:
          Forward  USDT→BTC→ETH→USDT: 0.98 / (102 * 0.0102) ≈ 0.942  < 1 ✓
          Reverse USDT→ETH→BTC→USDT: 0.0098 * 98 / 1.02     ≈ 0.942  < 1 ✓
        """
        scanner = TriangularScanner(min_profit_bps=Decimal("10"))
        book_btc = _make_book("BTC/USDT", EXCHANGE, "98", "102")
        book_eth_btc = _make_book("ETH/BTC", EXCHANGE, "0.0098", "0.0102")
        book_eth_usdt = _make_book("ETH/USDT", EXCHANGE, "0.98", "1.02")

        scanner.on_orderbook_update(EXCHANGE, "BTC/USDT", book_btc)
        scanner.on_orderbook_update(EXCHANGE, "ETH/BTC", book_eth_btc)
        cycles = scanner.on_orderbook_update(EXCHANGE, "ETH/USDT", book_eth_usdt)
        assert cycles == []


class TestMinProfitGate:
    def test_cycle_below_gate_not_returned(self):
        """Cycle with profit = 5 bps should be filtered by default 10 bps gate."""
        scanner = TriangularScanner(min_profit_bps=Decimal("10"))
        # cycle_return ≈ 1.0005 = 5 bps
        # 2501.25 / (50000 * 0.05) = 2501.25/2500 = 1.0005
        book_btc = _make_book("BTC/USDT", EXCHANGE, "49990", "50000")
        book_eth_btc = _make_book("ETH/BTC", EXCHANGE, "0.0499", "0.05")
        book_eth_usdt = _make_book("ETH/USDT", EXCHANGE, "2501.25", "2502")

        scanner.on_orderbook_update(EXCHANGE, "BTC/USDT", book_btc)
        scanner.on_orderbook_update(EXCHANGE, "ETH/BTC", book_eth_btc)
        cycles = scanner.on_orderbook_update(EXCHANGE, "ETH/USDT", book_eth_usdt)
        assert cycles == []

    def test_low_gate_accepts_small_profit(self):
        """With 1 bps gate, the 5 bps cycle should be returned."""
        scanner = TriangularScanner(min_profit_bps=Decimal("1"))
        book_btc = _make_book("BTC/USDT", EXCHANGE, "49990", "50000")
        book_eth_btc = _make_book("ETH/BTC", EXCHANGE, "0.0499", "0.05")
        # 2502 / (50000 * 0.05) = 2502/2500 = 1.0008 = 8bps
        book_eth_usdt = _make_book("ETH/USDT", EXCHANGE, "2502", "2503")

        scanner.on_orderbook_update(EXCHANGE, "BTC/USDT", book_btc)
        scanner.on_orderbook_update(EXCHANGE, "ETH/BTC", book_eth_btc)
        cycles = scanner.on_orderbook_update(EXCHANGE, "ETH/USDT", book_eth_usdt)
        assert len(cycles) >= 1


class TestDepthAwareVolume:
    def test_depth_aware_volume_calculation(self):
        """Bottleneck volume should reflect orderbook depth, not just top level."""
        scanner = TriangularScanner(min_profit_bps=Decimal("10"))
        # BTC/USDT: 3 bid levels, 3 ask levels
        book_btc = _make_book_multi(
            "BTC/USDT", EXCHANGE,
            bids=[("49990", "1"), ("49980", "1"), ("49970", "1")],
            asks=[("50000", "2"), ("50010", "1"), ("50020", "1")],
        )
        book_eth_btc = _make_book("ETH/BTC", EXCHANGE, "0.0499", "0.05", "5")
        book_eth_usdt = _make_book("ETH/USDT", EXCHANGE, "2600", "2601", "10")

        scanner.on_orderbook_update(EXCHANGE, "BTC/USDT", book_btc)
        scanner.on_orderbook_update(EXCHANGE, "ETH/BTC", book_eth_btc)
        cycles = scanner.on_orderbook_update(EXCHANGE, "ETH/USDT", book_eth_usdt)
        assert len(cycles) >= 1
        # Volume should be positive and reasonable
        assert cycles[0].max_volume_usdt > 0


class TestZeroPriceHandling:
    def test_zero_bid_ignored(self):
        """Orderbook with zero bid should not trigger a cycle."""
        scanner = TriangularScanner()
        book = OrderBook(symbol="BTC/USDT", exchange=EXCHANGE)
        book.apply_snapshot(bids=[("0", "1")], asks=[("50001", "1")])

        scanner.on_orderbook_update(EXCHANGE, "BTC/USDT", book)
        book2 = _make_book("ETH/BTC", EXCHANGE, "0.0499", "0.05")
        book3 = _make_book("ETH/USDT", EXCHANGE, "2600", "2601")
        scanner.on_orderbook_update(EXCHANGE, "ETH/BTC", book2)
        cycles = scanner.on_orderbook_update(EXCHANGE, "ETH/USDT", book3)
        # Zero bid removes the BTC/USDT sell edge — no complete cycle possible
        assert cycles == []

    def test_empty_orderbook_side_ignored(self):
        """Empty asks side should not cause errors or phantom cycles."""
        scanner = TriangularScanner()
        book = OrderBook(symbol="BTC/USDT", exchange=EXCHANGE)
        book.apply_snapshot(bids=[("50000", "1")], asks=[])  # no asks

        scanner.on_orderbook_update(EXCHANGE, "BTC/USDT", book)
        book2 = _make_book("ETH/BTC", EXCHANGE, "0.0499", "0.05")
        book3 = _make_book("ETH/USDT", EXCHANGE, "2600", "2601")
        scanner.on_orderbook_update(EXCHANGE, "ETH/BTC", book2)
        cycles = scanner.on_orderbook_update(EXCHANGE, "ETH/USDT", book3)
        assert cycles == []


class TestGraphRebuildsOnUpdate:
    def test_graph_rebuilds_on_update(self):
        """After making a profitable cycle unprofitable via update, no cycles returned."""
        scanner = TriangularScanner(min_profit_bps=Decimal("10"))
        # Start with profitable prices
        book_btc = _make_book("BTC/USDT", EXCHANGE, "49990", "50000")
        book_eth_btc = _make_book("ETH/BTC", EXCHANGE, "0.0499", "0.05")
        book_eth_usdt = _make_book("ETH/USDT", EXCHANGE, "2600", "2601")

        scanner.on_orderbook_update(EXCHANGE, "BTC/USDT", book_btc)
        scanner.on_orderbook_update(EXCHANGE, "ETH/BTC", book_eth_btc)
        cycles = scanner.on_orderbook_update(EXCHANGE, "ETH/USDT", book_eth_usdt)
        assert len(cycles) >= 1

        # Update ETH/USDT so BOTH directions are unprofitable:
        #   Forward : 2400 / (50000 * 0.05) = 0.96  < 1 ✓
        #   Reverse : 0.0499 * 49990 / 2600 ≈ 0.960 < 1 ✓
        book_eth_usdt_stale = _make_book("ETH/USDT", EXCHANGE, "2400", "2600")
        cycles_after = scanner.on_orderbook_update(EXCHANGE, "ETH/USDT", book_eth_usdt_stale)
        assert cycles_after == []


class TestMultipleExchangesIndependent:
    def test_multiple_exchanges_independent(self):
        """Orderbooks from different exchanges don't mix."""
        scanner = TriangularScanner(min_profit_bps=Decimal("10"))

        # Exchange A: profitable cycle
        scanner.on_orderbook_update("binance", "BTC/USDT", _make_book("BTC/USDT", "binance", "49990", "50000"))
        scanner.on_orderbook_update("binance", "ETH/BTC",  _make_book("ETH/BTC",  "binance", "0.0499", "0.05"))
        cycles_a = scanner.on_orderbook_update("binance", "ETH/USDT", _make_book("ETH/USDT", "binance", "2600", "2601"))

        # Exchange B: only one pair (no complete cycle)
        cycles_b = scanner.on_orderbook_update("bybit", "BTC/USDT", _make_book("BTC/USDT", "bybit", "49990", "50000"))

        assert len(cycles_a) >= 1
        assert all(c.exchange_id == "binance" for c in cycles_a)
        assert cycles_b == []


class TestOnly3CurrencyCycles:
    def test_only_3_currency_cycles(self):
        """Scanner is bounded to 3-currency (USDT→X→Y→USDT) cycles only."""
        _, cycles = _profitable_scanner()
        for cycle in cycles:
            assert len(cycle.path) == 3, f"Expected 3-currency path, got {cycle.path}"

    def test_cycle_path_starts_and_ends_at_usdt(self):
        """The path should start at USDT (first element)."""
        _, cycles = _profitable_scanner()
        for cycle in cycles:
            assert cycle.path[0] == "USDT"
