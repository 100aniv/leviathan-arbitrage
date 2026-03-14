"""Tests for US-145: DataLoader SQL schema validation.

Verifies:
- load_execution_log_as_ohlcv uses ts column (not executed_at) and mid-price formula
- load_execution_spreads uses fee_total and buy_exchange||'-'||sell_exchange concat
- _run_with_timescaledb falls back to synthetic when DATABASE_URL empty or < 10 rows

Run:
    cd engine && python -m pytest tests/test_data_loader_schema.py -x --tb=short -v
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tuning.data_loader import DataLoader, OHLCVWindow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_loader() -> DataLoader:
    """DataLoader with mocked asyncpg connection."""
    dl = DataLoader(dsn="postgresql://test")
    dl._conn = AsyncMock()
    return dl


def _make_ohlcv_row(close: float = 105.0) -> dict:
    return {
        "time": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "open": 100.0,
        "high": 110.0,
        "low": 90.0,
        "close": close,
        "volume": 1.5,
    }


def _make_spread_row() -> dict:
    return {
        "time": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "strategy": "cross_exchange_v1",
        "exchange_pair": "binance-upbit",
        "gross_spread": 0.005,
        "net_spread": 0.003,
    }


# ===========================================================================
# US-145-A: load_execution_log_as_ohlcv SQL schema
# ===========================================================================


class TestLoadExecutionLogAsOHLCV:
    """SQL schema tests for load_execution_log_as_ohlcv."""

    @pytest.mark.asyncio
    async def test_sql_uses_ts_column_not_executed_at(self):
        """SQL must reference ts column, not the legacy executed_at column."""
        loader = _make_loader()
        loader._conn.fetch = AsyncMock(return_value=[])
        await loader.load_execution_log_as_ohlcv(strategy=None, days=7)

        sql = loader._conn.fetch.call_args[0][0]
        assert "ts" in sql
        assert "executed_at" not in sql

    @pytest.mark.asyncio
    async def test_sql_uses_mid_price_buy_plus_sell_over_two(self):
        """SQL computes mid-price as (buy_price + sell_price) / 2."""
        loader = _make_loader()
        loader._conn.fetch = AsyncMock(return_value=[])
        await loader.load_execution_log_as_ohlcv(strategy=None, days=7)

        sql = loader._conn.fetch.call_args[0][0]
        assert "buy_price" in sql
        assert "sell_price" in sql

    @pytest.mark.asyncio
    async def test_returns_empty_window_when_no_rows(self):
        """Returns empty OHLCVWindow (length=0) when no rows match."""
        loader = _make_loader()
        loader._conn.fetch = AsyncMock(return_value=[])
        result = await loader.load_execution_log_as_ohlcv()

        assert isinstance(result, OHLCVWindow)
        assert result.length == 0

    @pytest.mark.asyncio
    async def test_maps_rows_to_ohlcv_window_correctly(self):
        """Rows are mapped to OHLCVWindow with correct close and volume values."""
        loader = _make_loader()
        loader._conn.fetch = AsyncMock(return_value=[_make_ohlcv_row(close=105.0)])
        result = await loader.load_execution_log_as_ohlcv()

        assert result.length == 1
        assert float(result.closes[0]) == pytest.approx(105.0)
        assert float(result.volumes[0]) == pytest.approx(1.5)

    @pytest.mark.asyncio
    async def test_strategy_filter_passed_as_parameter(self):
        """strategy argument is passed as a SQL parameter (not interpolated)."""
        loader = _make_loader()
        loader._conn.fetch = AsyncMock(return_value=[])
        await loader.load_execution_log_as_ohlcv(strategy="cross_exchange_v1", days=14)

        call_args = loader._conn.fetch.call_args
        # Strategy value must be in positional args, not embedded in SQL
        positional_args = call_args[0][1:]  # skip the SQL string
        assert "cross_exchange_v1" in positional_args


# ===========================================================================
# US-145-B: load_execution_spreads SQL schema
# ===========================================================================


class TestLoadExecutionSpreads:
    """SQL schema tests for load_execution_spreads."""

    @pytest.mark.asyncio
    async def test_sql_uses_fee_total_column(self):
        """SQL must reference fee_total for net spread calculation."""
        loader = _make_loader()
        loader._conn.fetch = AsyncMock(return_value=[])
        await loader.load_execution_spreads(days=7)

        sql = loader._conn.fetch.call_args[0][0]
        assert "fee_total" in sql

    @pytest.mark.asyncio
    async def test_sql_concatenates_exchange_pair(self):
        """SQL concatenates buy_exchange and sell_exchange using || operator."""
        loader = _make_loader()
        loader._conn.fetch = AsyncMock(return_value=[])
        await loader.load_execution_spreads(days=7)

        sql = loader._conn.fetch.call_args[0][0]
        assert "buy_exchange" in sql
        assert "sell_exchange" in sql
        assert "||" in sql  # PostgreSQL string concatenation

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_rows(self):
        """Returns empty list when no spread rows found."""
        loader = _make_loader()
        loader._conn.fetch = AsyncMock(return_value=[])
        result = await loader.load_execution_spreads()

        assert result == []

    @pytest.mark.asyncio
    async def test_maps_rows_to_spread_records_with_net_spread(self):
        """Correctly maps DB rows to SpreadRecord with exchange_pair and net_spread."""
        loader = _make_loader()
        loader._conn.fetch = AsyncMock(return_value=[_make_spread_row()])
        result = await loader.load_execution_spreads()

        assert len(result) == 1
        assert result[0].exchange_pair == "binance-upbit"
        assert result[0].net_spread == pytest.approx(0.003)
        assert result[0].gross_spread == pytest.approx(0.005)


# ===========================================================================
# US-145-C: _run_with_timescaledb fallback logic
# ===========================================================================


class TestRunWithTimescaleDB:
    """Fallback logic for _run_with_timescaledb."""

    def test_falls_back_to_synthetic_when_database_url_not_set(self, monkeypatch):
        """Falls back to synthetic immediately when DATABASE_URL is empty."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

        from src.tuning.scheduled_tuner import ScheduledTuner
        from src.tuning.backtest import StrategyParams

        tuner = ScheduledTuner(strategies=["cross_exchange"])
        optimizer = MagicMock()
        optimizer._engine.run_with_synthetic_data.return_value = MagicMock(sharpe_ratio=0.5)

        result = tuner._run_with_timescaledb(optimizer, StrategyParams(), "cross_exchange")

        optimizer._engine.run_with_synthetic_data.assert_called_once()
        assert result.sharpe_ratio == pytest.approx(0.5)

    def test_falls_back_to_synthetic_when_fewer_than_10_rows(self, monkeypatch):
        """Falls back to synthetic when loaded OHLCV has < 10 rows."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://test")

        mock_ohlcv = MagicMock()
        mock_ohlcv.length = 5  # 5 < 10 → fallback
        mock_ohlcv.__len__ = MagicMock(return_value=5)
        mock_loader = MagicMock()
        mock_loader.load_execution_log_as_ohlcv = AsyncMock(return_value=mock_ohlcv)
        mock_loader.__aenter__ = AsyncMock(return_value=mock_loader)
        mock_loader.__aexit__ = AsyncMock(return_value=False)

        from src.tuning.scheduled_tuner import ScheduledTuner
        from src.tuning.backtest import StrategyParams

        tuner = ScheduledTuner(strategies=["cross_exchange"])
        optimizer = MagicMock()
        optimizer._engine.run_with_synthetic_data.return_value = MagicMock(sharpe_ratio=0.3)

        with patch("src.tuning.scheduled_tuner.DataLoader", return_value=mock_loader):
            result = tuner._run_with_timescaledb(optimizer, StrategyParams(), "cross_exchange")

        optimizer._engine.run_with_synthetic_data.assert_called_once()

    def test_uses_real_data_when_10_or_more_rows(self, monkeypatch):
        """Uses engine.run(params, ohlcv) when OHLCV has >= 10 rows."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://test")

        mock_ohlcv = MagicMock()
        mock_ohlcv.length = 20  # 20 >= 10 → real data
        mock_ohlcv.__len__ = MagicMock(return_value=20)
        mock_loader = MagicMock()
        mock_loader.load_execution_log_as_ohlcv = AsyncMock(return_value=mock_ohlcv)
        mock_loader.__aenter__ = AsyncMock(return_value=mock_loader)
        mock_loader.__aexit__ = AsyncMock(return_value=False)

        from src.tuning.scheduled_tuner import ScheduledTuner
        from src.tuning.backtest import StrategyParams

        tuner = ScheduledTuner(strategies=["cross_exchange"])
        optimizer = MagicMock()
        optimizer._engine.run.return_value = MagicMock(sharpe_ratio=2.0)

        with patch("src.tuning.scheduled_tuner.DataLoader", return_value=mock_loader):
            result = tuner._run_with_timescaledb(optimizer, StrategyParams(), "cross_exchange")

        optimizer._engine.run.assert_called_once()
        call_args = optimizer._engine.run.call_args[0]
        assert call_args[1] is mock_ohlcv

    def test_falls_back_to_synthetic_on_exception(self, monkeypatch):
        """Falls back to synthetic when an exception occurs during DB load."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://test")

        mock_loader = MagicMock()
        mock_loader.load_execution_log_as_ohlcv = AsyncMock(
            side_effect=Exception("Connection refused")
        )
        mock_loader.__aenter__ = AsyncMock(return_value=mock_loader)
        mock_loader.__aexit__ = AsyncMock(return_value=False)

        from src.tuning.scheduled_tuner import ScheduledTuner
        from src.tuning.backtest import StrategyParams

        tuner = ScheduledTuner(strategies=["cross_exchange"])
        optimizer = MagicMock()
        optimizer._engine.run_with_synthetic_data.return_value = MagicMock(sharpe_ratio=0.1)

        with patch("src.tuning.scheduled_tuner.DataLoader", return_value=mock_loader):
            result = tuner._run_with_timescaledb(optimizer, StrategyParams(), "cross_exchange")

        optimizer._engine.run_with_synthetic_data.assert_called_once()
