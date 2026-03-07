"""Unit tests for src/cli/sandbox_paper_runner.py."""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.cli.sandbox_paper_runner import SandboxPaperRunner, main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_report_dict(total_trades=3, total_pnl=0.05, win_rate=0.67):
    return {"total_trades": total_trades, "total_pnl": total_pnl, "win_rate": win_rate}


def _make_metrics_report(trades=3, pnl=0.05, win_rate=0.67):
    report = MagicMock()
    report.total_trades = trades
    report.total_pnl = pnl
    report.win_rate = win_rate
    report.summary.return_value = f"trades={trades} pnl={pnl}"
    report.to_dict.return_value = _make_report_dict(trades, pnl, win_rate)
    return report


# ---------------------------------------------------------------------------
# SandboxPaperRunner construction
# ---------------------------------------------------------------------------


class TestSandboxPaperRunnerInit:
    def test_defaults(self):
        runner = SandboxPaperRunner(exchanges=["binance"])
        assert runner._symbol == "BTC/USDT"
        assert runner._duration == 300
        assert runner._initial_capital == 70.0
        assert runner._sandbox is True
        assert runner._verbose is False
        assert runner._running is False

    def test_custom_params(self):
        runner = SandboxPaperRunner(
            exchanges=["binance", "upbit"],
            symbol="ETH/USDT",
            duration=60,
            initial_capital=100.0,
            sandbox=False,
            verbose=True,
        )
        assert runner._exchanges == ["binance", "upbit"]
        assert runner._symbol == "ETH/USDT"
        assert runner._duration == 60
        assert runner._sandbox is False
        assert runner._verbose is True

    def test_metrics_initialized(self):
        runner = SandboxPaperRunner(exchanges=["binance"])
        assert runner._metrics is not None


# ---------------------------------------------------------------------------
# SandboxPaperRunner.run() — paper fallback path (no API keys)
# ---------------------------------------------------------------------------


class TestSandboxPaperRunnerRun:
    @pytest.mark.asyncio
    async def test_run_uses_paper_fallback_when_no_api_key(self):
        """Without API keys, falls back to PaperExchangeAdapter (mocked at source)."""
        runner = SandboxPaperRunner(exchanges=["binance", "upbit"], duration=0)

        mock_adapter = AsyncMock()
        mock_adapter.connect = AsyncMock()
        mock_adapter.disconnect = AsyncMock()

        fake_report = _make_metrics_report()

        import src.execution.paper_adapter as pa_mod
        with patch("os.environ.get", return_value=""), \
             patch.object(pa_mod, "PaperExchangeAdapter", return_value=mock_adapter), \
             patch.object(runner._metrics, "get_report", return_value=fake_report):
            result = await runner.run()

        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_run_returns_dict_with_report_fields(self):
        runner = SandboxPaperRunner(exchanges=["binance"], duration=0)

        mock_adapter = AsyncMock()
        mock_adapter.connect = AsyncMock()
        mock_adapter.disconnect = AsyncMock()

        fake_report = _make_metrics_report(trades=2, pnl=0.03)

        import src.execution.paper_adapter as pa_mod
        with patch("os.environ.get", return_value=""), \
             patch.object(pa_mod, "PaperExchangeAdapter", return_value=mock_adapter), \
             patch.object(runner._metrics, "get_report", return_value=fake_report):
            result = await runner.run()

        assert "total_trades" in result
        assert result["total_trades"] == 2

    @pytest.mark.asyncio
    async def test_run_disconnects_adapters_on_exit(self):
        """Adapter disconnect is called in the finally block."""
        runner = SandboxPaperRunner(exchanges=["binance"], duration=0)

        mock_adapter = AsyncMock()
        mock_adapter.connect = AsyncMock()
        mock_adapter.disconnect = AsyncMock()

        fake_report = _make_metrics_report()

        import src.execution.paper_adapter as pa_mod
        with patch("os.environ.get", return_value=""), \
             patch.object(pa_mod, "PaperExchangeAdapter", return_value=mock_adapter), \
             patch.object(runner._metrics, "get_report", return_value=fake_report):
            await runner.run()

        mock_adapter.disconnect.assert_called()

    @pytest.mark.asyncio
    async def test_run_with_two_exchanges_creates_two_adapters(self):
        runner = SandboxPaperRunner(exchanges=["binance", "upbit"], duration=0)

        adapters = []
        def make_adapter(**kwargs):
            a = AsyncMock()
            a.connect = AsyncMock()
            a.disconnect = AsyncMock()
            adapters.append(a)
            return a

        fake_report = _make_metrics_report()

        import src.execution.paper_adapter as pa_mod
        with patch("os.environ.get", return_value=""), \
             patch.object(pa_mod, "PaperExchangeAdapter", side_effect=make_adapter), \
             patch.object(runner._metrics, "get_report", return_value=fake_report):
            await runner.run()

        assert len(adapters) == 2


# ---------------------------------------------------------------------------
# main() — argument parsing and dispatch
# ---------------------------------------------------------------------------


class TestMain:
    @patch("src.cli.sandbox_paper_runner.asyncio.run")
    def test_main_default_exchanges(self, mock_asyncio_run):
        mock_asyncio_run.return_value = _make_report_dict()
        with patch("sys.argv", ["sandbox_paper_runner"]):
            main()
        mock_asyncio_run.assert_called_once()

    @patch("src.cli.sandbox_paper_runner.asyncio.run")
    def test_main_custom_exchange(self, mock_asyncio_run):
        mock_asyncio_run.return_value = _make_report_dict()
        with patch("sys.argv", ["sandbox_paper_runner", "--exchange", "okx"]):
            main()
        mock_asyncio_run.assert_called_once()

    @patch("src.cli.sandbox_paper_runner.asyncio.run")
    def test_main_saves_report(self, mock_asyncio_run, tmp_path):
        data = _make_report_dict(total_trades=4)
        mock_asyncio_run.return_value = data
        out_file = str(tmp_path / "report.json")
        with patch("sys.argv", ["sandbox_paper_runner", "--save-report", out_file]):
            main()
        saved = json.loads(Path(out_file).read_text())
        assert saved["total_trades"] == 4

    @patch("src.cli.sandbox_paper_runner.asyncio.run")
    def test_main_live_flag_disables_sandbox(self, mock_asyncio_run):
        mock_asyncio_run.return_value = _make_report_dict()
        with patch("sys.argv", ["sandbox_paper_runner", "--live"]):
            with patch("src.cli.sandbox_paper_runner.SandboxPaperRunner") as mock_cls:
                mock_instance = MagicMock()
                mock_cls.return_value = mock_instance
                mock_asyncio_run.return_value = _make_report_dict()
                main()
                call_kwargs = mock_cls.call_args[1]
                assert call_kwargs["sandbox"] is False

    @patch("src.cli.sandbox_paper_runner.asyncio.run")
    def test_main_default_symbol(self, mock_asyncio_run):
        mock_asyncio_run.return_value = _make_report_dict()
        with patch("sys.argv", ["sandbox_paper_runner"]):
            with patch("src.cli.sandbox_paper_runner.SandboxPaperRunner") as mock_cls:
                mock_instance = MagicMock()
                mock_cls.return_value = mock_instance
                mock_asyncio_run.return_value = _make_report_dict()
                main()
                call_kwargs = mock_cls.call_args[1]
                assert call_kwargs["symbol"] == "BTC/USDT"
