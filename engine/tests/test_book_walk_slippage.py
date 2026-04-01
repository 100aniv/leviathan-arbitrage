"""Tests for BookWalkSlippage and OrderBook.vwap_walk().

TDD test suite for US-060: these tests define the expected behaviour of:
1. OrderBook.vwap_walk(side, size) -> (vwap_price, filled_qty)
2. BookWalkSlippage(SlippageModel) — walks real orderbook depth for fill simulation
3. ShadowMode integration — default slippage model must be BookWalkSlippage

Run after implementation:
    cd engine && python -m pytest tests/test_book_walk_slippage.py -x --tb=short -v
"""
import pytest
from decimal import Decimal
from unittest.mock import MagicMock

from src.core.order_book import OrderBook
from src.core.models import OrderSide


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_book() -> OrderBook:
    """3-level ask book, 3-level bid book centred around 50 000 USDT."""
    book = OrderBook("BTC/USDT", "binance")
    book.asks = {
        Decimal("50000"): Decimal("2"),
        Decimal("50100"): Decimal("3"),
        Decimal("50200"): Decimal("5"),
    }
    book.bids = {
        Decimal("49800"): Decimal("5"),
        Decimal("49900"): Decimal("3"),
        Decimal("49950"): Decimal("2"),
    }
    return book


# ---------------------------------------------------------------------------
# OrderBook.vwap_walk() — 6 tests
# ---------------------------------------------------------------------------


def test_vwap_walk_single_level():
    """Order fits entirely within the best ask level — VWAP equals that price."""
    book = OrderBook("BTC/USDT", "binance")
    book.asks = {Decimal("50000"): Decimal("10")}

    vwap, filled = book.vwap_walk("buy", Decimal("5"))

    assert vwap == Decimal("50000")
    assert filled == Decimal("5")


def test_vwap_walk_multi_level():
    """Order spans 3 ask levels; VWAP is quantity-weighted average."""
    book = OrderBook("BTC/USDT", "binance")
    book.asks = {
        Decimal("50000"): Decimal("2"),
        Decimal("50100"): Decimal("3"),
        Decimal("50200"): Decimal("5"),
    }
    # 2@50000 + 3@50100 = 100000 + 150300 = 250300 / 5
    vwap, filled = book.vwap_walk("buy", Decimal("5"))

    expected_vwap = (Decimal("50000") * Decimal("2") + Decimal("50100") * Decimal("3")) / Decimal("5")
    assert vwap == expected_vwap
    assert filled == Decimal("5")


def test_vwap_walk_partial_last_level():
    """Order fills completely at L1 then takes partial qty from L2."""
    book = OrderBook("BTC/USDT", "binance")
    book.asks = {
        Decimal("50000"): Decimal("2"),
        Decimal("50100"): Decimal("10"),
    }
    # 2@50000 + 3@50100 = 100000 + 150300 = 250300 / 5
    vwap, filled = book.vwap_walk("buy", Decimal("5"))

    expected_vwap = (
        Decimal("50000") * Decimal("2") + Decimal("50100") * Decimal("3")
    ) / Decimal("5")
    assert vwap == expected_vwap
    assert filled == Decimal("5")


def test_vwap_walk_exceeds_depth():
    """Order size exceeds total book depth — only available qty is filled."""
    book = OrderBook("BTC/USDT", "binance")
    book.asks = {Decimal("50000"): Decimal("2"), Decimal("50100"): Decimal("1")}

    vwap, filled = book.vwap_walk("buy", Decimal("10"))

    # Only 3 BTC total available
    expected_vwap = (
        Decimal("50000") * Decimal("2") + Decimal("50100") * Decimal("1")
    ) / Decimal("3")
    assert vwap == expected_vwap
    assert filled == Decimal("3")


def test_vwap_walk_empty_book():
    """Empty book returns (0, 0) — no fill possible."""
    book = OrderBook("BTC/USDT", "binance")
    # asks dict is empty by default

    vwap, filled = book.vwap_walk("buy", Decimal("1"))

    assert vwap == Decimal("0")
    assert filled == Decimal("0")


def test_vwap_walk_sell_side():
    """SELL order walks bid levels highest-price-first."""
    book = OrderBook("BTC/USDT", "binance")
    book.bids = {
        Decimal("49900"): Decimal("3"),
        Decimal("50000"): Decimal("2"),
    }
    # Walk descending: 50000 first (2 BTC), then 49900 (2 more → total 4)
    # VWAP = (50000*2 + 49900*2) / 4 = 199800 / 4 = 49950
    vwap, filled = book.vwap_walk("sell", Decimal("4"))

    expected_vwap = (
        Decimal("50000") * Decimal("2") + Decimal("49900") * Decimal("2")
    ) / Decimal("4")
    assert vwap == expected_vwap
    assert filled == Decimal("4")


# ---------------------------------------------------------------------------
# BookWalkSlippage.apply() — 7 tests
# ---------------------------------------------------------------------------


def test_apply_buy_walks_asks():
    """BUY apply() returns VWAP of walked ask levels — always > best ask."""
    from src.modes.shadow import BookWalkSlippage

    book = _make_book()
    books = {"BTC/USDT": {"binance": book}}
    model = BookWalkSlippage(books=books)
    model.set_context("binance", "BTC/USDT")

    # Order for 3 BTC: 2@50000 + 1@50100 → VWAP = 50033.33…
    price = model.apply(Decimal("50000"), OrderSide.BUY, Decimal("3"))

    assert price > Decimal("50000")


def test_apply_sell_walks_bids():
    """SELL apply() returns VWAP of walked bid levels — always < best bid."""
    from src.modes.shadow import BookWalkSlippage

    book = _make_book()
    books = {"BTC/USDT": {"binance": book}}
    model = BookWalkSlippage(books=books)
    model.set_context("binance", "BTC/USDT")

    # Bids: 49950*2, 49900*3 → VWAP for 3 BTC fill
    price = model.apply(Decimal("50000"), OrderSide.SELL, Decimal("3"))

    assert price < Decimal("50000")


def test_fallback_no_book():
    """When the orderbook is missing, apply() uses conservative fallback BPS."""
    from src.modes.shadow import BookWalkSlippage

    model = BookWalkSlippage(books={}, fallback_bps=Decimal("10"))
    model.set_context("unknown_exchange", "BTC/USDT")

    price = model.apply(Decimal("50000"), OrderSide.BUY, Decimal("1"))

    expected = Decimal("50000") * (Decimal("1") + Decimal("10") / Decimal("10000"))
    assert price == expected


def test_fallback_empty_context():
    """When set_context() is never called, the model falls back gracefully."""
    from src.modes.shadow import BookWalkSlippage

    books = {"BTC/USDT": {"binance": _make_book()}}
    model = BookWalkSlippage(books=books)
    # set_context NOT called → _current_exchange/symbol default to "" → lookup fails

    price = model.apply(Decimal("50000"), OrderSide.BUY, Decimal("1"))

    # Must apply slippage (not return exact base_price)
    assert price > Decimal("50000")


def test_fallback_bps_configurable():
    """Custom fallback_bps is respected for both BUY and SELL."""
    from src.modes.shadow import BookWalkSlippage

    model = BookWalkSlippage(books={}, fallback_bps=Decimal("20"))
    model.set_context("x", "Y")

    price = model.apply(Decimal("10000"), OrderSide.BUY, Decimal("1"))

    expected = Decimal("10000") * (Decimal("1") + Decimal("20") / Decimal("10000"))
    assert price == expected


def test_insufficient_liquidity_penalty():
    """Order size > total book depth applies a penalty to the unfilled remainder."""
    from src.modes.shadow import BookWalkSlippage

    book = OrderBook("BTC/USDT", "binance")
    book.asks = {Decimal("50000"): Decimal("1")}  # only 1 BTC available
    books = {"BTC/USDT": {"binance": book}}
    model = BookWalkSlippage(books=books, depth_penalty_multiplier=2.0)
    model.set_context("binance", "BTC/USDT")

    # Request 3 BTC but only 1 exists → remainder penalised
    price = model.apply(Decimal("50000"), OrderSide.BUY, Decimal("3"))

    # Effective VWAP must be worse (higher) than best ask for BUY
    assert price > Decimal("50000")


def test_insufficient_liquidity_penalty_sell():
    """SELL order size > total bid depth penalises unfilled portion LOWER (worse for seller)."""
    from src.modes.shadow import BookWalkSlippage

    book = OrderBook("BTC/USDT", "binance")
    book.bids = {Decimal("50000"): Decimal("1")}  # only 1 BTC available
    books = {"BTC/USDT": {"binance": book}}
    model = BookWalkSlippage(books=books, depth_penalty_multiplier=2.0)
    model.set_context("binance", "BTC/USDT")

    # Request to sell 3 BTC but only 1 exists → remainder penalised LOWER
    price = model.apply(Decimal("50000"), OrderSide.SELL, Decimal("3"))

    # Effective VWAP must be worse (lower) than best bid for SELL
    assert price < Decimal("50000")
    # Penalty = 50000/2.0 = 25000; weighted = (50000*1 + 25000*2) / 3 = 33333.33
    expected = (Decimal("50000") * Decimal("1") + Decimal("25000") * Decimal("2")) / Decimal("3")
    assert price == expected


def test_vwap_equals_best_for_tiny_order():
    """A negligibly small order fills entirely at L1 — VWAP == best price."""
    from src.modes.shadow import BookWalkSlippage

    book = OrderBook("BTC/USDT", "binance")
    book.asks = {Decimal("50000"): Decimal("100"), Decimal("50100"): Decimal("50")}
    books = {"BTC/USDT": {"binance": book}}
    model = BookWalkSlippage(books=books)
    model.set_context("binance", "BTC/USDT")

    # 0.001 BTC << 100 BTC at L1 → should all fill at 50000
    price = model.apply(Decimal("50000"), OrderSide.BUY, Decimal("0.001"))

    assert price == Decimal("50000")


# ---------------------------------------------------------------------------
# Integration — 1 test
# ---------------------------------------------------------------------------


def test_shadow_mode_uses_book_walk_slippage():
    """ShadowMode default construction uses BookWalkSlippage, not PowerLawSlippage."""
    from src.modes.shadow import ShadowMode, BookWalkSlippage

    sg = MagicMock()
    sm = ShadowMode(signal_generator=sg)

    assert isinstance(sm._paper_executor.slippage_model, BookWalkSlippage)


def test_live_mode_paper_uses_book_walk_slippage():
    """US-348: LiveMode paper execution_mode wires BookWalkSlippage into PaperExecutor."""
    from src.modes.shadow import BookWalkSlippage
    from src.execution.paper import PaperExecutor
    from src.modes.live import LiveMode

    sg = MagicMock()
    sm = MagicMock()
    lm = LiveMode(
        signal_generator=sg,
        executor=MagicMock(),  # original executor — should be replaced for paper mode
        strategy_manager=sm,
        execution_mode="paper",
    )

    # AC1 생성: BookWalkSlippage instance created
    assert lm._book_walk_slippage is not None
    assert isinstance(lm._book_walk_slippage, BookWalkSlippage)

    # AC2 주입: executor replaced with PaperExecutor
    assert isinstance(lm._executor, PaperExecutor)
    assert isinstance(lm._executor.slippage_model, BookWalkSlippage)

    # AC3 호출: slippage model references the same live _books dict
    assert lm._book_walk_slippage._books is lm._books


def test_live_mode_live_keeps_original_executor():
    """US-348: LiveMode live execution_mode keeps the injected AtomicExecutor (no slippage sim)."""
    from src.modes.live import LiveMode
    from src.execution.paper import PaperExecutor

    sg = MagicMock()
    sm = MagicMock()
    original_executor = MagicMock()
    lm = LiveMode(
        signal_generator=sg,
        executor=original_executor,
        strategy_manager=sm,
        execution_mode="live",
    )

    # live mode must NOT replace executor with PaperExecutor
    assert lm._executor is original_executor
    assert lm._book_walk_slippage is None
    assert not isinstance(lm._executor, PaperExecutor)


def test_live_mode_paper_books_reference_is_shared():
    """US-348: BookWalkSlippage._books is the same object as LiveMode._books (live reference)."""
    from src.modes.live import LiveMode
    from src.modes.shadow import BookWalkSlippage

    lm = LiveMode(
        signal_generator=MagicMock(),
        executor=MagicMock(),
        strategy_manager=MagicMock(),
        execution_mode="paper",
    )

    # Mutate _books after construction — slippage model must see the update
    lm._books["BTC/USDT"] = {"binance": MagicMock()}
    assert "BTC/USDT" in lm._book_walk_slippage._books
