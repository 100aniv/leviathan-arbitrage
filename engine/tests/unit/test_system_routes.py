"""Unit tests for system monitoring routes (US-141).

Covers:
US-141 - GET /api/v1/system/containers:
- Returns 200 with list (empty when Docker unavailable)
- Returns correct container fields when Docker available
- Returns multiple containers
- Returns 401/403 without JWT

US-141 - GET /api/v1/system/resources:
- Returns 200 with all required resource keys
- Values are numeric or None (graceful degradation)
- Returns correct values when psutil provides data
- Returns None values when psutil unavailable (not 500)
- Returns 401/403 without JWT
"""
from __future__ import annotations

import pytest
from unittest.mock import patch
from httpx import AsyncClient, ASGITransport

from src.api.server import EngineContext, create_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(**kwargs) -> EngineContext:
    ctx = EngineContext(
        running=True,
        kill_switch_active=False,
        environment="test",
        execution_mode="shadow",
        strategies={},
        strategy_manager=None,
    )
    for k, v in kwargs.items():
        setattr(ctx, k, v)
    return ctx


def _auth_header() -> dict[str, str]:
    from src.api.auth import create_token
    return {"Authorization": f"Bearer {create_token('testuser')}"}


# ---------------------------------------------------------------------------
# GET /api/v1/system/containers
# ---------------------------------------------------------------------------

class TestGetContainers:
    @pytest.mark.asyncio
    async def test_containers_returns_200_with_auth(self):
        """Returns 200 when called with valid JWT."""
        ctx = _make_ctx()
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/system/containers", headers=_auth_header())
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_containers_response_is_list(self):
        """Response is a JSON list (possibly empty when Docker unavailable in CI)."""
        ctx = _make_ctx()
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/system/containers", headers=_auth_header())
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @pytest.mark.asyncio
    async def test_containers_returns_required_fields(self):
        """Each container item has name, status, health, cpu_pct, memory_mb, uptime_seconds."""
        mock_container = {
            "name": "leviathan-engine",
            "status": "running",
            "health": "healthy",
            "cpu_pct": None,
            "memory_mb": None,
            "uptime": "—",
        }
        with patch("src.api.routes.system._get_containers", return_value=[mock_container]):
            ctx = _make_ctx()
            app = create_app(ctx)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/v1/system/containers", headers=_auth_header())
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        item = body[0]
        assert item["name"] == "leviathan-engine"
        assert item["health"] == "healthy"
        assert item["status"] == "running"
        for field in ("status", "cpu_pct", "memory_mb", "uptime"):
            assert field in item, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_containers_returns_multiple_containers(self):
        """Returns all containers when Docker has multiple running."""
        mock_containers = [
            {"name": "engine", "status": "Up", "health": "healthy",
             "cpu_pct": None, "memory_mb": None, "uptime": "—"},
            {"name": "redis", "status": "Up", "health": "healthy",
             "cpu_pct": None, "memory_mb": None, "uptime": "—"},
            {"name": "timescaledb", "status": "Up", "health": "healthy",
             "cpu_pct": None, "memory_mb": None, "uptime": "—"},
        ]
        with patch("src.api.routes.system._get_containers", return_value=mock_containers):
            ctx = _make_ctx()
            app = create_app(ctx)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/v1/system/containers", headers=_auth_header())
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    @pytest.mark.asyncio
    async def test_containers_empty_list_when_docker_unavailable(self):
        """Returns empty list (not 500) when Docker is not available."""
        with patch("src.api.routes.system._get_containers", return_value=[]):
            ctx = _make_ctx()
            app = create_app(ctx)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/v1/system/containers", headers=_auth_header())
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_containers_requires_auth(self):
        """Returns 401/403 without JWT token."""
        ctx = _make_ctx()
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/system/containers")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# GET /api/v1/system/resources
# ---------------------------------------------------------------------------

class TestGetResources:
    @pytest.mark.asyncio
    async def test_resources_returns_200_with_auth(self):
        """Returns 200 when called with valid JWT."""
        ctx = _make_ctx()
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/system/resources", headers=_auth_header())
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_resources_returns_all_required_keys(self):
        """Response contains cpu_percent, memory_used_gb, memory_total_gb, disk_used_gb, disk_total_gb."""
        ctx = _make_ctx()
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/system/resources", headers=_auth_header())
        assert resp.status_code == 200
        body = resp.json()
        for key in ("cpu_pct", "memory_used_gb", "memory_total_gb", "disk_used_gb", "disk_total_gb"):
            assert key in body, f"Missing key: {key}"

    @pytest.mark.asyncio
    async def test_resources_values_are_numeric_or_none(self):
        """All resource values are float or None (no unexpected types)."""
        ctx = _make_ctx()
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/system/resources", headers=_auth_header())
        body = resp.json()
        for key in ("cpu_pct", "memory_used_gb", "memory_total_gb", "disk_used_gb", "disk_total_gb"):
            val = body[key]
            assert val is None or isinstance(val, (int, float)), (
                f"{key} should be numeric or None, got {type(val)}: {val}"
            )

    @pytest.mark.asyncio
    async def test_resources_returns_correct_values_from_psutil(self):
        """Returns accurate values when psutil provides data."""
        mock_resources = {
            "cpu_pct": 29.4,
            "memory_used_gb": 1.3,
            "memory_total_gb": 16.0,
            "disk_used_gb": 50.0,
            "disk_total_gb": 500.0,
        }
        with patch("src.api.routes.system._get_resources", return_value=mock_resources):
            ctx = _make_ctx()
            app = create_app(ctx)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/v1/system/resources", headers=_auth_header())
        assert resp.status_code == 200
        body = resp.json()
        assert body["cpu_pct"] == pytest.approx(29.4, abs=0.1)
        assert body["memory_used_gb"] == pytest.approx(1.3, abs=0.01)
        assert body["memory_total_gb"] == pytest.approx(16.0, abs=0.1)
        assert body["disk_total_gb"] == pytest.approx(500.0, abs=0.1)

    @pytest.mark.asyncio
    async def test_resources_returns_none_not_500_when_psutil_unavailable(self):
        """Returns None values (not 500 error) when psutil is not installed."""
        mock_resources = {
            "cpu_pct": None,
            "memory_used_gb": None,
            "memory_total_gb": None,
            "disk_used_gb": None,
            "disk_total_gb": None,
        }
        with patch("src.api.routes.system._get_resources", return_value=mock_resources):
            ctx = _make_ctx()
            app = create_app(ctx)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/v1/system/resources", headers=_auth_header())
        assert resp.status_code == 200
        body = resp.json()
        for key in ("cpu_pct", "memory_used_gb", "memory_total_gb", "disk_used_gb", "disk_total_gb"):
            assert body[key] is None

    @pytest.mark.asyncio
    async def test_resources_requires_auth(self):
        """Returns 401/403 without JWT token."""
        ctx = _make_ctx()
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/system/resources")
        assert resp.status_code in (401, 403)
