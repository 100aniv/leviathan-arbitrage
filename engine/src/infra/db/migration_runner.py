"""Auto-migration runner for engine startup."""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Try multiple paths: Docker mount (/app/docker/) or local dev (relative to repo root)
_INIT_SQL_CANDIDATES = [
    Path("/app/docker/init.sql"),                    # Docker volume mount
    Path(__file__).parents[4] / "docker" / "init.sql",  # Local dev
]
_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _find_init_sql() -> Path | None:
    for p in _INIT_SQL_CANDIDATES:
        if p.exists():
            return p
    return None


async def run_migrations(pool) -> None:
    """Apply pending migrations. Idempotent (IF NOT EXISTS everywhere).

    BUG-186: runner previously stopped at init.sql (schema_version=1) and never
    applied migrations/NNN_*.sql — so migration 006 (source column), 009
    (market_data_1m), 010 (retention restore), etc. never ran on fresh DBs.
    Now iterates migrations/*.sql and stamps each file by its numeric prefix.
    """
    async with pool.acquire() as conn:
        # Create schema_version tracking table
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version     INT         PRIMARY KEY,
                applied_at  TIMESTAMPTZ DEFAULT NOW(),
                description TEXT
            )
            """
        )

        # Advisory lock to prevent concurrent migration attempts
        await conn.execute("SELECT pg_advisory_lock(73318)")
        try:
            current = await conn.fetchval("SELECT MAX(version) FROM schema_version") or 0

            if current < 1:
                init_sql_path = _find_init_sql()
                if init_sql_path is None:
                    logger.warning("init.sql not found in any candidate path — skipping")
                    return
                init_sql = init_sql_path.read_text()
                async with conn.transaction():
                    await conn.execute(init_sql)
                    await conn.execute(
                        "INSERT INTO schema_version (version, description) VALUES (1, 'init.sql unified schema')"
                    )
                current = 1
                logger.info("Applied init.sql (version 1) from %s", init_sql_path)

            # BUG-186: apply numbered migrations files above current version.
            if not _MIGRATIONS_DIR.exists():
                logger.warning("migrations dir missing: %s", _MIGRATIONS_DIR)
                return
            applied = 0
            for sql_path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
                try:
                    version = int(sql_path.name.split("_", 1)[0])
                except ValueError:
                    logger.warning("skip unparseable migration filename: %s", sql_path.name)
                    continue
                if version <= current:
                    continue
                try:
                    async with conn.transaction():
                        await conn.execute(sql_path.read_text())
                        await conn.execute(
                            "INSERT INTO schema_version (version, description) VALUES ($1, $2) "
                            "ON CONFLICT (version) DO NOTHING",
                            version, sql_path.stem,
                        )
                    applied += 1
                    current = version  # advance so out-of-order prefixes still gate correctly
                    logger.info("Applied migration %d from %s", version, sql_path.name)
                except Exception as exc:
                    logger.error("migration %s failed: %r", sql_path.name, exc)
                    raise
            if applied:
                logger.info("applied %d migrations; schema_version=%d", applied, current)
            else:
                logger.debug("Schema already at version %d — no migrations needed", current)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(73318)")
