"""
Async database connection pool.

Provides asyncpg pool management and SQLAlchemy async engine setup.
"""

import asyncio
import logging

import asyncpg
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)

_DEFAULT_MIN_SIZE = 2
_DEFAULT_MAX_SIZE = 10
_DEFAULT_MAX_RETRIES = 5
_DEFAULT_RETRY_BASE_DELAY = 0.5  # seconds


class DatabasePool:
    """Async PostgreSQL connection pool via asyncpg."""

    def __init__(
        self,
        dsn: str,
        min_size: int = _DEFAULT_MIN_SIZE,
        max_size: int = _DEFAULT_MAX_SIZE,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        retry_base_delay: float = _DEFAULT_RETRY_BASE_DELAY,
    ) -> None:
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._pool: asyncpg.Pool | None = None

    async def initialize(self) -> None:
        """Create the connection pool with exponential backoff retry."""
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                self._pool = await asyncpg.create_pool(
                    dsn=self._dsn,
                    min_size=self._min_size,
                    max_size=self._max_size,
                )
                logger.info("Database pool initialized (attempt %d)", attempt)
                return
            except Exception as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    delay = self._retry_base_delay * (2 ** (attempt - 1))
                    logger.warning(
                        "DB pool init failed (attempt %d/%d): %s. Retrying in %.1fs",
                        attempt, self._max_retries, exc, delay,
                    )
                    await asyncio.sleep(delay)

        raise ConnectionError(
            f"Failed to initialize DB pool after {self._max_retries} attempts"
        ) from last_exc

    async def health_check(self) -> bool:
        """Run SELECT 1 to verify connectivity."""
        if self._pool is None:
            return False
        try:
            async with self._pool.acquire() as conn:
                result = await conn.fetchval("SELECT 1")
                return result == 1
        except Exception as exc:
            logger.warning("DB health check failed: %s", exc)
            return False

    async def close(self) -> None:
        """Close all pool connections."""
        if self._pool is not None:
            await self._pool.close()
            logger.info("Database pool closed")

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("DatabasePool not initialized. Call initialize() first.")
        return self._pool


def get_async_engine(dsn: str, **kwargs) -> AsyncEngine:
    """Create a SQLAlchemy async engine."""
    return create_async_engine(dsn, **kwargs)


def get_async_sessionmaker(dsn: str, **kwargs) -> sessionmaker:
    """Create an async sessionmaker bound to the given DSN."""
    engine = get_async_engine(dsn, **kwargs)
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
