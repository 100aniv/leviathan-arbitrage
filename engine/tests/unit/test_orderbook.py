"""Tests for OrderBook domain logic — pure Python, no mocking needed."""
import pytest
from decimal import Decimal
from src.core.order_book import OrderBook


class TestOrderBookSnapshot:
    def test_apply_snapshot_sets_bids_asks(self):
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        bids = [("30000.00", "1.5"), ("29999.00", "2.0")]
        asks = [("30001.00", "1.0"), ("30002.00", "3.0")]
        book.apply_snapshot(bids, asks)
        assert book.best_bid() == Decimal("30000.00")
        assert book.best_ask() == Decimal("30001.00")

    def test_snapshot_clears_previous_state(self):
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        book.apply_snapshot([("29000.00", "1.0")], [("29001.00", "1.0")])
        book.apply_snapshot([("30000.00", "1.5")], [("30001.00", "1.0")])
        assert len(book.bids) == 1
        assert book.best_bid() == Decimal("30000.00")

    def test_snapshot_ignores_zero_qty_levels(self):
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        book.apply_snapshot([("30000.00", "0"), ("29999.00", "1.0")], [("30001.00", "1.0")])
        assert Decimal("30000.00") not in book.bids
        assert len(book.bids) == 1


class TestOrderBookDelta:
    def test_apply_delta_add_new_bid_level(self):
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        book.apply_snapshot([("30000.00", "1.0")], [("30001.00", "1.0")])
        book.apply_delta([("30000.50", "0.5")], [])
        assert Decimal("30000.50") in book.bids

    def test_apply_delta_remove_level_with_zero_qty(self):
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        book.apply_snapshot(
            [("30000.00", "1.0"), ("29999.00", "2.0")],
            [("30001.00", "1.0")]
        )
        book.apply_delta([("30000.00", "0")], [])
        assert Decimal("30000.00") not in book.bids
        assert book.best_bid() == Decimal("29999.00")

    def test_apply_delta_update_existing_level(self):
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        book.apply_snapshot([("30000.00", "1.0")], [("30001.00", "1.0")])
        book.apply_delta([("30000.00", "2.5")], [])
        assert book.bids[Decimal("30000.00")] == Decimal("2.5")

    def test_apply_delta_updates_ask_levels(self):
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        book.apply_snapshot([("30000.00", "1.0")], [("30001.00", "1.0"), ("30002.00", "2.0")])
        book.apply_delta([], [("30001.00", "0")])
        assert Decimal("30001.00") not in book.asks
        assert book.best_ask() == Decimal("30002.00")


class TestOrderBookMidPrice:
    def test_depth_weighted_mid_price(self):
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        book.apply_snapshot(
            [("100.00", "2.0"), ("99.00", "1.0")],
            [("101.00", "1.0"), ("102.00", "3.0")]
        )
        mid = book.depth_weighted_mid_price(depth=2)
        assert isinstance(mid, Decimal)
        # bid VWAP = (100*2 + 99*1)/(2+1) = 299/3 ≈ 99.6667
        # ask VWAP = (101*1 + 102*3)/(1+3) = 407/4 = 101.75
        # mid = (99.6667 + 101.75) / 2 ≈ 100.708
        assert Decimal("99") < mid < Decimal("103")

    def test_depth_weighted_mid_uses_exact_decimal_arithmetic(self):
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        book.apply_snapshot([("100.00", "1.0")], [("101.00", "1.0")])
        mid = book.depth_weighted_mid_price(depth=1)
        assert mid == Decimal("100.5")

    def test_mid_price_empty_book_raises(self):
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        with pytest.raises(ValueError):
            book.depth_weighted_mid_price()

    def test_mid_price_respects_depth_limit(self):
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        # Asymmetric quantities: large qty at best bid, small at outer levels
        # → depth=3 VWAP will differ from depth=1 (single best level)
        book.apply_snapshot(
            [("100.00", "10.0"), ("99.00", "1.0"), ("98.00", "1.0")],
            [("101.00", "1.0"), ("102.00", "1.0"), ("103.00", "1.0")]
        )
        mid_depth1 = book.depth_weighted_mid_price(depth=1)
        mid_depth3 = book.depth_weighted_mid_price(depth=3)
        # Depth=1: bid_vwap=100, ask_vwap=101 → mid=100.5
        assert mid_depth1 == Decimal("100.5")
        # Depth=3: bid_vwap=(100*10+99+98)/12=99.75, ask_vwap=(101+102+103)/3=102 → mid=100.875
        assert mid_depth3 != mid_depth1
        assert mid_depth3 == Decimal("100.875")


class TestOrderBookSpread:
    def test_spread_calculation(self):
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        book.apply_snapshot([("30000.00", "1.0")], [("30001.00", "1.0")])
        assert book.spread() == Decimal("1.00")

    def test_spread_pct(self):
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        book.apply_snapshot([("100.00", "1.0")], [("101.00", "1.0")])
        spread_pct = book.spread_pct()
        assert spread_pct == Decimal("0.01")

    def test_spread_none_on_empty_book(self):
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        assert book.spread() is None
        assert book.spread_pct() is None


class TestVolumeAtPrice:
    def test_volume_at_bid_price(self):
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        book.apply_snapshot([("30000.00", "1.5"), ("29999.00", "2.0")], [("30001.00", "1.0")])
        assert book.volume_at_price(Decimal("30000.00"), "bid") == Decimal("1.5")
        assert book.volume_at_price(Decimal("29999.00"), "bid") == Decimal("2.0")

    def test_volume_at_ask_price(self):
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        book.apply_snapshot([("30000.00", "1.0")], [("30001.00", "0.75")])
        assert book.volume_at_price(Decimal("30001.00"), "ask") == Decimal("0.75")

    def test_volume_returns_zero_for_missing_level(self):
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        book.apply_snapshot([("30000.00", "1.0")], [("30001.00", "1.0")])
        assert book.volume_at_price(Decimal("99999.00"), "bid") == Decimal("0")
        assert book.volume_at_price(Decimal("99999.00"), "ask") == Decimal("0")

    def test_volume_invalid_side_raises(self):
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        book.apply_snapshot([("30000.00", "1.0")], [("30001.00", "1.0")])
        with pytest.raises(ValueError):
            book.volume_at_price(Decimal("30000.00"), "invalid")


class TestBinanceChecksum:
    def test_checksum_is_integer(self):
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        bids = [("30000.00", "1.0"), ("29999.00", "2.0"), ("29998.00", "0.5"),
                ("29997.00", "1.5"), ("29996.00", "3.0")]
        asks = [("30001.00", "1.0"), ("30002.00", "2.0"), ("30003.00", "0.5"),
                ("30004.00", "1.5"), ("30005.00", "3.0")]
        book.apply_snapshot(bids, asks)
        checksum = book.compute_checksum()
        assert isinstance(checksum, int)
        assert 0 <= checksum <= 0xFFFFFFFF

    def test_checksum_validates_against_itself(self):
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        bids = [("30000.00", "1.0"), ("29999.00", "2.0"), ("29998.00", "0.5"),
                ("29997.00", "1.5"), ("29996.00", "3.0")]
        asks = [("30001.00", "1.0"), ("30002.00", "2.0"), ("30003.00", "0.5"),
                ("30004.00", "1.5"), ("30005.00", "3.0")]
        book.apply_snapshot(bids, asks)
        checksum = book.compute_checksum()
        assert book.validate_checksum(checksum) is True

    def test_checksum_fails_for_wrong_value(self):
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        book.apply_snapshot([("30000.00", "1.0")], [("30001.00", "1.0")])
        checksum = book.compute_checksum()
        assert book.validate_checksum(checksum + 1) is False

    def test_checksum_changes_after_delta(self):
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        bids = [("30000.00", "1.0"), ("29999.00", "2.0"), ("29998.00", "0.5"),
                ("29997.00", "1.5"), ("29996.00", "3.0")]
        asks = [("30001.00", "1.0"), ("30002.00", "2.0"), ("30003.00", "0.5"),
                ("30004.00", "1.5"), ("30005.00", "3.0")]
        book.apply_snapshot(bids, asks)
        checksum_before = book.compute_checksum()
        book.apply_delta([("30000.00", "5.0")], [])
        checksum_after = book.compute_checksum()
        assert checksum_before != checksum_after
