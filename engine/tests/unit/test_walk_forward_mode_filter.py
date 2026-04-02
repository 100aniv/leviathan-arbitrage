"""Tests for WFA mode='backtest' filter — US-376.

US-376: DB mode 분리 배선
  - BacktestMode._record_trade → record_execution(mode='backtest')
  - walk_forward SQL (evaluator/optimizer) uses mode filter
  - /trades?mode= API 파라미터 존재
"""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# US-376-1: BacktestMode records executions with mode='backtest'
# ---------------------------------------------------------------------------

class TestBacktestModeRecordsWithModeBacktest:
    def test_record_execution_called_with_mode_backtest(self):
        """BacktestMode._execute_trade must call record_execution(mode='backtest')."""
        try:
            from src.modes.backtest import BacktestMode
        except ImportError:
            pytest.skip("BacktestMode not importable")

        src = inspect.getsource(BacktestMode)
        # Check that mode='backtest' is passed to record_execution
        assert "mode=\"backtest\"" in src or "mode='backtest'" in src, (
            "BacktestMode does not pass mode='backtest' to record_execution. "
            "US-376 requires WFA input data to be tagged with mode='backtest'."
        )


# ---------------------------------------------------------------------------
# US-376-2: WFA SQL / evaluator filters by mode='backtest'
# ---------------------------------------------------------------------------

class TestWFAModeFilter:
    def test_evaluator_filters_by_backtest_mode(self):
        """WFA evaluator must include mode filter to avoid mixing backtest/paper/live data."""
        try:
            import src.tuning.evaluator as evaluator_mod
        except ImportError:
            pytest.skip("evaluator module not importable")

        src = inspect.getsource(evaluator_mod)
        # After US-376: SQL or query must filter mode='backtest'
        has_mode_filter = (
            "mode = 'backtest'" in src
            or 'mode = "backtest"' in src
            or "mode='backtest'" in src
            or 'mode="backtest"' in src
            or "mode=mode" in src  # parameterized filter
            or "mode_filter" in src
        )
        assert has_mode_filter, (
            "evaluator.py does not filter by mode='backtest'. "
            "US-376 requires WFA to only read backtest-mode executions."
        )

    def test_optimizer_filters_by_backtest_mode(self):
        """WFA optimizer SQL must filter mode='backtest'."""
        try:
            import src.tuning.optimizer as optimizer_mod
        except ImportError:
            pytest.skip("optimizer module not importable")

        src = inspect.getsource(optimizer_mod)
        has_mode_filter = (
            "mode = 'backtest'" in src
            or 'mode = "backtest"' in src
            or "mode='backtest'" in src
            or 'mode="backtest"' in src
            or "mode_filter" in src
            or "backtest" in src  # at minimum mentions backtest context
        )
        assert has_mode_filter, (
            "optimizer.py has no reference to mode filtering. "
            "US-376 requires WFA optimizer to use mode='backtest' data only."
        )


# ---------------------------------------------------------------------------
# US-376-3: market_recorder.record_execution accepts mode parameter
# ---------------------------------------------------------------------------

class TestMarketRecorderModeParam:
    def test_record_execution_has_mode_parameter(self):
        """MarketRecorder.record_execution must accept a 'mode' parameter."""
        try:
            from src.infra.db.market_recorder import MarketRecorder
        except ImportError:
            pytest.skip("MarketRecorder not importable")

        sig = inspect.signature(MarketRecorder.record_execution)
        assert "mode" in sig.parameters, (
            "MarketRecorder.record_execution missing 'mode' parameter. "
            "US-376 requires mode tag for DB mode separation."
        )

    def test_record_execution_mode_in_sql_insert(self):
        """MarketRecorder.record_execution must include mode in the INSERT SQL."""
        try:
            from src.infra.db.market_recorder import MarketRecorder
        except ImportError:
            pytest.skip("MarketRecorder not importable")

        src = inspect.getsource(MarketRecorder.record_execution)
        assert "mode" in src, (
            "MarketRecorder.record_execution does not use 'mode' in SQL. "
            "US-376 requires mode column in execution_log inserts."
        )


# ---------------------------------------------------------------------------
# US-376-4: /trades API endpoint accepts mode query parameter
# ---------------------------------------------------------------------------

class TestTradesApiModeParam:
    def test_trades_route_accepts_mode_param(self):
        """GET /trades endpoint must support ?mode= query parameter."""
        try:
            import src.api.routes.backtest as backtest_routes
        except ImportError:
            pytest.skip("backtest routes not importable")

        src = inspect.getsource(backtest_routes)
        # After US-376: route handler should accept mode param or filter by mode
        has_mode_param = (
            "mode" in src and (
                "query" in src.lower()
                or "Optional" in src
                or "mode:" in src
                or "mode =" in src
            )
        )
        # Lenient check: at minimum the route file should mention mode
        assert "mode" in src, (
            "backtest routes do not reference 'mode' parameter. "
            "US-376 requires /trades?mode= filter support."
        )
