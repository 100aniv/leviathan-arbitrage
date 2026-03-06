"""Tests for DepthAnalyzer — VWAP and liquidity analysis."""
import pytest
from decimal import Decimal

from src.core.order_book import OrderBook
from src.core.depth_analyzer import DepthAnalyzer, VWAPResult


@pytest.fixture
def sample_book():
    book = OrderBook(symbol="BTC/USDT", exchange="binance")
    book.apply_snapshot(
        bids=[
            ("50000.00", "2.0"),
            ("49999.00", "3.0"),
            ("49998.00", "5.0"),
        ],
        asks=[
            ("50001.00", "1.5"),
            ("50002.00", "2.5"),
            ("50003.00", "4.0"),
        ],
    )
    return book


class TestVWAPBuy:
    def test_vwap_buy_single_level(self, sample_book):
        result = DepthAnalyzer.vwap_for_buy(sample_book, Decimal("1.0"))
        assert result.vwap == Decimal("50001.00")
        assert result.filled_qty == Decimal("1.0")
        assert result.levels_consumed == 1

    def test_vwap_buy_multiple_levels(self, sample_book):
        # Size = 2.0: 1.5 @ 50001, 0.5 @ 50002
        result = DepthAnalyzer.vwap_for_buy(sample_book, Decimal("2.0"))
        expected_vwap = (
            Decimal("1.5") * Decimal("50001") + Decimal("0.5") * Decimal("50002")
        ) / Decimal("2.0")
        assert result.vwap == expected_vwap
        assert result.levels_consumed == 2

    def test_vwap_buy_walks_all_levels(self, sample_book):
        # Total ask liquidity = 1.5 + 2.5 + 4.0 = 8.0
        result = DepthAnalyzer.vwap_for_buy(sample_book, Decimal("8.0"))
        assert result.filled_qty == Decimal("8.0")
        assert result.levels_consumed == 3

    def test_vwap_buy_insufficient_liquidity_raises(self, sample_book):
        with pytest.raises(ValueError, match="liquidity"):
            DepthAnalyzer.vwap_for_buy(sample_book, Decimal("100.0"))

    def test_vwap_buy_empty_book_raises(self):
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        with pytest.raises(ValueError):
            DepthAnalyzer.vwap_for_buy(book, Decimal("1.0"))

    def test_vwap_buy_exact_level_boundary(self, sample_book):
        # Exactly 1.5 fills first level only
        result = DepthAnalyzer.vwap_for_buy(sample_book, Decimal("1.5"))
        assert result.vwap == Decimal("50001.00")
        assert result.filled_qty == Decimal("1.5")
        assert result.levels_consumed == 1


class TestVWAPSell:
    def test_vwap_sell_single_level(self, sample_book):
        result = DepthAnalyzer.vwap_for_sell(sample_book, Decimal("1.0"))
        assert result.vwap == Decimal("50000.00")
        assert result.filled_qty == Decimal("1.0")

    def test_vwap_sell_multiple_levels(self, sample_book):
        # Size = 3.0: 2.0 @ 50000, 1.0 @ 49999
        result = DepthAnalyzer.vwap_for_sell(sample_book, Decimal("3.0"))
        expected_vwap = (
            Decimal("2.0") * Decimal("50000") + Decimal("1.0") * Decimal("49999")
        ) / Decimal("3.0")
        assert result.vwap == expected_vwap
        assert result.levels_consumed == 2

    def test_vwap_sell_insufficient_liquidity_raises(self, sample_book):
        with pytest.raises(ValueError, match="liquidity"):
            DepthAnalyzer.vwap_for_sell(sample_book, Decimal("100.0"))

    def test_vwap_sell_empty_book_raises(self):
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        with pytest.raises(ValueError):
            DepthAnalyzer.vwap_for_sell(book, Decimal("1.0"))

    def test_vwap_sell_walks_all_levels(self, sample_book):
        # Total bid liquidity = 2.0 + 3.0 + 5.0 = 10.0
        result = DepthAnalyzer.vwap_for_sell(sample_book, Decimal("10.0"))
        assert result.filled_qty == Decimal("10.0")
        assert result.levels_consumed == 3


class TestLiquidityAtDepth:
    def test_liquidity_bid_within_1pct(self, sample_book):
        # best bid = 50000, 1% → threshold = 49500
        # All bids (50000, 49999, 49998) are within 1%
        qty = DepthAnalyzer.liquidity_at_pct_depth(sample_book, Decimal("1"), "bid")
        assert qty == Decimal("10.0")  # 2+3+5

    def test_liquidity_ask_within_1pct(self, sample_book):
        # best ask = 50001, 1% → threshold = 50501
        # All asks within 1%
        qty = DepthAnalyzer.liquidity_at_pct_depth(sample_book, Decimal("1"), "ask")
        assert qty == Decimal("8.0")  # 1.5+2.5+4.0

    def test_liquidity_tight_depth_excludes_far_levels(self):
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        book.apply_snapshot(
            bids=[("100.00", "1.0"), ("90.00", "5.0")],  # 90 is >1% away
            asks=[("101.00", "1.0"), ("120.00", "5.0")],  # 120 is >1% away
        )
        bid_qty = DepthAnalyzer.liquidity_at_pct_depth(book, Decimal("1"), "bid")
        ask_qty = DepthAnalyzer.liquidity_at_pct_depth(book, Decimal("1"), "ask")
        assert bid_qty == Decimal("1.0")
        assert ask_qty == Decimal("1.0")

    def test_liquidity_invalid_side_raises(self, sample_book):
        with pytest.raises(ValueError):
            DepthAnalyzer.liquidity_at_pct_depth(sample_book, Decimal("1"), "invalid")

    def test_liquidity_empty_book_returns_zero(self):
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        assert DepthAnalyzer.liquidity_at_pct_depth(book, Decimal("1"), "bid") == Decimal("0")
        assert DepthAnalyzer.liquidity_at_pct_depth(book, Decimal("1"), "ask") == Decimal("0")
