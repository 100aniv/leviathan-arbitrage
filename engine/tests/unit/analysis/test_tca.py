"""Unit tests for TCA (Transaction Cost Analysis) — US-116."""
import pytest
from src.analysis.tca import TCAAnalyzer, PercentileTracker, ExecutionRecord


class TestPercentileTracker:
    """PercentileTracker unit tests."""

    def test_empty_returns_zero(self):
        t = PercentileTracker()
        assert t.percentile(50) == 0.0
        assert t.percentile(99) == 0.0
        assert t.count == 0

    def test_single_value(self):
        t = PercentileTracker()
        t.add(10.0)
        assert t.percentile(50) == 10.0
        assert t.percentile(99) == 10.0
        assert t.count == 1

    def test_p50_even_count(self):
        t = PercentileTracker()
        for v in [1, 2, 3, 4]:
            t.add(v)
        p50 = t.percentile(50)
        assert 2.0 <= p50 <= 3.0  # interpolated median

    def test_p95_p99(self):
        t = PercentileTracker(window_size=100)
        for i in range(1, 101):
            t.add(float(i))
        assert t.percentile(95) >= 94.0
        assert t.percentile(99) >= 98.0
        assert t.count == 100

    def test_window_overflow(self):
        t = PercentileTracker(window_size=5)
        for i in range(10):
            t.add(float(i))
        assert t.count == 5  # only last 5 retained

    def test_sorted_order_independence(self):
        """Values added in any order should produce same percentiles."""
        t1 = PercentileTracker()
        for v in [5, 1, 3, 2, 4]:
            t1.add(v)
        t2 = PercentileTracker()
        for v in [1, 2, 3, 4, 5]:
            t2.add(v)
        assert t1.percentile(50) == t2.percentile(50)


class TestTCAAnalyzer:
    """TCAAnalyzer unit tests."""

    def test_empty_summary(self):
        tca = TCAAnalyzer()
        s = tca.get_summary()
        assert s["sample_count"] == 0
        assert s["is_p50_bps"] == 0
        assert s["fill_rate_pct"] == 0
        assert len(s) == 7  # exactly 7 fields

    def test_summary_field_names(self):
        tca = TCAAnalyzer()
        s = tca.get_summary()
        expected_keys = {
            "is_p50_bps", "is_p95_bps",
            "latency_p50_ms", "latency_p95_ms", "latency_p99_ms",
            "fill_rate_pct", "sample_count",
        }
        assert set(s.keys()) == expected_keys

    def test_zero_is_when_prices_match(self):
        """IS = 0 when fill == expected."""
        tca = TCAAnalyzer()
        tca.record_execution(100.0, 100.0, 10.0, 1.0)
        s = tca.get_summary()
        assert s["is_p50_bps"] == 0.0
        assert s["sample_count"] == 1

    def test_is_calculation(self):
        """IS = abs(fill - expected) / expected * 10000 bps."""
        tca = TCAAnalyzer()
        # 1% slippage = 100 bps
        tca.record_execution(100.0, 101.0, 50.0, 1.0)
        s = tca.get_summary()
        assert s["is_p50_bps"] == 100.0

    def test_negative_slippage_is_absolute(self):
        """IS should be absolute (direction-agnostic)."""
        tca = TCAAnalyzer()
        tca.record_execution(100.0, 99.0, 50.0, 1.0)
        s = tca.get_summary()
        assert s["is_p50_bps"] == 100.0  # abs value

    def test_skip_invalid_price(self):
        """Expected price <= 0 should be skipped."""
        tca = TCAAnalyzer()
        tca.record_execution(0.0, 100.0, 10.0, 1.0)
        tca.record_execution(-1.0, 100.0, 10.0, 1.0)
        assert tca.get_summary()["sample_count"] == 0

    def test_latency_tracking(self):
        tca = TCAAnalyzer()
        tca.record_execution(100.0, 100.5, 150.0, 1.0)
        tca.record_execution(100.0, 100.5, 250.0, 1.0)
        s = tca.get_summary()
        assert s["latency_p50_ms"] >= 150.0
        assert s["latency_p95_ms"] >= 150.0

    def test_fill_rate_tracking(self):
        tca = TCAAnalyzer()
        tca.record_execution(100.0, 100.0, 10.0, 0.8)
        tca.record_execution(100.0, 100.0, 10.0, 1.0)
        s = tca.get_summary()
        assert s["fill_rate_pct"] == 90.0  # (0.8+1.0)/2 * 100

    def test_fill_rate_clamped(self):
        """Fill ratio clamped to [0, 1]."""
        tca = TCAAnalyzer()
        tca.record_execution(100.0, 100.0, 10.0, 1.5)  # > 1
        tca.record_execution(100.0, 100.0, 10.0, -0.1)  # < 0
        s = tca.get_summary()
        assert s["fill_rate_pct"] == 50.0  # (1.0+0.0)/2 * 100

    def test_strategy_id_stored(self):
        tca = TCAAnalyzer()
        tca.record_execution(100.0, 101.0, 10.0, 1.0, strategy_id="cross_exchange")
        assert tca._records[-1].strategy_id == "cross_exchange"

    def test_window_size_respected(self):
        tca = TCAAnalyzer(window_size=5)
        for i in range(10):
            tca.record_execution(100.0, 100.0 + i, 10.0, 1.0)
        assert tca.get_summary()["sample_count"] == 5

    def test_negative_latency_clamped(self):
        tca = TCAAnalyzer()
        tca.record_execution(100.0, 100.0, -50.0, 1.0)
        s = tca.get_summary()
        assert s["latency_p50_ms"] == 0.0


class TestExecutionRecord:
    """ExecutionRecord dataclass tests."""

    def test_creation(self):
        r = ExecutionRecord(
            expected_price=100.0,
            fill_price=101.0,
            latency_ms=50.0,
            filled_ratio=0.95,
        )
        assert r.expected_price == 100.0
        assert r.strategy_id == ""  # default
        assert r.timestamp is not None
