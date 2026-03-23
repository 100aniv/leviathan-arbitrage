"""Tests for TCA enhancements — US-329: Arrival Price, Timing, Per-strategy."""
import pytest
import time
from src.analysis.tca import TCAAnalyzer, ExecutionRecord


class TestTCATimingDecomposition:
    """US-329: Timing breakdown fields."""

    def test_record_with_timing(self):
        tca = TCAAnalyzer(window_size=100)
        now = time.time()
        tca.record_execution(
            expected_price=100.0,
            fill_price=100.05,
            latency_ms=50.0,
            filled_ratio=1.0,
            strategy_id="cross_exchange",
            signal_ts=now - 0.1,
            decision_ts=now - 0.08,
            submission_ts=now - 0.05,
            fill_ts=now,
        )
        summary = tca.get_summary()
        assert "timing" in summary
        assert summary["timing"]["signal_to_fill_p50_ms"] > 0
        assert summary["timing"]["signal_to_decision_p50_ms"] > 0

    def test_record_without_timing_no_timing_key(self):
        tca = TCAAnalyzer(window_size=100)
        tca.record_execution(
            expected_price=100.0,
            fill_price=100.05,
            latency_ms=50.0,
            filled_ratio=1.0,
        )
        summary = tca.get_summary()
        assert "timing" not in summary

    def test_backward_compatible(self):
        """Existing code that doesn't pass timing fields should still work."""
        tca = TCAAnalyzer()
        tca.record_execution(
            expected_price=50000.0,
            fill_price=50001.0,
            latency_ms=10.0,
            filled_ratio=1.0,
            strategy_id="spot_futures",
        )
        summary = tca.get_summary()
        assert summary["sample_count"] == 1
        assert summary["is_p50_bps"] > 0


class TestTCAPerStrategy:
    """US-329: Per-strategy TCA breakdown."""

    def test_strategy_summary(self):
        tca = TCAAnalyzer(window_size=100)
        for _ in range(5):
            tca.record_execution(100.0, 100.03, 20.0, 1.0, strategy_id="cross_exchange")
        for _ in range(3):
            tca.record_execution(100.0, 100.10, 50.0, 0.9, strategy_id="spot_futures")

        ce = tca.get_strategy_summary("cross_exchange")
        assert ce["sample_count"] == 5
        assert ce["strategy_id"] == "cross_exchange"

        sf = tca.get_strategy_summary("spot_futures")
        assert sf["sample_count"] == 3
        assert sf["is_p50_bps"] > ce["is_p50_bps"]  # spot_futures has more slippage

    def test_unknown_strategy_returns_error(self):
        tca = TCAAnalyzer()
        result = tca.get_strategy_summary("nonexistent")
        assert "error" in result

    def test_all_strategy_summaries(self):
        tca = TCAAnalyzer(window_size=100)
        tca.record_execution(100.0, 100.01, 10.0, 1.0, strategy_id="a")
        tca.record_execution(100.0, 100.02, 20.0, 1.0, strategy_id="b")
        all_s = tca.get_all_strategy_summaries()
        assert "a" in all_s
        assert "b" in all_s
        assert all_s["a"]["sample_count"] == 1


class TestArrivalPrice:
    """US-329: Arrival price recording."""

    def test_arrival_price_stored(self):
        tca = TCAAnalyzer(window_size=100)
        tca.record_execution(
            expected_price=100.0,
            fill_price=100.05,
            latency_ms=10.0,
            filled_ratio=1.0,
            arrival_price=99.98,
        )
        assert len(tca._records) == 1
        assert tca._records[0].arrival_price == 99.98

    def test_execution_record_defaults(self):
        rec = ExecutionRecord(expected_price=100.0, fill_price=100.01, latency_ms=5.0, filled_ratio=1.0)
        assert rec.arrival_price == 0.0
        assert rec.signal_ts == 0.0
        assert rec.fill_ts == 0.0
