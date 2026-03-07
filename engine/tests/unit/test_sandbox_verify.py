"""Unit tests for src/cli/sandbox_verify.py."""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.cli.sandbox_verify import SandboxVerifier, main


# ---------------------------------------------------------------------------
# SandboxVerifier construction
# ---------------------------------------------------------------------------


class TestSandboxVerifierInit:
    def test_defaults(self):
        v = SandboxVerifier(exchange_id="binance", symbol="BTC/USDT")
        assert v._exchange_id == "binance"
        assert v._symbol == "BTC/USDT"
        assert v._duration == 60
        assert v._sandbox is True
        assert v._running is False
        assert v._tick_count == 0

    def test_custom_params(self):
        v = SandboxVerifier(
            exchange_id="upbit",
            symbol="BTC/KRW",
            duration=120,
            sandbox=False,
        )
        assert v._exchange_id == "upbit"
        assert v._duration == 120
        assert v._sandbox is False

    def test_metrics_start_empty(self):
        v = SandboxVerifier(exchange_id="binance", symbol="BTC/USDT")
        assert v._latencies == []
        assert v._spreads == []
        assert v._prices == []
        assert v._errors == []


# ---------------------------------------------------------------------------
# _build_report
# ---------------------------------------------------------------------------


class TestBuildReport:
    def test_empty_metrics_success(self):
        v = SandboxVerifier(exchange_id="binance", symbol="BTC/USDT", duration=10)
        report = v._build_report(success=True)
        assert report["success"] is True
        assert report["tick_count"] == 0
        assert report["error_count"] == 0
        assert "latency_ms" not in report
        assert "spread_bps" not in report

    def test_with_latency_data(self):
        v = SandboxVerifier(exchange_id="binance", symbol="BTC/USDT")
        v._tick_count = 10
        v._latencies = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
        report = v._build_report(success=True)
        assert "latency_ms" in report
        assert report["latency_ms"]["mean"] == 55.0
        assert report["latency_ms"]["min"] == 10.0
        assert report["latency_ms"]["max"] == 100.0

    def test_with_spread_data(self):
        v = SandboxVerifier(exchange_id="binance", symbol="BTC/USDT")
        v._tick_count = 5
        v._spreads = [5.0, 10.0, 15.0, 20.0, 25.0]
        report = v._build_report(success=True)
        assert "spread_bps" in report
        assert report["spread_bps"]["min"] == 5.0
        assert report["spread_bps"]["max"] == 25.0

    def test_with_price_data(self):
        v = SandboxVerifier(exchange_id="binance", symbol="BTC/USDT")
        v._tick_count = 3
        v._prices = [50000.0, 50100.0, 50200.0]
        report = v._build_report(success=True)
        assert "price" in report
        assert report["price"]["first"] == 50000.0
        assert report["price"]["last"] == 50200.0

    def test_health_score_low_tick_count(self):
        v = SandboxVerifier(exchange_id="binance", symbol="BTC/USDT")
        v._tick_count = 2  # < 5 -> health_score -= 0.5
        report = v._build_report(success=True)
        assert report["health_score"] == 0.5

    def test_health_score_high_errors(self):
        v = SandboxVerifier(exchange_id="binance", symbol="BTC/USDT")
        v._tick_count = 10
        v._latencies = [50.0] * 10
        # >10% error rate
        v._errors = ["err"] * 5
        report = v._build_report(success=True)
        assert report["health_score"] <= 0.7

    def test_failure_report(self):
        v = SandboxVerifier(exchange_id="binance", symbol="BTC/USDT")
        report = v._build_report(success=False)
        assert report["success"] is False


# ---------------------------------------------------------------------------
# print_report
# ---------------------------------------------------------------------------


class TestPrintReport:
    def test_prints_exchange_and_symbol(self, capsys):
        v = SandboxVerifier(exchange_id="binance", symbol="BTC/USDT")
        report = {
            "exchange_id": "binance",
            "symbol": "BTC/USDT",
            "sandbox": True,
            "success": True,
            "duration_seconds": 30,
            "tick_count": 10,
            "error_count": 0,
            "health_score": 1.0,
        }
        v.print_report(report)
        out = capsys.readouterr().out
        assert "binance" in out
        assert "BTC/USDT" in out
        assert "HEALTHY" in out

    def test_prints_degraded_status(self, capsys):
        v = SandboxVerifier(exchange_id="binance", symbol="BTC/USDT")
        report = {
            "exchange_id": "binance",
            "symbol": "BTC/USDT",
            "sandbox": True,
            "success": False,
            "duration_seconds": 30,
            "tick_count": 2,
            "error_count": 3,
            "health_score": 0.7,
        }
        v.print_report(report)
        out = capsys.readouterr().out
        assert "DEGRADED" in out

    def test_prints_latency_section(self, capsys):
        v = SandboxVerifier(exchange_id="binance", symbol="BTC/USDT")
        report = {
            "exchange_id": "binance",
            "symbol": "BTC/USDT",
            "sandbox": True,
            "success": True,
            "duration_seconds": 30,
            "tick_count": 10,
            "error_count": 0,
            "health_score": 1.0,
            "latency_ms": {"mean": 50.0, "median": 48.0, "p95": 90.0, "min": 10.0, "max": 100.0},
        }
        v.print_report(report)
        out = capsys.readouterr().out
        assert "Latency" in out
        assert "50.0" in out


# ---------------------------------------------------------------------------
# run() — paper fallback (no API key)
# ---------------------------------------------------------------------------


class TestSandboxVerifierRun:
    @pytest.mark.asyncio
    async def test_run_paper_fallback_when_no_api_key(self):
        v = SandboxVerifier(exchange_id="binance", symbol="BTC/USDT", duration=0)

        with patch.dict("os.environ", {"BINANCE_API_KEY": "", "BINANCE_SECRET": ""}, clear=False), \
             patch("src.cli.sandbox_verify.SandboxVerifier._run_paper_fallback",
                   new_callable=AsyncMock) as mock_fallback:
            mock_fallback.return_value = {"success": True, "tick_count": 0, "health_score": 1.0}
            result = await v.run()

        mock_fallback.assert_called_once()
        assert result["success"] is True


# ---------------------------------------------------------------------------
# main() — argument parsing
# ---------------------------------------------------------------------------


class TestMain:
    @patch("src.cli.sandbox_verify.asyncio.run")
    def test_main_default_exchange(self, mock_asyncio_run):
        report = {"health_score": 1.0, "success": True}
        mock_asyncio_run.return_value = report
        with patch("sys.argv", ["sandbox_verify"]), \
             patch.object(SandboxVerifier, "print_report"):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 0

    @patch("src.cli.sandbox_verify.asyncio.run")
    def test_main_exit_1_on_low_health(self, mock_asyncio_run):
        report = {"health_score": 0.3, "success": False}
        mock_asyncio_run.return_value = report
        with patch("sys.argv", ["sandbox_verify"]), \
             patch.object(SandboxVerifier, "print_report"):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 1

    @patch("src.cli.sandbox_verify.SandboxVerifier")
    @patch("src.cli.sandbox_verify.asyncio.run")
    def test_main_live_flag_disables_sandbox(self, mock_asyncio_run, mock_cls):
        report = {"health_score": 1.0}
        mock_asyncio_run.return_value = report
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        with patch("sys.argv", ["sandbox_verify", "--live"]):
            with pytest.raises(SystemExit):
                main()
        call_kwargs = mock_cls.call_args[1]
        assert call_kwargs["sandbox"] is False

    @patch("src.cli.sandbox_verify.SandboxVerifier")
    @patch("src.cli.sandbox_verify.asyncio.run")
    def test_main_custom_symbol(self, mock_asyncio_run, mock_cls):
        report = {"health_score": 1.0}
        mock_asyncio_run.return_value = report
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        with patch("sys.argv", ["sandbox_verify", "--symbol", "ETH/USDT"]):
            with pytest.raises(SystemExit):
                main()
        call_kwargs = mock_cls.call_args[1]
        assert call_kwargs["symbol"] == "ETH/USDT"
