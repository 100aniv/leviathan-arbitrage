"""Unit tests for src/cli/backtest_cli.py."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.cli.backtest_cli import _print_result, _run_backtest, _run_optimization, main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(total_pnl=1.0, sharpe=1.2, drawdown=0.01, win_rate=0.65, trades=10):
    from src.tuning.backtest import BacktestResult
    return BacktestResult(
        total_pnl=total_pnl,
        sharpe_ratio=sharpe,
        max_drawdown=drawdown,
        win_rate=win_rate,
        num_trades=trades,
    )


def _make_ohlcv(length=100):
    import numpy as np
    from src.tuning.data_loader import OHLCVWindow
    arr = np.linspace(50000, 51000, length)
    return OHLCVWindow(
        times=np.arange(length, dtype=float),
        opens=arr - 10,
        highs=arr + 20,
        lows=arr - 20,
        closes=arr,
        volumes=np.ones(length),
    )


def _default_args(**kwargs):
    defaults = dict(
        data="synthetic",
        candles=200,
        capital=70.0,
        fee_rate=0.001,
        min_spread_bps=5.0,
        max_position=35.0,
        entry_threshold=0.0005,
        exit_threshold=0.0002,
        stop_loss=0.02,
        injection_rate=0.15,
        injection_bps=30.0,
        optimize=False,
        trials=10,
        train_periods=60,
        val_periods=20,
        output=None,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# _print_result
# ---------------------------------------------------------------------------


class TestPrintResult:
    def test_prints_pass_gate(self, capsys):
        result = _make_result(total_pnl=0.5, drawdown=0.01, win_rate=0.6, trades=5)
        _print_result("Test Label", result)
        out = capsys.readouterr().out
        assert "PASS" in out
        assert "Test Label" in out

    def test_prints_fail_gate_negative_pnl(self, capsys):
        result = _make_result(total_pnl=-1.0)
        _print_result("Fail Test", result)
        out = capsys.readouterr().out
        assert "FAIL" in out

    def test_prints_fail_gate_large_drawdown(self, capsys):
        result = _make_result(total_pnl=1.0, drawdown=0.05)
        _print_result("DD Fail", result)
        out = capsys.readouterr().out
        assert "FAIL" in out

    def test_zero_trades_pass_gate(self, capsys):
        # num_trades=0 means win_rate check is skipped (or 0)
        result = _make_result(total_pnl=0.1, drawdown=0.005, win_rate=0.0, trades=0)
        _print_result("Zero Trades", result)
        out = capsys.readouterr().out
        assert "PASS" in out


# ---------------------------------------------------------------------------
# _run_backtest
# ---------------------------------------------------------------------------


class TestRunBacktest:
    @patch("src.cli.backtest_cli.generate_synthetic_spreads")
    @patch("src.cli.backtest_cli.BacktestEngine")
    @patch("src.cli.backtest_cli.generate_synthetic_ohlcv")
    def test_synthetic_returns_dict(self, mock_ohlcv, mock_engine_cls, mock_spreads):
        ohlcv = _make_ohlcv()
        mock_ohlcv.return_value = ohlcv
        result_obj = _make_result()
        engine = MagicMock()
        engine.run.return_value = result_obj
        engine.run_on_spreads.return_value = _make_result()
        mock_engine_cls.return_value = engine
        mock_spreads.return_value = []

        args = _default_args(data="synthetic")
        out = _run_backtest(args)

        assert out["data_source"] == "synthetic"
        assert "result" in out
        assert out["result"]["total_pnl"] == 1.0

    @patch("src.cli.backtest_cli.BacktestEngine")
    @patch("src.cli.backtest_cli.FileDataLoader")
    def test_csv_path_calls_loader(self, mock_loader_cls, mock_engine_cls):
        ohlcv = _make_ohlcv()
        loader = MagicMock()
        loader.load.return_value = ohlcv
        mock_loader_cls.return_value = loader

        result_obj = _make_result()
        engine = MagicMock()
        engine.run.return_value = result_obj
        mock_engine_cls.return_value = engine

        args = _default_args(data="./data.csv")
        out = _run_backtest(args)

        loader.load.assert_called_once_with("./data.csv")
        assert out["data_source"] == "./data.csv"

    @patch("src.cli.backtest_cli.generate_synthetic_spreads")
    @patch("src.cli.backtest_cli.BacktestEngine")
    @patch("src.cli.backtest_cli.generate_synthetic_ohlcv")
    def test_result_contains_elapsed(self, mock_ohlcv, mock_engine_cls, mock_spreads):
        mock_ohlcv.return_value = _make_ohlcv()
        engine = MagicMock()
        engine.run.return_value = _make_result()
        engine.run_on_spreads.return_value = _make_result()
        mock_engine_cls.return_value = engine
        mock_spreads.return_value = []

        out = _run_backtest(_default_args())
        assert "elapsed_seconds" in out
        assert out["elapsed_seconds"] >= 0


# ---------------------------------------------------------------------------
# _run_optimization
# ---------------------------------------------------------------------------


class TestRunOptimization:
    @patch("src.cli.backtest_cli.generate_synthetic_ohlcv")
    def test_insufficient_data_returns_error(self, mock_ohlcv):
        """When optimizer returns empty results, return error dict."""
        mock_ohlcv.return_value = _make_ohlcv(5)

        with patch("src.tuning.optimizer.WalkForwardOptimizer") as mock_opt_cls:
            optimizer = MagicMock()
            optimizer.optimize.return_value = []
            mock_opt_cls.return_value = optimizer

            with patch("src.tuning.optimizer.TunerConfig"):
                from src.cli.backtest_cli import _run_optimization
                args = _default_args(optimize=True, candles=5)
                out = _run_optimization(args)

        assert out.get("error") == "insufficient_data"

    @patch("src.cli.backtest_cli.generate_synthetic_ohlcv")
    def test_optimization_returns_folds(self, mock_ohlcv):
        mock_ohlcv.return_value = _make_ohlcv()
        from src.tuning.backtest import StrategyParams

        fold = MagicMock()
        fold.best_params = StrategyParams()
        fold.train_result = _make_result()
        fold.val_result = _make_result()
        fold.shadow_mode = False

        with patch("src.tuning.optimizer.WalkForwardOptimizer") as mock_opt_cls:
            optimizer = MagicMock()
            optimizer.optimize.return_value = [fold]
            optimizer.select_best_fold.return_value = fold
            mock_opt_cls.return_value = optimizer

            with patch("src.tuning.optimizer.TunerConfig"):
                args = _default_args(optimize=True)
                out = _run_optimization(args)

        assert "folds" in out
        assert len(out["folds"]) == 1


# ---------------------------------------------------------------------------
# main() — argument parsing and dispatch
# ---------------------------------------------------------------------------


class TestMain:
    @patch("src.cli.backtest_cli._run_backtest")
    def test_main_calls_run_backtest_by_default(self, mock_run):
        mock_run.return_value = {"data_source": "synthetic"}
        with patch("sys.argv", ["backtest_cli"]):
            main()
        mock_run.assert_called_once()

    @patch("src.cli.backtest_cli._run_optimization")
    def test_main_calls_optimization_with_flag(self, mock_opt):
        mock_opt.return_value = {"folds": []}
        with patch("sys.argv", ["backtest_cli", "--optimize"]):
            main()
        mock_opt.assert_called_once()

    @patch("src.cli.backtest_cli._run_backtest")
    def test_main_saves_output_json(self, mock_run, tmp_path):
        result_data = {"data_source": "synthetic", "candles": 100}
        mock_run.return_value = result_data
        out_file = str(tmp_path / "result.json")
        with patch("sys.argv", ["backtest_cli", "--output", out_file]):
            main()
        saved = json.loads(Path(out_file).read_text())
        assert saved["data_source"] == "synthetic"

    def test_main_parses_candles_arg(self):
        with patch("sys.argv", ["backtest_cli", "--candles", "500"]):
            with patch("src.cli.backtest_cli._run_backtest") as mock_run:
                mock_run.return_value = {}
                main()
                args = mock_run.call_args[0][0]
                assert args.candles == 500

    def test_main_parses_capital_arg(self):
        with patch("sys.argv", ["backtest_cli", "--capital", "200.0"]):
            with patch("src.cli.backtest_cli._run_backtest") as mock_run:
                mock_run.return_value = {}
                main()
                args = mock_run.call_args[0][0]
                assert args.capital == 200.0
