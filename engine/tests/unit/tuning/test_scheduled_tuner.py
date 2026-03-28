"""Tests for ScheduledTuner (TDD)."""
from __future__ import annotations

import json

import src.tuning.scheduled_tuner as st_mod
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, patch as _patch

from src.tuning.scheduled_tuner import ScheduledTuner
from src.tuning.shadow_runner import ShadowRunner
from src.tuning.strategy_backtest import STRATEGY_TYPES


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestScheduledTunerInit:
    def test_default_strategies_excludes_cex_dex_only(self):
        """US-197: statistical_arb removed from EXCLUDED, only cex_dex remains."""
        with patch.object(ScheduledTuner, '_load_activation', return_value=None):
            tuner = ScheduledTuner()
        assert "cex_dex" not in tuner.strategies
        assert "statistical_arb" in tuner.strategies  # US-197: no longer excluded
        from src.tuning.scheduled_tuner import EXCLUDED
        expected = [s for s in STRATEGY_TYPES if s not in EXCLUDED]
        assert tuner.strategies == expected

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
    def test_creates_optuna_study_and_calls_optimize(self, tmp_path):
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
            tuner._params_path = tmp_path / "strategy_params.json"
            tuner._load_current_params = MagicMock(return_value=None)
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
        tuner._load_current_params = MagicMock(return_value=None)

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
        tuner._load_current_params = MagicMock(return_value=None)

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
        """start_scheduler registers jobs with APScheduler (weekly cron + initial date)."""
        mock_instance, p_flag, p_cls = self._scheduler_patches()
        with p_flag, p_cls:
            tuner = ScheduledTuner()
            tuner.start_scheduler()

        assert mock_instance.add_job.call_count >= 2
        mock_instance.start.assert_called_once()

    def test_cron_configured_sunday_hour_2(self):
        """start_scheduler uses cron trigger: day_of_week='sun', hour=2."""
        mock_instance, p_flag, p_cls = self._scheduler_patches()
        with p_flag, p_cls:
            tuner = ScheduledTuner()
            tuner.start_scheduler()

        # add_job is called twice: once for weekly cron, once for initial date trigger
        assert mock_instance.add_job.call_count >= 2
        # Find the cron call (first call)
        cron_call = mock_instance.add_job.call_args_list[0]
        _, kwargs = cron_call
        assert kwargs.get("day_of_week") == "sun"
        assert kwargs.get("hour") == 2


# ---------------------------------------------------------------------------
# US-068: STRATEGY_TYPES expansion + EXCLUDED constants
# ---------------------------------------------------------------------------


class TestUS068StrategyTypes:
    def test_latency_arb_not_in_strategy_types(self):
        """US-194: latency_arb merged into cross_exchange, removed from STRATEGY_TYPES."""
        assert "latency_arb" not in STRATEGY_TYPES

    def test_cex_dex_always_excluded(self):
        """Module-level EXCLUDED set contains 'cex_dex'."""
        assert hasattr(st_mod, "EXCLUDED"), "scheduled_tuner must define EXCLUDED set"
        assert "cex_dex" in st_mod.EXCLUDED

    def test_stat_arb_not_excluded(self):
        """US-197: statistical_arb removed from EXCLUDED (cross-asset redesign)."""
        assert hasattr(st_mod, "EXCLUDED"), "scheduled_tuner must define EXCLUDED set"
        assert "statistical_arb" not in st_mod.EXCLUDED


# ---------------------------------------------------------------------------
# US-068: activation.json filter
# ---------------------------------------------------------------------------


class TestActivationFilter:
    @pytest.mark.asyncio
    async def test_activation_filter_excludes_disabled(self, tmp_path):
        """run_optimization skips strategies marked False in activation.json."""
        activation_file = tmp_path / "activation.json"
        activation_file.write_text(
            json.dumps({"cross_exchange": True, "triangular": False})
        )

        tuner = ScheduledTuner(
            strategies=["cross_exchange", "triangular"],
            activation_path=activation_file,
        )
        tuner._optimize_strategy = MagicMock(
            return_value={"best_params": {}, "best_value": 1.0}
        )
        tuner._report_results = AsyncMock()
        tuner._load_current_params = MagicMock(return_value=None)

        result = await tuner.run_optimization()

        assert "triangular" not in result, "disabled strategy must not appear in results"
        assert "cross_exchange" in result


# ---------------------------------------------------------------------------
# US-068: TimescaleDB → synthetic fallback
# ---------------------------------------------------------------------------


class TestTimescaleDBFallback:
    def test_timescaledb_fallback_to_synthetic(self, tmp_path):
        """_optimize_strategy returns valid result even when DataLoader raises."""
        mock_study = MagicMock()
        mock_study.best_params = {"min_spread_bps": 5.0}
        mock_study.best_value = 0.8

        mock_wfo = MagicMock()
        mock_wfo._engine.run_with_synthetic_data.return_value = MagicMock(
            sharpe_ratio=0.8
        )

        with (
            patch("src.tuning.scheduled_tuner.optuna") as mock_optuna,
            patch(
                "src.tuning.scheduled_tuner.WalkForwardOptimizer",
                return_value=mock_wfo,
            ),
            patch(
                "src.tuning.scheduled_tuner.DataLoader",
                side_effect=ConnectionError("DB unreachable"),
            ),
        ):
            mock_optuna.create_study.return_value = mock_study
            tuner = ScheduledTuner(n_trials=3)
            tuner._params_path = tmp_path / "strategy_params.json"
            tuner._load_current_params = MagicMock(return_value=None)
            result = tuner._optimize_strategy("cross_exchange")

        assert "best_params" in result
        assert "best_value" in result


# ---------------------------------------------------------------------------
# US-068: ShadowRunner auto-apply integration
# ---------------------------------------------------------------------------


class TestShadowRunnerAutoApply:
    @pytest.mark.asyncio
    async def test_shadow_runner_auto_apply(self):
        """run_optimization calls ShadowRunner.apply_decision after Optuna."""
        tuner = ScheduledTuner(strategies=["cross_exchange"], n_trials=3)
        tuner._optimize_strategy = MagicMock(
            return_value={"best_params": {"min_spread_bps": 5.0}, "best_value": 1.5}
        )
        tuner._report_results = AsyncMock()
        tuner._load_current_params = MagicMock(return_value=None)

        mock_runner = MagicMock()
        mock_runner.apply_decision = AsyncMock(return_value=("APPLY", MagicMock()))

        with patch("src.tuning.scheduled_tuner.ShadowRunner", return_value=mock_runner):
            await tuner.run_optimization()

        mock_runner.apply_decision.assert_awaited_once()

    def test_strategy_params_json_updated(self, tmp_path):
        """_apply_params writes config_to_apply for the strategy to JSON."""
        runner = ShadowRunner()
        runner._params_path = tmp_path / "strategy_params.json"

        mock_result = MagicMock()
        mock_result.config_to_apply = {"min_spread_bps": 5.0, "max_position_usdt": 100.0}

        runner._apply_params("cross_exchange", mock_result)

        data = json.loads(runner._params_path.read_text())
        assert "cross_exchange" in data
        assert data["cross_exchange"] == {
            "min_spread_bps": 5.0,
            "max_position_usdt": 100.0,
        }


# ---------------------------------------------------------------------------
# US-068: WFE > 0 filter → READY status
# ---------------------------------------------------------------------------


class TestWFEPositiveFilter:
    @pytest.mark.asyncio
    async def test_wfe_positive_filter(self):
        """run_optimization sets status='READY' only for strategies with best_value > 0."""
        tuner = ScheduledTuner(strategies=["cross_exchange", "triangular"], n_trials=3)

        def fake_optimize(strategy: str) -> dict:
            return {
                "best_params": {},
                "best_value": 1.5 if strategy == "cross_exchange" else -0.3,
            }

        tuner._optimize_strategy = MagicMock(side_effect=fake_optimize)
        tuner._report_results = AsyncMock()
        tuner._load_current_params = MagicMock(return_value=None)

        result = await tuner.run_optimization()

        assert result["cross_exchange"].get("status") == "READY", (
            "positive WFE must yield status='READY'"
        )
        assert result["triangular"].get("status") != "READY", (
            "negative WFE must not yield status='READY'"
        )
