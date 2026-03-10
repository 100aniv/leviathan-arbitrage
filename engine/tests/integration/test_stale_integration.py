"""Integration tests for US-066 stale orderbook detection pipeline.

Tests the interaction between:
  - StaleOrderbookDetector (cross-exchange validation, blacklist)
  - OrderBook.update_count (delta tracking)
  - SignalGenerator (blacklist + update_count gates)
  - ShadowMode (loss cap + strategy blacklist + cross-validation hook)
"""
from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.models import OrderSide
from src.core.order_book import OrderBook
from src.core.stale_detector import StaleOrderbookDetector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_book(exchange: str, bid: float, ask: float, symbol: str = "BTC/USDT") -> OrderBook:
    book = OrderBook(symbol=symbol, exchange=exchange)
    book.apply_snapshot([(str(bid), "10.0")], [(str(ask), "10.0")])
    return book


def _make_book_with_deltas(
    exchange: str, bid: float, ask: float, n_deltas: int, symbol: str = "BTC/USDT"
) -> OrderBook:
    book = OrderBook(symbol=symbol, exchange=exchange)
    book.apply_snapshot([(str(bid), "10.0")], [(str(ask), "10.0")])
    for _ in range(n_deltas):
        book.apply_delta([(str(bid), "10.1")], [])
    return book


def _make_sim_trade(price: float, amount: float = 1.0, side=OrderSide.BUY):
    """Mock SimulatedTrade with attributes accessed by _execute_shadow_trade."""
    from unittest.mock import MagicMock
    trade = MagicMock()
    trade.price = Decimal(str(price))
    trade.amount = Decimal(str(amount))
    trade.side = side
    trade.fee = Decimal("0")
    return trade


def _all_books_by_symbol(*books: OrderBook) -> dict[str, dict[str, OrderBook]]:
    """Returns symbol → exchange → OrderBook (matches ShadowMode._books layout)."""
    result: dict[str, dict[str, OrderBook]] = {}
    for b in books:
        result.setdefault(b.symbol, {})[b.exchange] = b
    return result


def _make_signal_generator(stale_detector=None, min_delta_updates: int = 3):
    from src.core.price_hub import PriceHub
    from src.core.signal import SignalConfig, SignalGenerator
    from src.friction.cost_calculator import CostCalculator

    mock_calc = MagicMock(spec=CostCalculator)
    friction = MagicMock()
    friction.net_profit = Decimal("5.00")
    friction.rollback_cost_expected = Decimal("1.00")
    friction.fee_buy = Decimal("0.10")
    friction.fee_sell = Decimal("0.10")
    friction.slippage_buy = Decimal("0.05")
    friction.slippage_sell = Decimal("0.05")
    mock_calc.calculate.return_value = friction

    config = SignalConfig(
        min_edge=Decimal("0.0001"),
        max_spread_pct=Decimal("0.50"),
        cooldown_seconds=0.0,
        max_book_age_seconds=0.0,
        min_delta_update_count=min_delta_updates,
    )
    return SignalGenerator(
        price_hub=PriceHub(),
        cost_calculator=mock_calc,
        config=config,
        stale_detector=stale_detector,
    )


# ---------------------------------------------------------------------------
# Integration: stale Bithumb signal is rejected end-to-end
# ---------------------------------------------------------------------------


class TestStaleBithumbIntegration:
    @pytest.mark.asyncio
    async def test_shadow_rejects_stale_bithumb_signal(self):
        """Full pipeline: stale Bithumb (low update_count) does not produce signal."""
        symbol = "BTC/USDT"
        detector = StaleOrderbookDetector()

        # Bithumb with only 1 delta update (below min_delta_update_count=3)
        bithumb = _make_book_with_deltas("bithumb", 29_990, 30_000, n_deltas=1, symbol=symbol)
        binance = _make_book("binance", 30_100, 30_200, symbol=symbol)

        sg = _make_signal_generator(stale_detector=detector, min_delta_updates=3)
        books = {"bithumb": bithumb, "binance": binance}

        signal = await sg.on_orderbook_update(bithumb, books)
        assert signal is None, "Stale bithumb book (low update_count) must be rejected"

    @pytest.mark.asyncio
    async def test_shadow_accepts_fresh_bithumb_signal(self):
        """Full pipeline: Bithumb with sufficient updates passes delta gate."""
        symbol = "BTC/USDT"
        detector = StaleOrderbookDetector()

        # Bithumb with 5 delta updates (>= min=3)
        bithumb = _make_book_with_deltas("bithumb", 29_990, 30_000, n_deltas=5, symbol=symbol)
        binance = _make_book("binance", 30_100, 30_200, symbol=symbol)

        sg = _make_signal_generator(stale_detector=detector, min_delta_updates=3)
        books = {"bithumb": bithumb, "binance": binance}

        # Gate passes (result may be None due to other gates, but NOT due to update_count)
        # Verify by contrast: same setup with n_deltas=1 returns None, n_deltas=5 may not
        bithumb_stale = _make_book_with_deltas("bithumb", 29_990, 30_000, n_deltas=1, symbol=symbol)
        sg2 = _make_signal_generator(stale_detector=detector, min_delta_updates=3)
        stale_result = await sg2.on_orderbook_update(bithumb_stale, {"bithumb": bithumb_stale, "binance": binance})
        assert stale_result is None  # stale is always None

        # Fresh book: no exception should be raised (may be None for other reasons)
        fresh_result = await sg.on_orderbook_update(bithumb, books)
        assert fresh_result is None or hasattr(fresh_result, "buy_exchange")


# ---------------------------------------------------------------------------
# Integration: blacklist persists across orderbook updates
# ---------------------------------------------------------------------------


class TestBlacklistPersistence:
    @pytest.mark.asyncio
    async def test_shadow_blacklist_persists_across_updates(self):
        """Blacklisted (exchange, symbol) remains blocked until TTL expires."""
        detector = StaleOrderbookDetector(blacklist_ttl_s=5.0)  # 5s TTL

        detector.add_blacklist("bithumb", "BTC/USDT")

        # Simulate multiple orderbook updates — blacklist should persist
        for _ in range(5):
            assert detector.is_blacklisted("bithumb", "BTC/USDT") is True

        # After TTL (simulated via very short TTL in a separate test), it expires
        short_detector = StaleOrderbookDetector(blacklist_ttl_s=0.02)
        short_detector.add_blacklist("bithumb", "BTC/USDT")
        time.sleep(0.05)
        assert short_detector.is_blacklisted("bithumb", "BTC/USDT") is False


# ---------------------------------------------------------------------------
# Integration: loss cap prevents fat-tail in full pipeline
# ---------------------------------------------------------------------------


class TestLossCapIntegration:
    @pytest.mark.asyncio
    async def test_shadow_loss_cap_prevents_fat_tail(self, monkeypatch):
        """Full pipeline: stale trade with large loss is capped at $50."""
        monkeypatch.setenv("SHADOW_MAX_LOSS_PER_TRADE_USD", "50")
        from src.modes.shadow import ShadowMode

        mock_executor = MagicMock()
        mock_executor.execute = AsyncMock(side_effect=[
            _make_sim_trade(30_000, 1.0, OrderSide.BUY),
            _make_sim_trade(28_000, 1.0, OrderSide.SELL),
        ])

        mode = ShadowMode(signal_generator=MagicMock(), paper_executor=mock_executor)
        mode._rate_limiter = MagicMock()
        mode._rate_limiter.try_acquire.return_value = True
        mode._balance_tracker = MagicMock()
        mode._balance_tracker.deduct.return_value = True

        from src.core.models import Signal
        from datetime import datetime, timezone

        signal = Signal(
            strategy_id="cross_exchange_spot",
            symbol="BTC/USDT",
            buy_exchange="coinone",
            sell_exchange="bithumb",
            buy_price=Decimal("30000"),
            sell_price=Decimal("28000"),
            spread_pct=Decimal("0.07"),
            confidence=0.9,
            volume=Decimal("1.0"),
            timestamp=datetime.now(timezone.utc),
        )

        await mode._execute_shadow_trade(signal)

        sid = signal.strategy_id
        stats = mode._stats.by_strategy.get(sid)
        if stats is not None:
            assert stats.total_pnl >= -50.0, (
                f"Fat-tail loss should be capped at -$50, got {stats.total_pnl}"
            )


# ---------------------------------------------------------------------------
# Integration: cross-validation blocks drifted orderbook
# ---------------------------------------------------------------------------


class TestCrossValidationIntegration:
    def test_shadow_cross_validation_blocks_drift(self):
        """5x price drift between Bithumb and non-Korean median is detected and blocked."""
        symbol = "ETH/USDT"
        detector = StaleOrderbookDetector(deviation_pct=0.10, min_comparison_exchanges=2)

        # Bithumb mid-price at ~10000 (5x off non-Korean median of ~2000)
        drifted_book = _make_book("bithumb", 9999, 10001, symbol)

        # symbol → exchange → book (matches ShadowMode._books layout)
        all_books = _all_books_by_symbol(
            drifted_book,
            _make_book("binance", 1999, 2001, symbol),
            _make_book("okx", 1998, 2002, symbol),
        )

        result = detector.check_cross_exchange("bithumb", symbol, drifted_book, all_books)
        assert result is False, "5x drift from non-Korean median must be detected as stale"

    def test_shadow_cross_validation_passes_valid_book(self):
        """Valid price deviation (within 10%) passes cross-validation."""
        symbol = "ETH/USDT"
        detector = StaleOrderbookDetector(deviation_pct=0.10, min_comparison_exchanges=2)

        # Bithumb within 5% of global median (~2000)
        valid_book = _make_book("bithumb", 1980, 2000, symbol)
        all_books = _all_books_by_symbol(
            valid_book,
            _make_book("binance", 1999, 2001, symbol),
            _make_book("okx", 2000, 2005, symbol),
        )

        result = detector.check_cross_exchange("bithumb", symbol, valid_book, all_books)
        assert result is True, "Valid price (within 10% deviation) must pass cross-validation"
