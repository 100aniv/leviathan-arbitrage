"""WS-B: unit tests for TCAAdaptiveFeedback.compute_dynamic_min_spread."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.friction.cost_feedback_loop import TCAAdaptiveFeedback
from src.friction.fee_model import FeeModel


@pytest.fixture
def fee_model() -> FeeModel:
    return FeeModel()


class TestDynamicMinSpread:
    def test_insufficient_samples_returns_static_fallback(self, fee_model):
        """Cold start (<20 observations per leg) must return the static fallback."""
        fb = TCAAdaptiveFeedback(
            fee_model=fee_model,
            min_samples=20,
            funding_buffer_bps=Decimal("5"),
            margin_bps=Decimal("5"),
            static_fallback_bps=Decimal("27"),
        )
        # No observations recorded.
        dyn = fb.compute_dynamic_min_spread(
            strategy_id="futures_futures_v1",
            exchange_pair=("binance_futures", "bitget_futures"),
        )
        assert dyn == Decimal("27")

        # Even with some observations but below min_samples, fallback.
        for _ in range(5):
            fb.record_observation("futures_futures_v1", 12.0, exchange="binance_futures")
            fb.record_observation("futures_futures_v1", 14.0, exchange="bitget_futures")
        dyn2 = fb.compute_dynamic_min_spread(
            strategy_id="futures_futures_v1",
            exchange_pair=("binance_futures", "bitget_futures"),
        )
        assert dyn2 == Decimal("27")

    def test_formula_arithmetic_fee_plus_p95_plus_funding_plus_margin(self, fee_model):
        """With full samples: dynamic_min = fee_rt + p95_slippage + funding + margin."""
        fb = TCAAdaptiveFeedback(
            fee_model=fee_model,
            min_samples=20,
            funding_buffer_bps=Decimal("5"),
            margin_bps=Decimal("5"),
            static_fallback_bps=Decimal("99"),
        )
        # Fee round-trip: binance_futures taker 5bps + bitget_futures taker 6bps = 11bps
        # (FeeModel: binance_futures 0.0005 = 5bps, bitget_futures 0.0006 = 6bps default VIP0).
        # Feed 20 observations of 18.0 bps on each leg → p95 = 18.0.
        for _ in range(25):
            fb.record_observation("futures_futures_v1", 18.0, exchange="binance_futures")
            fb.record_observation("futures_futures_v1", 18.0, exchange="bitget_futures")

        dyn = fb.compute_dynamic_min_spread(
            strategy_id="futures_futures_v1",
            exchange_pair=("binance_futures", "bitget_futures"),
        )
        # Expected: 11 (fee) + 18 (p95) + 5 (fund) + 5 (margin) = 39 bps
        fee_rate_frac = fee_model.round_trip_fee_rate("binance_futures", "bitget_futures")
        expected = Decimal(str(fee_rate_frac)) * Decimal("10000") + Decimal("18") + Decimal("5") + Decimal("5")
        assert dyn == expected

    def test_dynamic_value_updates_as_observations_arrive(self, fee_model):
        """As slippage observations shift, the dynamic threshold must follow."""
        fb = TCAAdaptiveFeedback(
            fee_model=fee_model,
            min_samples=20,
            funding_buffer_bps=Decimal("5"),
            margin_bps=Decimal("5"),
            static_fallback_bps=Decimal("27"),
        )
        # First regime: all observations at 10 bps slippage (both legs).
        for _ in range(25):
            fb.record_observation("futures_futures_v1", 10.0, exchange="binance_futures")
            fb.record_observation("futures_futures_v1", 10.0, exchange="bitget_futures")
        dyn_low = fb.compute_dynamic_min_spread(
            strategy_id="futures_futures_v1",
            exchange_pair=("binance_futures", "bitget_futures"),
        )

        # Second regime: flood higher slippage — p95 must climb, dynamic must rise.
        for _ in range(100):
            fb.record_observation("futures_futures_v1", 40.0, exchange="binance_futures")
            fb.record_observation("futures_futures_v1", 40.0, exchange="bitget_futures")
        dyn_high = fb.compute_dynamic_min_spread(
            strategy_id="futures_futures_v1",
            exchange_pair=("binance_futures", "bitget_futures"),
        )
        assert dyn_high > dyn_low
        # Delta should approximate the p95 difference (30 bps).
        assert (dyn_high - dyn_low) >= Decimal("25")

    def test_safety_floor_never_below_fee_plus_one(self, fee_model):
        """Even if slippage/funding/margin are all 0, min >= fee_roundtrip + 1."""
        fb = TCAAdaptiveFeedback(
            fee_model=fee_model,
            min_samples=20,
            funding_buffer_bps=Decimal("0"),
            margin_bps=Decimal("0"),
            static_fallback_bps=Decimal("99"),
        )
        # All zeros — record 25 zero-slippage observations per leg.
        for _ in range(25):
            fb.record_observation("futures_futures_v1", 0.0, exchange="binance_futures")
            fb.record_observation("futures_futures_v1", 0.0, exchange="bitget_futures")

        dyn = fb.compute_dynamic_min_spread(
            strategy_id="futures_futures_v1",
            exchange_pair=("binance_futures", "bitget_futures"),
        )
        fee_bps = Decimal(str(fee_model.round_trip_fee_rate("binance_futures", "bitget_futures"))) * Decimal("10000")
        # Safety floor: dynamic_min >= fee + 1 bp
        assert dyn >= fee_bps + Decimal("1")

    def test_custom_static_fallback_override(self, fee_model):
        """Call-site may override static_fallback_bps (e.g., FR uses min_funding_diff_bps)."""
        fb = TCAAdaptiveFeedback(
            fee_model=fee_model,
            min_samples=20,
            static_fallback_bps=Decimal("27"),
        )
        # Cold start + explicit override → returns override, not engine-wide default.
        dyn = fb.compute_dynamic_min_spread(
            strategy_id="funding_rate_v1",
            exchange_pair=("binance", "bitget"),
            static_fallback_bps=Decimal("2"),
        )
        assert dyn == Decimal("2")
