"""Tests for async database connection pool."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestDatabasePool:
    """Test asyncpg connection pool management."""

    @pytest.mark.asyncio
    async def test_pool_creation(self):
        """Pool is created with correct parameters."""
        from src.infra.db.connection import DatabasePool

        with patch("src.infra.db.connection.asyncpg.create_pool", new_callable=AsyncMock) as mock_create:
            mock_pool = AsyncMock()
            mock_create.return_value = mock_pool

            pool = DatabasePool(dsn="postgresql://user:pass@localhost/testdb")
            await pool.initialize()

            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["dsn"] == "postgresql://user:pass@localhost/testdb"

    @pytest.mark.asyncio
    async def test_health_check_success(self):
        """Health check passes when SELECT 1 succeeds."""
        from src.infra.db.connection import DatabasePool

        with patch("src.infra.db.connection.asyncpg.create_pool", new_callable=AsyncMock) as mock_create:
            mock_conn = AsyncMock()
            mock_conn.fetchval = AsyncMock(return_value=1)
            acquire_ctx = MagicMock()
            acquire_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
            acquire_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_pool = MagicMock()
            mock_pool.acquire = MagicMock(return_value=acquire_ctx)
            mock_pool.close = AsyncMock()
            mock_create.return_value = mock_pool

            pool = DatabasePool(dsn="postgresql://user:pass@localhost/testdb")
            await pool.initialize()
            result = await pool.health_check()

            assert result is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self):
        """Health check returns False on connection error."""
        from src.infra.db.connection import DatabasePool

        with patch("src.infra.db.connection.asyncpg.create_pool", new_callable=AsyncMock) as mock_create:
            mock_conn = AsyncMock()
            mock_conn.fetchval = AsyncMock(side_effect=Exception("Connection refused"))
            acquire_ctx = MagicMock()
            acquire_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
            acquire_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_pool = MagicMock()
            mock_pool.acquire = MagicMock(return_value=acquire_ctx)
            mock_create.return_value = mock_pool

            pool = DatabasePool(dsn="postgresql://user:pass@localhost/testdb")
            await pool.initialize()
            result = await pool.health_check()

            assert result is False

    @pytest.mark.asyncio
    async def test_retry_on_initial_connection_failure(self):
        """Pool initialization retries with exponential backoff."""
        from src.infra.db.connection import DatabasePool

        call_count = 0

        async def create_pool_with_failure(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Connection refused")
            return AsyncMock()

        with patch("src.infra.db.connection.asyncpg.create_pool", side_effect=create_pool_with_failure):
            with patch("src.infra.db.connection.asyncio.sleep", new_callable=AsyncMock):
                pool = DatabasePool(dsn="postgresql://user:pass@localhost/testdb", max_retries=3)
                await pool.initialize()

        assert call_count == 3

    @pytest.mark.asyncio
    async def test_pool_close(self):
        """Pool closes cleanly."""
        from src.infra.db.connection import DatabasePool

        with patch("src.infra.db.connection.asyncpg.create_pool", new_callable=AsyncMock) as mock_create:
            mock_pool = AsyncMock()
            mock_create.return_value = mock_pool

            pool = DatabasePool(dsn="postgresql://user:pass@localhost/testdb")
            await pool.initialize()
            await pool.close()

            mock_pool.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_engine_returns_async_engine(self):
        """SQLAlchemy async engine is created."""
        from src.infra.db.connection import get_async_engine

        with patch("src.infra.db.connection.create_async_engine") as mock_engine_factory:
            mock_engine = MagicMock()
            mock_engine_factory.return_value = mock_engine

            engine = get_async_engine("postgresql+asyncpg://user:pass@localhost/testdb")

            mock_engine_factory.assert_called_once()
            assert engine == mock_engine

    @pytest.mark.asyncio
    async def test_get_session_returns_sessionmaker(self):
        """Async sessionmaker is returned."""
        from src.infra.db.connection import get_async_sessionmaker

        with patch("src.infra.db.connection.create_async_engine") as mock_engine_factory:
            mock_engine = MagicMock()
            mock_engine_factory.return_value = mock_engine

            session_factory = get_async_sessionmaker("postgresql+asyncpg://user:pass@localhost/testdb")
            assert session_factory is not None
