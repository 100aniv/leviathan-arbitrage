"""US-135: DB migration runner tests.

Tests cover:
- test_migration_runner_creates_schema_version_table: schema_version table is created on first run
- test_migration_runner_applies_init_sql_when_version_is_zero: init.sql executed when schema at v0
- test_migration_runner_idempotent: running twice with version already set skips re-apply
- test_migration_runner_skips_when_init_sql_missing: logs warning and returns if init.sql not found
- test_init_sql_contains_required_tables: init.sql covers all critical hypertables
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

INIT_SQL_PATH = (
    Path(__file__).resolve().parents[3] / "docker" / "init.sql"
)

REQUIRED_TABLES = [
    "orderbook_snapshots",
    "execution_log",
    "ohlcv_1m",
    "spreads",
    "signals",
    "position_wal",
    "trades",
    "orders",
]


def _make_mock_pool(current_version: int) -> MagicMock:
    """Return a mock asyncpg pool whose connection returns the given schema version."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=current_version)
    # Support async with conn.transaction():
    conn.transaction = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=None),
        )
    )

    pool = MagicMock()
    pool.acquire = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=None),
        )
    )
    return pool, conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migration_runner_creates_schema_version_table():
    """schema_version table is created via CREATE TABLE IF NOT EXISTS."""
    pool, conn = _make_mock_pool(current_version=1)  # already at v1 → skip apply

    from src.infra.db.migration_runner import run_migrations

    await run_migrations(pool)

    # First execute call must be the CREATE TABLE IF NOT EXISTS schema_version
    first_call_sql = conn.execute.call_args_list[0][0][0]
    assert "schema_version" in first_call_sql
    assert "CREATE TABLE IF NOT EXISTS" in first_call_sql


@pytest.mark.asyncio
async def test_migration_runner_applies_init_sql_when_version_is_zero():
    """When schema version is 0, init.sql is read and executed."""
    pool, conn = _make_mock_pool(current_version=0)

    from src.infra.db.migration_runner import run_migrations

    with patch("src.infra.db.migration_runner._find_init_sql", return_value=INIT_SQL_PATH):
        await run_migrations(pool)

    # After schema_version CREATE, execute should be called with init.sql content
    # and then with the INSERT INTO schema_version
    call_sqls = [c[0][0] for c in conn.execute.call_args_list]
    assert any("schema_version" in sql and "INSERT" in sql for sql in call_sqls), (
        "Expected INSERT INTO schema_version after applying init.sql"
    )


@pytest.mark.asyncio
async def test_migration_runner_idempotent():
    """Running run_migrations twice when version >= 1 does NOT re-apply init.sql."""
    pool, conn = _make_mock_pool(current_version=1)

    from src.infra.db.migration_runner import run_migrations

    await run_migrations(pool)
    await run_migrations(pool)

    # init.sql content should NOT appear in any execute call
    call_sqls = [c[0][0] for c in conn.execute.call_args_list]
    # Only the schema_version CREATE TABLE call is expected
    assert not any("CREATE TABLE IF NOT EXISTS orderbook_snapshots" in sql for sql in call_sqls), (
        "init.sql tables should not be re-applied when version >= 1"
    )


@pytest.mark.asyncio
async def test_migration_runner_skips_when_init_sql_missing(tmp_path, caplog):
    """When init.sql does not exist, logs a warning and returns without error."""
    import logging

    pool, conn = _make_mock_pool(current_version=0)
    missing_path = tmp_path / "nonexistent_init.sql"

    from src.infra.db.migration_runner import run_migrations

    with (
        patch("src.infra.db.migration_runner._find_init_sql", return_value=None),
        caplog.at_level(logging.WARNING, logger="src.infra.db.migration_runner"),
    ):
        await run_migrations(pool)

    # Should log a warning about missing init.sql
    assert any("init.sql" in r.message.lower() or "not found" in r.message.lower()
               for r in caplog.records), (
        "Expected a warning about missing init.sql"
    )


def test_init_sql_contains_required_tables():
    """init.sql must define all critical hypertables for the engine."""
    assert INIT_SQL_PATH.exists(), f"init.sql not found at {INIT_SQL_PATH}"
    content = INIT_SQL_PATH.read_text(encoding="utf-8").lower()

    missing = [t for t in REQUIRED_TABLES if t not in content]
    assert not missing, f"init.sql is missing table definitions for: {missing}"
