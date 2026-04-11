"""Unit tests for PATCH /api/v1/settings/mode (US-107).

Covers:
- mode="shadow" -> 200 with {"mode": "shadow"}
- mode="paper"  -> 200 with {"mode": "paper"}
- mode="invalid" -> 400
- mode="live" with no LiveGate -> 403
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock
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
        execution_mode="paper",
        strategies={},
        strategy_manager=None,
    )
    for k, v in kwargs.items():
        setattr(ctx, k, v)
    return ctx


def _auth_header() -> dict[str, str]:
    from src.api.auth import create_token
    return {"Authorization": f"Bearer {create_token('testuser')}"}


@pytest.fixture
def app():
    return create_app(_make_ctx())


@pytest.fixture
def transport(app):
    return ASGITransport(app=app)


# ---------------------------------------------------------------------------
# PATCH /api/v1/settings/mode
# ---------------------------------------------------------------------------

class TestPatchMode:
    @pytest.mark.asyncio
    async def test_patch_mode_shadow(self, transport):
        """mode='shadow' is rejected with 400 — shadow mode removed."""
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                "/api/v1/settings/mode",
                json={"mode": "shadow"},
                headers=_auth_header(),
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_patch_mode_shadow_updates_ctx(self, app):
        """mode='shadow' is rejected — engine context not modified."""
        transport = ASGITransport(app=app)
        orig_mode = getattr(app.state.engine_context, "execution_mode", None)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                "/api/v1/settings/mode",
                json={"mode": "shadow"},
                headers=_auth_header(),
            )
        assert resp.status_code == 400
        assert getattr(app.state.engine_context, "execution_mode", None) == orig_mode

    @pytest.mark.asyncio
    async def test_patch_mode_paper(self, transport):
        """mode='paper' returns 200 with mode field set to paper."""
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                "/api/v1/settings/mode",
                json={"mode": "paper"},
                headers=_auth_header(),
            )
        assert resp.status_code == 200
        assert resp.json()["mode"] == "paper"

    @pytest.mark.asyncio
    async def test_patch_mode_invalid(self, transport):
        """mode='invalid' returns 400 Bad Request."""
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                "/api/v1/settings/mode",
                json={"mode": "invalid"},
                headers=_auth_header(),
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_patch_mode_live_no_livegate(self, transport):
        """mode='live' without LiveGate returns 403 Forbidden."""
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                "/api/v1/settings/mode",
                json={"mode": "live"},
                headers=_auth_header(),
            )
        assert resp.status_code == 403
        body = resp.json()
        assert "LiveGate" in body.get("error", "")

    @pytest.mark.asyncio
    async def test_patch_mode_requires_auth(self, transport):
        """PATCH /settings/mode without token returns 401 or 403."""
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                "/api/v1/settings/mode",
                json={"mode": "shadow"},
            )
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_patch_mode_shadow_response_contains_livegate_null(self, transport):
        """Response for non-live mode has livegate: null."""
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                "/api/v1/settings/mode",
                json={"mode": "shadow"},
                headers=_auth_header(),
            )
        assert resp.json().get("livegate") is None
