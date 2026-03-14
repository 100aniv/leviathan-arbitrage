"""Weekly auto-tuner: runs Optuna optimization per strategy on a cron schedule."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

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

# Module-level imports for test patching
try:
    from src.tuning.data_loader import DataLoader
except ImportError:
    DataLoader = None  # type: ignore[assignment,misc]

try:
    from src.tuning.shadow_runner import ShadowRunner
except ImportError:
    ShadowRunner = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# Strategies permanently excluded regardless of activation file
EXCLUDED = {"cex_dex", "statistical_arb"}


class ScheduledTuner:
    """매주 자동 파라미터 최적화 스케줄러."""

    def __init__(
        self,
        strategies: list[str] | None = None,
        n_trials: int = 100,
        data_source: str | None = None,
        activation_path: Path | str | None = None,
    ) -> None:
        self.n_trials = n_trials
        self.data_source = data_source or os.environ.get("TUNER_DATA_SOURCE", "synthetic")
        self.alerter = TelegramAlerter()
        self._scheduler = None

        # Determine base strategy list
        base = strategies if strategies is not None else list(STRATEGY_TYPES)

        # Apply activation filter (explicit path takes precedence over env var)
        resolved_path: Path | None = None
        if activation_path is not None:
            resolved_path = Path(activation_path)
        else:
            env_path = Path(
                os.environ.get("STRATEGY_ACTIVATION_PATH", "config/strategy_activation.json")
            )
            if env_path.exists():
                resolved_path = env_path

        if resolved_path is not None:
            active = self._load_activation(resolved_path)
            if active is not None:
                base = [s for s in base if s in active]

        # Always remove EXCLUDED
        self.strategies = [s for s in base if s not in EXCLUDED]

    def _load_activation(self, path: Path) -> list[str] | None:
        """Load active strategies from strategy_activation.json (US-067 output).

        Returns list of active strategy names, or None if file not found/invalid.
        """
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return [s for s, v in data.items() if v is True or (isinstance(v, dict) and v.get("active", False))]
        except Exception as exc:
            logger.warning("Failed to load activation file %s: %s", path, exc)
            return None

    async def run_optimization(self) -> dict:
        """전략별 독립 Optuna 최적화 실행."""
        if not _OPTUNA_AVAILABLE:
            logger.error("optuna is not installed; skipping optimization")
            return {}

        results: dict = {}
        for strategy in self.strategies:
            try:
                result = self._optimize_strategy(strategy)
                # WFE gate: mark READY for positive best_value
                if result.get("best_value", 0.0) > 0:
                    result["status"] = "READY"
                results[strategy] = result
            except Exception as exc:
                logger.error("Optimization failed for %s: %s", strategy, exc)
                results[strategy] = {"error": str(exc)}

        await self._apply_shadow_decisions(results)
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
            if self.data_source == "timescaledb":
                result = self._run_with_timescaledb(optimizer, params, strategy)
            else:
                result = optimizer._engine.run_with_synthetic_data(params)
            return result.sharpe_ratio

        study.optimize(objective, n_trials=self.n_trials)

        return {
            "best_params": study.best_params,
            "best_value": study.best_value,
        }

    def _run_with_timescaledb(
        self, optimizer: WalkForwardOptimizer, params: StrategyParams, strategy: str
    ):
        """Load real data from TimescaleDB and run optimization."""
        from concurrent.futures import ThreadPoolExecutor

        dsn = os.environ.get("DATABASE_URL", "")
        if not dsn:
            logger.warning("DATABASE_URL not set, falling back to synthetic")
            return optimizer._engine.run_with_synthetic_data(params)

        def _load_sync():
            import asyncio
            loop = asyncio.new_event_loop()
            try:
                async def _do():
                    async with DataLoader(dsn=dsn) as loader:
                        return await loader.load_execution_log_as_ohlcv(days=30)
                return loop.run_until_complete(_do())
            finally:
                loop.close()

        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(_load_sync)
                ohlcv = future.result(timeout=60)

            if ohlcv.length < 10:
                logger.warning(
                    "Insufficient data (%d rows), falling back to synthetic", ohlcv.length
                )
                return optimizer._engine.run_with_synthetic_data(params)

            return optimizer._engine.run(params, ohlcv)
        except Exception as exc:
            logger.error("TimescaleDB loader failed: %s, falling back to synthetic", exc)
            return optimizer._engine.run_with_synthetic_data(params)

    async def _apply_shadow_decisions(self, results: dict) -> None:
        """Optuna 결과를 ShadowRunner.apply_decision으로 검증 후 APPLY/REJECT 결정."""
        if ShadowRunner is None:
            return
        runner = ShadowRunner()
        for strategy, data in results.items():
            if "error" in data or not data.get("best_params"):
                continue
            try:
                bp = data["best_params"]
                shadow_params = StrategyParams(
                    min_spread_bps=bp.get("min_spread_bps", 5.0),
                    max_position_size=bp.get("max_position_size", 1000.0),
                    entry_threshold=bp.get("entry_threshold", 0.001),
                    exit_threshold=bp.get("exit_threshold", 0.0001),
                    stop_loss_pct=bp.get("stop_loss_pct", 0.005),
                )
                baseline_params = StrategyParams()
                decision, _ = await runner.apply_decision(
                    strategy_id=strategy,
                    strategy_type=strategy,
                    baseline_params=baseline_params,
                    shadow_params=shadow_params,
                )
                data["shadow_decision"] = decision
            except Exception as exc:
                logger.warning("ShadowRunner failed for %s: %s", strategy, exc)

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
                decision = data.get("shadow_decision", "—")
                lines.append(
                    f"✅ `{strategy}`: sharpe={val:.4f} | {param_str} | shadow={decision}"
                )

        message = "\n".join(lines)
        await self.alerter.send_alert(message, level="INFO")

    def start_scheduler(self) -> None:
        """APScheduler로 매주 일요일 02:00 UTC 스케줄 등록."""
        if not _APSCHEDULER_AVAILABLE:
            logger.error("apscheduler is not installed; cannot start scheduler")
            return

        self._scheduler = AsyncIOScheduler()
        self._scheduler.add_job(
            self.run_optimization,
            "cron",
            day_of_week="sun",
            hour=2,
        )
        self._scheduler.start()
        logger.info("Auto-tuner scheduler started (every Sunday 02:00 UTC)")

    def stop(self) -> None:
        """Shutdown the APScheduler instance."""
        if self._scheduler is not None:
            try:
                self._scheduler.shutdown(wait=False)
                logger.info("Auto-tuner scheduler stopped")
            except Exception as exc:
                logger.warning("Scheduler shutdown error: %s", exc)


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
