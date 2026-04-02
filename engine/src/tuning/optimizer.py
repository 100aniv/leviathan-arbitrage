"""Bayesian walk-forward optimizer using Optuna."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

import optuna
from optuna.samplers import TPESampler

from src.tuning.backtest import BacktestEngine, TuningBacktestResult, StrategyParams
from src.tuning.data_loader import DataLoader, OHLCVWindow
from src.tuning.strategy_backtest import STRATEGY_TYPES, StrategyBacktestEngine

logger = logging.getLogger(__name__)
optuna.logging.set_verbosity(optuna.logging.WARNING)


class ObjectiveType(str, Enum):
    MAXIMIZE_SHARPE = "maximize_sharpe"
    MAXIMIZE_PNL = "maximize_pnl"
    MINIMIZE_DRAWDOWN = "minimize_drawdown"


@dataclass
class WalkForwardWindow:
    """Index bounds for a single train/validate fold."""

    train_start_idx: int
    train_end_idx: int
    val_start_idx: int
    val_end_idx: int


@dataclass
class OptimizationResult:
    """Outcome of one walk-forward fold."""

    best_params: StrategyParams
    train_result: TuningBacktestResult
    val_result: TuningBacktestResult
    n_trials: int
    shadow_mode: bool = True  # params run in shadow 24 h before live application


@dataclass
class TunerConfig:
    """All knobs for the auto-tuner."""

    n_trials: int = 100
    n_jobs: int = 1
    objective: ObjectiveType = ObjectiveType.MAXIMIZE_SHARPE

    # Walk-forward window sizes (in candles)
    train_periods: int = 60
    val_periods: int = 20

    # Shadow duration before live application
    shadow_duration_hours: float = 24.0

    # Parameter search bounds
    min_spread_bps_range: tuple[float, float] = (3.0, 50.0)
    max_position_size_range: tuple[float, float] = (100.0, 10_000.0)
    entry_threshold_range: tuple[float, float] = (0.0001, 0.01)
    exit_threshold_range: tuple[float, float] = (0.00005, 0.005)
    stop_loss_pct_range: tuple[float, float] = (0.005, 0.05)


class WalkForwardOptimizer:
    """
    Bayesian walk-forward optimizer.

    For each fold:
      - Train: Optuna TPE on window T (maximizes chosen objective)
      - Validate: evaluate best params on window T+1
      - Shadow: new params flagged for 24 h shadow mode before live

    Parameters:
        config: TunerConfig controlling trials, bounds, and objectives.
        engine: BacktestEngine instance (injectable for testing).
    """

    def __init__(
        self,
        config: TunerConfig | None = None,
        engine: BacktestEngine | StrategyBacktestEngine | None = None,
        strategy_type: str | None = None,
    ) -> None:
        self._config = config or TunerConfig()
        if engine is not None:
            self._engine = engine
        elif strategy_type is not None:
            if strategy_type not in STRATEGY_TYPES:
                raise ValueError(
                    f"strategy_type must be one of {STRATEGY_TYPES}, got {strategy_type!r}"
                )
            self._engine: BacktestEngine | StrategyBacktestEngine = StrategyBacktestEngine(
                strategy_type=strategy_type
            )
        else:
            self._engine = BacktestEngine()

    # ------------------------------------------------------------------
    # Walk-forward window construction
    # ------------------------------------------------------------------

    def _build_windows(self, total_length: int) -> list[WalkForwardWindow]:
        """Slice data into non-overlapping train/validate pairs."""
        windows: list[WalkForwardWindow] = []
        train_n = self._config.train_periods
        val_n = self._config.val_periods
        step = val_n  # advance by one val period each fold

        idx = 0
        while idx + train_n + val_n <= total_length:
            windows.append(
                WalkForwardWindow(
                    train_start_idx=idx,
                    train_end_idx=idx + train_n,
                    val_start_idx=idx + train_n,
                    val_end_idx=idx + train_n + val_n,
                )
            )
            idx += step

        return windows

    # ------------------------------------------------------------------
    # Optuna objective factory
    # ------------------------------------------------------------------

    def _make_objective(
        self,
        ohlcv: OHLCVWindow,
        window: WalkForwardWindow,
        loader: DataLoader,
    ) -> Callable[[optuna.Trial], float]:
        cfg = self._config
        engine = self._engine

        def objective(trial: optuna.Trial) -> float:
            params = StrategyParams(
                min_spread_bps=trial.suggest_float(
                    "min_spread_bps", *cfg.min_spread_bps_range
                ),
                max_position_size=trial.suggest_float(
                    "max_position_size", *cfg.max_position_size_range
                ),
                entry_threshold=trial.suggest_float(
                    "entry_threshold", *cfg.entry_threshold_range, log=True
                ),
                exit_threshold=trial.suggest_float(
                    "exit_threshold", *cfg.exit_threshold_range, log=True
                ),
                stop_loss_pct=trial.suggest_float(
                    "stop_loss_pct", *cfg.stop_loss_pct_range
                ),
            )

            train_slice = loader.slice_window(
                ohlcv, window.train_start_idx, window.train_end_idx
            )
            result = engine.run(params, train_slice)

            if cfg.objective == ObjectiveType.MAXIMIZE_SHARPE:
                return result.sharpe_ratio
            elif cfg.objective == ObjectiveType.MAXIMIZE_PNL:
                return result.total_pnl
            else:  # MINIMIZE_DRAWDOWN: drawdown <= 0; less negative = better; maximize directly
                return result.max_drawdown

        return objective

    # ------------------------------------------------------------------
    # Main optimization loop
    # ------------------------------------------------------------------

    def optimize(
        self,
        ohlcv: OHLCVWindow,
        loader: DataLoader,
    ) -> list[OptimizationResult]:
        """
        Run walk-forward Bayesian optimization.

        Returns one OptimizationResult per fold. Each result has shadow_mode=True,
        indicating the new parameters must be validated in shadow for
        ``config.shadow_duration_hours`` hours before live application.
        """
        windows = self._build_windows(ohlcv.length)
        if not windows:
            logger.warning("Not enough data for walk-forward optimization")
            return []

        results: list[OptimizationResult] = []

        for i, window in enumerate(windows):
            logger.info("Walk-forward fold %d/%d", i + 1, len(windows))

            study = optuna.create_study(
                direction="maximize",
                sampler=TPESampler(seed=42 + i),
            )
            study.optimize(
                self._make_objective(ohlcv, window, loader),
                n_trials=self._config.n_trials,
                n_jobs=self._config.n_jobs,
            )

            best = study.best_params
            best_params = StrategyParams(
                min_spread_bps=best["min_spread_bps"],
                max_position_size=best["max_position_size"],
                entry_threshold=best["entry_threshold"],
                exit_threshold=best["exit_threshold"],
                stop_loss_pct=best["stop_loss_pct"],
            )

            train_slice = loader.slice_window(
                ohlcv, window.train_start_idx, window.train_end_idx
            )
            val_slice = loader.slice_window(
                ohlcv, window.val_start_idx, window.val_end_idx
            )

            results.append(
                OptimizationResult(
                    best_params=best_params,
                    train_result=self._engine.run(best_params, train_slice),
                    val_result=self._engine.run(best_params, val_slice),
                    n_trials=self._config.n_trials,
                    shadow_mode=True,
                )
            )

        return results

    # ------------------------------------------------------------------
    # Fold selection
    # ------------------------------------------------------------------

    def select_best_fold(
        self, results: list[OptimizationResult]
    ) -> OptimizationResult | None:
        """Return the fold with the best out-of-sample (validation) objective value."""
        if not results:
            return None

        if self._config.objective == ObjectiveType.MAXIMIZE_SHARPE:
            return max(results, key=lambda r: r.val_result.sharpe_ratio)
        elif self._config.objective == ObjectiveType.MAXIMIZE_PNL:
            return max(results, key=lambda r: r.val_result.total_pnl)
        else:  # MINIMIZE_DRAWDOWN: drawdown <= 0; -0.01 > -0.20, so maximize directly
            return max(results, key=lambda r: r.val_result.max_drawdown)
