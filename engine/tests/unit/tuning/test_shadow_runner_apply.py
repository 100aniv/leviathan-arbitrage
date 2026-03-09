"""Tests for ShadowRunner.apply_decision and DataLoader execution log methods (US-046)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tuning.backtest import StrategyParams
from src.tuning.data_loader import DataLoader, OHLCVWindow, SpreadRecord
from src.tuning.shadow_runner import ShadowResult, ShadowRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_shadow_result(strategy_id: str = "cross_exchange") -> ShadowResult:
    """Build a minimal ShadowResult for use in tests."""
    mock_baseline = MagicMock()
    mock_baseline.total_pnl = 0.5
    mock_baseline.sharpe_ratio = 1.0
    mock_baseline.win_rate = 0.6
    mock_baseline.num_trades = 80
    mock_baseline.max_drawdown = 0.03

    mock_shadow = MagicMock()
    mock_shadow.total_pnl = 1.2
    mock_shadow.sharpe_ratio = 1.8
    mock_shadow.win_rate = 0.72
    mock_shadow.num_trades = 100
    mock_shadow.max_drawdown = 0.02

    mock_evaluation = MagicMock()
    mock_evaluation.recommendation = "APPLY: improved metrics"
    mock_evaluation.sim_real_variance_pct = 5.0
    mock_evaluation.t_statistic = 2.5
    mock_evaluation.p_value = 0.02
    mock_evaluation.is_significant = True
    mock_evaluation.passes_variance_check = True

    # Use a real StrategyParams so _mark_for_monitoring can JSON-serialize __dict__
    real_params = StrategyParams(
        min_spread_bps=5.0,
        max_position_size=100.0,
        entry_threshold=0.001,
        exit_threshold=0.0005,
        stop_loss_pct=0.02,
    )

    return ShadowResult(
        strategy_id=strategy_id,
        shadow_params=real_params,
        baseline_result=mock_baseline,
        shadow_result=mock_shadow,
        evaluation=mock_evaluation,
        config_to_apply={"min_spread_bps": 5.0, "max_position_size": 100.0},
    )


def _make_runner(tmp_path: Path) -> ShadowRunner:
    """Create a ShadowRunner with paths redirected to tmp_path."""
    runner = ShadowRunner()
    # Redirect file paths to tmp_path so tests are hermetic
    runner._params_path = tmp_path / "strategy_params.json"
    return runner


# ---------------------------------------------------------------------------
# apply_decision — APPLY
# ---------------------------------------------------------------------------


class TestApplyDecisionApply:
    @pytest.mark.asyncio
    async def test_calls_apply_params_and_sends_telegram_alert(self, tmp_path):
        """apply_decision routes APPLY → _apply_params called + send_alert called."""
        runner = _make_runner(tmp_path)
        result = _make_shadow_result("cross_exchange")

        runner.evaluate_and_decide = MagicMock(return_value=("APPLY", result))
        runner._apply_params = MagicMock()
        runner._mark_for_monitoring = MagicMock()
        runner._alerter.send_alert = AsyncMock(return_value=True)

        await runner.apply_decision(
            strategy_id="cross_exchange",
            strategy_type="cross_exchange",
            baseline_params=MagicMock(),
            shadow_params=MagicMock(),
        )

        runner._apply_params.assert_called_once_with("cross_exchange", result)
        runner._mark_for_monitoring.assert_not_called()
        runner._alerter.send_alert.assert_awaited_once()


# ---------------------------------------------------------------------------
# apply_decision — MONITOR
# ---------------------------------------------------------------------------


class TestApplyDecisionMonitor:
    @pytest.mark.asyncio
    async def test_calls_mark_for_monitoring_and_sends_telegram_alert(self, tmp_path):
        """apply_decision routes MONITOR → _mark_for_monitoring called + send_alert called."""
        runner = _make_runner(tmp_path)
        result = _make_shadow_result("triangular")

        runner.evaluate_and_decide = MagicMock(return_value=("MONITOR", result))
        runner._apply_params = MagicMock()
        runner._mark_for_monitoring = MagicMock()
        runner._alerter.send_alert = AsyncMock(return_value=True)

        await runner.apply_decision(
            strategy_id="triangular",
            strategy_type="triangular",
            baseline_params=MagicMock(),
            shadow_params=MagicMock(),
        )

        runner._mark_for_monitoring.assert_called_once_with("triangular", result)
        runner._apply_params.assert_not_called()
        runner._alerter.send_alert.assert_awaited_once()


# ---------------------------------------------------------------------------
# apply_decision — REJECT
# ---------------------------------------------------------------------------


class TestApplyDecisionReject:
    @pytest.mark.asyncio
    async def test_sends_telegram_alert_only_without_any_file_writes(self, tmp_path):
        """apply_decision routes REJECT → only send_alert called, no file mutations."""
        runner = _make_runner(tmp_path)
        result = _make_shadow_result("spot_futures")

        runner.evaluate_and_decide = MagicMock(return_value=("REJECT", result))
        runner._apply_params = MagicMock()
        runner._mark_for_monitoring = MagicMock()
        runner._alerter.send_alert = AsyncMock(return_value=True)

        await runner.apply_decision(
            strategy_id="spot_futures",
            strategy_type="spot_futures",
            baseline_params=MagicMock(),
            shadow_params=MagicMock(),
        )

        runner._apply_params.assert_not_called()
        runner._mark_for_monitoring.assert_not_called()
        runner._alerter.send_alert.assert_awaited_once()


# ---------------------------------------------------------------------------
# _apply_params
# ---------------------------------------------------------------------------


class TestApplyParams:
    def test_creates_new_json_file_when_config_does_not_exist(self, tmp_path):
        """_apply_params creates strategy_params.json when file does not exist."""
        runner = _make_runner(tmp_path)
        result = _make_shadow_result("cross_exchange")

        assert not runner._params_path.exists()

        runner._apply_params("cross_exchange", result)

        assert runner._params_path.exists()
        data = json.loads(runner._params_path.read_text())
        assert "cross_exchange" in data

    def test_updates_only_target_strategy_and_preserves_others(self, tmp_path):
        """_apply_params updates target strategy entry while leaving other strategies intact."""
        runner = _make_runner(tmp_path)
        existing = {
            "cross_exchange": {"min_spread_bps": 10.0},
            "triangular": {"min_spread_bps": 20.0},
        }
        runner._params_path.parent.mkdir(parents=True, exist_ok=True)
        runner._params_path.write_text(json.dumps(existing))

        result = _make_shadow_result("cross_exchange")
        runner._apply_params("cross_exchange", result)

        data = json.loads(runner._params_path.read_text())
        assert data["triangular"]["min_spread_bps"] == 20.0
        assert data["cross_exchange"] != existing["cross_exchange"]


# ---------------------------------------------------------------------------
# _mark_for_monitoring
# ---------------------------------------------------------------------------


class TestMarkForMonitoring:
    def test_creates_monitor_queue_json_with_strategy_entry(self, tmp_path):
        """_mark_for_monitoring creates monitor_queue.json containing the strategy."""
        runner = _make_runner(tmp_path)
        result = _make_shadow_result("spot_futures")

        queue_path = tmp_path / "monitor_queue.json"
        assert not queue_path.exists()

        runner._mark_for_monitoring("spot_futures", result)

        assert queue_path.exists()
        data = json.loads(queue_path.read_text())
        assert "spot_futures" in data

    def test_appends_to_existing_queue_without_overwriting_prior_entries(self, tmp_path):
        """_mark_for_monitoring adds new entry to existing monitor_queue.json."""
        runner = _make_runner(tmp_path)
        queue_path = tmp_path / "monitor_queue.json"

        existing = {"triangular": {"shadow_pnl": 0.5, "shadow_sharpe": 1.1}}
        queue_path.write_text(json.dumps(existing))

        result = _make_shadow_result("funding_rate")
        runner._mark_for_monitoring("funding_rate", result)

        data = json.loads(queue_path.read_text())
        assert "triangular" in data
        assert "funding_rate" in data


# ---------------------------------------------------------------------------
# DataLoader.load_execution_log_as_ohlcv
# ---------------------------------------------------------------------------


class TestLoadExecutionLogAsOhlcv:
    @pytest.mark.asyncio
    async def test_returns_ohlcv_window_populated_from_db_rows(self):
        """load_execution_log_as_ohlcv returns OHLCVWindow with data from asyncpg fetch."""
        loader = DataLoader(dsn="postgresql://fake")
        loader._conn = MagicMock()

        fake_rows = [
            {
                "time": datetime(2026, 1, 1, 0, 0),
                "open": 100.0, "high": 105.0, "low": 98.0,
                "close": 102.0, "volume": 1000.0,
            },
            {
                "time": datetime(2026, 1, 1, 1, 0),
                "open": 102.0, "high": 108.0, "low": 101.0,
                "close": 107.0, "volume": 1500.0,
            },
        ]
        loader._conn.fetch = AsyncMock(return_value=fake_rows)

        window = await loader.load_execution_log_as_ohlcv(
            strategy="cross_exchange",
            days=7,
        )

        assert isinstance(window, OHLCVWindow)
        assert window.length == 2
        assert window.closes[0] == pytest.approx(102.0)
        assert window.closes[1] == pytest.approx(107.0)

    @pytest.mark.asyncio
    async def test_returns_empty_ohlcv_window_when_query_returns_no_rows(self):
        """load_execution_log_as_ohlcv returns empty OHLCVWindow when DB has no rows."""
        loader = DataLoader(dsn="postgresql://fake")
        loader._conn = MagicMock()
        loader._conn.fetch = AsyncMock(return_value=[])

        window = await loader.load_execution_log_as_ohlcv(
            strategy="cross_exchange",
            days=7,
        )

        assert isinstance(window, OHLCVWindow)
        assert window.length == 0


# ---------------------------------------------------------------------------
# DataLoader.load_execution_spreads
# ---------------------------------------------------------------------------


class TestLoadExecutionSpreads:
    @pytest.mark.asyncio
    async def test_returns_spread_record_list_from_db_rows(self):
        """load_execution_spreads returns list[SpreadRecord] populated from asyncpg fetch."""
        loader = DataLoader(dsn="postgresql://fake")
        loader._conn = MagicMock()

        fake_rows = [
            {
                "time": datetime(2026, 1, 1, 0, 0),
                "strategy": "cross_exchange",
                "exchange_pair": "binance-coinone",
                "gross_spread": 0.005,
                "net_spread": 0.003,
            },
            {
                "time": datetime(2026, 1, 1, 1, 0),
                "strategy": "cross_exchange",
                "exchange_pair": "binance-upbit",
                "gross_spread": 0.008,
                "net_spread": 0.005,
            },
        ]
        loader._conn.fetch = AsyncMock(return_value=fake_rows)

        records = await loader.load_execution_spreads(days=7)

        assert len(records) == 2
        assert all(isinstance(r, SpreadRecord) for r in records)
        assert records[0].exchange_pair == "binance-coinone"
        assert records[1].gross_spread == pytest.approx(0.008)
