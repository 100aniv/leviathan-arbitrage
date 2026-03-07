"""Coverage tests for shadow_runner.py — targeting 95%+ coverage."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tuning.backtest import BacktestResult, StrategyParams
from src.tuning.evaluator import EvaluationReport
from src.tuning.shadow_runner import ShadowResult, ShadowRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backtest_result(pnl: float = 1.0, sharpe: float = 1.5) -> BacktestResult:
    return BacktestResult(
        total_pnl=pnl,
        sharpe_ratio=sharpe,
        max_drawdown=0.05,
        win_rate=0.6,
        num_trades=10,
        returns=[0.01, 0.02, -0.005, 0.015],
    )


def _make_evaluation(recommendation: str = "APPLY — params improved") -> EvaluationReport:
    return EvaluationReport(
        sim_total_pnl=1.2,
        live_total_pnl=1.0,
        sim_real_variance_pct=3.0,
        t_statistic=2.5,
        p_value=0.03,
        is_significant=True,
        passes_variance_check=True,
        recommendation=recommendation,
    )


def _make_runner_with_mocks() -> tuple[ShadowRunner, MagicMock, MagicMock]:
    engine = MagicMock()
    evaluator = MagicMock()
    runner = ShadowRunner(engine=engine, evaluator=evaluator)
    return runner, engine, evaluator


# ---------------------------------------------------------------------------
# ShadowRunner initialization
# ---------------------------------------------------------------------------


class TestShadowRunnerInit:
    def test_default_init_creates_all_components(self):
        runner = ShadowRunner()
        assert runner._engine is not None
        assert runner._evaluator is not None
        assert runner._loader is not None

    def test_custom_engine_and_evaluator_injected(self):
        engine = MagicMock()
        evaluator = MagicMock()
        runner = ShadowRunner(engine=engine, evaluator=evaluator)
        assert runner._engine is engine
        assert runner._evaluator is evaluator


# ---------------------------------------------------------------------------
# evaluate()
# ---------------------------------------------------------------------------


class TestShadowRunnerEvaluate:
    def test_evaluate_synthetic_returns_shadow_result(self):
        runner, engine, evaluator = _make_runner_with_mocks()
        baseline = _make_backtest_result(pnl=1.0)
        shadow = _make_backtest_result(pnl=1.5)
        evaluation = _make_evaluation("APPLY — improved")
        engine.run.side_effect = [baseline, shadow]
        evaluator.evaluate.return_value = evaluation

        params = StrategyParams()
        result = runner.evaluate(
            strategy_id="test_strat",
            strategy_type="cross_exchange",
            baseline_params=params,
            shadow_params=params,
            data_source="synthetic",
        )

        assert isinstance(result, ShadowResult)
        assert result.strategy_id == "test_strat"
        assert result.baseline_result is baseline
        assert result.shadow_result is shadow
        assert result.evaluation is evaluation
        assert result.elapsed_seconds >= 0

    def test_evaluate_with_csv_path_uses_loader(self):
        # Line 81: data_source != "synthetic" → self._loader.load(data_source)
        runner, engine, evaluator = _make_runner_with_mocks()
        baseline = _make_backtest_result(pnl=1.0)
        shadow = _make_backtest_result(pnl=1.2)
        evaluation = _make_evaluation("MONITOR — marginal")
        engine.run.side_effect = [baseline, shadow]
        evaluator.evaluate.return_value = evaluation

        mock_ohlcv = MagicMock()
        runner._loader = MagicMock()
        runner._loader.load.return_value = mock_ohlcv

        params = StrategyParams()
        result = runner.evaluate(
            strategy_id="strat_csv",
            strategy_type="triangular",
            baseline_params=params,
            shadow_params=params,
            data_source="/path/to/data.csv",
        )

        runner._loader.load.assert_called_once_with("/path/to/data.csv")
        assert isinstance(result, ShadowResult)

    def test_evaluate_engine_called_twice(self):
        runner, engine, evaluator = _make_runner_with_mocks()
        baseline = _make_backtest_result()
        shadow = _make_backtest_result()
        engine.run.side_effect = [baseline, shadow]
        evaluator.evaluate.return_value = _make_evaluation()

        params = StrategyParams()
        runner.evaluate("s1", "cross_exchange", params, params)

        assert engine.run.call_count == 2

    def test_evaluate_builds_strategy_config(self):
        runner, engine, evaluator = _make_runner_with_mocks()
        engine.run.side_effect = [_make_backtest_result(), _make_backtest_result()]
        evaluator.evaluate.return_value = _make_evaluation()

        params = StrategyParams(min_spread_bps=10.0)
        result = runner.evaluate(
            strategy_id="s1",
            strategy_type="spot_futures",
            baseline_params=params,
            shadow_params=params,
        )

        assert isinstance(result.config_to_apply, dict)
        assert "min_basis_bps" in result.config_to_apply  # spot_futures mapping

    def test_evaluate_with_custom_num_candles(self):
        runner, engine, evaluator = _make_runner_with_mocks()
        engine.run.side_effect = [_make_backtest_result(), _make_backtest_result()]
        evaluator.evaluate.return_value = _make_evaluation()

        params = StrategyParams()
        result = runner.evaluate(
            strategy_id="s1",
            strategy_type="cross_exchange",
            baseline_params=params,
            shadow_params=params,
            num_candles=500,
        )

        assert isinstance(result, ShadowResult)


# ---------------------------------------------------------------------------
# evaluate_and_decide()
# ---------------------------------------------------------------------------


class TestEvaluateAndDecide:
    def _setup_runner(self, recommendation: str) -> tuple[ShadowRunner, StrategyParams]:
        runner, engine, evaluator = _make_runner_with_mocks()
        baseline = _make_backtest_result()
        shadow = _make_backtest_result()
        evaluation = _make_evaluation(recommendation)
        engine.run.side_effect = [baseline, shadow]
        evaluator.evaluate.return_value = evaluation
        return runner, StrategyParams()

    def test_decision_apply_when_recommendation_starts_with_apply(self):
        # Lines 139-140
        runner, params = self._setup_runner("APPLY — sharpe improved 12%")
        decision, result = runner.evaluate_and_decide(
            "s1", "cross_exchange", params, params
        )
        assert decision == "APPLY"

    def test_decision_monitor_when_recommendation_starts_with_monitor(self):
        # Lines 141-142
        runner, params = self._setup_runner("MONITOR — marginal improvement, more data needed")
        decision, result = runner.evaluate_and_decide(
            "s1", "cross_exchange", params, params
        )
        assert decision == "MONITOR"

    def test_decision_reject_for_other_recommendations(self):
        # Lines 143-144
        runner, params = self._setup_runner("REJECT — shadow underperformed baseline")
        decision, result = runner.evaluate_and_decide(
            "s1", "cross_exchange", params, params
        )
        assert decision == "REJECT"

    def test_returns_shadow_result_alongside_decision(self):
        runner, params = self._setup_runner("APPLY — strong improvement")
        decision, result = runner.evaluate_and_decide(
            "s1", "cross_exchange", params, params
        )
        assert isinstance(result, ShadowResult)
        assert result.strategy_id == "s1"


# ---------------------------------------------------------------------------
# print_report()
# ---------------------------------------------------------------------------


class TestPrintReport:
    def test_print_report_calls_logger_info(self):
        # Lines 148-166
        runner = ShadowRunner(engine=MagicMock(), evaluator=MagicMock())
        baseline = _make_backtest_result(pnl=1.0, sharpe=1.0)
        shadow = _make_backtest_result(pnl=1.5, sharpe=1.8)
        evaluation = _make_evaluation("APPLY — better sharpe")

        result = ShadowResult(
            strategy_id="test_strat",
            shadow_params=StrategyParams(),
            baseline_result=baseline,
            shadow_result=shadow,
            evaluation=evaluation,
            config_to_apply={"min_spread_bps": 5.0},
            elapsed_seconds=0.5,
        )

        with patch("src.tuning.shadow_runner.logger") as mock_logger:
            runner.print_report(result)
            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args[0]
            assert "shadow_report" in call_args[0]

    def test_print_report_includes_strategy_id_in_output(self):
        runner = ShadowRunner(engine=MagicMock(), evaluator=MagicMock())
        baseline = _make_backtest_result(pnl=0.5, sharpe=0.8)
        shadow = _make_backtest_result(pnl=1.2, sharpe=1.5)
        evaluation = _make_evaluation("MONITOR — watch")

        result = ShadowResult(
            strategy_id="funding_rate_v2",
            shadow_params=StrategyParams(),
            baseline_result=baseline,
            shadow_result=shadow,
            evaluation=evaluation,
            config_to_apply={},
            elapsed_seconds=1.2,
        )

        with patch("src.tuning.shadow_runner.logger") as mock_logger:
            runner.print_report(result)
            logged_text = mock_logger.info.call_args[0][1]
            assert "funding_rate_v2" in logged_text

    def test_print_report_shows_recommendation(self):
        runner = ShadowRunner(engine=MagicMock(), evaluator=MagicMock())
        baseline = _make_backtest_result()
        shadow = _make_backtest_result()
        evaluation = _make_evaluation("REJECT — failed variance check")

        result = ShadowResult(
            strategy_id="latency_arb_v1",
            shadow_params=StrategyParams(),
            baseline_result=baseline,
            shadow_result=shadow,
            evaluation=evaluation,
            config_to_apply={},
        )

        with patch("src.tuning.shadow_runner.logger") as mock_logger:
            runner.print_report(result)
            logged_text = mock_logger.info.call_args[0][1]
            assert "REJECT" in logged_text


# ---------------------------------------------------------------------------
# ShadowResult dataclass
# ---------------------------------------------------------------------------


class TestShadowResult:
    def test_shadow_result_default_elapsed(self):
        result = ShadowResult(
            strategy_id="s1",
            shadow_params=StrategyParams(),
            baseline_result=_make_backtest_result(),
            shadow_result=_make_backtest_result(),
            evaluation=_make_evaluation(),
            config_to_apply={},
        )
        assert result.elapsed_seconds == 0.0

    def test_shadow_result_fields_accessible(self):
        params = StrategyParams(min_spread_bps=7.0)
        result = ShadowResult(
            strategy_id="test",
            shadow_params=params,
            baseline_result=_make_backtest_result(pnl=2.0),
            shadow_result=_make_backtest_result(pnl=3.0),
            evaluation=_make_evaluation(),
            config_to_apply={"key": "val"},
            elapsed_seconds=0.42,
        )
        assert result.strategy_id == "test"
        assert result.shadow_params is params
        assert result.config_to_apply == {"key": "val"}
        assert result.elapsed_seconds == pytest.approx(0.42)
