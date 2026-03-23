"""Tests for TCA Calibrator — US-333."""
import pytest
from unittest.mock import MagicMock
from src.analysis.tca_calibrator import TCACalibrator


class TestTCACalibrator:
    def test_calibrate_with_data(self):
        mock_tca = MagicMock()
        mock_tca.get_summary.return_value = {
            "is_p50_bps": 3.0,
            "is_p95_bps": 8.5,
            "sample_count": 100,
        }
        mock_tca.get_all_strategy_summaries.return_value = {}

        cal = TCACalibrator(tca_analyzer=mock_tca, safety_margin_bps=2.0)
        result = cal.calibrate()

        assert "error" not in result
        assert result["recommended_slippage_buffer_bps"] == 10.5  # 8.5 + 2.0
        assert result["recommended_min_edge_bps"] == 13.5  # 3.0 + 10.5
        assert result["sample_count"] == 100

    def test_calibrate_insufficient_samples(self):
        mock_tca = MagicMock()
        mock_tca.get_summary.return_value = {"sample_count": 5}

        cal = TCACalibrator(tca_analyzer=mock_tca, min_samples=20)
        result = cal.calibrate()
        assert "error" in result

    def test_calibrate_no_tca(self):
        cal = TCACalibrator(tca_analyzer=None)
        result = cal.calibrate()
        assert "error" in result

    def test_per_strategy_calibration(self):
        mock_tca = MagicMock()
        mock_tca.get_summary.return_value = {
            "is_p50_bps": 3.0, "is_p95_bps": 8.0, "sample_count": 50,
        }
        mock_tca.get_all_strategy_summaries.return_value = {
            "cross_exchange": {"is_p95_bps": 6.0, "sample_count": 30},
            "spot_futures": {"is_p95_bps": 12.0, "sample_count": 20},
        }

        cal = TCACalibrator(tca_analyzer=mock_tca, safety_margin_bps=1.0)
        result = cal.calibrate()
        assert "per_strategy" in result
        assert result["per_strategy"]["cross_exchange"]["recommended_buffer_bps"] == 7.0
        assert result["per_strategy"]["spot_futures"]["recommended_buffer_bps"] == 13.0
