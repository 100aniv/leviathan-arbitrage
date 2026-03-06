"""Tests for PriceHub — global best bid/ask aggregator."""
import pytest
from decimal import Decimal

from src.core.order_book import OrderBook
from src.core.price_hub import BestPrice, PriceHub


class TestPriceHubBestBid:
    def test_returns_none_when_no_books(self):
        hub = PriceHub()
        assert hub.best_bid("BTC/USDT") is None

    def test_best_bid_single_exchange(self):
        hub = PriceHub()
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        book.apply_snapshot([("50000.00", "1.0")], [("50001.00", "1.0")])
        hub.update(book)
        result = hub.best_bid("BTC/USDT")
        assert result is not None
        assert result.price == Decimal("50000.00")
        assert result.exchange == "binance"

    def test_best_bid_picks_highest_across_exchanges(self):
        hub = PriceHub()
        book_b = OrderBook(symbol="BTC/USDT", exchange="binance")
        book_b.apply_snapshot([("50000.00", "1.0")], [("50001.00", "1.0")])
        book_o = OrderBook(symbol="BTC/USDT", exchange="okx")
        book_o.apply_snapshot([("50010.00", "0.5")], [("50011.00", "1.0")])
        hub.update(book_b)
        hub.update(book_o)
        result = hub.best_bid("BTC/USDT")
        assert result.price == Decimal("50010.00")
        assert result.exchange == "okx"

    def test_best_bid_ignores_other_symbols(self):
        hub = PriceHub()
        book = OrderBook(symbol="ETH/USDT", exchange="binance")
        book.apply_snapshot([("3000.00", "1.0")], [("3001.00", "1.0")])
        hub.update(book)
        assert hub.best_bid("BTC/USDT") is None

    def test_best_bid_updates_on_new_snapshot(self):
        hub = PriceHub()
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        book.apply_snapshot([("50000.00", "1.0")], [("50001.00", "1.0")])
        hub.update(book)
        book.apply_snapshot([("51000.00", "1.0")], [("51001.00", "1.0")])
        hub.update(book)
        result = hub.best_bid("BTC/USDT")
        assert result.price == Decimal("51000.00")

    def test_best_bid_qty_attribution(self):
        hub = PriceHub()
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        book.apply_snapshot([("50000.00", "3.75")], [("50001.00", "1.0")])
        hub.update(book)
        result = hub.best_bid("BTC/USDT")
        assert result.qty == Decimal("3.75")


class TestPriceHubBestAsk:
    def test_returns_none_when_no_books(self):
        hub = PriceHub()
        assert hub.best_ask("BTC/USDT") is None

    def test_best_ask_picks_lowest_across_exchanges(self):
        hub = PriceHub()
        book_b = OrderBook(symbol="BTC/USDT", exchange="binance")
        book_b.apply_snapshot([("50000.00", "1.0")], [("50001.00", "1.0")])
        book_o = OrderBook(symbol="BTC/USDT", exchange="okx")
        book_o.apply_snapshot([("50010.00", "0.5")], [("50008.00", "1.0")])
        hub.update(book_b)
        hub.update(book_o)
        result = hub.best_ask("BTC/USDT")
        assert result.price == Decimal("50001.00")
        assert result.exchange == "binance"

    def test_best_ask_source_attribution(self):
        hub = PriceHub()
        book = OrderBook(symbol="BTC/USDT", exchange="bybit")
        book.apply_snapshot([("50000.00", "1.0")], [("50001.50", "2.5")])
        hub.update(book)
        result = hub.best_ask("BTC/USDT")
        assert result.exchange == "bybit"
        assert result.qty == Decimal("2.5")

    def test_best_ask_ignores_other_symbols(self):
        hub = PriceHub()
        book = OrderBook(symbol="ETH/USDT", exchange="binance")
        book.apply_snapshot([("3000.00", "1.0")], [("3001.00", "1.0")])
        hub.update(book)
        assert hub.best_ask("BTC/USDT") is None


class TestPriceHubExchanges:
    def test_exchanges_for_symbol(self):
        hub = PriceHub()
        for exch in ["binance", "okx", "bybit"]:
            book = OrderBook(symbol="BTC/USDT", exchange=exch)
            book.apply_snapshot([("50000.00", "1.0")], [("50001.00", "1.0")])
            hub.update(book)
        exchanges = hub.exchanges_for("BTC/USDT")
        assert set(exchanges) == {"binance", "okx", "bybit"}

    def test_exchanges_empty_for_unknown_symbol(self):
        hub = PriceHub()
        assert hub.exchanges_for("ETH/USDT") == []

    def test_multiple_symbols_isolated(self):
        hub = PriceHub()
        book_btc = OrderBook(symbol="BTC/USDT", exchange="binance")
        book_btc.apply_snapshot([("50000.00", "1.0")], [("50001.00", "1.0")])
        book_eth = OrderBook(symbol="ETH/USDT", exchange="okx")
        book_eth.apply_snapshot([("3000.00", "1.0")], [("3001.00", "1.0")])
        hub.update(book_btc)
        hub.update(book_eth)
        assert hub.exchanges_for("BTC/USDT") == ["binance"]
        assert hub.exchanges_for("ETH/USDT") == ["okx"]
