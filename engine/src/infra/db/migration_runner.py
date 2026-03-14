"""Auto-migration runner for engine startup."""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Try multiple paths: Docker mount (/app/docker/) or local dev (relative to repo root)
_INIT_SQL_CANDIDATES = [
    Path("/app/docker/init.sql"),                    # Docker volume mount
    Path(__file__).parents[4] / "docker" / "init.sql",  # Local dev
]


def _find_init_sql() -> Path | None:
    for p in _INIT_SQL_CANDIDATES:
        if p.exists():
            return p
    return None


async def run_migrations(pool) -> None:
    """Apply pending migrations. Idempotent (IF NOT EXISTS everywhere)."""
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
                logger.info("Applied init.sql (version 1) from %s", init_sql_path)
            else:
                logger.debug("Schema already at version %d — no migrations needed", current)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(73318)")
