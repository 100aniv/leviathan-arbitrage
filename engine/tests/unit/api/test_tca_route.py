"""Unit tests for TCA API route — US-116."""
import pytest
from unittest.mock import MagicMock
from httpx import AsyncClient, ASGITransport
from src.api.server import EngineContext, create_app
from src.api.auth import create_token


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {create_token('test')}"}


@pytest.fixture
def app_with_tca():
    ctx = EngineContext()
    mock_tca = MagicMock()
    mock_tca.get_summary.return_value = {
        "is_p50_bps": 2.5,
        "is_p95_bps": 8.1,
        "latency_p50_ms": 120.0,
        "latency_p95_ms": 350.0,
        "latency_p99_ms": 780.0,
        "fill_rate_pct": 94.5,
        "sample_count": 150,
    }
    ctx.tca_analyzer = mock_tca
    return create_app(ctx)


@pytest.fixture
def app_without_tca():
    ctx = EngineContext()
    return create_app(ctx)


@pytest.mark.asyncio
async def test_tca_summary_with_data(app_with_tca, auth_headers):
    transport = ASGITransport(app=app_with_tca)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/tca/summary", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["sample_count"] == 150
        assert data["is_p50_bps"] == 2.5
        assert data["fill_rate_pct"] == 94.5
        assert len(data) == 7


@pytest.mark.asyncio
async def test_tca_summary_without_analyzer(app_without_tca, auth_headers):
    transport = ASGITransport(app=app_without_tca)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/tca/summary", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["sample_count"] == 0
        assert data["is_p50_bps"] == 0


@pytest.mark.asyncio
async def test_tca_summary_requires_auth(app_with_tca):
    """TCA endpoint must reject unauthenticated requests."""
    transport = ASGITransport(app=app_with_tca)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/tca/summary")
        assert resp.status_code == 401
