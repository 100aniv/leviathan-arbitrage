"""Tests for ShadowLiveReporter — US-330."""
import pytest
from unittest.mock import MagicMock
from src.analysis.shadow_live_reporter import ShadowLiveReporter


class TestShadowLiveReporter:
    def test_generate_report_empty(self):
        reporter = ShadowLiveReporter(output_path="/tmp/test-slr.json")
        report = reporter.generate_report()
        assert report["trade_count"] == 0
        assert report["pnl_gap_pct"] == 0.0

    def test_record_and_report(self):
        reporter = ShadowLiveReporter(output_path="/tmp/test-slr.json")
        reporter.record_trade(shadow_pnl=10.0, virtual_live_pnl=12.0)
        reporter.record_trade(shadow_pnl=5.0, virtual_live_pnl=6.0)
        report = reporter.generate_report()
        assert report["trade_count"] == 2
        assert report["shadow_pnl"] == 15.0
        assert report["virtual_live_pnl"] == 18.0
        # Gap: (15-18)/18 * 100 = -16.67%
        assert report["pnl_gap_pct"] < 0

    def test_with_tca_analyzer(self):
        mock_tca = MagicMock()
        mock_tca.get_summary.return_value = {
            "is_p50_bps": 3.5,
            "is_p95_bps": 8.2,
            "fill_rate_pct": 95.0,
        }
        reporter = ShadowLiveReporter(tca_analyzer=mock_tca, output_path="/tmp/test-slr2.json")
        reporter.record_trade(shadow_pnl=1.0)
        report = reporter.generate_report()
        assert report["slippage"]["is_p50_bps"] == 3.5
        assert report["fill_rate_pct"] == 95.0

    def test_should_report_interval(self):
        reporter = ShadowLiveReporter(output_path="/tmp/test-slr3.json")
        assert reporter.should_report(interval_s=0) is True
        reporter.generate_report()
        assert reporter.should_report(interval_s=3600) is False
        assert reporter.should_report(interval_s=0) is True
