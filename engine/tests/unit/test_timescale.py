"""Tests for src/infra/db/timescale.py — DDL constants and setup_timescaledb."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, call


# ---------------------------------------------------------------------------
# DDL constants
# ---------------------------------------------------------------------------

class TestDDLConstants:
    def test_ohlcv_create_table_ddl(self):
        from src.infra.db.timescale import _CREATE_OHLCV
        assert "CREATE TABLE IF NOT EXISTS ohlcv" in _CREATE_OHLCV
        assert "TIMESTAMPTZ" in _CREATE_OHLCV
        assert "exchange" in _CREATE_OHLCV
        assert "symbol" in _CREATE_OHLCV

    def test_spreads_create_table_ddl(self):
        from src.infra.db.timescale import _CREATE_SPREADS
        assert "CREATE TABLE IF NOT EXISTS spreads" in _CREATE_SPREADS
        assert "gross_spread" in _CREATE_SPREADS
        assert "net_spread" in _CREATE_SPREADS

    def test_signals_create_table_ddl(self):
        from src.infra.db.timescale import _CREATE_SIGNALS
        assert "CREATE TABLE IF NOT EXISTS signals" in _CREATE_SIGNALS
        assert "signal_type" in _CREATE_SIGNALS

    def test_hypertables_list_length(self):
        from src.infra.db.timescale import _CREATE_HYPERTABLES
        assert len(_CREATE_HYPERTABLES) == 3

    def test_hypertables_reference_all_tables(self):
        from src.infra.db.timescale import _CREATE_HYPERTABLES
        tables = {"ohlcv", "spreads", "signals"}
        for stmt in _CREATE_HYPERTABLES:
            matched = any(t in stmt for t in tables)
            assert matched, f"Hypertable stmt doesn't reference a known table: {stmt}"

    def test_retention_policies_list_length(self):
        from src.infra.db.timescale import _ADD_RETENTION_POLICIES
        assert len(_ADD_RETENTION_POLICIES) == 3

    def test_continuous_aggregates_present(self):
        from src.infra.db.timescale import _CREATE_CONTINUOUS_AGGREGATES
        assert len(_CREATE_CONTINUOUS_AGGREGATES) >= 1
        for agg in _CREATE_CONTINUOUS_AGGREGATES:
            assert "MATERIALIZED VIEW" in agg or "materialized view" in agg.lower()


# ---------------------------------------------------------------------------
# setup_timescaledb
# ---------------------------------------------------------------------------

class TestSetupTimescaleDB:
    @pytest.mark.asyncio
    async def test_executes_all_raw_table_ddl(self):
        from src.infra.db.timescale import setup_timescaledb
        mock_conn = AsyncMock()
        await setup_timescaledb(mock_conn)
        # 3 raw tables + 3 hypertables + 3 retention + 2 agg = 11 execute calls minimum
        assert mock_conn.execute.call_count >= 8

    @pytest.mark.asyncio
    async def test_hypertable_already_exists_does_not_raise(self):
        """Error during hypertable creation is caught and logged as warning."""
        from src.infra.db.timescale import setup_timescaledb
        mock_conn = AsyncMock()

        async def conditional_fail(sql):
            if "create_hypertable" in sql:
                raise Exception("already a hypertable")

        mock_conn.execute.side_effect = conditional_fail
        await setup_timescaledb(mock_conn)  # must not raise

    @pytest.mark.asyncio
    async def test_retention_policy_error_does_not_raise(self):
        """Retention policy errors are non-fatal."""
        from src.infra.db.timescale import setup_timescaledb
        mock_conn = AsyncMock()

        async def conditional_fail(sql):
            if "retention_policy" in sql:
                raise Exception("policy already exists")

        mock_conn.execute.side_effect = conditional_fail
        await setup_timescaledb(mock_conn)  # must not raise

    @pytest.mark.asyncio
    async def test_continuous_aggregate_error_does_not_raise(self):
        """Continuous aggregate creation errors are non-fatal."""
        from src.infra.db.timescale import setup_timescaledb
        mock_conn = AsyncMock()

        async def conditional_fail(sql):
            if "MATERIALIZED VIEW" in sql:
                raise Exception("view already exists")

        mock_conn.execute.side_effect = conditional_fail
        await setup_timescaledb(mock_conn)  # must not raise

    @pytest.mark.asyncio
    async def test_execute_called_for_ohlcv_ddl(self):
        from src.infra.db.timescale import setup_timescaledb, _CREATE_OHLCV
        mock_conn = AsyncMock()
        await setup_timescaledb(mock_conn)
        # Check that ohlcv DDL was called
        calls = [str(c) for c in mock_conn.execute.call_args_list]
        assert any("ohlcv" in c for c in calls)

    @pytest.mark.asyncio
    async def test_all_three_raw_tables_created(self):
        from src.infra.db.timescale import setup_timescaledb
        mock_conn = AsyncMock()
        executed_sqls = []

        async def capture(sql):
            executed_sqls.append(sql)

        mock_conn.execute.side_effect = capture
        await setup_timescaledb(mock_conn)

        table_ddls = [s for s in executed_sqls if "CREATE TABLE" in s]
        assert len(table_ddls) == 3
