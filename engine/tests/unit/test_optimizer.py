"""Tests for WalkForwardOptimizer (TDD)."""
from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from src.tuning.backtest import BacktestResult, StrategyParams
from src.tuning.data_loader import OHLCVWindow
from src.tuning.optimizer import (
    ObjectiveType,
    OptimizationResult,
    TunerConfig,
    WalkForwardOptimizer,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ohlcv(n: int, seed: int = 42) -> OHLCVWindow:
    rng = np.random.default_rng(seed)
    closes = 50_000.0 + np.cumsum(rng.normal(0, 100, n))
    return OHLCVWindow(
        times=np.arange(n, dtype=float),
        opens=closes - 50,
        highs=closes + 100,
        lows=closes - 100,
        closes=closes,
        volumes=rng.uniform(1, 10, n),
    )


def _make_loader(ohlcv: OHLCVWindow) -> MagicMock:
    """Mock loader whose slice_window returns real slices of provided ohlcv."""
    loader = MagicMock()
    loader.slice_window.side_effect = lambda w, s, e: OHLCVWindow(
        times=w.times[s:e],
        opens=w.opens[s:e],
        highs=w.highs[s:e],
        lows=w.lows[s:e],
        closes=w.closes[s:e],
        volumes=w.volumes[s:e],
    )
    return loader


def _make_bt_result(sharpe: float = 1.0, pnl: float = 100.0) -> BacktestResult:
    return BacktestResult(
        total_pnl=pnl,
        sharpe_ratio=sharpe,
        max_drawdown=-0.05,
        win_rate=0.6,
        num_trades=10,
        returns=[0.001] * 20,
    )


# ---------------------------------------------------------------------------
# WalkForwardWindow construction
# ---------------------------------------------------------------------------


class TestBuildWindows:
    def test_basic_window_count(self):
        cfg = TunerConfig(train_periods=10, val_periods=5, n_trials=3)
        opt = WalkForwardOptimizer(config=cfg)
        windows = opt._build_windows(25)
        # step=5: [0-10,10-15], [5-15,15-20], [10-20,20-25] → 3 windows
        assert len(windows) == 3

    def test_first_window_indices(self):
        cfg = TunerConfig(train_periods=10, val_periods=5, n_trials=3)
        opt = WalkForwardOptimizer(config=cfg)
        w = opt._build_windows(25)[0]
        assert w.train_start_idx == 0
        assert w.train_end_idx == 10
        assert w.val_start_idx == 10
        assert w.val_end_idx == 15

    def test_train_val_boundary_continuous(self):
        cfg = TunerConfig(train_periods=20, val_periods=10, n_trials=3)
        opt = WalkForwardOptimizer(config=cfg)
        windows = opt._build_windows(30)
        for w in windows:
            assert w.train_end_idx == w.val_start_idx

    def test_insufficient_data_returns_empty(self):
        cfg = TunerConfig(train_periods=100, val_periods=50, n_trials=3)
        opt = WalkForwardOptimizer(config=cfg)
        assert opt._build_windows(10) == []

    def test_exact_fit(self):
        cfg = TunerConfig(train_periods=10, val_periods=5, n_trials=3)
        opt = WalkForwardOptimizer(config=cfg)
        # Exactly one window
        windows = opt._build_windows(15)
        assert len(windows) == 1


# ---------------------------------------------------------------------------
# Optimizer execution
# ---------------------------------------------------------------------------


class TestOptimize:
    def test_returns_results_for_sufficient_data(self):
        cfg = TunerConfig(train_periods=15, val_periods=5, n_trials=3)
        ohlcv = _make_ohlcv(50)
        opt = WalkForwardOptimizer(config=cfg)
        results = opt.optimize(ohlcv, _make_loader(ohlcv))
        assert len(results) > 0

    def test_results_are_optimization_result_type(self):
        cfg = TunerConfig(train_periods=15, val_periods=5, n_trials=3)
        ohlcv = _make_ohlcv(50)
        opt = WalkForwardOptimizer(config=cfg)
        for r in opt.optimize(ohlcv, _make_loader(ohlcv)):
            assert isinstance(r, OptimizationResult)
            assert isinstance(r.best_params, StrategyParams)

    def test_shadow_mode_true_by_default(self):
        cfg = TunerConfig(train_periods=15, val_periods=5, n_trials=3)
        ohlcv = _make_ohlcv(50)
        opt = WalkForwardOptimizer(config=cfg)
        for r in opt.optimize(ohlcv, _make_loader(ohlcv)):
            assert r.shadow_mode is True

    def test_empty_data_returns_empty(self):
        cfg = TunerConfig(train_periods=20, val_periods=10, n_trials=3)
        ohlcv = _make_ohlcv(5)
        opt = WalkForwardOptimizer(config=cfg)
        assert opt.optimize(ohlcv, _make_loader(ohlcv)) == []

    def test_params_within_configured_bounds(self):
        cfg = TunerConfig(
            train_periods=15,
            val_periods=5,
            n_trials=5,
            min_spread_bps_range=(2.0, 10.0),
            max_position_size_range=(200.0, 600.0),
            entry_threshold_range=(0.001, 0.005),
            exit_threshold_range=(0.0005, 0.002),
            stop_loss_pct_range=(0.01, 0.03),
        )
        ohlcv = _make_ohlcv(50)
        opt = WalkForwardOptimizer(config=cfg)
        for r in opt.optimize(ohlcv, _make_loader(ohlcv)):
            p = r.best_params
            assert cfg.min_spread_bps_range[0] <= p.min_spread_bps <= cfg.min_spread_bps_range[1]
            assert cfg.max_position_size_range[0] <= p.max_position_size <= cfg.max_position_size_range[1]
            assert cfg.entry_threshold_range[0] <= p.entry_threshold <= cfg.entry_threshold_range[1]
            assert cfg.stop_loss_pct_range[0] <= p.stop_loss_pct <= cfg.stop_loss_pct_range[1]

    def test_n_trials_recorded(self):
        cfg = TunerConfig(train_periods=15, val_periods=5, n_trials=7)
        ohlcv = _make_ohlcv(50)
        opt = WalkForwardOptimizer(config=cfg)
        for r in opt.optimize(ohlcv, _make_loader(ohlcv)):
            assert r.n_trials == 7


# ---------------------------------------------------------------------------
# select_best_fold
# ---------------------------------------------------------------------------


class TestSelectBestFold:
    def _result(self, sharpe: float, pnl: float = 100.0) -> OptimizationResult:
        bt = _make_bt_result(sharpe=sharpe, pnl=pnl)
        return OptimizationResult(
            best_params=StrategyParams(),
            train_result=bt,
            val_result=bt,
            n_trials=5,
        )

    def test_select_best_sharpe(self):
        opt = WalkForwardOptimizer(config=TunerConfig(objective=ObjectiveType.MAXIMIZE_SHARPE))
        results = [self._result(0.5), self._result(2.0), self._result(1.0)]
        best = opt.select_best_fold(results)
        assert best.val_result.sharpe_ratio == pytest.approx(2.0)

    def test_select_best_pnl(self):
        opt = WalkForwardOptimizer(config=TunerConfig(objective=ObjectiveType.MAXIMIZE_PNL))
        results = [self._result(1.0, pnl=50.0), self._result(0.5, pnl=200.0)]
        best = opt.select_best_fold(results)
        assert best.val_result.total_pnl == pytest.approx(200.0)

    def test_select_best_minimize_drawdown(self):
        cfg = TunerConfig(objective=ObjectiveType.MINIMIZE_DRAWDOWN)
        opt = WalkForwardOptimizer(config=cfg)
        r1 = self._result(1.0)
        r1.val_result = BacktestResult(
            total_pnl=100, sharpe_ratio=1.0, max_drawdown=-0.01, win_rate=0.5, num_trades=5
        )
        r2 = self._result(1.0)
        r2.val_result = BacktestResult(
            total_pnl=100, sharpe_ratio=1.0, max_drawdown=-0.20, win_rate=0.5, num_trades=5
        )
        best = opt.select_best_fold([r1, r2])
        assert best.val_result.max_drawdown == pytest.approx(-0.01)

    def test_select_best_empty_returns_none(self):
        opt = WalkForwardOptimizer()
        assert opt.select_best_fold([]) is None
