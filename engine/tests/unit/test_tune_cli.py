"""Unit tests for src/cli/tune_cli.py."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.cli.tune_cli import _run_tune, main
from src.tuning.backtest import BacktestResult, StrategyParams


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(total_pnl=0.5, sharpe=0.8, drawdown=0.01, win_rate=0.6, trades=5):
    return BacktestResult(
        total_pnl=total_pnl,
        sharpe_ratio=sharpe,
        max_drawdown=drawdown,
        win_rate=win_rate,
        num_trades=trades,
    )


def _make_fold(sharpe=0.9):
    fold = MagicMock()
    fold.best_params = StrategyParams()
    fold.train_result = _make_result()
    fold.val_result = _make_result(sharpe=sharpe)
    fold.shadow_mode = False
    return fold


def _default_args(**kwargs):
    defaults = dict(
        data="synthetic",
        candles=200,
        capital=70.0,
        fee_rate=0.001,
        strategy="cross_exchange",
        trials=10,
        train_periods=60,
        val_periods=20,
        shadow=False,
        output=None,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# _run_tune — error paths
# ---------------------------------------------------------------------------


class TestRunTuneErrors:
    @patch("src.cli.tune_cli.generate_synthetic_ohlcv")
    @patch("src.cli.tune_cli.WalkForwardOptimizer")
    @patch("src.cli.tune_cli.TunerConfig")
    @patch("src.cli.tune_cli.BacktestEngine")
    def test_insufficient_data_returns_error(
        self, mock_engine_cls, mock_config, mock_opt_cls, mock_ohlcv
    ):
        import numpy as np
        from src.tuning.data_loader import OHLCVWindow
        arr = np.array([50000.0] * 5)
        mock_ohlcv.return_value = OHLCVWindow(
            times=np.arange(5, dtype=float),
            opens=arr, highs=arr, lows=arr, closes=arr, volumes=np.ones(5),
        )
        optimizer = MagicMock()
        optimizer.optimize.return_value = []
        mock_opt_cls.return_value = optimizer
        mock_engine_cls.return_value = MagicMock()

        result = _run_tune(_default_args(candles=5))
        assert result.get("error") == "insufficient_data"

    @patch("src.cli.tune_cli.generate_synthetic_ohlcv")
    @patch("src.cli.tune_cli.WalkForwardOptimizer")
    @patch("src.cli.tune_cli.TunerConfig")
    @patch("src.cli.tune_cli.BacktestEngine")
    def test_no_valid_fold_returns_error(
        self, mock_engine_cls, mock_config, mock_opt_cls, mock_ohlcv
    ):
        import numpy as np
        from src.tuning.data_loader import OHLCVWindow
        arr = np.linspace(50000, 51000, 100)
        mock_ohlcv.return_value = OHLCVWindow(
            times=np.arange(100, dtype=float),
            opens=arr, highs=arr, lows=arr, closes=arr, volumes=np.ones(100),
        )
        optimizer = MagicMock()
        optimizer.optimize.return_value = [_make_fold()]
        optimizer.select_best_fold.return_value = None  # no valid fold
        mock_opt_cls.return_value = optimizer
        mock_engine_cls.return_value = MagicMock()

        result = _run_tune(_default_args())
        assert result.get("error") == "no_valid_fold"


# ---------------------------------------------------------------------------
# _run_tune — success path
# ---------------------------------------------------------------------------


class TestRunTuneSuccess:
    def _setup_mocks(self, mock_ohlcv, mock_engine_cls, mock_config, mock_opt_cls,
                     mock_bridge, folds=None):
        import numpy as np
        from src.tuning.data_loader import OHLCVWindow
        arr = np.linspace(50000, 51000, 200)
        mock_ohlcv.return_value = OHLCVWindow(
            times=np.arange(200, dtype=float),
            opens=arr, highs=arr, lows=arr, closes=arr, volumes=np.ones(200),
        )
        if folds is None:
            folds = [_make_fold()]
        best = folds[0]
        optimizer = MagicMock()
        optimizer.optimize.return_value = folds
        optimizer.select_best_fold.return_value = best
        mock_opt_cls.return_value = optimizer
        mock_engine_cls.return_value = MagicMock()
        mock_bridge.return_value = {"min_spread_bps": 5.0}
        return best

    @patch("src.cli.tune_cli.params_to_strategy_config")
    @patch("src.cli.tune_cli.generate_synthetic_ohlcv")
    @patch("src.cli.tune_cli.WalkForwardOptimizer")
    @patch("src.cli.tune_cli.TunerConfig")
    @patch("src.cli.tune_cli.BacktestEngine")
    def test_returns_dict_with_folds(
        self, mock_engine_cls, mock_config, mock_opt_cls, mock_ohlcv, mock_bridge
    ):
        self._setup_mocks(mock_ohlcv, mock_engine_cls, mock_config, mock_opt_cls, mock_bridge)
        result = _run_tune(_default_args())
        assert "folds" in result
        assert len(result["folds"]) == 1

    @patch("src.cli.tune_cli.params_to_strategy_config")
    @patch("src.cli.tune_cli.generate_synthetic_ohlcv")
    @patch("src.cli.tune_cli.WalkForwardOptimizer")
    @patch("src.cli.tune_cli.TunerConfig")
    @patch("src.cli.tune_cli.BacktestEngine")
    def test_returns_best_params(
        self, mock_engine_cls, mock_config, mock_opt_cls, mock_ohlcv, mock_bridge
    ):
        self._setup_mocks(mock_ohlcv, mock_engine_cls, mock_config, mock_opt_cls, mock_bridge)
        result = _run_tune(_default_args())
        assert "best_params" in result
        assert "min_spread_bps" in result["best_params"]

    @patch("src.cli.tune_cli.params_to_strategy_config")
    @patch("src.cli.tune_cli.generate_synthetic_ohlcv")
    @patch("src.cli.tune_cli.WalkForwardOptimizer")
    @patch("src.cli.tune_cli.TunerConfig")
    @patch("src.cli.tune_cli.BacktestEngine")
    def test_returns_strategy_config(
        self, mock_engine_cls, mock_config, mock_opt_cls, mock_ohlcv, mock_bridge
    ):
        self._setup_mocks(mock_ohlcv, mock_engine_cls, mock_config, mock_opt_cls, mock_bridge)
        result = _run_tune(_default_args())
        assert "strategy_config" in result

    @patch("src.cli.tune_cli.params_to_strategy_config")
    @patch("src.cli.tune_cli.generate_synthetic_ohlcv")
    @patch("src.cli.tune_cli.WalkForwardOptimizer")
    @patch("src.cli.tune_cli.TunerConfig")
    @patch("src.cli.tune_cli.BacktestEngine")
    def test_csv_path_uses_file_loader(
        self, mock_engine_cls, mock_config, mock_opt_cls, mock_ohlcv, mock_bridge
    ):
        import numpy as np
        from src.tuning.data_loader import OHLCVWindow
        arr = np.linspace(50000, 51000, 200)
        ohlcv = OHLCVWindow(
            times=np.arange(200, dtype=float),
            opens=arr, highs=arr, lows=arr, closes=arr, volumes=np.ones(200),
        )
        best = _make_fold()
        optimizer = MagicMock()
        optimizer.optimize.return_value = [best]
        optimizer.select_best_fold.return_value = best
        mock_opt_cls.return_value = optimizer
        mock_engine_cls.return_value = MagicMock()
        mock_bridge.return_value = {}

        with patch("src.cli.tune_cli.FileDataLoader") as mock_loader_cls:
            loader = MagicMock()
            loader.load.return_value = ohlcv
            mock_loader_cls.return_value = loader
            result = _run_tune(_default_args(data="./data.csv"))

        loader.load.assert_called_once_with("./data.csv")
        assert "folds" in result


# ---------------------------------------------------------------------------
# _run_tune — shadow mode
# ---------------------------------------------------------------------------


class TestRunTuneShadow:
    @patch("src.cli.tune_cli.ShadowRunner")
    @patch("src.cli.tune_cli.params_to_strategy_config")
    @patch("src.cli.tune_cli.generate_synthetic_ohlcv")
    @patch("src.cli.tune_cli.WalkForwardOptimizer")
    @patch("src.cli.tune_cli.TunerConfig")
    @patch("src.cli.tune_cli.BacktestEngine")
    def test_shadow_flag_calls_shadow_runner(
        self, mock_engine_cls, mock_config, mock_opt_cls, mock_ohlcv,
        mock_bridge, mock_shadow_cls
    ):
        import numpy as np
        from src.tuning.data_loader import OHLCVWindow
        arr = np.linspace(50000, 51000, 200)
        mock_ohlcv.return_value = OHLCVWindow(
            times=np.arange(200, dtype=float),
            opens=arr, highs=arr, lows=arr, closes=arr, volumes=np.ones(200),
        )
        best = _make_fold()
        optimizer = MagicMock()
        optimizer.optimize.return_value = [best]
        optimizer.select_best_fold.return_value = best
        mock_opt_cls.return_value = optimizer
        mock_engine_cls.return_value = MagicMock()
        mock_bridge.return_value = {}

        shadow_instance = MagicMock()
        shadow_result = MagicMock()
        shadow_result.baseline_result = _make_result()
        shadow_result.shadow_result = _make_result()
        shadow_result.evaluation = MagicMock()
        shadow_result.evaluation.sim_real_variance_pct = 1.5
        shadow_result.evaluation.recommendation = "APPLY"
        shadow_instance.evaluate_and_decide.return_value = ("APPLY", shadow_result)
        shadow_instance.print_report = MagicMock()
        mock_shadow_cls.return_value = shadow_instance

        result = _run_tune(_default_args(shadow=True))

        shadow_instance.evaluate_and_decide.assert_called_once()
        assert "shadow" in result
        assert result["shadow"]["decision"] == "APPLY"


# ---------------------------------------------------------------------------
# main() — argument parsing
# ---------------------------------------------------------------------------


class TestMain:
    @patch("src.cli.tune_cli._run_tune")
    def test_main_calls_run_tune(self, mock_run):
        mock_run.return_value = {"folds": [], "best_params": {}}
        with patch("sys.argv", ["tune_cli"]):
            main()
        mock_run.assert_called_once()

    @patch("src.cli.tune_cli._run_tune")
    def test_main_saves_output_json(self, mock_run, tmp_path):
        data = {"folds": [{"fold": 1}], "best_params": {"min_spread_bps": 5.0}}
        mock_run.return_value = data
        out_file = str(tmp_path / "tune_result.json")
        with patch("sys.argv", ["tune_cli", "--output", out_file]):
            main()
        saved = json.loads(Path(out_file).read_text())
        assert saved["best_params"]["min_spread_bps"] == 5.0

    @patch("src.cli.tune_cli._run_tune")
    def test_main_no_output_on_error(self, mock_run, tmp_path):
        """When result has 'error' key, output file should not be written."""
        mock_run.return_value = {"error": "insufficient_data"}
        out_file = str(tmp_path / "tune_result.json")
        with patch("sys.argv", ["tune_cli", "--output", out_file]):
            main()
        assert not Path(out_file).exists()

    @patch("src.cli.tune_cli._run_tune")
    def test_main_parses_trials(self, mock_run):
        mock_run.return_value = {"folds": []}
        with patch("sys.argv", ["tune_cli", "--trials", "25"]):
            main()
        args = mock_run.call_args[0][0]
        assert args.trials == 25

    @patch("src.cli.tune_cli._run_tune")
    def test_main_parses_shadow_flag(self, mock_run):
        mock_run.return_value = {"folds": []}
        with patch("sys.argv", ["tune_cli", "--shadow"]):
            main()
        args = mock_run.call_args[0][0]
        assert args.shadow is True

    @patch("src.cli.tune_cli._run_tune")
    def test_main_default_strategy(self, mock_run):
        mock_run.return_value = {"folds": []}
        with patch("sys.argv", ["tune_cli"]):
            main()
        args = mock_run.call_args[0][0]
        assert args.strategy == "cross_exchange"
