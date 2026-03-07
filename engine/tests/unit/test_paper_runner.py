"""Unit tests for src/cli/paper_runner.py."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.cli.paper_runner import PaperTradingRunner, main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_report(total_trades=5, total_pnl=0.05, win_rate=0.6):
    report = MagicMock()
    report.total_trades = total_trades
    report.total_pnl = total_pnl
    report.win_rate = win_rate
    report.summary.return_value = f"Summary: trades={total_trades} pnl={total_pnl}"
    report.to_dict.return_value = {
        "total_trades": total_trades,
        "total_pnl": total_pnl,
        "win_rate": win_rate,
    }
    return report


# ---------------------------------------------------------------------------
# PaperTradingRunner construction
# ---------------------------------------------------------------------------


class TestPaperTradingRunnerInit:
    def test_defaults(self):
        runner = PaperTradingRunner()
        assert runner._duration == 300
        assert runner._initial_capital == 70.0
        assert runner._running is False

    def test_custom_params(self):
        runner = PaperTradingRunner(
            duration_seconds=60,
            initial_capital=100.0,
            spread_injection_rate=0.3,
            spread_injection_bps=30,
            tick_interval=0.1,
            verbose=True,
        )
        assert runner._duration == 60
        assert runner._initial_capital == 100.0
        assert runner._spread_injection_bps == 30
        assert runner._verbose is True

    def test_trade_log_starts_empty(self):
        runner = PaperTradingRunner()
        assert runner._trade_log == []


# ---------------------------------------------------------------------------
# save_trade_log
# ---------------------------------------------------------------------------


class TestSaveTradeLog:
    def test_no_trades_prints_message(self, capsys, tmp_path):
        runner = PaperTradingRunner()
        runner.save_trade_log(tmp_path / "log.csv")
        out = capsys.readouterr().out
        assert "No trades" in out

    def test_saves_csv_with_trades(self, tmp_path):
        runner = PaperTradingRunner()
        runner._trade_log = [
            {"timestamp": "2026-01-01T00:00:00+00:00", "strategy_id": "arb", "status": "success", "pnl": 0.01},
            {"timestamp": "2026-01-01T00:00:01+00:00", "strategy_id": "arb", "status": "success", "pnl": -0.005},
        ]
        log_path = tmp_path / "trades.csv"
        runner.save_trade_log(log_path)
        content = log_path.read_text()
        assert "timestamp" in content
        assert "strategy_id" in content
        assert "arb" in content

    def test_creates_parent_dirs(self, tmp_path):
        runner = PaperTradingRunner()
        runner._trade_log = [
            {"timestamp": "t", "strategy_id": "s", "status": "ok", "pnl": 0.0}
        ]
        nested = tmp_path / "sub" / "dir" / "log.csv"
        runner.save_trade_log(nested)
        assert nested.exists()


# ---------------------------------------------------------------------------
# PaperTradingRunner.run() — fully mocked
# ---------------------------------------------------------------------------


class TestPaperTradingRunnerRun:
    @pytest.mark.asyncio
    async def test_run_returns_report(self):
        runner = PaperTradingRunner(duration_seconds=0)

        mock_exchange = AsyncMock()
        fake_report = _make_report()

        with patch("src.cli.paper_runner.PaperExchangeAdapter", return_value=mock_exchange), \
             patch("src.cli.paper_runner.AtomicExecutor"), \
             patch("src.cli.paper_runner.TradeRequestConsumer", return_value=AsyncMock()), \
             patch.object(runner, "_start_signal_pipeline", new_callable=AsyncMock, return_value=[]), \
             patch.object(runner._metrics, "get_report", return_value=fake_report):
            report = await runner.run()

        assert report is fake_report

    @pytest.mark.asyncio
    async def test_run_stops_immediately_with_zero_duration(self):
        runner = PaperTradingRunner(duration_seconds=0)

        mock_exchange = AsyncMock()
        fake_report = _make_report(total_trades=0)

        with patch("src.cli.paper_runner.PaperExchangeAdapter", return_value=mock_exchange), \
             patch("src.cli.paper_runner.AtomicExecutor"), \
             patch("src.cli.paper_runner.TradeRequestConsumer", return_value=AsyncMock()), \
             patch.object(runner, "_start_signal_pipeline", new_callable=AsyncMock, return_value=[]), \
             patch.object(runner._metrics, "get_report", return_value=fake_report):
            report = await runner.run()

        assert report.total_trades == 0


# ---------------------------------------------------------------------------
# main() — argument parsing and dispatch
# ---------------------------------------------------------------------------


class TestMain:
    @patch("src.cli.paper_runner.asyncio.run")
    def test_main_default_args(self, mock_asyncio_run):
        mock_asyncio_run.return_value = _make_report(total_trades=5)
        with patch("sys.argv", ["paper_runner"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 0

    @patch("src.cli.paper_runner.asyncio.run")
    def test_main_exit_1_when_no_trades(self, mock_asyncio_run):
        mock_asyncio_run.return_value = _make_report(total_trades=0)
        with patch("sys.argv", ["paper_runner"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 1

    @patch("src.cli.paper_runner.asyncio.run")
    def test_main_saves_report_json(self, mock_asyncio_run, tmp_path):
        report = _make_report(total_trades=3)
        mock_asyncio_run.return_value = report
        out_file = str(tmp_path / "report.json")
        with patch("sys.argv", ["paper_runner", "--save-report", out_file]):
            with pytest.raises(SystemExit):
                main()
        data = json.loads(Path(out_file).read_text())
        assert data["total_trades"] == 3

    @patch("src.cli.paper_runner.asyncio.run")
    def test_main_parses_duration(self, mock_asyncio_run):
        report = _make_report(total_trades=1)
        mock_asyncio_run.return_value = report
        with patch("sys.argv", ["paper_runner", "--duration", "120"]):
            with pytest.raises(SystemExit):
                main()
        mock_asyncio_run.assert_called_once()

    @patch("src.cli.paper_runner.asyncio.run")
    def test_main_saves_trade_log(self, mock_asyncio_run, tmp_path):
        report = _make_report(total_trades=2)
        mock_asyncio_run.return_value = report
        log_file = str(tmp_path / "trades.csv")

        with patch("sys.argv", ["paper_runner", "--save-log", log_file]), \
             patch("src.cli.paper_runner.PaperTradingRunner.save_trade_log") as mock_save:
            with pytest.raises(SystemExit):
                main()
            mock_save.assert_called_once_with(log_file)

    @patch("src.cli.paper_runner.asyncio.run")
    def test_main_verbose_flag(self, mock_asyncio_run):
        report = _make_report(total_trades=1)
        mock_asyncio_run.return_value = report
        with patch("sys.argv", ["paper_runner", "--verbose"]):
            with pytest.raises(SystemExit):
                main()
        mock_asyncio_run.assert_called_once()
