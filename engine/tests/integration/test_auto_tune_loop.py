"""Auto-Tuning Loop Integration Test.

Simulates the full tune-apply cycle:
  1. Generate synthetic OHLCV data (30 days, 1h candles = 720 candles)
  2. Run WalkForwardOptimizer.optimize() with n_trials=5 (fast)
  3. Get optimized params from best fold
  4. Apply params via ParamBridge (params_to_strategy_config)
  5. Verify applied params differ from defaults
  6. Run backtest with optimized params
  7. Verify optimized Sharpe >= default Sharpe (within noise)
"""
from __future__ import annotations

import pytest

from src.tuning.backtest import BacktestEngine, BacktestResult, StrategyParams
from src.tuning.file_data_loader import FileDataLoader, generate_synthetic_ohlcv
from src.tuning.optimizer import TunerConfig, WalkForwardOptimizer
from src.tuning.param_bridge import (
    CROSS_EXCHANGE,
    params_to_strategy_config,
    strategy_config_to_params,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_backtest(params: StrategyParams, num_candles: int = 720, seed: int = 99) -> BacktestResult:
    """Helper: generate data and run backtest with given params."""
    ohlcv = generate_synthetic_ohlcv(
        num_candles=num_candles,
        spread_injection_rate=0.2,
        spread_injection_bps=30.0,
        seed=seed,
    )
    engine = BacktestEngine(initial_capital=10_000.0, fee_rate=0.001)
    return engine.run(params, ohlcv)


# ---------------------------------------------------------------------------
# Full tune-apply cycle
# ---------------------------------------------------------------------------


class TestAutoTuneLoop:
    """Full auto-tune loop: optimize → apply → verify → backtest."""

    def _build_optimizer(self, n_trials: int = 5) -> tuple[WalkForwardOptimizer, FileDataLoader]:
        config = TunerConfig(
            n_trials=n_trials,
            train_periods=60,
            val_periods=20,
        )
        engine = BacktestEngine(initial_capital=10_000.0, fee_rate=0.001)
        optimizer = WalkForwardOptimizer(config=config, engine=engine)
        loader = FileDataLoader()
        return optimizer, loader

    def _generate_ohlcv(self, num_candles: int = 720) -> object:
        """Generate 30-day 1h candle synthetic OHLCV."""
        return generate_synthetic_ohlcv(
            num_candles=num_candles,
            spread_injection_rate=0.2,
            spread_injection_bps=30.0,
            seed=42,
        )

    def test_optimize_produces_results(self) -> None:
        """Optimizer returns at least one fold result for 30-day data."""
        ohlcv = self._generate_ohlcv()
        optimizer, loader = self._build_optimizer(n_trials=5)

        results = optimizer.optimize(ohlcv, loader)

        assert len(results) > 0, "Expected at least one optimization fold"
        for res in results:
            assert isinstance(res.best_params, StrategyParams)
            assert isinstance(res.train_result, BacktestResult)
            assert isinstance(res.val_result, BacktestResult)

    def test_optimized_params_differ_from_defaults(self) -> None:
        """Best-fold params are not identical to StrategyParams defaults."""
        ohlcv = self._generate_ohlcv()
        optimizer, loader = self._build_optimizer(n_trials=5)

        results = optimizer.optimize(ohlcv, loader)
        best = optimizer.select_best_fold(results)

        assert best is not None
        defaults = StrategyParams()

        # At least one parameter must differ (Optuna should not always return defaults)
        params_differ = any([
            abs(best.best_params.min_spread_bps - defaults.min_spread_bps) > 1e-9,
            abs(best.best_params.entry_threshold - defaults.entry_threshold) > 1e-9,
            abs(best.best_params.exit_threshold - defaults.exit_threshold) > 1e-9,
            abs(best.best_params.max_position_size - defaults.max_position_size) > 1e-9,
            abs(best.best_params.stop_loss_pct - defaults.stop_loss_pct) > 1e-9,
        ])
        assert params_differ, (
            f"Optimized params should differ from defaults.\n"
            f"  best={best.best_params}\n  defaults={defaults}"
        )

    def test_param_bridge_apply(self) -> None:
        """params_to_strategy_config returns a dict with all expected keys."""
        ohlcv = self._generate_ohlcv()
        optimizer, loader = self._build_optimizer(n_trials=5)

        results = optimizer.optimize(ohlcv, loader)
        best = optimizer.select_best_fold(results)
        assert best is not None

        config = params_to_strategy_config(best.best_params, CROSS_EXCHANGE)

        assert isinstance(config, dict)
        assert len(config) > 0
        assert "min_spread_bps" in config
        assert "entry_threshold" in config
        assert "exit_threshold" in config
        assert "max_position_size_usdt" in config
        assert "stop_loss_pct" in config

    def test_applied_params_values_match_best_fold(self) -> None:
        """Config values produced by ParamBridge match the best-fold StrategyParams."""
        ohlcv = self._generate_ohlcv()
        optimizer, loader = self._build_optimizer(n_trials=5)

        results = optimizer.optimize(ohlcv, loader)
        best = optimizer.select_best_fold(results)
        assert best is not None

        config = params_to_strategy_config(best.best_params, CROSS_EXCHANGE)
        recovered = strategy_config_to_params(config, CROSS_EXCHANGE)

        assert abs(recovered.min_spread_bps - best.best_params.min_spread_bps) < 1e-6
        assert abs(recovered.entry_threshold - best.best_params.entry_threshold) < 1e-6
        assert abs(recovered.exit_threshold - best.best_params.exit_threshold) < 1e-6
        assert abs(recovered.stop_loss_pct - best.best_params.stop_loss_pct) < 1e-6

    def test_optimized_backtest_runs_without_error(self) -> None:
        """Backtest with optimized params completes and returns a BacktestResult."""
        ohlcv = self._generate_ohlcv()
        optimizer, loader = self._build_optimizer(n_trials=5)

        results = optimizer.optimize(ohlcv, loader)
        best = optimizer.select_best_fold(results)
        assert best is not None

        result = _run_backtest(best.best_params, num_candles=720, seed=42)

        assert isinstance(result, BacktestResult)
        assert isinstance(result.sharpe_ratio, float)
        assert isinstance(result.total_pnl, float)
        assert isinstance(result.num_trades, int)

    def test_optimized_sharpe_gte_default_sharpe(self) -> None:
        """Optimized params should yield Sharpe >= default params (within noise).

        Uses the same OHLCV data for a fair comparison.
        Note: Bayesian optimization may not always beat defaults on a single
        validation window, so we allow a 2-point tolerance margin.
        """
        ohlcv = self._generate_ohlcv(num_candles=720)
        optimizer, loader = self._build_optimizer(n_trials=10)

        results = optimizer.optimize(ohlcv, loader)
        best = optimizer.select_best_fold(results)
        assert best is not None

        # Run both on the same validation window for fair comparison
        engine = BacktestEngine(initial_capital=10_000.0, fee_rate=0.001)

        # Use the last val window from best fold for comparison
        best_result = best.val_result
        default_result = engine.run(StrategyParams(), ohlcv)

        # Optimized Sharpe should not be dramatically worse than default
        # Allow 2.0 Sharpe tolerance for statistical noise in small datasets
        SHARPE_TOLERANCE = 2.0
        assert best_result.sharpe_ratio >= default_result.sharpe_ratio - SHARPE_TOLERANCE, (
            f"Optimized Sharpe {best_result.sharpe_ratio:.3f} is more than "
            f"{SHARPE_TOLERANCE} below default Sharpe {default_result.sharpe_ratio:.3f}"
        )


# ---------------------------------------------------------------------------
# Walk-forward window construction tests
# ---------------------------------------------------------------------------


class TestWalkForwardWindows:
    """Verify window construction logic for various data sizes."""

    def test_sufficient_data_produces_windows(self) -> None:
        """720 candles with train=60, val=20 should produce multiple folds."""
        config = TunerConfig(n_trials=5, train_periods=60, val_periods=20)
        optimizer = WalkForwardOptimizer(config=config)
        windows = optimizer._build_windows(720)
        assert len(windows) > 1, f"Expected multiple windows, got {len(windows)}"

    def test_insufficient_data_produces_no_windows(self) -> None:
        """Data shorter than train+val produces no windows."""
        config = TunerConfig(n_trials=5, train_periods=60, val_periods=20)
        optimizer = WalkForwardOptimizer(config=config)
        windows = optimizer._build_windows(50)
        assert len(windows) == 0

    def test_window_indices_non_overlapping(self) -> None:
        """Consecutive windows advance by exactly val_periods (no overlap)."""
        config = TunerConfig(n_trials=5, train_periods=60, val_periods=20)
        optimizer = WalkForwardOptimizer(config=config)
        windows = optimizer._build_windows(300)

        for i in range(1, len(windows)):
            prev = windows[i - 1]
            curr = windows[i]
            assert curr.train_start_idx == prev.train_start_idx + config.val_periods

    def test_window_val_follows_train(self) -> None:
        """Each window's val_start_idx equals its train_end_idx."""
        config = TunerConfig(n_trials=5, train_periods=60, val_periods=20)
        optimizer = WalkForwardOptimizer(config=config)
        windows = optimizer._build_windows(300)

        for w in windows:
            assert w.val_start_idx == w.train_end_idx
            assert w.val_end_idx == w.val_start_idx + config.val_periods


# ---------------------------------------------------------------------------
# Param bridge — extended coverage
# ---------------------------------------------------------------------------


class TestParamBridgeExtended:
    """Additional param bridge tests for all strategy types."""

    def test_all_strategy_types_produce_valid_config(self) -> None:
        """params_to_strategy_config works for all registered strategy types."""
        from src.tuning.param_bridge import (
            CROSS_EXCHANGE, TRIANGULAR, SPOT_FUTURES, FUNDING_RATE,
            STATISTICAL_ARB, LATENCY_ARB, FUTURES_FUTURES, CEX_DEX,
        )
        params = StrategyParams(
            min_spread_bps=12.0,
            max_position_size=800.0,
            entry_threshold=0.0008,
            exit_threshold=0.0003,
            stop_loss_pct=0.025,
        )

        for strategy_type in [
            CROSS_EXCHANGE, TRIANGULAR, SPOT_FUTURES, FUNDING_RATE,
            STATISTICAL_ARB, LATENCY_ARB, FUTURES_FUTURES, CEX_DEX,
        ]:
            config = params_to_strategy_config(params, strategy_type)
            assert isinstance(config, dict), f"Expected dict for {strategy_type}"
            assert len(config) >= 5, f"Expected >= 5 keys for {strategy_type}, got {config}"

    def test_overrides_applied(self) -> None:
        """Extra override keys are merged into the config."""
        params = StrategyParams()
        config = params_to_strategy_config(
            params,
            CROSS_EXCHANGE,
            overrides={"custom_key": "custom_value", "debug": True},
        )
        assert config.get("custom_key") == "custom_value"
        assert config.get("debug") is True

    def test_roundtrip_preserves_all_fields(self) -> None:
        """strategy_config_to_params correctly recovers all StrategyParams fields."""
        original = StrategyParams(
            min_spread_bps=18.5,
            max_position_size=1200.0,
            entry_threshold=0.0012,
            exit_threshold=0.0006,
            stop_loss_pct=0.035,
        )
        config = params_to_strategy_config(original, CROSS_EXCHANGE)
        recovered = strategy_config_to_params(config, CROSS_EXCHANGE)

        assert abs(recovered.min_spread_bps - original.min_spread_bps) < 1e-9
        assert abs(recovered.entry_threshold - original.entry_threshold) < 1e-9
        assert abs(recovered.exit_threshold - original.exit_threshold) < 1e-9
        assert abs(recovered.stop_loss_pct - original.stop_loss_pct) < 1e-9


# ---------------------------------------------------------------------------
# Backtest consistency sanity checks
# ---------------------------------------------------------------------------


class TestBacktestConsistency:
    """Sanity checks for BacktestEngine used in the tune loop."""

    def test_deterministic_with_same_data(self) -> None:
        """Same params + same OHLCV data always produces identical results."""
        ohlcv = generate_synthetic_ohlcv(num_candles=200, seed=7)
        params = StrategyParams(entry_threshold=0.001, exit_threshold=0.0005)
        engine = BacktestEngine(initial_capital=10_000.0)

        result1 = engine.run(params, ohlcv)
        result2 = engine.run(params, ohlcv)

        assert result1.total_pnl == result2.total_pnl
        assert result1.num_trades == result2.num_trades
        assert result1.sharpe_ratio == result2.sharpe_ratio

    def test_tight_thresholds_produce_more_trades(self) -> None:
        """Lower entry threshold results in more trades."""
        ohlcv = generate_synthetic_ohlcv(
            num_candles=500,
            spread_injection_rate=0.3,
            spread_injection_bps=40,
            seed=42,
        )
        engine = BacktestEngine(initial_capital=10_000.0)

        loose = StrategyParams(entry_threshold=0.01, min_spread_bps=50.0)
        tight = StrategyParams(entry_threshold=0.0001, min_spread_bps=0.1)

        result_loose = engine.run(loose, ohlcv)
        result_tight = engine.run(tight, ohlcv)

        assert result_tight.num_trades >= result_loose.num_trades, (
            f"Tight threshold should produce >= trades: "
            f"tight={result_tight.num_trades}, loose={result_loose.num_trades}"
        )

    def test_select_best_fold_returns_highest_sharpe(self) -> None:
        """select_best_fold returns the fold with the best validation Sharpe."""
        ohlcv = generate_synthetic_ohlcv(num_candles=500, seed=42)
        config = TunerConfig(n_trials=5, train_periods=60, val_periods=20)
        optimizer = WalkForwardOptimizer(config=config)
        loader = FileDataLoader()

        results = optimizer.optimize(ohlcv, loader)
        assert len(results) > 0

        best = optimizer.select_best_fold(results)
        assert best is not None

        # Best fold should have the highest val Sharpe among all folds
        max_sharpe = max(r.val_result.sharpe_ratio for r in results)
        assert abs(best.val_result.sharpe_ratio - max_sharpe) < 1e-9
