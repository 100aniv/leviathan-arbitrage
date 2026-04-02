"""Out-of-sample performance evaluator with Sim-Real variance and t-test."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats

from src.tuning.backtest import TuningBacktestResult


@dataclass
class EvaluationReport:
    """Summary of shadow-mode vs live trading comparison."""

    sim_total_pnl: float
    live_total_pnl: float
    sim_real_variance_pct: float  # |sim - live| / |live| * 100; target: < 5%
    t_statistic: float
    p_value: float
    is_significant: bool  # p < 0.05
    passes_variance_check: bool  # sim_real_variance_pct < 5%
    recommendation: str


class OutOfSampleEvaluator:
    """
    Compare shadow-mode backtesting results vs live trading results.

    Data contract: inputs must be TuningBacktestResult objects sourced from
    mode='backtest' execution records only — WFA must not mix live/paper data
    (US-376).

    Metrics:
    - Sim-Real Variance: |sim_pnl - live_pnl| / |live_pnl| * 100 (target < 5%)
    - Welch's t-test on return distributions (two-sided, unequal variance)
    """

    SIM_REAL_VARIANCE_TARGET_PCT: float = 5.0
    SIGNIFICANCE_LEVEL: float = 0.05
    MIN_SAMPLES: int = 10

    def evaluate(
        self,
        sim_result: TuningBacktestResult,
        live_result: TuningBacktestResult,
    ) -> EvaluationReport:
        """Compare simulation vs live trading performance."""
        sim_pnl = sim_result.total_pnl
        live_pnl = live_result.total_pnl

        # Sim-Real Variance
        denominator = abs(live_pnl) if abs(live_pnl) > 1e-8 else 1e-8
        variance_pct = abs(sim_pnl - live_pnl) / denominator * 100.0

        # Welch's t-test on returns
        sim_r = np.array(sim_result.returns, dtype=float)
        live_r = np.array(live_result.returns, dtype=float)

        t_stat, p_val = 0.0, 1.0
        is_significant = False
        if len(sim_r) >= self.MIN_SAMPLES and len(live_r) >= self.MIN_SAMPLES:
            t_stat, p_val = stats.ttest_ind(sim_r, live_r, equal_var=False)
            t_stat = float(t_stat)
            p_val = float(p_val)
            is_significant = p_val < self.SIGNIFICANCE_LEVEL

        passes_variance = variance_pct < self.SIM_REAL_VARIANCE_TARGET_PCT

        if passes_variance and not is_significant:
            rec = "APPLY: sim-real variance < 5% and returns not significantly different"
        elif passes_variance and is_significant:
            rec = "MONITOR: variance within bounds but return distributions differ significantly"
        else:
            rec = f"REJECT: sim-real variance {variance_pct:.1f}% exceeds 5% target"

        return EvaluationReport(
            sim_total_pnl=sim_pnl,
            live_total_pnl=live_pnl,
            sim_real_variance_pct=variance_pct,
            t_statistic=t_stat,
            p_value=p_val,
            is_significant=is_significant,
            passes_variance_check=passes_variance,
            recommendation=rec,
        )

    def compare_sharpe(self, baseline: TuningBacktestResult, candidate: TuningBacktestResult) -> bool:
        """Return True if candidate Sharpe ratio strictly exceeds baseline."""
        return candidate.sharpe_ratio > baseline.sharpe_ratio
