"""Tests for US-260/261: AdaptiveThreshold rolling percentile + volatility weight."""
from __future__ import annotations

import pytest

from src.core.adaptive_threshold import AdaptiveThreshold


class TestAdaptiveThresholdStaticFallback:
    """Before min_samples reached, use static thresholds."""

    def test_not_ready_initially(self):
        at = AdaptiveThreshold(min_samples=60, static_entry=10.0, static_exit=5.0)
        assert not at.is_ready

    def test_static_fallback_when_not_ready(self):
        at = AdaptiveThreshold(min_samples=60, static_entry=10.0, static_exit=5.0)
        for i in range(30):
            at.update(float(i))
        entry, exit_ = at.thresholds
        assert entry == 10.0
        assert exit_ == 5.0

    def test_sample_count(self):
        at = AdaptiveThreshold()
        assert at.sample_count == 0
        at.update(5.0)
        at.update(10.0)
        assert at.sample_count == 2


class TestAdaptiveThresholdDynamic:
    """After min_samples, use percentile-based thresholds."""

    def test_becomes_ready(self):
        at = AdaptiveThreshold(min_samples=10, window=100)
        for i in range(10):
            at.update(float(i))
        assert at.is_ready

    def test_dynamic_thresholds_change(self):
        at = AdaptiveThreshold(
            min_samples=10, window=100,
            entry_percentile=90.0, exit_percentile=50.0,
            static_entry=20.0, static_exit=2.5,  # S22: raised so outlier filter allows 0-19 range (cap=20*2=40)
        )
        # Feed uniform spread data
        for i in range(100):
            at.update(float(i % 20))  # 0-19 repeating
        entry, exit_ = at.thresholds
        # 90th percentile of 0-19 ≈ 17.1, 50th ≈ 9.5
        assert entry > 15.0  # roughly 90th pctile
        assert exit_ > 5.0   # roughly 50th pctile
        assert entry > exit_  # entry must exceed exit

    def test_entry_always_gte_exit(self):
        """Sanity: entry threshold should never be below exit."""
        at = AdaptiveThreshold(
            min_samples=5, window=50,
            entry_percentile=95.0, exit_percentile=50.0,
        )
        for _ in range(50):
            at.update(10.0)  # constant → percentiles equal
        entry, exit_ = at.thresholds
        assert entry >= exit_


class TestVolatilityMultiplier:
    """Volatility multiplier widens thresholds during high-vol."""

    def test_baseline_established(self):
        at = AdaptiveThreshold(min_samples=10, vol_lookback=10, window=100)
        for i in range(60):
            at.update(float(i % 10))
        # Baseline is set when thresholds property is accessed (triggers _volatility_multiplier)
        _ = at.thresholds
        assert at._vol_baseline_set

    def test_high_vol_widens_thresholds(self):
        at = AdaptiveThreshold(
            min_samples=10, vol_lookback=10, window=200,
            entry_percentile=90.0, exit_percentile=50.0,
            static_entry=30.0, static_exit=5.0,  # S22: raised so outlier filter allows range 0-29
        )
        # Establish baseline with low-vol data
        for i in range(60):
            at.update(5.0 + (i % 3) * 0.1)  # tight range
        entry_calm, _ = at.thresholds

        # Now inject high-vol data
        for i in range(60):
            at.update(float(i % 30))  # wide range (0-29, all < 30*2=60 cap)
        entry_volatile, _ = at.thresholds

        # High-vol entry should be >= calm entry (volatility multiplier effect)
        assert entry_volatile >= entry_calm

    def test_multiplier_capped(self):
        at = AdaptiveThreshold(
            min_samples=5, vol_lookback=5, window=50,
            vol_multiplier_cap=2.0,
        )
        for i in range(5):
            at.update(1.0)
        # Baseline set from low-vol
        for i in range(10):
            at.update(float(i * 100))  # extreme vol
        mult = at._volatility_multiplier()
        assert mult <= 2.0


class TestPercentileCalculation:
    """Direct _percentile method tests."""

    def test_empty(self):
        at = AdaptiveThreshold()
        assert at._percentile(50.0) == 0.0

    def test_single_value(self):
        at = AdaptiveThreshold()
        at.update(7.5)
        assert at._percentile(50.0) == 7.5

    def test_known_percentiles(self):
        # S22: static_entry=100 so outlier filter allows all values 1-100
        at = AdaptiveThreshold(window=100, static_entry=100.0)
        for i in range(1, 101):
            at.update(float(i))
        # 50th percentile of 1-100 ≈ 50.5
        p50 = at._percentile(50.0)
        assert 49.0 < p50 < 52.0
        # 95th percentile ≈ 95.05
        p95 = at._percentile(95.0)
        assert 94.0 < p95 < 97.0
