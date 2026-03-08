"""Integration tests for multi-signal production pipeline (US-022).

Verifies that:
1. RealDataSignalProducer produces all 4 signal types end-to-end
2. ShadowMode delegates to RealDataSignalProducer (no inline _evaluate_*)
3. TriangularScanner integrates with RealDataSignalProducer
4. Signal metadata is complete and correct
"""
from __future__ import annotations

import inspect
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import Signal
from src.core.multi_signal import MultiStrategySignalProducer
from src.core.order_book import OrderBook
from src.core.real_signal_producer import RealDataSignalProducer
from src.core.triangular_scanner import TriangleCycle, TriangularScanner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_book(symbol: str, exchange: str, bid: str, ask: str, qty: str = "1") -> OrderBook:
    book = OrderBook(symbol=symbol, exchange=exchange)
    book.apply_snapshot(bids=[(bid, qty)], asks=[(ask, qty)])
    return book


def _mock_signal(strategy_id: str, symbol: str = "BTC/USDT") -> Signal:
    return Signal(
        strategy_id=strategy_id,
        symbol=symbol,
        buy_exchange="binance",
        sell_exchange="bybit",
        buy_price=Decimal("50000"),
        sell_price=Decimal("50100"),
        spread_pct=Decimal("0.002"),
        confidence=0.8,
        volume=Decimal("0.01"),
    )


def _make_full_producer() -> tuple[MagicMock, RealDataSignalProducer]:
    """Create producer with all 4 signal types returning distinct signals."""
    mock_multi = MagicMock()
    mock_multi.produce_triangular_signal = AsyncMock(
        return_value=_mock_signal("triangular")
    )
    mock_multi.produce_spot_futures_signal = AsyncMock(
        return_value=_mock_signal("spot_futures_basis")
    )
    mock_multi.produce_futures_futures_signal = AsyncMock(
        return_value=_mock_signal("futures_futures_spread")
    )
    mock_multi.produce_funding_rate_signal = AsyncMock(
        return_value=_mock_signal("funding_rate_arb")
    )
    scanner = TriangularScanner(min_profit_bps=Decimal("10"))
    producer = RealDataSignalProducer(
        multi_signal_producer=mock_multi,
        triangular_scanner=scanner,
        futures_exchanges={"binance_futures"},
    )
    return mock_multi, producer


# ---------------------------------------------------------------------------
# Test: All 4 signal types produced in a single session
# ---------------------------------------------------------------------------

class TestAllFourSignalTypes:
    """Verify that RealDataSignalProducer can emit all 4 signal types."""

    @pytest.mark.asyncio
    async def test_triangular_signal_end_to_end(self):
        """Profitable USDT→BTC→ETH→USDT cycle → triangular signal."""
        mock_multi, producer = _make_full_producer()

        book_btc = _make_book("BTC/USDT", "binance", "49990", "50000", "2")
        book_eth_btc = _make_book("ETH/BTC", "binance", "0.0499", "0.05", "10")
        book_eth_usdt = _make_book("ETH/USDT", "binance", "2600", "2601", "5")

        await producer.on_orderbook_update("binance", "BTC/USDT", book_btc, {}, {})
        await producer.on_orderbook_update("binance", "ETH/BTC", book_eth_btc, {}, {})
        result = await producer.on_orderbook_update(
            "binance", "ETH/USDT", book_eth_usdt, {}, {}
        )

        mock_multi.produce_triangular_signal.assert_called()
        assert any(s.strategy_id == "triangular" for s in result)

    @pytest.mark.asyncio
    async def test_spot_futures_signal_end_to_end(self):
        """Futures > spot → spot_futures signal."""
        mock_multi, producer = _make_full_producer()

        spot = _make_book("BTC/USDT", "binance", "50000", "50010")
        fut = _make_book("BTC/USDT", "binance_futures", "50200", "50210")

        all_books = {"BTC/USDT": {"binance": spot}}
        futures_books = {"BTC/USDT": {"binance_futures": fut}}

        result = await producer.on_orderbook_update(
            "binance", "BTC/USDT", spot, all_books, futures_books
        )

        mock_multi.produce_spot_futures_signal.assert_called_once()
        assert any(s.strategy_id == "spot_futures_basis" for s in result)

    @pytest.mark.asyncio
    async def test_futures_futures_signal_end_to_end(self):
        """Crossed futures prices → futures_futures signal."""
        mock_multi, producer = _make_full_producer()

        book_a = _make_book("BTC/USDT", "binance_futures", "50200", "50210")
        book_b = _make_book("BTC/USDT", "bybit_futures", "50050", "50060")

        futures_books = {"BTC/USDT": {"binance_futures": book_a, "bybit_futures": book_b}}

        result = await producer.on_orderbook_update(
            "binance_futures", "BTC/USDT", book_a, {}, futures_books
        )

        mock_multi.produce_futures_futures_signal.assert_called()
        assert any(s.strategy_id == "futures_futures_spread" for s in result)

    @pytest.mark.asyncio
    async def test_funding_rate_signal_end_to_end(self):
        """Rate differential across exchanges → funding_rate signal."""
        mock_multi, producer = _make_full_producer()

        rates = {
            "binance_futures": {"BTC/USDT": 0.003},
            "bybit": {"BTC/USDT": -0.001},
        }
        ref_book = _make_book("BTC/USDT", "binance", "50000", "50001")
        books = {"BTC/USDT": {"binance": ref_book}}

        result = await producer.on_funding_rates_updated(rates=rates, books=books)

        mock_multi.produce_funding_rate_signal.assert_called_once()
        assert any(s.strategy_id == "funding_rate_arb" for s in result)

    @pytest.mark.asyncio
    async def test_all_four_signal_types_produced(self):
        """A combined scenario that exercises all 4 signal types."""
        mock_multi, producer = _make_full_producer()

        signals_collected: list[Signal] = []

        # 1. Triangular: prime scanner with 3 books
        book_btc = _make_book("BTC/USDT", "binance", "49990", "50000", "2")
        book_eth_btc = _make_book("ETH/BTC", "binance", "0.0499", "0.05", "10")
        book_eth_usdt = _make_book("ETH/USDT", "binance", "2600", "2601", "5")

        await producer.on_orderbook_update("binance", "BTC/USDT", book_btc, {}, {})
        await producer.on_orderbook_update("binance", "ETH/BTC", book_eth_btc, {}, {})
        result = await producer.on_orderbook_update("binance", "ETH/USDT", book_eth_usdt, {}, {})
        signals_collected.extend(result)

        # 2. Spot-futures
        spot = _make_book("BTC/USDT", "binance", "50000", "50010")
        fut = _make_book("BTC/USDT", "binance_futures", "50200", "50210")
        result = await producer.on_orderbook_update(
            "binance", "BTC/USDT", spot,
            {"BTC/USDT": {"binance": spot}},
            {"BTC/USDT": {"binance_futures": fut}},
        )
        signals_collected.extend(result)

        # 3. Futures-futures
        book_a = _make_book("BTC/USDT", "binance_futures", "50200", "50210")
        book_b = _make_book("BTC/USDT", "bybit_futures", "50050", "50060")
        result = await producer.on_orderbook_update(
            "binance_futures", "BTC/USDT", book_a, {},
            {"BTC/USDT": {"binance_futures": book_a, "bybit_futures": book_b}},
        )
        signals_collected.extend(result)

        # 4. Funding rate
        rates = {
            "binance_futures": {"BTC/USDT": 0.003},
            "bybit": {"BTC/USDT": -0.001},
        }
        ref = _make_book("BTC/USDT", "binance", "50000", "50001")
        result = await producer.on_funding_rates_updated(
            rates=rates, books={"BTC/USDT": {"binance": ref}}
        )
        signals_collected.extend(result)

        # Verify all 4 types
        strategy_ids = {s.strategy_id for s in signals_collected}
        assert "triangular" in strategy_ids
        assert "spot_futures_basis" in strategy_ids
        assert "futures_futures_spread" in strategy_ids
        assert "funding_rate_arb" in strategy_ids


# ---------------------------------------------------------------------------
# Test: Shadow mode delegates to RealDataSignalProducer
# ---------------------------------------------------------------------------

class TestShadowModeDelegation:
    """Verify shadow.py wiring: no inline _evaluate_*, delegation via producer."""

    def test_shadow_mode_no_inline_evaluate_triangular(self):
        """shadow.py should not define _evaluate_triangular inline."""
        from src.modes.shadow import ShadowMode
        # The method should NOT exist on ShadowMode anymore
        assert not hasattr(ShadowMode, "_evaluate_triangular"), \
            "_evaluate_triangular should be removed from ShadowMode (moved to RealDataSignalProducer)"

    def test_shadow_mode_no_inline_evaluate_statistical_arb(self):
        from src.modes.shadow import ShadowMode
        assert not hasattr(ShadowMode, "_evaluate_statistical_arb"), \
            "_evaluate_statistical_arb should be removed from ShadowMode"

    def test_shadow_mode_no_inline_evaluate_latency_arb(self):
        from src.modes.shadow import ShadowMode
        assert not hasattr(ShadowMode, "_evaluate_latency_arb"), \
            "_evaluate_latency_arb should be removed from ShadowMode"

    def test_shadow_mode_no_inline_evaluate_spot_futures(self):
        from src.modes.shadow import ShadowMode
        assert not hasattr(ShadowMode, "_evaluate_spot_futures"), \
            "_evaluate_spot_futures should be removed from ShadowMode"

    def test_shadow_mode_no_inline_evaluate_futures_futures(self):
        from src.modes.shadow import ShadowMode
        assert not hasattr(ShadowMode, "_evaluate_futures_futures"), \
            "_evaluate_futures_futures should be removed from ShadowMode"

    def test_shadow_mode_no_inline_evaluate_funding_rate_arb(self):
        from src.modes.shadow import ShadowMode
        assert not hasattr(ShadowMode, "_evaluate_funding_rate_arb"), \
            "_evaluate_funding_rate_arb should be removed from ShadowMode"

    def test_shadow_mode_has_evaluate_multi_strategies(self):
        """The delegation method must still exist."""
        from src.modes.shadow import ShadowMode
        assert hasattr(ShadowMode, "_evaluate_multi_strategies")

    def test_shadow_mode_has_real_signal_producer_init(self):
        """ShadowMode.__init__ should reference _real_signal_producer."""
        from src.modes.shadow import ShadowMode
        src = inspect.getsource(ShadowMode.__init__)
        assert "_real_signal_producer" in src

    def test_evaluate_multi_strategies_delegates_to_producer(self):
        """_evaluate_multi_strategies should call _real_signal_producer.on_orderbook_update."""
        from src.modes.shadow import ShadowMode
        src = inspect.getsource(ShadowMode._evaluate_multi_strategies)
        assert "_real_signal_producer" in src
        assert "on_orderbook_update" in src


# ---------------------------------------------------------------------------
# Test: Signal metadata completeness
# ---------------------------------------------------------------------------

class TestSignalMetadata:
    """Verify that produced signals have all required fields."""

    @pytest.mark.asyncio
    async def test_signal_has_required_fields(self):
        """Every Signal has strategy_id, symbol, buy_exchange, sell_exchange, prices."""
        mock_multi, producer = _make_full_producer()

        rates = {
            "binance_futures": {"BTC/USDT": 0.003},
            "bybit": {"BTC/USDT": -0.001},
        }
        ref = _make_book("BTC/USDT", "binance", "50000", "50001")
        result = await producer.on_funding_rates_updated(
            rates=rates, books={"BTC/USDT": {"binance": ref}}
        )

        assert len(result) >= 1
        for sig in result:
            assert sig.strategy_id
            assert sig.symbol
            assert sig.buy_exchange
            assert sig.sell_exchange
            assert sig.buy_price > 0
            assert sig.sell_price > 0


# ---------------------------------------------------------------------------
# Test: TriangularScanner + RealDataSignalProducer integration
# ---------------------------------------------------------------------------

class TestTriangularScannerIntegration:
    """Verify TriangularScanner feeds into RealDataSignalProducer correctly."""

    @pytest.mark.asyncio
    async def test_scanner_detects_cycle_and_producer_emits(self):
        """TriangularScanner's on_orderbook_update → TriangleCycle → producer signal."""
        scanner = TriangularScanner(min_profit_bps=Decimal("10"))

        # Feed profitable cycle
        book_btc = _make_book("BTC/USDT", "binance", "49990", "50000", "2")
        book_eth_btc = _make_book("ETH/BTC", "binance", "0.0499", "0.05", "10")
        book_eth_usdt = _make_book("ETH/USDT", "binance", "2600", "2601", "5")

        scanner.on_orderbook_update("binance", "BTC/USDT", book_btc)
        scanner.on_orderbook_update("binance", "ETH/BTC", book_eth_btc)
        cycles = scanner.on_orderbook_update("binance", "ETH/USDT", book_eth_usdt)

        assert len(cycles) >= 1
        cycle = cycles[0]
        assert isinstance(cycle, TriangleCycle)
        assert cycle.exchange_id == "binance"
        assert cycle.profit_pct > Decimal("0")
        assert cycle.max_volume_usdt > 0

    def test_scanner_independent_per_exchange(self):
        """Scanner keeps separate graphs per exchange."""
        scanner = TriangularScanner(min_profit_bps=Decimal("10"))

        # Binance: full triangle
        scanner.on_orderbook_update("binance", "BTC/USDT", _make_book("BTC/USDT", "binance", "49990", "50000"))
        scanner.on_orderbook_update("binance", "ETH/BTC", _make_book("ETH/BTC", "binance", "0.0499", "0.05"))
        cycles_binance = scanner.on_orderbook_update(
            "binance", "ETH/USDT", _make_book("ETH/USDT", "binance", "2600", "2601")
        )

        # Bybit: only 1 pair
        cycles_bybit = scanner.on_orderbook_update(
            "bybit", "BTC/USDT", _make_book("BTC/USDT", "bybit", "49990", "50000")
        )

        assert len(cycles_binance) >= 1
        assert cycles_bybit == []
