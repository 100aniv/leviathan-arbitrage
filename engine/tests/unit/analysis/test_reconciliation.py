"""Tests for 3-Way Reconciliation Reporter — US-335."""
import pytest
from src.analysis.reconciliation_reporter import ReconciliationReporter


class TestReconciliationReporter:
    def test_ok_when_matching(self):
        reporter = ReconciliationReporter(output_path="/tmp/test-recon.json")
        result = reporter.reconcile(
            engine_pnl=100.0,
            exchange_balance_delta=100.5,
            db_pnl=100.2,
        )
        assert result.status == "OK"
        assert result.max_drift_pct < 1.0

    def test_warning_on_moderate_drift(self):
        reporter = ReconciliationReporter(output_path="/tmp/test-recon2.json")
        result = reporter.reconcile(
            engine_pnl=100.0,
            exchange_balance_delta=97.0,
            db_pnl=100.0,
        )
        assert result.status == "WARNING"
        assert 1.0 <= result.max_drift_pct <= 5.0

    def test_critical_on_large_drift(self):
        reporter = ReconciliationReporter(output_path="/tmp/test-recon3.json")
        result = reporter.reconcile(
            engine_pnl=100.0,
            exchange_balance_delta=80.0,
            db_pnl=100.0,
        )
        assert result.status == "CRITICAL"
        assert result.max_drift_pct > 5.0

    def test_zero_values(self):
        reporter = ReconciliationReporter(output_path="/tmp/test-recon4.json")
        result = reporter.reconcile(0.0, 0.0, 0.0)
        assert result.status == "OK"
        assert result.max_drift_pct == 0.0

    def test_history_tracking(self):
        reporter = ReconciliationReporter(output_path="/tmp/test-recon5.json")
        reporter.reconcile(100.0, 100.0, 100.0)
        reporter.reconcile(100.0, 95.0, 100.0)
        history = reporter.get_history()
        assert len(history) == 2
        assert history[0]["status"] == "OK"
        assert history[1]["status"] == "CRITICAL"  # 5% drift = CRITICAL (>=5%)
