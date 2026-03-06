"""Backtest and tuning integration tests.

Tests the full backtest → optimize → shadow evaluate workflow
using synthetic data (no external dependencies).
"""
from __future__ import annotations

import pytest

from src.tuning.backtest import BacktestEngine, BacktestResult, StrategyParams
from src.tuning.evaluator import OutOfSampleEvaluator
from src.tuning.file_data_loader import (
    FileDataLoader,
    generate_synthetic_ohlcv,
    generate_synthetic_spreads,
)
from src.tuning.optimizer import TunerConfig, WalkForwardOptimizer
from src.tuning.param_bridge import (
    params_to_strategy_config,
    strategy_config_to_params,
)
from src.tuning.shadow_runner import ShadowRunner


class TestSyntheticDataGeneration:
    """Test synthetic data generation."""

    def test_ohlcv_generation(self):
        ohlcv = generate_synthetic_ohlcv(num_candles=100, seed=42)
        assert ohlcv.length == 100
        assert len(ohlcv.opens) == 100
        assert len(ohlcv.highs) == 100
        assert len(ohlcv.lows) == 100
        assert len(ohlcv.closes) == 100
        assert len(ohlcv.volumes) == 100

    def test_ohlcv_price_relationships(self):
        ohlcv = generate_synthetic_ohlcv(num_candles=100, seed=42)
        for i in range(100):
            assert ohlcv.lows[i] <= ohlcv.opens[i]
            assert ohlcv.lows[i] <= ohlcv.closes[i]
            assert ohlcv.highs[i] >= ohlcv.opens[i]
            assert ohlcv.highs[i] >= ohlcv.closes[i]

    def test_ohlcv_reproducible_with_seed(self):
        a = generate_synthetic_ohlcv(num_candles=50, seed=123)
        b = generate_synthetic_ohlcv(num_candles=50, seed=123)
        assert list(a.closes) == list(b.closes)

    def test_spread_generation(self):
        spreads = generate_synthetic_spreads(num_records=100, seed=42)
        assert len(spreads) == 100
        assert all(s.strategy == "cross_exchange" for s in spreads)

    def test_file_data_loader(self):
        loader = FileDataLoader()
        ohlcv = loader.load("synthetic")
        assert ohlcv.length == 2000  # default


class TestBacktestIntegration:
    """Test backtest engine with various data sources."""

    def test_ohlcv_backtest_produces_trades(self):
        ohlcv = generate_synthetic_ohlcv(num_candles=500, seed=42)
        engine = BacktestEngine(initial_capital=70.0, fee_rate=0.001)
        params = StrategyParams(
            entry_threshold=0.0005,
            exit_threshold=0.0002,
        )
        result = engine.run(params, ohlcv)
        assert result.num_trades > 0
        assert isinstance(result.sharpe_ratio, float)
        assert isinstance(result.max_drawdown, float)

    def test_spread_backtest_produces_trades(self):
        spreads = generate_synthetic_spreads(num_records=500, seed=42)
        engine = BacktestEngine(initial_capital=70.0)
        params = StrategyParams(entry_threshold=0.0005, exit_threshold=0.0002)
        result = engine.run_on_spreads(params, spreads)
        assert isinstance(result, BacktestResult)

    def test_empty_data_returns_zero_result(self):
        from src.tuning.data_loader import OHLCVWindow
        import numpy as np

        ohlcv = OHLCVWindow(
            times=np.array([], dtype="datetime64[ms]"),
            opens=np.array([], dtype=float),
            highs=np.array([], dtype=float),
            lows=np.array([], dtype=float),
            closes=np.array([], dtype=float),
            volumes=np.array([], dtype=float),
        )
        engine = BacktestEngine()
        result = engine.run(StrategyParams(), ohlcv)
        assert result.total_pnl == 0.0
        assert result.num_trades == 0


class TestOptimizationIntegration:
    """Test walk-forward optimization end-to-end."""

    def test_optimization_produces_results(self):
        ohlcv = generate_synthetic_ohlcv(num_candles=500, seed=42)
        loader = FileDataLoader()

        config = TunerConfig(
            n_trials=5,
            train_periods=60,
            val_periods=20,
        )
        engine = BacktestEngine(initial_capital=70.0)
        optimizer = WalkForwardOptimizer(config=config, engine=engine)

        results = optimizer.optimize(ohlcv, loader)
        assert len(results) > 0

        for res in results:
            assert isinstance(res.best_params, StrategyParams)
            assert isinstance(res.train_result, BacktestResult)
            assert isinstance(res.val_result, BacktestResult)

    def test_select_best_fold(self):
        ohlcv = generate_synthetic_ohlcv(num_candles=500, seed=42)
        loader = FileDataLoader()

        config = TunerConfig(n_trials=5, train_periods=60, val_periods=20)
        optimizer = WalkForwardOptimizer(config=config)

        results = optimizer.optimize(ohlcv, loader)
        best = optimizer.select_best_fold(results)
        assert best is not None

    def test_at_least_one_positive_sharpe_fold(self):
        """With spread injection, at least one fold should have positive Sharpe."""
        ohlcv = generate_synthetic_ohlcv(
            num_candles=500,
            spread_injection_rate=0.2,
            spread_injection_bps=40,
            seed=42,
        )
        loader = FileDataLoader()

        config = TunerConfig(n_trials=10, train_periods=60, val_periods=20)
        optimizer = WalkForwardOptimizer(config=config)

        results = optimizer.optimize(ohlcv, loader)
        positive_folds = [r for r in results if r.val_result.sharpe_ratio > 0]
        assert len(positive_folds) >= 1, (
            f"Expected at least 1 positive Sharpe fold, got {len(positive_folds)}/{len(results)}"
        )


class TestParamBridge:
    """Test parameter bridge conversion."""

    def test_params_to_strategy_config(self):
        params = StrategyParams(
            min_spread_bps=10.0,
            max_position_size=500.0,
            entry_threshold=0.001,
            exit_threshold=0.0005,
            stop_loss_pct=0.02,
        )

        config = params_to_strategy_config(params, "cross_exchange")
        assert "min_spread_bps" in config
        assert "entry_threshold" in config
        assert config["max_position_usdt"] == 500.0

    def test_roundtrip_conversion(self):
        original = StrategyParams(
            min_spread_bps=15.0,
            max_position_size=750.0,
            entry_threshold=0.002,
            exit_threshold=0.001,
            stop_loss_pct=0.03,
        )

        config = params_to_strategy_config(original, "cross_exchange")
        recovered = strategy_config_to_params(config, "cross_exchange")

        assert abs(recovered.min_spread_bps - original.min_spread_bps) < 1e-6
        assert abs(recovered.entry_threshold - original.entry_threshold) < 1e-6
        assert abs(recovered.stop_loss_pct - original.stop_loss_pct) < 1e-6

    def test_all_strategy_types(self):
        params = StrategyParams()
        for st in [
            "cross_exchange", "triangular", "spot_futures",
            "funding_rate", "statistical_arb", "latency_arb",
            "futures_futures", "cex_dex",
        ]:
            config = params_to_strategy_config(params, st)
            assert isinstance(config, dict)
            assert len(config) > 0


class TestShadowRunner:
    """Test shadow mode evaluation."""

    def test_shadow_evaluation(self):
        baseline = StrategyParams()
        optimized = StrategyParams(
            min_spread_bps=10.0,
            entry_threshold=0.001,
            exit_threshold=0.0003,
        )

        runner = ShadowRunner()
        result = runner.evaluate(
            strategy_id="test_strategy",
            strategy_type="cross_exchange",
            baseline_params=baseline,
            shadow_params=optimized,
            num_candles=200,
        )

        assert result.strategy_id == "test_strategy"
        assert isinstance(result.evaluation.recommendation, str)
        assert result.evaluation.recommendation.startswith(("APPLY", "MONITOR", "REJECT"))

    def test_evaluate_and_decide(self):
        runner = ShadowRunner()
        decision, result = runner.evaluate_and_decide(
            strategy_id="test",
            strategy_type="cross_exchange",
            baseline_params=StrategyParams(),
            shadow_params=StrategyParams(entry_threshold=0.001),
            num_candles=200,
        )

        assert decision in ("APPLY", "MONITOR", "REJECT")


class TestOutOfSampleEvaluator:
    """Test evaluator integration."""

    def test_evaluator_with_backtest_results(self):
        ohlcv = generate_synthetic_ohlcv(num_candles=200, seed=42)
        engine = BacktestEngine(initial_capital=70.0)

        params_a = StrategyParams(entry_threshold=0.0005)
        params_b = StrategyParams(entry_threshold=0.001)

        result_a = engine.run(params_a, ohlcv)
        result_b = engine.run(params_b, ohlcv)

        evaluator = OutOfSampleEvaluator()
        report = evaluator.evaluate(result_a, result_b)

        assert isinstance(report.sim_real_variance_pct, float)
        assert isinstance(report.recommendation, str)
