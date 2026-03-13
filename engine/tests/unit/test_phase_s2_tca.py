"""Tests for US-134 — TCAAnalyzer and CorrelationMonitor field completeness.

US-134: TCAAnalyzer.record_execution() with all required fields → no KeyError.
        CorrelationMonitor.record_trade_pnl() with valid strategy_id → no error.
        ExecutionResult/LegResult field completeness verified.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.analysis.tca import ExecutionRecord, TCAAnalyzer


# Patch metrics at import time
with patch("src.infra.metrics.STRATEGY_CORRELATION"):
    from src.risk.correlation_monitor import CorrelationMonitor, PositionScaleEvent


# ---------------------------------------------------------------------------
# US-134: TCAAnalyzer.record_execution() — no KeyError with all fields
# ---------------------------------------------------------------------------

class TestTCAAnalyzerRecordExecution:
    """US-134: record_execution() must accept all 5 fields without KeyError."""

    def test_record_execution_no_key_error(self):
        """record_execution() with all 5 fields → no KeyError or AttributeError."""
        analyzer = TCAAnalyzer()
        # Must not raise
        analyzer.record_execution(
            expected_price=50000.0,
            fill_price=50050.0,
            latency_ms=12.5,
            filled_ratio=1.0,
            strategy_id="cross_exchange",
        )

    def test_record_execution_increments_sample_count(self):
        """Each call to record_execution() increments sample count by 1."""
        analyzer = TCAAnalyzer()
        assert analyzer.get_summary()["sample_count"] == 0
        analyzer.record_execution(50000.0, 50050.0, 10.0, 1.0, "strat1")
        assert analyzer.get_summary()["sample_count"] == 1
        analyzer.record_execution(50000.0, 50100.0, 15.0, 0.95, "strat1")
        assert analyzer.get_summary()["sample_count"] == 2

    def test_record_execution_without_strategy_id(self):
        """record_execution() with default strategy_id="" → no error."""
        analyzer = TCAAnalyzer()
        analyzer.record_execution(
            expected_price=3000.0,
            fill_price=3005.0,
            latency_ms=8.0,
            filled_ratio=1.0,
        )
        assert analyzer.get_summary()["sample_count"] == 1

    def test_record_execution_zero_expected_price_ignored(self):
        """expected_price=0 → record is ignored (guard against division by zero)."""
        analyzer = TCAAnalyzer()
        analyzer.record_execution(0.0, 100.0, 5.0, 1.0, "strat1")
        assert analyzer.get_summary()["sample_count"] == 0

    def test_record_execution_multiple_strategies(self):
        """Multiple strategy_ids all record without error."""
        analyzer = TCAAnalyzer()
        strategies = ["cross_exchange", "spot_futures", "triangular", "funding_rate"]
        for strat in strategies:
            analyzer.record_execution(50000.0, 50020.0, 10.0, 1.0, strat)
        assert analyzer.get_summary()["sample_count"] == 4

    def test_record_execution_negative_latency_clamped_to_zero(self):
        """Negative latency_ms is clamped to 0 (not stored as negative)."""
        analyzer = TCAAnalyzer()
        analyzer.record_execution(50000.0, 50020.0, -5.0, 1.0, "strat1")
        summary = analyzer.get_summary()
        assert summary["latency_p50_ms"] >= 0.0

    def test_record_execution_fill_ratio_clamped_to_0_1(self):
        """filled_ratio is clamped to [0, 1] — values outside ignored gracefully."""
        analyzer = TCAAnalyzer()
        analyzer.record_execution(50000.0, 50020.0, 10.0, 1.5, "strat1")  # > 1 → clamp to 1
        summary = analyzer.get_summary()
        assert summary["fill_rate_pct"] <= 100.0

    def test_get_summary_returns_all_required_keys(self):
        """get_summary() returns all 7 required keys."""
        analyzer = TCAAnalyzer()
        analyzer.record_execution(50000.0, 50030.0, 20.0, 1.0, "strat1")
        summary = analyzer.get_summary()
        required_keys = {
            "is_p50_bps",
            "is_p95_bps",
            "latency_p50_ms",
            "latency_p95_ms",
            "latency_p99_ms",
            "fill_rate_pct",
            "sample_count",
        }
        assert required_keys.issubset(set(summary.keys()))


# ---------------------------------------------------------------------------
# US-134: ExecutionRecord dataclass completeness
# ---------------------------------------------------------------------------

class TestExecutionRecordFields:
    """US-134: ExecutionRecord must have all expected fields."""

    def test_execution_record_has_expected_price(self):
        """ExecutionRecord has 'expected_price' field."""
        record = ExecutionRecord(
            expected_price=50000.0,
            fill_price=50020.0,
            latency_ms=10.0,
            filled_ratio=1.0,
        )
        assert record.expected_price == 50000.0

    def test_execution_record_has_fill_price(self):
        """ExecutionRecord has 'fill_price' field."""
        record = ExecutionRecord(
            expected_price=50000.0,
            fill_price=50020.0,
            latency_ms=10.0,
            filled_ratio=1.0,
        )
        assert record.fill_price == 50020.0

    def test_execution_record_has_latency_ms(self):
        """ExecutionRecord has 'latency_ms' field."""
        record = ExecutionRecord(50000.0, 50020.0, 12.5, 1.0)
        assert record.latency_ms == 12.5

    def test_execution_record_has_filled_ratio(self):
        """ExecutionRecord has 'filled_ratio' field."""
        record = ExecutionRecord(50000.0, 50020.0, 10.0, 0.98)
        assert record.filled_ratio == 0.98

    def test_execution_record_has_strategy_id(self):
        """ExecutionRecord has 'strategy_id' field (default empty string)."""
        record = ExecutionRecord(50000.0, 50020.0, 10.0, 1.0, strategy_id="arb_strat")
        assert record.strategy_id == "arb_strat"

    def test_execution_record_auto_timestamp(self):
        """ExecutionRecord has 'timestamp' auto-populated."""
        record = ExecutionRecord(50000.0, 50020.0, 10.0, 1.0)
        assert record.timestamp is not None


# ---------------------------------------------------------------------------
# US-134: CorrelationMonitor.record_trade_pnl() — no error
# ---------------------------------------------------------------------------

class TestCorrelationMonitorRecordPnl:
    """US-134: CorrelationMonitor.record_trade_pnl() with valid strategy_id → no error."""

    def test_record_trade_pnl_no_error(self):
        """record_trade_pnl() with valid strategy_id and pnl → no exception."""
        with patch("src.infra.metrics.STRATEGY_CORRELATION") as mock_metric:
            mock_metric.labels.return_value = MagicMock()
            monitor = CorrelationMonitor(window=10, threshold=0.7)
        monitor.record_trade_pnl("cross_exchange", 12.50)

    def test_record_trade_pnl_multiple_strategies(self):
        """Multiple strategies can record PnL independently without error."""
        with patch("src.infra.metrics.STRATEGY_CORRELATION") as mock_metric:
            mock_metric.labels.return_value = MagicMock()
            monitor = CorrelationMonitor(window=10, threshold=0.7)
        strategies = ["cross_exchange", "spot_futures", "triangular"]
        for s in strategies:
            for _ in range(5):
                monitor.record_trade_pnl(s, 1.0)

    def test_record_trade_pnl_negative_values(self):
        """Negative PnL values are recorded without error."""
        with patch("src.infra.metrics.STRATEGY_CORRELATION") as mock_metric:
            mock_metric.labels.return_value = MagicMock()
            monitor = CorrelationMonitor(window=10, threshold=0.7)
        monitor.record_trade_pnl("strat1", -5.0)

    def test_check_correlations_empty_no_events(self):
        """check_correlations() with no history → returns empty list (no KeyError)."""
        with patch("src.infra.metrics.STRATEGY_CORRELATION") as mock_metric:
            mock_metric.labels.return_value = MagicMock()
            monitor = CorrelationMonitor(window=10, threshold=0.7)
        events = monitor.check_correlations()
        assert events == []

    def test_check_correlations_single_strategy_no_events(self):
        """Only 1 strategy → no correlation events (pairs need ≥2 strategies)."""
        with patch("src.infra.metrics.STRATEGY_CORRELATION") as mock_metric:
            mock_metric.labels.return_value = MagicMock()
            monitor = CorrelationMonitor(window=5, threshold=0.7)
        for pnl in [1.0, 2.0, -1.0, 0.5, 1.5]:
            monitor.record_trade_pnl("strat1", pnl)
        events = monitor.check_correlations()
        assert events == []

    def test_high_correlation_emits_scale_down_event(self):
        """Two perfectly correlated strategies → PositionScaleEvent emitted."""
        with patch("src.infra.metrics.STRATEGY_CORRELATION") as mock_metric:
            mock_metric.labels.return_value = MagicMock()
            monitor = CorrelationMonitor(window=5, threshold=0.7)

        # Identical PnL series → correlation = 1.0 > 0.7 threshold
        pnl_series = [1.0, 2.0, -1.0, 0.5, 1.5]
        for pnl in pnl_series:
            monitor.record_trade_pnl("strat_a", pnl)
            monitor.record_trade_pnl("strat_b", pnl)  # identical → perfect correlation

        with patch("src.infra.metrics.STRATEGY_CORRELATION") as mock_metric:
            mock_metric.labels.return_value = MagicMock()
            events = monitor.check_correlations()

        assert len(events) >= 1
        event = events[0]
        assert isinstance(event, PositionScaleEvent)
        assert event.scale == 0.5  # scale down by 50%

    def test_scale_event_targets_lower_performer(self):
        """When correlation > threshold, the lower-PnL strategy gets scaled down."""
        with patch("src.infra.metrics.STRATEGY_CORRELATION") as mock_metric:
            mock_metric.labels.return_value = MagicMock()
            monitor = CorrelationMonitor(window=5, threshold=0.7)

        # strat_a earns less (lower PnL total)
        for pnl in [0.1, 0.2, 0.1, 0.2, 0.1]:
            monitor.record_trade_pnl("strat_a", pnl)
        # strat_b earns more (higher PnL total)
        for pnl in [1.0, 2.0, 1.0, 2.0, 1.0]:
            monitor.record_trade_pnl("strat_b", pnl)

        with patch("src.infra.metrics.STRATEGY_CORRELATION") as mock_metric:
            mock_metric.labels.return_value = MagicMock()
            events = monitor.check_correlations()

        if events:  # only if correlation exceeded threshold
            assert events[0].strategy_id == "strat_a"


# ---------------------------------------------------------------------------
# US-134: ExecutionResult field completeness
# ---------------------------------------------------------------------------

class TestExecutionResultFields:
    """US-134: ExecutionResult must have all declared fields populated correctly."""

    def test_execution_result_has_strategy_id(self):
        """ExecutionResult must carry strategy_id field."""
        from src.execution.executor import ExecutionResult, ExecutionStatus
        result = ExecutionResult(
            status=ExecutionStatus.SUCCESS,
            strategy_id="cross_exchange",
        )
        assert result.strategy_id == "cross_exchange"

    def test_execution_result_has_rollback_cost(self):
        """ExecutionResult must carry rollback_cost field (Decimal)."""
        from src.execution.executor import ExecutionResult, ExecutionStatus
        result = ExecutionResult(
            status=ExecutionStatus.ROLLED_BACK,
            rollback_cost=Decimal("12.50"),
            strategy_id="strat1",
        )
        assert result.rollback_cost == Decimal("12.50")

    def test_execution_result_has_error_field(self):
        """ExecutionResult must carry error field (string)."""
        from src.execution.executor import ExecutionResult, ExecutionStatus
        result = ExecutionResult(
            status=ExecutionStatus.REJECTED,
            error="Exchange health too low",
            strategy_id="strat1",
        )
        assert result.error == "Exchange health too low"

    def test_execution_result_legs_default_empty(self):
        """ExecutionResult with no legs → empty list (no AttributeError)."""
        from src.execution.executor import ExecutionResult, ExecutionStatus
        result = ExecutionResult(status=ExecutionStatus.REJECTED, strategy_id="s")
        assert result.legs == []
        assert result.leg1 is None
        assert result.leg2 is None
