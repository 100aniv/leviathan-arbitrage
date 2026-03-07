"""Shadow mode runner — validate optimized parameters before live application.

Registers optimized parameters as shadow strategies that receive signals
but do not emit TradeRequests. After the shadow period, compares shadow
performance against live to decide: APPLY, MONITOR, or REJECT.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from src.tuning.backtest import BacktestEngine, BacktestResult, StrategyParams
from src.tuning.evaluator import EvaluationReport, OutOfSampleEvaluator
from src.tuning.file_data_loader import FileDataLoader, generate_synthetic_ohlcv
from src.tuning.param_bridge import params_to_strategy_config

logger = logging.getLogger(__name__)


@dataclass
class ShadowResult:
    """Result of a shadow mode evaluation."""

    strategy_id: str
    shadow_params: StrategyParams
    baseline_result: BacktestResult
    shadow_result: BacktestResult
    evaluation: EvaluationReport
    config_to_apply: dict
    elapsed_seconds: float = 0.0


class ShadowRunner:
    """Runs shadow mode evaluation for optimized parameters.

    Workflow:
    1. Run baseline backtest with current parameters
    2. Run shadow backtest with optimized parameters
    3. Compare using OutOfSampleEvaluator
    4. Return recommendation: APPLY, MONITOR, or REJECT
    """

    def __init__(
        self,
        engine: BacktestEngine | None = None,
        evaluator: OutOfSampleEvaluator | None = None,
    ) -> None:
        self._engine = engine or BacktestEngine(initial_capital=70.0)
        self._evaluator = evaluator or OutOfSampleEvaluator()
        self._loader = FileDataLoader()

    def evaluate(
        self,
        strategy_id: str,
        strategy_type: str,
        baseline_params: StrategyParams,
        shadow_params: StrategyParams,
        data_source: str = "synthetic",
        num_candles: int = 2000,
    ) -> ShadowResult:
        """Run shadow evaluation comparing baseline vs optimized params.

        Args:
            strategy_id: ID of the strategy being evaluated.
            strategy_type: Strategy type for config mapping.
            baseline_params: Current live parameters.
            shadow_params: Optimized parameters to evaluate.
            data_source: "synthetic" or path to CSV.
            num_candles: Number of candles for synthetic data.

        Returns:
            ShadowResult with evaluation and recommendation.
        """
        start = time.time()

        # Load data
        if data_source == "synthetic":
            ohlcv = generate_synthetic_ohlcv(num_candles=num_candles)
        else:
            ohlcv = self._loader.load(data_source)

        # Run baseline
        baseline_result = self._engine.run(baseline_params, ohlcv)

        # Run shadow
        shadow_result = self._engine.run(shadow_params, ohlcv)

        # Evaluate
        evaluation = self._evaluator.evaluate(shadow_result, baseline_result)

        # Build config to apply
        config = params_to_strategy_config(shadow_params, strategy_type)

        elapsed = time.time() - start

        result = ShadowResult(
            strategy_id=strategy_id,
            shadow_params=shadow_params,
            baseline_result=baseline_result,
            shadow_result=shadow_result,
            evaluation=evaluation,
            config_to_apply=config,
            elapsed_seconds=elapsed,
        )

        logger.info(
            "Shadow evaluation for %s: baseline_pnl=%.4f shadow_pnl=%.4f "
            "recommendation=%s",
            strategy_id,
            baseline_result.total_pnl,
            shadow_result.total_pnl,
            evaluation.recommendation,
        )

        return result

    def evaluate_and_decide(
        self,
        strategy_id: str,
        strategy_type: str,
        baseline_params: StrategyParams,
        shadow_params: StrategyParams,
        data_source: str = "synthetic",
        num_candles: int = 2000,
    ) -> tuple[str, ShadowResult]:
        """Evaluate and return (decision, result) where decision is APPLY/MONITOR/REJECT."""
        result = self.evaluate(
            strategy_id=strategy_id,
            strategy_type=strategy_type,
            baseline_params=baseline_params,
            shadow_params=shadow_params,
            data_source=data_source,
            num_candles=num_candles,
        )

        # Extract decision from recommendation string
        rec = result.evaluation.recommendation
        if rec.startswith("APPLY"):
            decision = "APPLY"
        elif rec.startswith("MONITOR"):
            decision = "MONITOR"
        else:
            decision = "REJECT"

        return decision, result

    def print_report(self, result: ShadowResult) -> None:
        """Log human-readable shadow evaluation report."""
        br = result.baseline_result
        sr = result.shadow_result
        ev = result.evaluation

        lines = [
            f"Shadow Mode Evaluation: {result.strategy_id}",
            f"Baseline: PnL=${br.total_pnl:.4f} Sharpe={br.sharpe_ratio:.4f} "
            f"WinRate={br.win_rate * 100:.1f}% Trades={br.num_trades} MDD={br.max_drawdown * 100:.2f}%",
            f"Shadow:   PnL=${sr.total_pnl:.4f} Sharpe={sr.sharpe_ratio:.4f} "
            f"WinRate={sr.win_rate * 100:.1f}% Trades={sr.num_trades} MDD={sr.max_drawdown * 100:.2f}%",
            f"Eval: Variance={ev.sim_real_variance_pct:.1f}% T={ev.t_statistic:.4f} "
            f"P={ev.p_value:.4f} Significant={ev.is_significant} VarianceOK={ev.passes_variance_check}",
            f">>> {ev.recommendation}",
            f"Sharpe improved={sr.sharpe_ratio > br.sharpe_ratio} "
            f"PnL improved={sr.total_pnl > br.total_pnl} Time={result.elapsed_seconds:.2f}s",
        ]
        logger.info("shadow_report\n%s", "\n".join(lines))
