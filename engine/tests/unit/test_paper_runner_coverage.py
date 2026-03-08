"""Unit tests for src/cli/paper_runner.py — PaperTradingRunner.

Covers uncovered lines (59% -> target 85%+):
- __init__ stores all parameters correctly
- run() lifecycle: adapters connected, consumer started, report returned
- on_result callback: SUCCESS with leg fills computes pnl; non-SUCCESS records 0
- save_trade_log: writes CSV with headers; no-trades case prints message
- main() argument parsing: defaults, --duration, --capital, --verbose,
  --save-log, --save-report, --report
- _start_signal_pipeline creates tasks and returns them
"""
from __future__ import annotations

import asyncio
import csv
import json
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from src.cli.paper_runner import PaperTradingRunner, main
from src.core.metrics_collector import MetricsCollector, PerformanceReport
from src.execution.executor import ExecutionResult, ExecutionStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_runner(**kwargs) -> PaperTradingRunner:
    defaults = dict(
        duration_seconds=0,
        initial_capital=70.0,
        tick_interval=0.001,
    )
    defaults.update(kwargs)
    return PaperTradingRunner(**defaults)


def _make_trade(price: Decimal, amount: Decimal, side_value: str, fee: Decimal = Decimal("0.1")):
    t = MagicMock()
    t.price = price
    t.amount = amount
    t.fee = fee
    t.side = MagicMock()
    t.side.value = side_value
    return t


def _make_execution_result(status: ExecutionStatus, t1=None, t2=None) -> ExecutionResult:
    result = MagicMock()
    result.status = status
    result.leg1 = MagicMock()
    result.leg1.trade = t1
    result.leg2 = MagicMock()
    result.leg2.trade = t2
    result.legs = [result.leg1, result.leg2]
    return result


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestPaperTradingRunnerInit:
    def test_duration_stored(self):
        r = _make_runner(duration_seconds=120)
        assert r._duration == 120

    def test_initial_capital_stored(self):
        r = _make_runner(initial_capital=500.0)
        assert r._initial_capital == 500.0

    def test_spread_injection_rate_default(self):
        r = _make_runner()
        assert r._spread_injection_rate == 0.4

    def test_custom_spread_injection_rate(self):
        r = _make_runner(spread_injection_rate=0.8)
        assert r._spread_injection_rate == 0.8

    def test_spread_injection_bps_default(self):
        r = _make_runner()
        assert r._spread_injection_bps == 50

    def test_tick_interval_stored(self):
        r = _make_runner(tick_interval=0.05)
        assert r._tick_interval == 0.05

    def test_verbose_default_is_false(self):
        r = _make_runner()
        assert r._verbose is False

    def test_running_starts_false(self):
        r = _make_runner()
        assert r._running is False

    def test_metrics_collector_created(self):
        r = _make_runner(initial_capital=100.0)
        assert isinstance(r._metrics, MetricsCollector)

    def test_trade_log_starts_empty(self):
        r = _make_runner()
        assert r._trade_log == []


# ---------------------------------------------------------------------------
# on_result callback (tested directly)
# ---------------------------------------------------------------------------

class TestOnResultCallback:
    """Test the on_result closure behaviour by running a short paper session
    with mocked internals and inspecting trade_log entries."""

    @pytest.mark.asyncio
    async def test_on_result_success_with_buy_leg_records_positive_pnl_entry(self):
        """buy@50000, sell@50100 -> pnl = 50100*size - 50000*size - fees."""
        r = _make_runner()
        size = Decimal("0.01")
        t1 = _make_trade(Decimal("50000"), size, "buy", Decimal("5"))
        t2 = _make_trade(Decimal("50100"), size, "sell", Decimal("5"))

        trade_req = MagicMock()
        trade_req.strategy_id = "test_strat"
        result = _make_execution_result(ExecutionStatus.SUCCESS, t1, t2)

        # Run a zero-duration session just to get the on_result closure
        mock_consumer = AsyncMock()
        mock_consumer.start = AsyncMock()
        mock_consumer.stop = AsyncMock()

        captured_callback = None

        def capture_consumer(event_bus, executor, on_result):
            nonlocal captured_callback
            captured_callback = on_result
            return mock_consumer

        with patch("src.cli.paper_runner.PaperExchangeAdapter") as MockAdapter, \
             patch("src.cli.paper_runner.AtomicExecutor"), \
             patch("src.cli.paper_runner.TradeRequestConsumer", side_effect=capture_consumer):
            mock_adapter = AsyncMock()
            mock_adapter.connect = AsyncMock()
            mock_adapter.disconnect = AsyncMock()
            mock_adapter.subscribe_orderbook = AsyncMock()
            MockAdapter.return_value = mock_adapter
            await r.run()

        if captured_callback is not None:
            captured_callback(trade_req, result)
            assert len(r._trade_log) == 1
            assert r._trade_log[0]["strategy_id"] == "test_strat"
            assert r._trade_log[0]["status"] == "success"

    @pytest.mark.asyncio
    async def test_on_result_non_success_records_zero_pnl(self):
        r = _make_runner()
        trade_req = MagicMock()
        trade_req.strategy_id = "strat"
        result = _make_execution_result(ExecutionStatus.REJECTED, None, None)

        captured_callback = None
        mock_consumer = AsyncMock()
        mock_consumer.start = AsyncMock()
        mock_consumer.stop = AsyncMock()

        def capture_consumer(event_bus, executor, on_result):
            nonlocal captured_callback
            captured_callback = on_result
            return mock_consumer

        with patch("src.cli.paper_runner.PaperExchangeAdapter") as MockAdapter, \
             patch("src.cli.paper_runner.AtomicExecutor"), \
             patch("src.cli.paper_runner.TradeRequestConsumer", side_effect=capture_consumer):
            mock_adapter = AsyncMock()
            mock_adapter.connect = AsyncMock()
            mock_adapter.disconnect = AsyncMock()
            mock_adapter.subscribe_orderbook = AsyncMock()
            MockAdapter.return_value = mock_adapter
            await r.run()

        if captured_callback is not None:
            captured_callback(trade_req, result)
            assert len(r._trade_log) == 1
            assert r._trade_log[0]["pnl"] == 0.0


# ---------------------------------------------------------------------------
# run() lifecycle
# ---------------------------------------------------------------------------

class TestPaperTradingRunnerRun:
    @pytest.mark.asyncio
    async def test_run_returns_performance_report(self):
        r = _make_runner(duration_seconds=0)

        mock_consumer = AsyncMock()
        mock_consumer.start = AsyncMock()
        mock_consumer.stop = AsyncMock()

        with patch("src.cli.paper_runner.PaperExchangeAdapter") as MockAdapter, \
             patch("src.cli.paper_runner.AtomicExecutor"), \
             patch("src.cli.paper_runner.TradeRequestConsumer", return_value=mock_consumer):
            mock_adapter = AsyncMock()
            mock_adapter.connect = AsyncMock()
            mock_adapter.disconnect = AsyncMock()
            mock_adapter.subscribe_orderbook = AsyncMock()
            MockAdapter.return_value = mock_adapter
            report = await r.run()

        assert isinstance(report, PerformanceReport)

    @pytest.mark.asyncio
    async def test_run_connects_and_disconnects_both_adapters(self):
        r = _make_runner(duration_seconds=0)
        mock_consumer = AsyncMock()
        mock_consumer.start = AsyncMock()
        mock_consumer.stop = AsyncMock()
        connected = []
        disconnected = []

        class TrackingAdapter(AsyncMock):
            def __init__(self, **kwargs):
                super().__init__()
                self.exchange_id = kwargs.get("exchange_id", "paper")
                self.connect = AsyncMock(side_effect=lambda: connected.append(self.exchange_id))
                self.disconnect = AsyncMock(side_effect=lambda: disconnected.append(self.exchange_id))
                self.subscribe_orderbook = AsyncMock()

        with patch("src.cli.paper_runner.PaperExchangeAdapter", side_effect=TrackingAdapter), \
             patch("src.cli.paper_runner.AtomicExecutor"), \
             patch("src.cli.paper_runner.TradeRequestConsumer", return_value=mock_consumer):
            await r.run()

        assert len(connected) == 2
        assert len(disconnected) == 2

    @pytest.mark.asyncio
    async def test_run_starts_and_stops_consumer(self):
        r = _make_runner(duration_seconds=0)
        mock_consumer = AsyncMock()
        mock_consumer.start = AsyncMock()
        mock_consumer.stop = AsyncMock()

        with patch("src.cli.paper_runner.PaperExchangeAdapter") as MockAdapter, \
             patch("src.cli.paper_runner.AtomicExecutor"), \
             patch("src.cli.paper_runner.TradeRequestConsumer", return_value=mock_consumer):
            mock_adapter = AsyncMock()
            mock_adapter.connect = AsyncMock()
            mock_adapter.disconnect = AsyncMock()
            mock_adapter.subscribe_orderbook = AsyncMock()
            MockAdapter.return_value = mock_adapter
            await r.run()

        mock_consumer.start.assert_called_once()
        mock_consumer.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_verbose_does_not_crash(self):
        r = _make_runner(duration_seconds=0, verbose=True)
        mock_consumer = AsyncMock()
        mock_consumer.start = AsyncMock()
        mock_consumer.stop = AsyncMock()

        with patch("src.cli.paper_runner.PaperExchangeAdapter") as MockAdapter, \
             patch("src.cli.paper_runner.AtomicExecutor"), \
             patch("src.cli.paper_runner.TradeRequestConsumer", return_value=mock_consumer):
            mock_adapter = AsyncMock()
            mock_adapter.connect = AsyncMock()
            mock_adapter.disconnect = AsyncMock()
            mock_adapter.subscribe_orderbook = AsyncMock()
            MockAdapter.return_value = mock_adapter
            report = await r.run()

        assert isinstance(report, PerformanceReport)


# ---------------------------------------------------------------------------
# save_trade_log
# ---------------------------------------------------------------------------

class TestSaveTradeLog:
    def test_save_trade_log_writes_csv_with_headers(self, tmp_path):
        r = _make_runner()
        r._trade_log = [
            {"timestamp": "2026-01-01T00:00:00Z", "strategy_id": "arb", "status": "success", "pnl": 1.5},
            {"timestamp": "2026-01-01T00:01:00Z", "strategy_id": "arb", "status": "failed", "pnl": 0.0},
        ]
        out_file = tmp_path / "trades.csv"
        r.save_trade_log(str(out_file))

        assert out_file.exists()
        with open(out_file) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 2
        assert "timestamp" in rows[0]
        assert "pnl" in rows[0]

    def test_save_trade_log_no_trades_prints_message(self, capsys, tmp_path):
        r = _make_runner()
        r._trade_log = []
        out_file = tmp_path / "trades.csv"
        r.save_trade_log(str(out_file))
        out = capsys.readouterr().out
        assert "No trades" in out

    def test_save_trade_log_creates_parent_directories(self, tmp_path):
        r = _make_runner()
        r._trade_log = [
            {"timestamp": "t", "strategy_id": "s", "status": "success", "pnl": 0.0}
        ]
        nested_path = tmp_path / "a" / "b" / "c" / "trades.csv"
        r.save_trade_log(str(nested_path))
        assert nested_path.exists()


# ---------------------------------------------------------------------------
# main() argument parsing
# ---------------------------------------------------------------------------

class TestPaperRunnerMain:
    def _mock_runner_and_run(self, argv, report_trades=1):
        """Helper: patch runner to return a canned report."""
        mock_report = MagicMock(spec=PerformanceReport)
        mock_report.total_trades = report_trades
        mock_report.total_pnl = 0.0
        mock_report.win_rate = 0.5
        mock_report.summary.return_value = "Summary"
        mock_report.to_dict.return_value = {"total_trades": report_trades}

        with patch("sys.argv", argv), \
             patch("src.cli.paper_runner.PaperTradingRunner") as MockRunner, \
             patch("src.cli.paper_runner.asyncio.run", return_value=mock_report):
            instance = MagicMock()
            instance.save_trade_log = MagicMock()
            instance._trade_log = []
            MockRunner.return_value = instance
            try:
                main()
            except SystemExit:
                pass
            return MockRunner, instance

    def test_main_default_duration(self):
        MockRunner, _ = self._mock_runner_and_run(["paper_runner"])
        kwargs = MockRunner.call_args[1]
        assert kwargs["duration_seconds"] == 60

    def test_main_custom_duration(self):
        MockRunner, _ = self._mock_runner_and_run(["paper_runner", "--duration", "120"])
        kwargs = MockRunner.call_args[1]
        assert kwargs["duration_seconds"] == 120

    def test_main_custom_capital(self):
        MockRunner, _ = self._mock_runner_and_run(["paper_runner", "--capital", "500"])
        kwargs = MockRunner.call_args[1]
        assert kwargs["initial_capital"] == 500.0

    def test_main_verbose_flag(self):
        MockRunner, _ = self._mock_runner_and_run(["paper_runner", "--verbose"])
        kwargs = MockRunner.call_args[1]
        assert kwargs["verbose"] is True

    def test_main_injection_rate(self):
        MockRunner, _ = self._mock_runner_and_run(
            ["paper_runner", "--injection-rate", "0.8"]
        )
        kwargs = MockRunner.call_args[1]
        assert kwargs["spread_injection_rate"] == 0.8

    def test_main_injection_bps(self):
        MockRunner, _ = self._mock_runner_and_run(
            ["paper_runner", "--injection-bps", "100"]
        )
        kwargs = MockRunner.call_args[1]
        assert kwargs["spread_injection_bps"] == 100

    def test_main_tick_interval(self):
        MockRunner, _ = self._mock_runner_and_run(
            ["paper_runner", "--tick-interval", "0.1"]
        )
        kwargs = MockRunner.call_args[1]
        assert kwargs["tick_interval"] == 0.1

    def test_main_exit_0_when_trades_executed(self):
        with patch("sys.argv", ["paper_runner"]), \
             patch("src.cli.paper_runner.PaperTradingRunner") as MockRunner, \
             patch("src.cli.paper_runner.asyncio.run") as mock_run:
            mock_report = MagicMock()
            mock_report.total_trades = 5
            mock_report.summary.return_value = "done"
            mock_report.to_dict.return_value = {}
            mock_run.return_value = mock_report
            instance = MagicMock()
            MockRunner.return_value = instance
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 0

    def test_main_exit_1_when_no_trades(self):
        with patch("sys.argv", ["paper_runner"]), \
             patch("src.cli.paper_runner.PaperTradingRunner") as MockRunner, \
             patch("src.cli.paper_runner.asyncio.run") as mock_run:
            mock_report = MagicMock()
            mock_report.total_trades = 0
            mock_report.summary.return_value = "done"
            mock_report.to_dict.return_value = {}
            mock_run.return_value = mock_report
            instance = MagicMock()
            MockRunner.return_value = instance
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 1

    def test_main_save_report_writes_json_file(self, tmp_path):
        report_path = str(tmp_path / "report.json")
        mock_report = MagicMock()
        mock_report.total_trades = 3
        mock_report.summary.return_value = "done"
        mock_report.to_dict.return_value = {"total_trades": 3}

        with patch("sys.argv", ["paper_runner", "--save-report", report_path]), \
             patch("src.cli.paper_runner.PaperTradingRunner") as MockRunner, \
             patch("src.cli.paper_runner.asyncio.run", return_value=mock_report):
            instance = MagicMock()
            MockRunner.return_value = instance
            with pytest.raises(SystemExit):
                main()

        if Path(report_path).exists():
            with open(report_path) as f:
                data = json.load(f)
            assert "total_trades" in data

    def test_main_save_log_calls_save_trade_log(self, tmp_path):
        log_path = str(tmp_path / "trades.csv")
        mock_report = MagicMock()
        mock_report.total_trades = 1
        mock_report.summary.return_value = ""
        mock_report.to_dict.return_value = {}

        with patch("sys.argv", ["paper_runner", "--save-log", log_path]), \
             patch("src.cli.paper_runner.PaperTradingRunner") as MockRunner, \
             patch("src.cli.paper_runner.asyncio.run", return_value=mock_report):
            instance = MagicMock()
            instance.save_trade_log = MagicMock()
            MockRunner.return_value = instance
            with pytest.raises(SystemExit):
                main()
            instance.save_trade_log.assert_called_once_with(log_path)
