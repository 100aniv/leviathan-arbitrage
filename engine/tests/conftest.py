"""
pytest conftest for LEVIATHAN engine tests.
Provides shared fixtures for unit, integration, and e2e tests.
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator, Generator
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Event loop
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def event_loop_policy() -> asyncio.DefaultEventLoopPolicy:
    return asyncio.DefaultEventLoopPolicy()


# ---------------------------------------------------------------------------
# Environment setup
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def set_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force test environment variables so Settings() works without a real .env."""
    monkeypatch.setenv("ENGINE_ENV", "test")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/15")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://leviathan:leviathan@localhost:5432/leviathan_test",
    )
    monkeypatch.setenv("BINANCE_API_KEY", "test_key")
    monkeypatch.setenv("BINANCE_API_SECRET", "test_secret")
    monkeypatch.setenv("OKX_API_KEY", "test_key")
    monkeypatch.setenv("OKX_API_SECRET", "test_secret")
    monkeypatch.setenv("OKX_PASSPHRASE", "test_passphrase")
    monkeypatch.setenv("BYBIT_API_KEY", "test_key")
    monkeypatch.setenv("BYBIT_API_SECRET", "test_secret")


# ---------------------------------------------------------------------------
# FakeRedis fixture (no real Redis needed for unit tests)
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_redis() -> Any:
    """In-memory fake Redis client for unit tests."""
    import fakeredis.aioredis

    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
async def fake_redis_async() -> AsyncGenerator[Any, None]:
    """Async fake Redis context manager."""
    import fakeredis.aioredis

    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


# ---------------------------------------------------------------------------
# Mock exchange client
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_exchange() -> MagicMock:
    """Generic mock exchange adapter."""
    exchange = MagicMock()
    exchange.id = "binance"
    exchange.fetch_order_book = AsyncMock(
        return_value={
            "bids": [[Decimal("50000.00"), Decimal("1.5")], [Decimal("49999.00"), Decimal("2.0")]],
            "asks": [[Decimal("50001.00"), Decimal("1.2")], [Decimal("50002.00"), Decimal("3.0")]],
            "timestamp": 1_700_000_000_000,
            "symbol": "BTC/USDT",
        }
    )
    exchange.create_order = AsyncMock(
        return_value={
            "id": "test_order_001",
            "status": "closed",
            "filled": Decimal("1.0"),
            "average": Decimal("50001.00"),
            "fee": {"cost": Decimal("0.50"), "currency": "USDT"},
        }
    )
    exchange.cancel_order = AsyncMock(return_value={"id": "test_order_001", "status": "canceled"})
    exchange.fetch_balance = AsyncMock(
        return_value={
            "USDT": {"free": Decimal("10000.00"), "used": Decimal("0.00"), "total": Decimal("10000.00")},
            "BTC": {"free": Decimal("0.10"), "used": Decimal("0.00"), "total": Decimal("0.10")},
        }
    )
    return exchange


# ---------------------------------------------------------------------------
# Sample orderbook data
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_orderbook() -> dict[str, Any]:
    return {
        "symbol": "BTC/USDT",
        "exchange": "binance",
        "bids": [
            [Decimal("50000.00"), Decimal("1.500")],
            [Decimal("49999.00"), Decimal("2.000")],
            [Decimal("49998.00"), Decimal("5.000")],
        ],
        "asks": [
            [Decimal("50001.00"), Decimal("1.200")],
            [Decimal("50002.00"), Decimal("3.000")],
            [Decimal("50003.00"), Decimal("8.000")],
        ],
        "timestamp": 1_700_000_000_000,
    }


@pytest.fixture
def sample_orderbook_binance(sample_orderbook: dict[str, Any]) -> dict[str, Any]:
    return {**sample_orderbook, "exchange": "binance"}


@pytest.fixture
def sample_orderbook_okx() -> dict[str, Any]:
    return {
        "symbol": "BTC/USDT",
        "exchange": "okx",
        "bids": [
            [Decimal("50010.00"), Decimal("0.800")],
            [Decimal("50009.00"), Decimal("1.500")],
        ],
        "asks": [
            [Decimal("50011.00"), Decimal("0.700")],
            [Decimal("50012.00"), Decimal("2.000")],
        ],
        "timestamp": 1_700_000_000_000,
    }


# ---------------------------------------------------------------------------
# Sample trade / position data
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_position() -> dict[str, Any]:
    return {
        "strategy_id": "cross_exchange_spot_v1",
        "exchange_id": "binance",
        "symbol": "BTC/USDT",
        "side": "LONG",
        "quantity": Decimal("0.10"),
        "avg_price": Decimal("50001.00"),
    }
