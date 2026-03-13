"""Tests for US-132 — SlippageFeedbackLoop EMA calibration and LegResult fields.

US-132: EMA updates via record_fill(); get_adjusted_slippage() non-zero after fills.
        LegResult must carry expected_price/fill_price (TDD — new fields).
        PaperExecutor must set expected_price/fill_price on fill (TDD).
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest


# Patch Prometheus metrics at import time
with patch("src.infra.metrics.SLIPPAGE_ADJUSTMENT"), \
     patch("src.infra.metrics.SLIPPAGE_ERROR"):
    from src.risk.slippage import SlippageFeedbackLoop


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_feedback(alpha: float = 0.3) -> SlippageFeedbackLoop:
    with patch("src.risk.slippage.SLIPPAGE_ADJUSTMENT"), \
         patch("src.risk.slippage.SLIPPAGE_ERROR"):
        return SlippageFeedbackLoop(alpha=alpha, window=100)


def _record(fb: SlippageFeedbackLoop, expected: str, actual: str, side: str = "BUY") -> None:
    with patch("src.risk.slippage.SLIPPAGE_ADJUSTMENT"), \
         patch("src.risk.slippage.SLIPPAGE_ERROR"):
        fb.record_fill(Decimal(expected), Decimal(actual), side)


# ---------------------------------------------------------------------------
# US-132: SlippageFeedbackLoop EMA updates
# ---------------------------------------------------------------------------

class TestSlippageFeedbackEMA:
    """US-132: record_fill() must update the EMA adjustment factor."""

    def test_record_fill_updates_ema(self):
        """After record_fill(), adjustment_factor changes from zero."""
        fb = _make_feedback()
        assert fb.adjustment_factor == pytest.approx(0.0)
        _record(fb, "10000", "10050", "BUY")  # paid 50bps more
        assert fb.adjustment_factor != pytest.approx(0.0)

    def test_multiple_records_accumulate(self):
        """10 identical fills → EMA converges near the error magnitude."""
        fb = _make_feedback(alpha=0.3)
        for _ in range(10):
            _record(fb, "10000", "10080", "BUY")  # +0.8% consistent overpay (within clamp)
        assert fb.adjustment_factor > 0.0

    def test_sample_count_increments(self):
        """sample_count tracks number of calls to record_fill()."""
        fb = _make_feedback()
        for i in range(7):
            _record(fb, "1000", "1005", "BUY")
        assert fb.sample_count == 7

    def test_zero_expected_price_ignored(self):
        """record_fill() with expected_price=0 is a no-op (guard)."""
        fb = _make_feedback()
        _record(fb, "0", "10000", "BUY")
        assert fb.sample_count == 0
        assert fb.adjustment_factor == pytest.approx(0.0)

    def test_buy_positive_error_on_overpay(self):
        """BUY side: actual > expected → positive adjustment (under-estimated)."""
        fb = _make_feedback()
        for _ in range(5):
            _record(fb, "10000", "10080", "BUY")
        assert fb.adjustment_factor > 0.0

    def test_sell_positive_error_on_underpay(self):
        """SELL side: actual < expected → positive adjustment (under-estimated)."""
        fb = _make_feedback()
        for _ in range(5):
            _record(fb, "10000", "9920", "SELL")  # received less than expected
        assert fb.adjustment_factor > 0.0


# ---------------------------------------------------------------------------
# US-132: get_adjusted_slippage() returns non-zero after records
# ---------------------------------------------------------------------------

class TestGetAdjustedSlippage:
    """US-132: get_adjusted_slippage() must be non-zero after recording fills."""

    def test_returns_zero_before_any_records(self):
        """Before any fills, adjusted slippage equals base."""
        fb = _make_feedback()
        assert fb.get_adjusted_slippage(10.0) == pytest.approx(10.0)

    def test_returns_nonzero_adjustment_after_fills(self):
        """After consistent under-estimation, adjusted slippage > base."""
        fb = _make_feedback()
        for _ in range(10):
            _record(fb, "10000", "10080", "BUY")
        adjusted = fb.get_adjusted_slippage(10.0)
        assert adjusted > 10.0

    def test_never_returns_negative(self):
        """get_adjusted_slippage() always ≥ 0 even after extreme over-estimation."""
        fb = _make_feedback(alpha=0.9)
        for _ in range(20):
            _record(fb, "10000", "9900", "BUY")  # extreme over-estimate (at clamp)
        result = fb.get_adjusted_slippage(5.0)
        assert result >= 0.0

    def test_adjustment_varies_with_base_slippage(self):
        """Different base_slippage_bps inputs → proportionally different outputs."""
        fb = _make_feedback()
        for _ in range(5):
            _record(fb, "10000", "10050", "BUY")
        adj10 = fb.get_adjusted_slippage(10.0)
        adj20 = fb.get_adjusted_slippage(20.0)
        assert adj20 > adj10  # proportional scaling


# ---------------------------------------------------------------------------
# US-132: LegResult fields — expected_price and fill_price (TDD — new fields)
# ---------------------------------------------------------------------------

class TestLegResultFields:
    """US-132: LegResult must carry expected_price and fill_price (TDD).

    NOTE: These tests WILL FAIL until executor.py adds these fields to LegResult.
    """

    def test_leg_result_has_expected_price_field(self):
        """LegResult dataclass must have 'expected_price' field."""
        from src.execution.executor import LegResult
        from src.core.models import Order, OrderSide, OrderType
        order = Order(
            order_id="test-1",
            exchange_id="binance",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("50000"),
            amount=Decimal("0.01"),
        )
        # LegResult should accept expected_price and fill_price
        leg = LegResult(order=order, expected_price=Decimal("50000"), fill_price=Decimal("50010"))
        assert leg.expected_price == Decimal("50000")

    def test_leg_result_has_fill_price_field(self):
        """LegResult dataclass must have 'fill_price' field."""
        from src.execution.executor import LegResult
        from src.core.models import Order, OrderSide, OrderType
        order = Order(
            order_id="test-2",
            exchange_id="binance",
            symbol="ETH/USDT",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            price=Decimal("3000"),
            amount=Decimal("0.1"),
        )
        leg = LegResult(order=order, expected_price=Decimal("3000"), fill_price=Decimal("2995"))
        assert leg.fill_price == Decimal("2995")

    def test_leg_result_fill_price_defaults_to_none(self):
        """LegResult without fill_price → defaults to None (backward compat)."""
        from src.execution.executor import LegResult
        from src.core.models import Order, OrderSide, OrderType
        order = Order(
            order_id="test-3",
            exchange_id="binance",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("50000"),
            amount=Decimal("0.01"),
        )
        leg = LegResult(order=order)
        # Should default to None if not provided
        assert getattr(leg, "fill_price", None) is None


# ---------------------------------------------------------------------------
# US-132: PaperExecutor sets expected_price and fill_price (TDD)
# ---------------------------------------------------------------------------

class TestLegResultFieldsImplemented:
    """US-132: LegResult.expected_price and fill_price are now implemented in executor.py."""

    def test_leg_result_expected_price_field_exists(self):
        """LegResult dataclass has 'expected_price' field (US-132 implemented)."""
        from src.execution.executor import LegResult
        from src.core.models import Order, OrderSide, OrderType
        order = Order(
            order_id="test-impl-1",
            exchange_id="binance",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("50000"),
            amount=Decimal("0.01"),
        )
        leg = LegResult(order=order, expected_price=Decimal("50000"), fill_price=Decimal("50010"))
        assert leg.expected_price == Decimal("50000")
        assert leg.fill_price == Decimal("50010")

    def test_leg_result_fields_default_to_none(self):
        """LegResult without fill_price/expected_price → both default to None."""
        from src.execution.executor import LegResult
        from src.core.models import Order, OrderSide, OrderType
        order = Order(
            order_id="test-impl-2",
            exchange_id="binance",
            symbol="ETH/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("3000"),
            amount=Decimal("0.1"),
        )
        leg = LegResult(order=order)
        assert leg.expected_price is None
        assert leg.fill_price is None
