"""Tests for ScheduledTuner (TDD)."""
from __future__ import annotations

import src.tuning.scheduled_tuner as st_mod
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, patch as _patch

from src.tuning.scheduled_tuner import ScheduledTuner
from src.tuning.strategy_backtest import STRATEGY_TYPES


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestScheduledTunerInit:
    def test_default_strategies_equals_strategy_types(self):
        tuner = ScheduledTuner()
        assert tuner.strategies == list(STRATEGY_TYPES)

    def test_custom_strategies_list_is_stored(self):
        custom = ["cross_exchange", "triangular"]
        tuner = ScheduledTuner(strategies=custom)
        assert tuner.strategies == custom

    def test_custom_n_trials_is_stored(self):
        tuner = ScheduledTuner(n_trials=50)
        assert tuner.n_trials == 50


# ---------------------------------------------------------------------------
# _optimize_strategy
# ---------------------------------------------------------------------------


class TestOptimizeStrategy:
    def test_creates_optuna_study_and_calls_optimize(self):
        """_optimize_strategy creates an optuna study and calls optimize."""
        mock_study = MagicMock()
        mock_study.best_params = {"min_spread_bps": 5.0}
        mock_study.best_value = 1.23

        mock_engine = MagicMock()
        mock_engine.run_with_synthetic_data.return_value = MagicMock(sharpe_ratio=1.23)
        mock_wfo = MagicMock()
        mock_wfo._engine = mock_engine

        with (
            patch("src.tuning.scheduled_tuner.optuna") as mock_optuna,
            patch("src.tuning.scheduled_tuner.WalkForwardOptimizer", return_value=mock_wfo),
        ):
            mock_optuna.create_study.return_value = mock_study
            mock_optuna.Trial = MagicMock()

            tuner = ScheduledTuner(n_trials=5)
            result = tuner._optimize_strategy("cross_exchange")

        mock_optuna.create_study.assert_called_once()
        mock_study.optimize.assert_called_once()
        assert "best_params" in result
        assert "best_value" in result

    def test_propagates_exception_on_failure(self):
        """_optimize_strategy propagates exceptions (run_optimization catches them)."""
        with patch(
            "src.tuning.scheduled_tuner.WalkForwardOptimizer",
            side_effect=ValueError("wfo init failed"),
        ):
            tuner = ScheduledTuner(n_trials=5)
            with pytest.raises(ValueError, match="wfo init failed"):
                tuner._optimize_strategy("cross_exchange")


# ---------------------------------------------------------------------------
# run_optimization
# ---------------------------------------------------------------------------


class TestRunOptimization:
    @pytest.mark.asyncio
    async def test_iterates_all_strategies_and_returns_dict(self):
        """run_optimization runs every strategy and keys the dict by strategy name."""
        strategies = ["cross_exchange", "triangular"]
        tuner = ScheduledTuner(strategies=strategies, n_trials=3)

        fake_result = {"best_params": {}, "best_value": 0.5}
        tuner._optimize_strategy = MagicMock(return_value=fake_result)
        tuner._report_results = AsyncMock()

        result = await tuner.run_optimization()

        assert set(result.keys()) == set(strategies)
        assert tuner._optimize_strategy.call_count == len(strategies)

    @pytest.mark.asyncio
    async def test_continues_after_partial_failure(self):
        """run_optimization catches exception from one strategy and continues others."""
        strategies = ["cross_exchange", "triangular", "spot_futures"]
        tuner = ScheduledTuner(strategies=strategies, n_trials=3)

        def side_effect(strategy):
            if strategy == "triangular":
                raise RuntimeError("triangular exploded")
            return {"best_params": {}, "best_value": 1.0}

        tuner._optimize_strategy = MagicMock(side_effect=side_effect)
        tuner._report_results = AsyncMock()

        result = await tuner.run_optimization()

        assert "error" in result["triangular"]
        assert "triangular exploded" in result["triangular"]["error"]
        assert "best_value" in result["cross_exchange"]
        assert "best_value" in result["spot_futures"]


# ---------------------------------------------------------------------------
# _report_results
# ---------------------------------------------------------------------------


class TestReportResults:
    @pytest.mark.asyncio
    async def test_calls_send_alert_with_results(self):
        """_report_results calls TelegramAlerter.send_alert once."""
        tuner = ScheduledTuner()
        tuner.alerter = MagicMock()
        tuner.alerter.send_alert = AsyncMock(return_value=True)

        results = {
            "cross_exchange": {"best_params": {"min_spread_bps": 5.0}, "best_value": 1.5},
        }
        await tuner._report_results(results)

        tuner.alerter.send_alert.assert_awaited_once()
        sent_msg = tuner.alerter.send_alert.call_args[0][0]
        assert "cross_exchange" in sent_msg

    @pytest.mark.asyncio
    async def test_handles_empty_results_without_error(self):
        """_report_results does not raise when results dict is empty."""
        tuner = ScheduledTuner()
        tuner.alerter = MagicMock()
        tuner.alerter.send_alert = AsyncMock(return_value=True)

        await tuner._report_results({})

        tuner.alerter.send_alert.assert_awaited_once()


# ---------------------------------------------------------------------------
# start_scheduler
# ---------------------------------------------------------------------------


class TestStartScheduler:
    def _scheduler_patches(self):
        """Fake APScheduler availability for tests."""
        mock_instance = MagicMock()
        mock_cls = MagicMock(return_value=mock_instance)
        return (
            mock_instance,
            patch.object(st_mod, "_APSCHEDULER_AVAILABLE", True),
            patch.object(st_mod, "AsyncIOScheduler", mock_cls, create=True),
        )

    def test_add_job_is_called(self):
        """start_scheduler registers a job with APScheduler."""
        mock_instance, p_flag, p_cls = self._scheduler_patches()
        with p_flag, p_cls:
            tuner = ScheduledTuner()
            tuner.start_scheduler()

        mock_instance.add_job.assert_called_once()
        mock_instance.start.assert_called_once()

    def test_cron_configured_sunday_hour_2(self):
        """start_scheduler uses cron trigger: day_of_week='sun', hour=2."""
        mock_instance, p_flag, p_cls = self._scheduler_patches()
        with p_flag, p_cls:
            tuner = ScheduledTuner()
            tuner.start_scheduler()

        _, kwargs = mock_instance.add_job.call_args
        assert kwargs.get("day_of_week") == "sun"
        assert kwargs.get("hour") == 2
