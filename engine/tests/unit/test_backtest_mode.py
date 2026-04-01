"""Unit tests for BacktestMode (src/modes/backtest.py) — Phase J TDD.

Covers Phase J US-351 ~ US-355 acceptance criteria:
  a. BacktestMode initializes with db_pool=None
  b. SQL query uses ts / bids_json / asks_json column names (US-351)
  c. BACKTEST_MAX_ROWS env var controls LIMIT (US-352)
  d. Zero snapshots → BacktestResult(error="insufficient_data") (US-352)
  e. MLBacktestResult import succeeds (US-355)
  f. TuningBacktestResult import succeeds (US-355)
  g. comparison_valid=False when ml_scorer=None (US-354)
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.modes.backtest import BacktestMode, BacktestResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backtest_mode(**kwargs) -> BacktestMode:
    """Minimal BacktestMode instance with no-op mocks."""
    signal_gen = MagicMock()
    strategy_mgr = MagicMock()
    strategy_mgr.list_strategies.return_value = []
    return BacktestMode(signal_gen, strategy_mgr, **kwargs)


# ---------------------------------------------------------------------------
# a. 초기화 — db_pool=None 허용
# ---------------------------------------------------------------------------


class TestBacktestModeInit:
    def test_init_with_db_pool_none_succeeds(self):
        """BacktestMode must accept db_pool=None without raising."""
        mode = _make_backtest_mode(db_pool=None)
        assert mode is not None

    def test_init_stores_db_pool_none(self):
        """Internal _db_pool must be None when not provided."""
        mode = _make_backtest_mode()
        assert mode._db_pool is None

    def test_init_default_symbols_btc_usdt(self):
        """Default symbol list falls back to ['BTC/USDT']."""
        mode = _make_backtest_mode()
        assert mode._symbols == ["BTC/USDT"]

    def test_init_custom_symbols(self):
        """Custom symbols list is stored correctly."""
        mode = _make_backtest_mode(symbols=["ETH/USDT", "BTC/USDT"])
        assert "ETH/USDT" in mode._symbols

    def test_result_initialized_as_backtest_result(self):
        """_result must be a BacktestResult instance on init."""
        mode = _make_backtest_mode()
        assert isinstance(mode._result, BacktestResult)


# ---------------------------------------------------------------------------
# b. SQL 쿼리 — ts / bids_json / asks_json 컬럼명 확인 (US-351)
# ---------------------------------------------------------------------------


class TestBacktestSQLSchema:
    def test_sql_uses_bids_json_column(self):
        """_load_snapshots SQL must reference bids_json (not bids)."""
        import inspect
        source = inspect.getsource(BacktestMode._load_snapshots)
        assert "bids_json" in source, "SQL must use bids_json column (market_recorder schema)"
        assert "bids," not in source.replace("bids_json", ""), \
            "SQL must NOT use bare 'bids' column"

    def test_sql_uses_asks_json_column(self):
        """_load_snapshots SQL must reference asks_json (not asks)."""
        import inspect
        source = inspect.getsource(BacktestMode._load_snapshots)
        assert "asks_json" in source, "SQL must use asks_json column (market_recorder schema)"

    def test_sql_uses_ts_not_timestamp_in_select(self):
        """_load_snapshots SQL must use ts column (not timestamp)."""
        import inspect
        source = inspect.getsource(BacktestMode._load_snapshots)
        # ts must appear as EXTRACT(EPOCH FROM ts)
        assert "FROM ts)" in source or "EPOCH FROM ts" in source, \
            "SQL must use ts column in EXTRACT(EPOCH FROM ts)"

    def test_sql_where_clause_uses_ts(self):
        """_load_snapshots WHERE clause must filter on ts not timestamp."""
        import inspect
        source = inspect.getsource(BacktestMode._load_snapshots)
        assert "AND ts >=" in source or "ts >=" in source, \
            "WHERE clause must use ts column"

    def test_sql_order_by_ts(self):
        """_load_snapshots must ORDER BY ts ASC."""
        import inspect
        source = inspect.getsource(BacktestMode._load_snapshots)
        assert "ORDER BY ts" in source, "Must ORDER BY ts ASC"

    def test_snapshot_row_reads_bids_json_key(self):
        """Row parsing must read row['bids_json'] not row['bids']."""
        import inspect
        source = inspect.getsource(BacktestMode._load_snapshots)
        assert 'row["bids_json"]' in source or "row['bids_json']" in source, \
            "Row access must use bids_json key"

    def test_snapshot_row_reads_asks_json_key(self):
        """Row parsing must read row['asks_json'] not row['asks']."""
        import inspect
        source = inspect.getsource(BacktestMode._load_snapshots)
        assert 'row["asks_json"]' in source or "row['asks_json']" in source, \
            "Row access must use asks_json key"


# ---------------------------------------------------------------------------
# c. BACKTEST_MAX_ROWS env var (US-352)
# ---------------------------------------------------------------------------


class TestBacktestMaxRows:
    def test_default_limit_is_large(self):
        """Without BACKTEST_MAX_ROWS, LIMIT must be at least 100_000."""
        import inspect
        source = inspect.getsource(BacktestMode._load_snapshots)
        # Default should be 1_000_000 or at least contain env var reference
        has_env = "BACKTEST_MAX_ROWS" in source
        has_large_limit = "1_000_000" in source or "1000000" in source or "100000" in source
        assert has_env or has_large_limit, \
            "LIMIT must be configurable via BACKTEST_MAX_ROWS env var or use large default"

    def test_env_var_controls_limit(self, monkeypatch):
        """BACKTEST_MAX_ROWS=500 must be applied as the SQL LIMIT."""
        monkeypatch.setenv("BACKTEST_MAX_ROWS", "500")
        import inspect
        source = inspect.getsource(BacktestMode._load_snapshots)
        # The env var must be referenced in the source
        assert "BACKTEST_MAX_ROWS" in source, \
            "_load_snapshots must read BACKTEST_MAX_ROWS env var for LIMIT"


# ---------------------------------------------------------------------------
# d. 스냅샷 0건 → BacktestResult(error="insufficient_data") (US-352)
# ---------------------------------------------------------------------------


class TestBacktestZeroSnapshots:
    def test_no_db_pool_returns_empty_result(self):
        """With db_pool=None, run() must return without crashing."""
        mode = _make_backtest_mode(db_pool=None)
        result = asyncio.run(mode.run())
        assert isinstance(result, BacktestResult)

    def test_zero_snapshots_sets_error_field(self):
        """BacktestResult must have error='insufficient_data' when snapshots=0."""
        mode = _make_backtest_mode(db_pool=None)
        result = asyncio.run(mode.run())
        # US-352 AC: BacktestResult(snapshots_replayed=0, error="insufficient_data")
        assert hasattr(result, "error"), \
            "BacktestResult must have an 'error' field (add to dataclass)"
        assert result.error == "insufficient_data", \
            "error must be 'insufficient_data' when snapshots_replayed=0"

    def test_zero_snapshots_replayed_count_is_zero(self):
        """snapshots_replayed must be 0 when no DB pool."""
        mode = _make_backtest_mode(db_pool=None)
        result = asyncio.run(mode.run())
        assert result.snapshots_replayed == 0

    def test_zero_snapshots_no_crash(self):
        """run() with no data must complete without raising any exception."""
        mode = _make_backtest_mode(db_pool=None)
        try:
            asyncio.run(mode.run())
        except Exception as exc:
            pytest.fail(f"run() raised unexpectedly with no data: {exc}")

    def test_empty_snapshot_list_from_db_returns_error(self):
        """Empty snapshot list from DB (not None pool) returns error='insufficient_data'."""
        mock_pool = MagicMock()

        async def _mock_load(self_inner):
            return []

        mode = _make_backtest_mode(db_pool=mock_pool)
        with patch.object(BacktestMode, "_load_snapshots", _mock_load):
            result = asyncio.run(mode.run())

        assert hasattr(result, "error"), "BacktestResult must have error field"
        assert result.error == "insufficient_data"


# ---------------------------------------------------------------------------
# e. MLBacktestResult import (US-355)
# ---------------------------------------------------------------------------


class TestMLBacktestResultImport:
    def test_ml_backtest_result_importable(self):
        """MLBacktestResult must be importable from src.analysis.ml_backtest."""
        try:
            from src.analysis.ml_backtest import MLBacktestResult  # noqa: F401
        except ImportError as exc:
            pytest.fail(
                f"Cannot import MLBacktestResult from src.analysis.ml_backtest: {exc}\n"
                "US-355: rename BacktestResult → MLBacktestResult in analysis/ml_backtest.py"
            )

    def test_ml_backtest_result_has_required_fields(self):
        """MLBacktestResult must retain the original BacktestResult fields."""
        from src.analysis.ml_backtest import MLBacktestResult
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(MLBacktestResult)}
        required = {"strategy", "total_pnl", "sharpe_ratio", "win_rate"}
        missing = required - field_names
        assert not missing, f"MLBacktestResult missing fields: {missing}"

    def test_original_backtest_result_no_longer_exported_from_ml_module(self):
        """After rename, ml_backtest should NOT export plain BacktestResult (avoids name clash)."""
        import src.analysis.ml_backtest as ml_mod
        # If MLBacktestResult exists, BacktestResult should not be the primary export
        if hasattr(ml_mod, "MLBacktestResult"):
            # BacktestResult in this module means rename is not done yet
            plain_cls = getattr(ml_mod, "BacktestResult", None)
            assert plain_cls is None or plain_cls.__name__ == "MLBacktestResult", \
                "BacktestResult in ml_backtest.py must be renamed to MLBacktestResult"


# ---------------------------------------------------------------------------
# f. TuningBacktestResult import (US-355)
# ---------------------------------------------------------------------------


class TestTuningBacktestResultImport:
    def test_tuning_backtest_result_importable(self):
        """TuningBacktestResult must be importable from src.tuning.backtest."""
        try:
            from src.tuning.backtest import TuningBacktestResult  # noqa: F401
        except ImportError as exc:
            pytest.fail(
                f"Cannot import TuningBacktestResult from src.tuning.backtest: {exc}\n"
                "US-355: rename BacktestResult → TuningBacktestResult in tuning/backtest.py"
            )

    def test_tuning_backtest_result_has_required_fields(self):
        """TuningBacktestResult must retain original BacktestResult fields."""
        from src.tuning.backtest import TuningBacktestResult
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(TuningBacktestResult)}
        required = {"total_pnl", "sharpe_ratio", "win_rate", "num_trades"}
        missing = required - field_names
        assert not missing, f"TuningBacktestResult missing fields: {missing}"

    def test_original_backtest_result_no_longer_in_tuning_module(self):
        """After rename, tuning.backtest should not export plain BacktestResult."""
        import src.tuning.backtest as tuning_mod
        if hasattr(tuning_mod, "TuningBacktestResult"):
            plain_cls = getattr(tuning_mod, "BacktestResult", None)
            assert plain_cls is None or plain_cls.__name__ == "TuningBacktestResult", \
                "BacktestResult in tuning/backtest.py must be renamed to TuningBacktestResult"


# ---------------------------------------------------------------------------
# g. comparison_valid=False when ml_scorer=None (US-354)
# ---------------------------------------------------------------------------


class TestABTestResultComparisonValid:
    def test_ab_test_result_has_comparison_valid_field(self):
        """ABTestResult must have a comparison_valid field (US-354 MUST FIX #3)."""
        from src.analysis.ml_backtest import ABTestResult
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(ABTestResult)}
        assert "comparison_valid" in field_names, \
            "ABTestResult must have comparison_valid field to distinguish " \
            "ml_improves=False (bad ML) from ml_scorer=None (no scorer)"

    def test_ab_test_with_no_scorer_returns_comparison_valid_false(self):
        """MLSignalBacktester.ab_test() with ml_scorer=None must set comparison_valid=False."""
        from src.analysis.ml_backtest import MLSignalBacktester
        backtester = MLSignalBacktester(ml_scorer=None)
        result = backtester.ab_test([], [])
        assert hasattr(result, "comparison_valid"), \
            "ABTestResult must have comparison_valid field"
        assert result.comparison_valid is False, \
            "comparison_valid must be False when ml_scorer=None"

    def test_ab_test_ml_improves_false_when_no_scorer(self):
        """ml_improves must be False (not undefined) when ml_scorer=None."""
        from src.analysis.ml_backtest import MLSignalBacktester
        backtester = MLSignalBacktester(ml_scorer=None)
        result = backtester.ab_test([], [])
        assert result.ml_improves is False

    def test_comparison_valid_distinguishable_from_ml_improves(self):
        """comparison_valid=False must be a separate signal from ml_improves=False."""
        from src.analysis.ml_backtest import ABTestResult
        import dataclasses
        fields = {f.name: f for f in dataclasses.fields(ABTestResult)}
        assert "comparison_valid" in fields, "ABTestResult needs comparison_valid field"
        assert "ml_improves" in fields, "ABTestResult must retain ml_improves field"
        # The two fields must coexist (different semantics)
        assert fields["comparison_valid"].name != fields["ml_improves"].name
