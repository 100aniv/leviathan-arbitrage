"""Tests for SlippageFeedbackLoop (US-115) — EMA-based slippage calibration."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest


# Mock Prometheus metrics before importing the module
with patch("src.infra.metrics.SLIPPAGE_ADJUSTMENT") as _mock_adj, \
     patch("src.infra.metrics.SLIPPAGE_ERROR") as _mock_err:
    from src.risk.slippage import SlippageFeedbackLoop


@pytest.fixture
def feedback() -> SlippageFeedbackLoop:
    with patch("src.risk.slippage.SLIPPAGE_ADJUSTMENT"), \
         patch("src.risk.slippage.SLIPPAGE_ERROR"):
        return SlippageFeedbackLoop(alpha=0.3, window=100)


def _record(fb: SlippageFeedbackLoop, expected: str, actual: str, side: str = "BUY") -> None:
    with patch("src.risk.slippage.SLIPPAGE_ADJUSTMENT"), \
         patch("src.risk.slippage.SLIPPAGE_ERROR"):
        fb.record_fill(Decimal(expected), Decimal(actual), side)


# ---------------------------------------------------------------------------
# EMA convergence
# ---------------------------------------------------------------------------

class TestEMAConvergence:
    def test_converges_after_10_identical(self, feedback: SlippageFeedbackLoop) -> None:
        for _ in range(10):
            _record(feedback, "10000", "10050", "BUY")  # +0.5% error (50bps, within ±1% clamp)
        assert feedback.adjustment_factor == pytest.approx(0.005, rel=0.1)

    def test_initial_adjustment_is_zero(self) -> None:
        with patch("src.risk.slippage.SLIPPAGE_ADJUSTMENT"), \
             patch("src.risk.slippage.SLIPPAGE_ERROR"):
            fb = SlippageFeedbackLoop()
        assert fb.adjustment_factor == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Direction detection
# ---------------------------------------------------------------------------

class TestDirection:
    def test_underestimate_positive(self, feedback: SlippageFeedbackLoop) -> None:
        for _ in range(5):
            _record(feedback, "10000", "10080", "BUY")  # paid more (+0.8%, within clamp)
        assert feedback.adjustment_factor > 0.0

    def test_overestimate_negative(self, feedback: SlippageFeedbackLoop) -> None:
        for _ in range(5):
            _record(feedback, "10000", "9920", "BUY")  # paid less (-0.8%, within clamp)
        assert feedback.adjustment_factor < 0.0

    def test_perfect_estimate_near_zero(self, feedback: SlippageFeedbackLoop) -> None:
        for _ in range(10):
            _record(feedback, "100", "100", "BUY")
        assert feedback.adjustment_factor == pytest.approx(0.0, abs=0.01)


# ---------------------------------------------------------------------------
# Adjusted slippage
# ---------------------------------------------------------------------------

class TestAdjustedSlippage:
    def test_increases_on_underestimate(self, feedback: SlippageFeedbackLoop) -> None:
        for _ in range(10):
            _record(feedback, "10000", "10080", "BUY")
        assert feedback.get_adjusted_slippage(10.0) > 10.0

    def test_decreases_on_overestimate(self, feedback: SlippageFeedbackLoop) -> None:
        for _ in range(10):
            _record(feedback, "10000", "9920", "BUY")
        assert feedback.get_adjusted_slippage(10.0) < 10.0

    def test_non_negative(self, feedback: SlippageFeedbackLoop) -> None:
        for _ in range(20):
            _record(feedback, "10000", "9900", "BUY")  # -1% (at clamp limit)
        result = feedback.get_adjusted_slippage(5.0)
        assert result >= 0.0  # get_adjusted_slippage now floors at 0


# ---------------------------------------------------------------------------
# No fill_price application guard
# ---------------------------------------------------------------------------

class TestNoFillPriceApplication:
    def test_no_apply_fill_price_method(self) -> None:
        with patch("src.risk.slippage.SLIPPAGE_ADJUSTMENT"), \
             patch("src.risk.slippage.SLIPPAGE_ERROR"):
            fb = SlippageFeedbackLoop()
        assert not hasattr(fb, "apply_fill_price")

    def test_no_adjust_price_method(self) -> None:
        with patch("src.risk.slippage.SLIPPAGE_ADJUSTMENT"), \
             patch("src.risk.slippage.SLIPPAGE_ERROR"):
            fb = SlippageFeedbackLoop()
        assert not hasattr(fb, "adjust_price")


# ---------------------------------------------------------------------------
# Zero guard + sample count
# ---------------------------------------------------------------------------

class TestGuardsAndCount:
    def test_zero_expected_ignored(self, feedback: SlippageFeedbackLoop) -> None:
        _record(feedback, "0", "100", "BUY")
        assert feedback.sample_count == 0

    def test_sample_count_tracks(self, feedback: SlippageFeedbackLoop) -> None:
        for _ in range(5):
            _record(feedback, "100", "105", "BUY")
        assert feedback.sample_count == 5

    def test_zero_expected_does_not_corrupt_ema(self, feedback: SlippageFeedbackLoop) -> None:
        for _ in range(5):
            _record(feedback, "100", "105", "BUY")
        adj_before = feedback.adjustment_factor
        _record(feedback, "0", "999", "BUY")
        assert feedback.adjustment_factor == pytest.approx(adj_before, rel=1e-9)
