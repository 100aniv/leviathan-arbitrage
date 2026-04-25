"""Weekly auto-tuner: runs Optuna optimization per strategy on a cron schedule."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

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
EXCLUDED = {"cex_dex"}


class InsufficientDataError(RuntimeError):
    """Raised when TimescaleDB has fewer rows than the minimum required for WFE."""


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
        self._params_path = Path(
            os.environ.get("STRATEGY_PARAMS_PATH", "config/strategy_params.json")
        )
        self._reload_callback: Callable[[], None] | None = None

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

        # Apply activation filter when:
        # - activation_path was explicitly provided (caller requested filtering), OR
        # - strategies was not explicitly provided (using default list)
        # Skip only when strategies is explicit AND activation_path came from env fallback.
        _apply_filter = resolved_path is not None and (
            strategies is None or activation_path is not None
        )
        if _apply_filter:
            active = self._load_activation(resolved_path)
            if active is not None:
                base = [s for s in base if s in active]

        # Always remove EXCLUDED
        self.strategies = [s for s in base if s not in EXCLUDED]

    def _load_activation(self, path: Path) -> list[str] | None:
        """Load active strategies from strategy_activation.json (US-067 output).

        Returns list of active strategy names, or None if file not found/invalid.
        Handles both simple format ({name: bool}) and StrategyValidationOrchestrator
        format ({active_strategies: [...], results: {...}}).
        """
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            # StrategyValidationOrchestrator format (US-067): has active_strategies key
            if "active_strategies" in data:
                active = data["active_strategies"]
                if not active:
                    # Empty = inconclusive or all disabled; skip filter
                    return None
                # Strip _v1 suffix if present
                return [s.removesuffix("_v1") for s in active]
            # Original simple format: {strategy_name: bool/dict}
            return [s for s, v in data.items() if v is True or (isinstance(v, dict) and v.get("active", False))]
        except Exception as exc:
            logger.warning("Failed to load activation file %s: %s", path, exc)
            return None

    async def run_optimization(self) -> dict:
        """전략별 독립 Optuna 최적화 실행."""
        if not _OPTUNA_AVAILABLE:
            logger.error("optuna is not installed; skipping optimization")
            return {}

        # Devil's Advocate: backup current params before optimization
        _previous_params = self._load_current_params()

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

        # Devil's Advocate: rollback if new params are harmful
        verdict = await self._evaluate_tuning_impact(results, _previous_params)
        if verdict == "HARMFUL":
            logger.warning("tuner_rollback reason=new params worse than previous")
            await self._rollback_params(_previous_params)
            return results

        await self._apply_shadow_decisions(results)
        await self._write_params(results)
        await self._report_results(results)
        return results

    def _load_current_params(self) -> dict | None:
        """Load current strategy params for rollback comparison."""
        if self._params_path.exists():
            try:
                return json.loads(self._params_path.read_text())
            except Exception:
                return None
        return None

    async def _evaluate_tuning_impact(self, results: dict, previous: dict | None) -> str:
        """Evaluate if new tuning improved or degraded performance.

        Returns: PROVEN | NEUTRAL | HARMFUL | BUG
        """
        if previous is None:
            return "NEUTRAL"  # No baseline to compare

        try:
            # Only compare strategies that ran in the current optimization
            current_strategies = {
                k for k, v in results.items()
                if isinstance(v, dict) and "best_value" in v
            }
            if not current_strategies:
                return "NEUTRAL"

            new_avg = sum(
                results[s].get("best_value", 0) for s in current_strategies
            )
            # Compare only against previous values for the same strategies
            prev_values = previous.get("_tuner_meta", {}).get("best_values", {})
            matched_prev = {s: prev_values[s] for s in current_strategies if s in prev_values}
            if not matched_prev:
                return "NEUTRAL"

            prev_avg = sum(matched_prev.values())

            if new_avg > prev_avg * 1.05:
                logger.info("tuner_verdict verdict=PROVEN new=%s prev=%s", new_avg, prev_avg)
                return "PROVEN"
            elif new_avg < prev_avg * 0.95:
                logger.warning("tuner_verdict verdict=HARMFUL new=%s prev=%s", new_avg, prev_avg)
                return "HARMFUL"
            else:
                logger.info("tuner_verdict verdict=NEUTRAL new=%s prev=%s", new_avg, prev_avg)
                return "NEUTRAL"
        except Exception as exc:
            logger.error("tuner_evaluation_error error=%s", exc)
            return "BUG"

    async def _rollback_params(self, previous: dict | None) -> None:
        """Rollback to previous params."""
        if previous is None:
            return
        try:
            self._params_path.write_text(json.dumps(previous, indent=2))
            logger.info("tuner_params_rolled_back")
            if self._reload_callback:
                self._reload_callback()
        except Exception as exc:
            logger.error("tuner_rollback_failed error=%s", exc)

    def _optimize_strategy(self, strategy: str) -> dict:
        """단일 전략 Optuna 최적화 (n_trials, TPESampler)."""
        config = TunerConfig(n_trials=self.n_trials)
        optimizer = WalkForwardOptimizer(config=config, strategy_type=strategy)

        # Pre-flight data sufficiency check for timescaledb path (min 3 days = 72 rows)
        if self.data_source == "timescaledb":
            try:
                self._check_sufficient_real_data(optimizer, strategy)
            except InsufficientDataError as exc:
                logger.warning(
                    "Skipping real-data WFE for %s: %s", strategy, exc
                )
                return {
                    "status": "INSUFFICIENT_DATA",
                    "error": str(exc),
                    "data_type": "real_timescaledb",
                }

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
            "data_type": "real_timescaledb" if self.data_source == "timescaledb" else "synthetic_gbm",
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
        # SIT-3: asyncpg DSN → standard postgresql (asyncpg scheme not accepted by sync drivers)
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")

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

    def _check_sufficient_real_data(
        self, optimizer: WalkForwardOptimizer, strategy: str
    ) -> None:
        """Pre-flight check: raises InsufficientDataError if TimescaleDB has < MIN_ROWS.

        Called before the Optuna study so that the study aborts immediately rather
        than pruning every single trial.  Does NOT fall back to synthetic.
        """
        from concurrent.futures import ThreadPoolExecutor

        dsn = os.environ.get("DATABASE_URL", "")
        if not dsn:
            raise InsufficientDataError("DATABASE_URL not set")
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")

        MIN_ROWS = int(os.environ.get("TUNER_MIN_REAL_DATA_ROWS", "72"))

        def _load_sync():
            import asyncio as _aio
            loop = _aio.new_event_loop()
            try:
                async def _do():
                    async with DataLoader(dsn=dsn) as loader:
                        return await loader.load_execution_log_as_ohlcv(days=30)
                return loop.run_until_complete(_do())
            finally:
                loop.close()

        with ThreadPoolExecutor(max_workers=1) as pool:
            ohlcv = pool.submit(_load_sync).result(timeout=60)

        if ohlcv.length < MIN_ROWS:
            raise InsufficientDataError(
                f"Only {ohlcv.length} hourly rows available; need at least {MIN_ROWS} (3 days)"
            )

    async def _write_params(self, results: dict) -> None:
        """최적화 결과를 config/strategy_params.json에 원자적으로 쓰기 (US-179)."""
        ready_strategies = {
            s: d for s, d in results.items()
            if "error" not in d and d.get("best_params") and d.get("status") == "READY"
        }
        # Collect real-data WFE results (READY or INSUFFICIENT_DATA) for _real_wfe section
        real_wfe_results = {
            s: d for s, d in results.items()
            if d.get("data_type") == "real_timescaledb"
        }
        if not ready_strategies and not real_wfe_results:
            return

        # Load existing params to merge
        existing: dict = {}
        if self._params_path.exists():
            try:
                existing = json.loads(self._params_path.read_text())
            except Exception as exc:
                logger.warning("Failed to read existing params: %s", exc)

        # SIT-3: param_bridge로 키 매핑 (min_spread_bps→min_profit_bps 등)
        from src.tuning.param_bridge import _PARAM_MAPPINGS

        for strategy, data in ready_strategies.items():
            raw_params = dict(data["best_params"])
            # Apply param_bridge key mapping for the strategy
            mapping = _PARAM_MAPPINGS.get(strategy, {})
            entry = {}
            for raw_key, raw_val in raw_params.items():
                mapped_key = mapping.get(raw_key, raw_key)  # fallback to raw key
                entry[mapped_key] = raw_val
            entry["wfe"] = data.get("best_value", 0.0)
            entry["data_type"] = data.get("data_type", "synthetic_gbm")
            # SIT-3: synthetic 결과는 기존 real params를 덮어쓰지 않음
            if entry["data_type"] == "synthetic_gbm" and strategy in existing:
                old_type = existing[strategy].get("data_type", "")
                if old_type == "real_timescaledb":
                    logger.info(
                        "tuner_skip_synthetic_overwrite strategy=%s reason=real data params preserved over synthetic",
                        strategy,
                    )
                    continue
            # PHOENIX: preserve DISABLED/DISABLED_PHASE2 status — tuner must not re-enable manually gated strategies
            current_status = existing.get(strategy, {}).get("status", "")
            if current_status in ("DISABLED", "DISABLED_PHASE2"):
                entry["status"] = current_status
                logger.info("tuner_preserve_disabled_status strategy=%s status=%s", strategy, current_status)
            else:
                entry["status"] = "READY"
            existing[strategy] = entry

        # Record real-data WFE results into _real_wfe section
        if real_wfe_results:
            real_wfe_section = existing.get("_real_wfe", {})
            for strategy, data in real_wfe_results.items():
                if data.get("status") == "INSUFFICIENT_DATA":
                    real_wfe_section[strategy] = {
                        "status": "INSUFFICIENT_DATA",
                        "error": data.get("error", ""),
                        "data_type": "real_timescaledb",
                    }
                else:
                    real_wfe_section[strategy] = {
                        "status": "READY",
                        "wfe": data.get("best_value", 0.0),
                        "best_params": data.get("best_params", {}),
                        "data_type": "real_timescaledb",
                    }
            existing["_real_wfe"] = real_wfe_section

        # _tuner_meta: store best_values + timestamp for Devil's Advocate comparison
        meta = {
            "best_values": {
                k: v.get("best_value", 0)
                for k, v in results.items()
                if isinstance(v, dict)
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        existing["_tuner_meta"] = meta

        # JSON schema validation: all values must be JSON-serialisable numbers/strings
        try:
            serialized = json.dumps(existing, indent=2)
        except (TypeError, ValueError) as exc:
            logger.error("ScheduledTuner: params JSON serialization failed: %s", exc)
            return

        # Atomic write via temp file + rename
        try:
            self._params_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._params_path.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    f.write(serialized)
                os.rename(tmp, self._params_path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except Exception as exc:
            logger.error("ScheduledTuner: failed to write params: %s", exc)
            return

        logger.info("ScheduledTuner: parameters hot-reloaded")
        if self._reload_callback is not None:
            try:
                self._reload_callback()
            except Exception as exc:
                logger.warning("ScheduledTuner: reload_callback error: %s", exc)

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
        """APScheduler로 매주 일요일 02:00 UTC 스케줄 등록 + 엔진 시작 5분 후 초기 실행."""
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
        # Initial run 5 minutes after engine start
        initial_run_time = datetime.now(timezone.utc) + timedelta(minutes=5)
        self._scheduler.add_job(
            self.run_optimization,
            "date",
            run_date=initial_run_time,
        )
        self._scheduler.start()
        logger.info("Auto-tuner scheduler started (every Sunday 02:00 UTC)")
        logger.info("Auto-tuner initial run scheduled in 5 minutes")

    def stop(self) -> None:
        """Shutdown the APScheduler instance."""
        if self._scheduler is not None:
            try:
                self._scheduler.shutdown(wait=False)
                logger.info("Auto-tuner scheduler stopped")
            except Exception as exc:
                logger.warning("Scheduler shutdown error: %s", exc)


class ShadowMiniTuner:
    """US-234: Shadow 전용 미니 튜너 — 2시간 데이터 후 Optuna n_trials=20.

    live 파라미터 오염 방지: strategy_params.json 직접 수정 금지.
    결과는 hot_reload_callback(shadow_params: dict)으로만 전달.
    별도 스레드에서 실행 (asyncio 이벤트 루프 차단 방지).
    """

    ACTIVATION_HOURS = 2  # 최소 Shadow 실행 시간 (시간)
    N_TRIALS = 20

    def __init__(
        self,
        hot_reload_callback: "Callable[[dict], None] | None" = None,
        n_trials: int = N_TRIALS,
    ) -> None:
        self._callback = hot_reload_callback
        self.n_trials = n_trials
        self._triggered = False

    def should_activate(self, shadow_elapsed_seconds: float) -> bool:
        """Shadow 2시간 경과 여부 확인."""
        return shadow_elapsed_seconds >= self.ACTIVATION_HOURS * 3600

    def run_in_thread(
        self,
        shadow_elapsed_seconds: float,
        win_rate: float = 0.5,
        total_trades: int = 0,
        expected_edge_bps: float = 0.0,
    ) -> None:
        """조건 충족 시 별도 스레드에서 미니 튜너 실행."""
        if self._triggered:
            return
        if not self.should_activate(shadow_elapsed_seconds):
            return
        if not _OPTUNA_AVAILABLE:
            logger.warning("ShadowMiniTuner: optuna not installed, skipping")
            return

        self._triggered = True
        import threading
        t = threading.Thread(
            target=self._run_sync,
            args=(win_rate, total_trades, expected_edge_bps),
            daemon=True,
            name="shadow_mini_tuner",
        )
        t.start()
        logger.info("ShadowMiniTuner: started in background thread")

    def _run_sync(
        self,
        win_rate: float,
        total_trades: int,
        expected_edge_bps: float,
    ) -> None:
        """Optuna n_trials=20으로 min_edge_bps 최적화 (shadow 전용)."""
        try:
            study = optuna.create_study(
                direction="maximize",
                sampler=optuna.samplers.TPESampler(seed=42),
            )

            def objective(trial: "optuna.Trial") -> float:
                min_edge_bps = trial.suggest_float("min_edge_bps", 1.0, 50.0)
                # 단순 heuristic: 낮은 edge는 더 많은 거래 → WR이 낮으면 불리
                # 높은 edge는 신호 질 향상 → WR 보정
                score = (win_rate - 0.5) * 2.0  # [-1, 1] 정규화
                # edge가 expected_edge_bps보다 크면 신호 미발생 → 페널티
                if min_edge_bps > max(expected_edge_bps, 1.0) * 3:
                    score -= 1.0
                # trade count가 충분하면 보너스
                if total_trades >= 30:
                    score += 0.1
                return score

            study.optimize(objective, n_trials=self.n_trials)

            best_params = study.best_params
            logger.info(
                "ShadowMiniTuner: optimization complete best_params=%s best_value=%.6f "
                "note=shadow-local only, strategy_params.json not modified",
                best_params, study.best_value,
            )

            if self._callback is not None:
                try:
                    self._callback(best_params)
                except Exception as exc:
                    logger.warning("ShadowMiniTuner: callback error: %s", exc)

        except Exception as exc:
            logger.error("ShadowMiniTuner._run_sync error: %s", exc)


if __name__ == "__main__":
    if not _OPTUNA_AVAILABLE:
        raise SystemExit("optuna is required: pip install optuna")
    if not _APSCHEDULER_AVAILABLE:
        raise SystemExit("apscheduler is required: pip install apscheduler")

    n_trials = int(os.environ.get("TUNER_N_TRIALS", "100"))

    async def _run() -> None:
        tuner = ScheduledTuner(n_trials=n_trials)
        tuner.start_scheduler()
        try:
            while True:
                await asyncio.sleep(3600)
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("Auto-tuner shutting down")
        finally:
            tuner.stop()

    asyncio.run(_run())
