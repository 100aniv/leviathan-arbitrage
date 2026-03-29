"""Tests for market impact estimation — US-284."""
from __future__ import annotations

import logging

import pytest

from src.core.market_impact import estimate_market_impact


class TestZeroVolume:
    def test_zero_volume_returns_zero(self) -> None:
        """ADV=0 → returns 0.0 (avoid ZeroDivisionError)."""
        assert estimate_market_impact(1000.0, 0.0) == pytest.approx(0.0)

    def test_negative_volume_returns_zero(self) -> None:
        assert estimate_market_impact(1000.0, -1.0) == pytest.approx(0.0)


class TestLargeOrderWarning:
    def test_large_order_warning(self, caplog) -> None:
        """order/ADV ratio > 1% should emit a WARNING log."""
        with caplog.at_level(logging.WARNING, logger="src.core.market_impact"):
            estimate_market_impact(size_usd=200.0, daily_volume_usd=1000.0)  # 20% ratio
        assert any("large_order" in r.message for r in caplog.records)

    def test_small_order_no_warning(self, caplog) -> None:
        """order/ADV ratio <= 1% should NOT emit a warning."""
        with caplog.at_level(logging.WARNING, logger="src.core.market_impact"):
            estimate_market_impact(size_usd=5.0, daily_volume_usd=10_000.0)  # 0.05%
        assert not any("large_order" in r.message for r in caplog.records)


class TestCorrectBps:
    def test_small_order_correct_bps(self) -> None:
        """impact_bps = eta * (size/ADV) * 10_000.  eta=0.1, size=100, ADV=10_000 → 0.1*0.01*10000=10."""
        result = estimate_market_impact(100.0, 10_000.0, eta=0.1)
        assert result == pytest.approx(10.0)

    def test_impact_scales_linearly_with_size(self) -> None:
        """Doubling order size doubles impact (linear model)."""
        i1 = estimate_market_impact(100.0, 100_000.0, eta=0.1)
        i2 = estimate_market_impact(200.0, 100_000.0, eta=0.1)
        assert i2 == pytest.approx(i1 * 2.0, rel=1e-9)

    def test_impact_zero_for_zero_size(self) -> None:
        assert estimate_market_impact(0.0, 10_000.0) == pytest.approx(0.0)
