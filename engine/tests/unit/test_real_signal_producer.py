"""Unit tests for RealDataSignalProducer."""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import Signal
from src.core.order_book import OrderBook
from src.core.real_signal_producer import RealDataSignalProducer
from src.core.triangular_scanner import TriangularScanner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_book(symbol: str, exchange: str, bid: str, ask: str, qty: str = "1") -> OrderBook:
    book = OrderBook(symbol=symbol, exchange=exchange)
    book.apply_snapshot(bids=[(bid, qty)], asks=[(ask, qty)])
    return book


def _mock_signal(strategy_id: str = "test") -> Signal:
    return Signal(
        strategy_id=strategy_id,
        symbol="BTC/USDT",
        buy_exchange="binance",
        sell_exchange="bybit",
        buy_price=Decimal("50000"),
        sell_price=Decimal("50100"),
        spread_pct=Decimal("0.002"),
        confidence=0.8,
        volume=Decimal("0.01"),
    )


def _make_producer(return_signal: Signal | None = None) -> tuple[Any, RealDataSignalProducer]:
    """Create a RealDataSignalProducer with a mocked MultiStrategySignalProducer."""
    mock_multi = MagicMock()
    mock_multi.produce_triangular_signal = AsyncMock(return_value=return_signal)
    mock_multi.produce_spot_futures_signal = AsyncMock(return_value=return_signal)
    mock_multi.produce_futures_futures_signal = AsyncMock(return_value=return_signal)
    mock_multi.produce_funding_rate_signal = AsyncMock(return_value=return_signal)

    scanner = TriangularScanner(min_profit_bps=Decimal("10"))
    producer = RealDataSignalProducer(
        multi_signal_producer=mock_multi,
        triangular_scanner=scanner,
        futures_exchanges={"binance_futures", "bybit_futures"},
    )
    return mock_multi, producer


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestOnOrderbookUpdate:
    @pytest.mark.asyncio
    async def test_returns_empty_list_for_no_signals(self):
        """No arb opportunity → empty list returned."""
        _, producer = _make_producer(return_signal=None)
        book = _make_book("BTC/USDT", "binance", "50000", "50001")
        result = await producer.on_orderbook_update(
            exchange_id="binance",
            symbol="BTC/USDT",
            book=book,
            all_books={"BTC/USDT": {"binance": book}},
            futures_books={},
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_spot_futures_signal_returned(self):
        """When futures > spot, produce_spot_futures_signal is called and signal returned."""
        sig = _mock_signal("spot_futures_basis")
        mock_multi, producer = _make_producer(return_signal=sig)

        spot_book = _make_book("BTC/USDT", "binance", "50000", "50010")
        fut_book = _make_book("BTC/USDT", "binance_futures", "50200", "50210")

        all_books = {"BTC/USDT": {"binance": spot_book}}
        futures_books = {"BTC/USDT": {"binance_futures": fut_book}}

        result = await producer.on_orderbook_update(
            exchange_id="binance",
            symbol="BTC/USDT",
            book=spot_book,
            all_books=all_books,
            futures_books=futures_books,
        )
        mock_multi.produce_spot_futures_signal.assert_called_once()
        assert sig in result

    @pytest.mark.asyncio
    async def test_futures_futures_signal_returned(self):
        """When two futures books have crossed prices, produce_futures_futures_signal called."""
        sig = _mock_signal("futures_futures_spread")
        mock_multi, producer = _make_producer(return_signal=sig)

        book_a = _make_book("BTC/USDT", "binance_futures", "50200", "50210")
        book_b = _make_book("BTC/USDT", "bybit_futures", "50050", "50060")

        futures_books = {"BTC/USDT": {"binance_futures": book_a, "bybit_futures": book_b}}

        result = await producer.on_orderbook_update(
            exchange_id="binance_futures",
            symbol="BTC/USDT",
            book=book_a,
            all_books={},
            futures_books=futures_books,
        )
        mock_multi.produce_futures_futures_signal.assert_called()
        assert sig in result

    @pytest.mark.asyncio
    async def test_korean_exchange_skips_spot_futures(self):
        """Upbit/bithumb/coinone are skipped for spot-futures to avoid stale data."""
        sig = _mock_signal("spot_futures_basis")
        mock_multi, producer = _make_producer(return_signal=sig)

        book = _make_book("BTC/KRW", "upbit", "68000000", "68010000")
        fut_book = _make_book("BTC/USDT", "binance_futures", "50200", "50210")

        all_books = {"BTC/KRW": {"upbit": book}}
        futures_books = {"BTC/KRW": {"binance_futures": fut_book}}

        await producer.on_orderbook_update(
            exchange_id="upbit",
            symbol="BTC/KRW",
            book=book,
            all_books=all_books,
            futures_books=futures_books,
        )
        mock_multi.produce_spot_futures_signal.assert_not_called()

    @pytest.mark.asyncio
    async def test_triangular_signal_returned_when_profitable(self):
        """Full profitable cycle → triangular signal produced."""
        sig = _mock_signal("triangular")
        mock_multi, producer = _make_producer(return_signal=sig)

        # Set up a profitable cycle in the embedded scanner
        book_btc = _make_book("BTC/USDT", "binance", "49990", "50000", "2")
        book_eth_btc = _make_book("ETH/BTC", "binance", "0.0499", "0.05", "10")
        book_eth_usdt = _make_book("ETH/USDT", "binance", "2600", "2601", "5")

        all_books: dict = {}

        # Prime the scanner via the first two books
        await producer.on_orderbook_update("binance", "BTC/USDT", book_btc, all_books, {})
        await producer.on_orderbook_update("binance", "ETH/BTC",  book_eth_btc, all_books, {})

        # Third update should trigger cycle detection
        result = await producer.on_orderbook_update(
            "binance", "ETH/USDT", book_eth_usdt, all_books, {}
        )
        mock_multi.produce_triangular_signal.assert_called()
        assert sig in result


class TestOnFundingRatesUpdated:
    @pytest.mark.asyncio
    async def test_funding_rate_signal_produced(self):
        """High/low funding rate diff across exchanges → signal produced."""
        sig = _mock_signal("funding_rate_arb")
        mock_multi, producer = _make_producer(return_signal=sig)

        rates = {
            "binance_futures": {"BTC/USDT": 0.003},
            "bybit": {"BTC/USDT": -0.001},
        }
        ref_book = _make_book("BTC/USDT", "binance", "50000", "50001")
        books = {"BTC/USDT": {"binance": ref_book}}

        result = await producer.on_funding_rates_updated(rates=rates, books=books)
        mock_multi.produce_funding_rate_signal.assert_called_once()
        assert sig in result

    @pytest.mark.asyncio
    async def test_single_exchange_rate_no_signal(self):
        """Only one exchange has rates → diff undefined, no signal."""
        _, producer = _make_producer(return_signal=None)

        rates = {"binance_futures": {"BTC/USDT": 0.003}}
        ref_book = _make_book("BTC/USDT", "binance", "50000", "50001")
        books = {"BTC/USDT": {"binance": ref_book}}

        result = await producer.on_funding_rates_updated(rates=rates, books=books)
        assert result == []

    @pytest.mark.asyncio
    async def test_no_reference_book_no_signal(self):
        """No orderbook reference price → no signal even with valid rates."""
        _, producer = _make_producer(return_signal=None)

        rates = {
            "binance_futures": {"BTC/USDT": 0.003},
            "bybit": {"BTC/USDT": -0.001},
        }

        result = await producer.on_funding_rates_updated(rates=rates, books={})
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_rates(self):
        """Empty rates dict → empty result."""
        _, producer = _make_producer(return_signal=None)
        result = await producer.on_funding_rates_updated(rates={}, books={})
        assert result == []


class TestFuturesFuturesDirection:
    @pytest.mark.asyncio
    async def test_no_signal_when_prices_not_crossed(self):
        """If bid_a < ask_b and bid_b < ask_a → no spread → produce not called."""
        mock_multi, producer = _make_producer(return_signal=None)

        # Normal spread: book_a bid < book_b ask, no crossing
        book_a = _make_book("BTC/USDT", "binance_futures", "50000", "50010")
        book_b = _make_book("BTC/USDT", "bybit_futures",   "50005", "50015")

        futures_books = {"BTC/USDT": {"binance_futures": book_a, "bybit_futures": book_b}}

        result = await producer.on_orderbook_update(
            exchange_id="binance_futures",
            symbol="BTC/USDT",
            book=book_a,
            all_books={},
            futures_books=futures_books,
        )
        mock_multi.produce_futures_futures_signal.assert_not_called()
        assert result == []


# ---------------------------------------------------------------------------
# US-188: Cross-asset statistical arbitrage tests
# ---------------------------------------------------------------------------


def _make_stat_arb_producer(return_signal: Signal | None = None):
    """Producer with produce_statistical_arb_signal mocked."""
    mock_multi, producer = _make_producer(return_signal)
    mock_multi.produce_statistical_arb_signal = AsyncMock(return_value=return_signal)
    return mock_multi, producer


class TestCrossAssetStatArb:
    @pytest.mark.asyncio
    async def test_cross_asset_signal_generation(self):
        """BTC/USDT–ETH/USDT on same exchange emits signal after sufficient history."""
        sig = _mock_signal("statistical_arb_zscore")
        mock_multi, producer = _make_stat_arb_producer(return_signal=sig)

        # Set low threshold and short history to trigger signal quickly
        producer._stat_arb_z_threshold = 0.0
        producer._stat_arb_min_history = 1

        btc_book = _make_book("BTC/USDT", "binance", "50000", "50010")
        eth_book = _make_book("ETH/USDT", "binance", "3000", "3005")
        all_books = {
            "BTC/USDT": {"binance": btc_book},
            "ETH/USDT": {"binance": eth_book},
        }

        result = await producer.on_orderbook_update(
            exchange_id="binance",
            symbol="BTC/USDT",
            book=btc_book,
            all_books=all_books,
            futures_books={},
        )

        mock_multi.produce_statistical_arb_signal.assert_called()
        call_kwargs = mock_multi.produce_statistical_arb_signal.call_args.kwargs
        assert call_kwargs["symbol"] == "BTC/USDT"
        assert call_kwargs["symbol2"] == "ETH/USDT"
        assert call_kwargs["buy_exchange"] == "binance"
        assert call_kwargs["sell_exchange"] == "binance"

    @pytest.mark.asyncio
    async def test_korean_exchange_excluded(self):
        """Korean exchanges (upbit, bithumb, coinone) are never evaluated for stat arb."""
        mock_multi, producer = _make_stat_arb_producer(return_signal=_mock_signal())
        producer._stat_arb_z_threshold = 0.0
        producer._stat_arb_min_history = 1

        btc_book = _make_book("BTC/USDT", "upbit", "50000", "50010")
        eth_book = _make_book("ETH/USDT", "upbit", "3000", "3005")
        all_books = {
            "BTC/USDT": {"upbit": btc_book},
            "ETH/USDT": {"upbit": eth_book},
        }

        await producer.on_orderbook_update(
            exchange_id="upbit",
            symbol="BTC/USDT",
            book=btc_book,
            all_books=all_books,
            futures_books={},
        )

        mock_multi.produce_statistical_arb_signal.assert_not_called()

    @pytest.mark.asyncio
    async def test_fail_closed_cointegration(self):
        """_is_cointegrated_for_pair returns False when statsmodels raises ValueError."""
        from src.strategies.statistical_arb import StatArbConfig, StatisticalArbStrategy

        config = StatArbConfig(
            min_history=10,
            enable_cointegration=True,
            cointegration_pvalue=0.05,
        )
        strategy = StatisticalArbStrategy("s", MagicMock(), config)
        # Build a _PairState with constant (non-cointegrated) prices
        from collections import deque
        from src.strategies.statistical_arb import _PairState, _KalmanHedgeRatio, StatArbState
        ps = _PairState(
            kalman=_KalmanHedgeRatio(),
            prices_a=deque([1.0] * 15, maxlen=100),  # constant → ValueError in coint
            prices_b=deque([2.0] * 15, maxlen=100),  # constant
            spreads=deque([0.0] * 15, maxlen=100),
            state=StatArbState.FLAT,
        )
        result = strategy._is_cointegrated_for_pair(ps)
        # Constant arrays cause ValueError → fail-closed → False
        assert result is False
