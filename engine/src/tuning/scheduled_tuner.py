"""Weekly auto-tuner: runs Optuna optimization per strategy on a cron schedule."""
from __future__ import annotations

import asyncio
import logging
import os

try:
    import optuna
    from optuna.samplers import TPESampler

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    _OPTUNA_AVAILABLE = True
except ImportError:
    _OPTUNA_AVAILABLE = False

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    _APSCHEDULER_AVAILABLE = True
except ImportError:
    _APSCHEDULER_AVAILABLE = False

from src.infra.telegram import TelegramAlerter
from src.tuning.backtest import StrategyParams
from src.tuning.optimizer import TunerConfig, WalkForwardOptimizer
from src.tuning.strategy_backtest import STRATEGY_TYPES

logger = logging.getLogger(__name__)


class ScheduledTuner:
    """매주 자동 파라미터 최적화 스케줄러."""

    def __init__(self, strategies: list[str] | None = None, n_trials: int = 100) -> None:
        self.strategies = strategies or list(STRATEGY_TYPES)
        self.n_trials = n_trials
        self.alerter = TelegramAlerter()

    async def run_optimization(self) -> dict:
        """전략별 독립 Optuna 최적화 실행."""
        if not _OPTUNA_AVAILABLE:
            logger.error("optuna is not installed; skipping optimization")
            return {}

        results: dict = {}
        for strategy in self.strategies:
            try:
                result = self._optimize_strategy(strategy)
                results[strategy] = result
            except Exception as exc:
                logger.error("Optimization failed for %s: %s", strategy, exc)
                results[strategy] = {"error": str(exc)}

        await self._report_results(results)
        return results

    def _optimize_strategy(self, strategy: str) -> dict:
        """단일 전략 Optuna 최적화 (n_trials, TPESampler)."""
        config = TunerConfig(n_trials=self.n_trials)
        optimizer = WalkForwardOptimizer(config=config, strategy_type=strategy)

        study = optuna.create_study(
            direction="maximize",
            sampler=TPESampler(seed=42),
        )

        def objective(trial: optuna.Trial) -> float:
            params = StrategyParams(
                min_spread_bps=trial.suggest_float(
                    "min_spread_bps", *config.min_spread_bps_range
                ),
                max_position_size=trial.suggest_float(
                    "max_position_size", *config.max_position_size_range
                ),
                entry_threshold=trial.suggest_float(
                    "entry_threshold", *config.entry_threshold_range, log=True
                ),
                exit_threshold=trial.suggest_float(
                    "exit_threshold", *config.exit_threshold_range, log=True
                ),
                stop_loss_pct=trial.suggest_float(
                    "stop_loss_pct", *config.stop_loss_pct_range
                ),
            )
            result = optimizer._engine.run_with_synthetic_data(params)
            return result.sharpe_ratio

        study.optimize(objective, n_trials=self.n_trials)

        return {
            "best_params": study.best_params,
            "best_value": study.best_value,
        }

    async def _report_results(self, results: dict) -> None:
        """Telegram으로 최적화 결과 보고."""
        lines = ["📊 *Weekly Auto-Tuning Results*\n"]
        for strategy, data in results.items():
            if "error" in data:
                lines.append(f"❌ `{strategy}`: {data['error']}")
            else:
                val = data.get("best_value", 0.0)
                params = data.get("best_params", {})
                param_str = ", ".join(f"{k}={v:.4g}" for k, v in params.items())
                lines.append(f"✅ `{strategy}`: sharpe={val:.4f} | {param_str}")

        message = "\n".join(lines)
        await self.alerter.send_alert(message, level="INFO")

    def start_scheduler(self) -> None:
        """APScheduler로 매주 일요일 02:00 UTC 스케줄 등록."""
        if not _APSCHEDULER_AVAILABLE:
            logger.error("apscheduler is not installed; cannot start scheduler")
            return

        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            self.run_optimization,
            "cron",
            day_of_week="sun",
            hour=2,
        )
        scheduler.start()
        logger.info("Auto-tuner scheduler started (every Sunday 02:00 UTC)")


if __name__ == "__main__":
    if not _OPTUNA_AVAILABLE:
        raise SystemExit("optuna is required: pip install optuna")
    if not _APSCHEDULER_AVAILABLE:
        raise SystemExit("apscheduler is required: pip install apscheduler")

    n_trials = int(os.environ.get("TUNER_N_TRIALS", "100"))
    tuner = ScheduledTuner(n_trials=n_trials)
    tuner.start_scheduler()

    loop = asyncio.get_event_loop()
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        logger.info("Auto-tuner shutting down")
