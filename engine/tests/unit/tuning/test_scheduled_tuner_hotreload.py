"""US-179: ScheduledTuner hot-reload — atomic write and JSON validation."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tuning.scheduled_tuner import ScheduledTuner
from src.tuning.shadow_runner import ShadowRunner


# ---------------------------------------------------------------------------
# strategy_params.json write after optimization
# ---------------------------------------------------------------------------


class TestStrategyParamsWrite:
    def test_apply_params_writes_strategy_params_json(self, tmp_path):
        """_apply_params writes updated params to strategy_params.json."""
        runner = ShadowRunner()
        runner._params_path = tmp_path / "strategy_params.json"

        mock_result = MagicMock()
        mock_result.config_to_apply = {"min_spread_bps": 7.0, "max_position_usdt": 200.0}

        runner._apply_params("cross_exchange", mock_result)

        assert runner._params_path.exists(), "strategy_params.json must be created"
        data = json.loads(runner._params_path.read_text())
        assert "cross_exchange" in data

    def test_apply_params_preserves_other_strategies(self, tmp_path):
        """_apply_params does not remove other strategies from JSON."""
        params_path = tmp_path / "strategy_params.json"
        params_path.write_text(json.dumps({
            "triangular": {"min_profit_bps": 5.0},
            "cross_exchange": {"min_spread_bps": 3.0},
        }))

        runner = ShadowRunner()
        runner._params_path = params_path

        mock_result = MagicMock()
        mock_result.config_to_apply = {"min_spread_bps": 8.0}

        runner._apply_params("cross_exchange", mock_result)

        data = json.loads(params_path.read_text())
        assert "triangular" in data, "other strategy data must be preserved"
        assert data["triangular"]["min_profit_bps"] == 5.0

    def test_apply_params_updates_existing_strategy(self, tmp_path):
        """_apply_params overwrites existing strategy params with new values."""
        params_path = tmp_path / "strategy_params.json"
        params_path.write_text(json.dumps({
            "cross_exchange": {"min_spread_bps": 3.0},
        }))

        runner = ShadowRunner()
        runner._params_path = params_path

        mock_result = MagicMock()
        mock_result.config_to_apply = {"min_spread_bps": 8.0}

        runner._apply_params("cross_exchange", mock_result)

        data = json.loads(params_path.read_text())
        assert data["cross_exchange"]["min_spread_bps"] == 8.0


# ---------------------------------------------------------------------------
# Atomic write (temp → rename)
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    def test_write_is_valid_json_after_apply_params(self, tmp_path):
        """File written by _apply_params is valid JSON."""
        runner = ShadowRunner()
        runner._params_path = tmp_path / "strategy_params.json"

        mock_result = MagicMock()
        mock_result.config_to_apply = {"min_spread_bps": 5.0}

        runner._apply_params("cross_exchange", mock_result)

        content = runner._params_path.read_text()
        parsed = json.loads(content)
        assert isinstance(parsed, dict)

    def test_apply_params_does_not_corrupt_existing_file_on_failure(self, tmp_path):
        """On error, existing strategy_params.json is not corrupted."""
        params_path = tmp_path / "strategy_params.json"
        original_content = json.dumps({"cross_exchange": {"min_spread_bps": 3.0}})
        params_path.write_text(original_content)

        runner = ShadowRunner()
        runner._params_path = params_path

        # Simulate failure: config_to_apply raises AttributeError
        bad_result = MagicMock(spec=[])  # no config_to_apply attribute

        try:
            runner._apply_params("cross_exchange", bad_result)
        except Exception:
            pass

        # File should still be readable as valid JSON
        current_content = params_path.read_text()
        parsed = json.loads(current_content)
        assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# run_optimization writes params via ShadowRunner
# ---------------------------------------------------------------------------


class TestRunOptimizationHotReload:
    @pytest.mark.asyncio
    async def test_run_optimization_invokes_shadow_runner_apply(self):
        """run_optimization calls ShadowRunner.apply_decision after optimization."""
        tuner = ScheduledTuner(strategies=["cross_exchange"], n_trials=1)
        tuner._optimize_strategy = MagicMock(
            return_value={"best_params": {"min_spread_bps": 5.0}, "best_value": 1.2}
        )
        tuner._report_results = AsyncMock()
        # Isolate from on-disk params to prevent Devil's Advocate rollback interference
        tuner._load_current_params = MagicMock(return_value=None)

        mock_runner = MagicMock()
        mock_runner.apply_decision = AsyncMock(return_value=("APPLY", MagicMock()))

        with patch("src.tuning.scheduled_tuner.ShadowRunner", return_value=mock_runner):
            await tuner.run_optimization()

        mock_runner.apply_decision.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_optimization_skips_negative_wfe_strategy(self):
        """Strategies with negative best_value are not applied."""
        tuner = ScheduledTuner(strategies=["cross_exchange"], n_trials=1)
        tuner._optimize_strategy = MagicMock(
            return_value={"best_params": {}, "best_value": -0.5}  # negative WFE
        )
        tuner._report_results = AsyncMock()
        # Isolate from on-disk params
        tuner._load_current_params = MagicMock(return_value=None)

        apply_called = []

        async def mock_apply(*args, **kwargs):
            apply_called.append(True)
            return ("SKIP", MagicMock())

        mock_runner = MagicMock()
        mock_runner.apply_decision = AsyncMock(side_effect=mock_apply)

        with patch("src.tuning.scheduled_tuner.ShadowRunner", return_value=mock_runner):
            result = await tuner.run_optimization()

        # Status should not be READY for negative WFE
        assert result.get("cross_exchange", {}).get("status") != "READY"
