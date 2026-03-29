"""Tests for SlippageFeedbackLoop — US-283 (execution layer)."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

with patch("src.infra.metrics.SLIPPAGE_ADJUSTMENT"), \
     patch("src.infra.metrics.SLIPPAGE_ERROR"):
    from src.risk.slippage import SlippageFeedbackLoop


def _make_fb(alpha: float = 0.3, window: int = 100) -> SlippageFeedbackLoop:
    with patch("src.risk.slippage.SLIPPAGE_ADJUSTMENT"), \
         patch("src.risk.slippage.SLIPPAGE_ERROR"):
        return SlippageFeedbackLoop(alpha=alpha, window=window)


def _record(fb: SlippageFeedbackLoop, expected: str, actual: str, side: str = "BUY") -> None:
    with patch("src.risk.slippage.SLIPPAGE_ADJUSTMENT"), \
         patch("src.risk.slippage.SLIPPAGE_ERROR"):
        fb.record_fill(Decimal(expected), Decimal(actual), side)


# ---------------------------------------------------------------------------
# Deque tracking
# ---------------------------------------------------------------------------

class TestRecordAddsToDeque:
    def test_record_adds_to_deque(self) -> None:
        """Each valid record_fill increments sample_count."""
        fb = _make_fb()
        assert fb.sample_count == 0
        _record(fb, "100", "101")
        assert fb.sample_count == 1
        _record(fb, "100", "100")
        assert fb.sample_count == 2

    def test_record_disabled_skips(self) -> None:
        """Zero expected_price → record is ignored, sample_count stays 0."""
        fb = _make_fb()
        _record(fb, "0", "100")
        assert fb.sample_count == 0

    def test_window_caps_deque_length(self) -> None:
        fb = _make_fb(window=5)
        for _ in range(10):
            _record(fb, "100", "101")
        # deque maxlen caps storage at window=5
        assert len(fb._errors) == 5
        # but sample_count tracks total calls
        assert fb.sample_count == 10


# ---------------------------------------------------------------------------
# Adjustment bps
# ---------------------------------------------------------------------------

class TestGetAdjustmentBps:
    def test_get_adjustment_bps_average(self) -> None:
        """After consistent underestimate (+0.8%), adjusted > base."""
        fb = _make_fb(alpha=0.5)
        for _ in range(10):
            _record(fb, "10000", "10080")  # +0.8% underpaid
        assert fb.get_adjusted_slippage(10.0) > 10.0

    def test_get_adjustment_bps_clamping(self) -> None:
        """Errors > ±1% are clamped at ±1%.  Adjustment stays bounded."""
        fb = _make_fb(alpha=1.0)
        _record(fb, "100", "200")   # +100% — should clamp to +1%
        assert fb.adjustment_factor == pytest.approx(0.01, abs=1e-9)

    def test_get_adjustment_bps_empty_returns_zero(self) -> None:
        """No fills → adjustment_factor == 0 → adjusted == base."""
        fb = _make_fb()
        assert fb.get_adjusted_slippage(10.0) == pytest.approx(10.0)

    def test_get_adjusted_slippage_never_negative(self) -> None:
        """Heavy overestimate must not produce negative slippage."""
        fb = _make_fb(alpha=1.0)
        for _ in range(20):
            _record(fb, "10000", "9900")   # -1% (clamp limit)
        assert fb.get_adjusted_slippage(5.0) >= 0.0

    def test_sell_side_direction(self) -> None:
        """SELL: received less than expected → positive error."""
        fb = _make_fb(alpha=1.0)
        _record(fb, "100", "99", side="SELL")   # received less
        assert fb.adjustment_factor > 0.0
