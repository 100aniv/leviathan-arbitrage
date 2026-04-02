"""Unit tests for paper mode API routes — US-332/372.

Covers:
- PaperStartRequest: singular params (exchange_id/strategy_id), duration_hours
- POST /api/paper/complete: records result, updates cumulative hours
- GET /api/paper/cumulative: returns cumulative tracker state
- GET /api/paper/result/{session_id}: returns saved session result
- Cumulative hours accumulate across multiple sessions (US-332)
- satisfied=True when total_hours >= 24 (US-332)
"""
from __future__ import annotations

import json
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.auth import create_token
from src.api.server import EngineContext, create_app


def _auth_header() -> dict:
    return {"Authorization": f"Bearer {create_token('test_user')}"}


def _make_context(**kwargs) -> EngineContext:
    ctx = EngineContext(
        running=True,
        kill_switch_active=False,
        environment="test",
        execution_mode="paper",
        strategies={},
        strategy_manager=None,
    )
    # Set engine default (can be overridden via kwargs)
    ctx.engine = kwargs.pop("engine", MagicMock())
    for k, v in kwargs.items():
        setattr(ctx, k, v)
    return ctx


@pytest.fixture
def app_with_engine(tmp_path):
    """FastAPI app with a mock engine context and temp state dir."""
    ctx = _make_context()
    app = create_app(ctx)
    return app, tmp_path


# ---------------------------------------------------------------------------
# US-372: PaperStartRequest — singular params
# ---------------------------------------------------------------------------

class TestPaperStartRequest:
    @pytest.mark.asyncio
    async def test_start_accepts_singular_exchange_and_strategy(self, tmp_path):
        """PaperStartRequest accepts exchange_id/strategy_id singular fields."""
        ctx = _make_context()
        app = create_app(ctx)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/paper/start",
                json={
                    "exchange_id": "binance",
                    "strategy_id": "cross_exchange_v1",
                    "duration_hours": 1,
                },
                headers=_auth_header(),
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert "session_id" in data
        assert data["params"]["exchange_id"] == "binance"
        assert data["params"]["strategy_id"] == "cross_exchange_v1"

    @pytest.mark.asyncio
    async def test_start_accepts_duration_hours(self):
        """PaperStartRequest duration_hours field accepted."""
        ctx = _make_context()
        app = create_app(ctx)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/paper/start",
                json={"duration_hours": 4, "seed_capital": 100.0},
                headers=_auth_header(),
            )

        assert resp.status_code == 200
        assert resp.json()["params"]["duration_hours"] == 4

    @pytest.mark.asyncio
    async def test_start_returns_503_when_engine_not_initialized(self):
        """Returns 503 when engine is None."""
        ctx = _make_context(engine=None)
        app = create_app(ctx)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/paper/start",
                json={},
                headers=_auth_header(),
            )

        assert resp.status_code == 503
        assert resp.json()["error"] == "engine_not_initialized"


# ---------------------------------------------------------------------------
# US-372: POST /api/paper/complete
# ---------------------------------------------------------------------------

class TestPaperComplete:
    @pytest.mark.asyncio
    async def test_complete_records_session_result(self, tmp_path):
        """POST /api/paper/complete saves result file and returns recorded status."""
        ctx = _make_context()
        app = create_app(ctx)
        state_dir = tmp_path / ".omc" / "state"
        state_dir.mkdir(parents=True)
        cum_file = state_dir / "paper-cumulative-hours.json"

        with (
            patch("src.api.routes.paper._STATE_DIR", state_dir),
            patch("src.api.routes.paper._CUMULATIVE_FILE", cum_file),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/api/paper/complete",
                    json={
                        "session_id": "abc12345",
                        "exchange_id": "binance",
                        "strategy_id": "cross_exchange_v1",
                        "duration_hours": 2.0,
                        "pnl_usd": 15.0,
                        "sharpe": 2.5,
                        "crash_count": 0,
                    },
                    headers=_auth_header(),
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "recorded"
        assert data["session_id"] == "abc12345"
        assert data["total_hours"] == 2.0

    @pytest.mark.asyncio
    async def test_complete_creates_result_file(self, tmp_path):
        """POST /api/paper/complete writes paper-results-{session_id}.json."""
        ctx = _make_context()
        app = create_app(ctx)
        state_dir = tmp_path / ".omc" / "state"
        state_dir.mkdir(parents=True)
        cum_file = state_dir / "paper-cumulative-hours.json"

        with (
            patch("src.api.routes.paper._STATE_DIR", state_dir),
            patch("src.api.routes.paper._CUMULATIVE_FILE", cum_file),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                await client.post(
                    "/api/paper/complete",
                    json={"session_id": "testXX99", "duration_hours": 1.0},
                    headers=_auth_header(),
                )

        result_file = state_dir / "paper-results-testXX99.json"
        assert result_file.exists(), "Result file must be created by /complete"
        saved = json.loads(result_file.read_text())
        assert saved["session_id"] == "testXX99"
        assert "completed_at" in saved


# ---------------------------------------------------------------------------
# US-332: Cumulative hours tracking across sessions
# ---------------------------------------------------------------------------

class TestCumulativeHours:
    @pytest.mark.asyncio
    async def test_cumulative_hours_accumulate_across_sessions(self, tmp_path):
        """Multiple /complete calls accumulate total_hours (US-332)."""
        ctx = _make_context()
        app = create_app(ctx)
        state_dir = tmp_path / ".omc" / "state"
        state_dir.mkdir(parents=True)
        cum_file = state_dir / "paper-cumulative-hours.json"

        with (
            patch("src.api.routes.paper._STATE_DIR", state_dir),
            patch("src.api.routes.paper._CUMULATIVE_FILE", cum_file),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                # Session 1: 10H
                r1 = await client.post(
                    "/api/paper/complete",
                    json={"session_id": "sess1", "duration_hours": 10.0},
                    headers=_auth_header(),
                )
                assert r1.json()["total_hours"] == 10.0
                assert r1.json()["satisfied"] is False

                # Session 2: 8H → total 18H
                r2 = await client.post(
                    "/api/paper/complete",
                    json={"session_id": "sess2", "duration_hours": 8.0},
                    headers=_auth_header(),
                )
                assert r2.json()["total_hours"] == 18.0
                assert r2.json()["satisfied"] is False

                # Session 3: 6H → total 24H → satisfied
                r3 = await client.post(
                    "/api/paper/complete",
                    json={"session_id": "sess3", "duration_hours": 6.0},
                    headers=_auth_header(),
                )
                assert r3.json()["total_hours"] == 24.0
                assert r3.json()["satisfied"] is True

    @pytest.mark.asyncio
    async def test_satisfied_true_when_total_hours_ge_24(self, tmp_path):
        """satisfied becomes True only after cumulative hours >= 24 (US-332)."""
        ctx = _make_context()
        app = create_app(ctx)
        state_dir = tmp_path / ".omc" / "state"
        state_dir.mkdir(parents=True)
        cum_file = state_dir / "paper-cumulative-hours.json"

        with (
            patch("src.api.routes.paper._STATE_DIR", state_dir),
            patch("src.api.routes.paper._CUMULATIVE_FILE", cum_file),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/api/paper/complete",
                    json={"session_id": "big_session", "duration_hours": 25.0},
                    headers=_auth_header(),
                )

        assert resp.json()["satisfied"] is True
        assert resp.json()["total_hours"] >= 24.0

    @pytest.mark.asyncio
    async def test_get_cumulative_returns_tracker_state(self, tmp_path):
        """GET /api/paper/cumulative returns current tracker state."""
        ctx = _make_context()
        app = create_app(ctx)
        state_dir = tmp_path / ".omc" / "state"
        state_dir.mkdir(parents=True)
        cum_file = state_dir / "paper-cumulative-hours.json"

        with (
            patch("src.api.routes.paper._STATE_DIR", state_dir),
            patch("src.api.routes.paper._CUMULATIVE_FILE", cum_file),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                await client.post(
                    "/api/paper/complete",
                    json={"session_id": "s1", "duration_hours": 5.0},
                    headers=_auth_header(),
                )
                resp = await client.get("/api/paper/cumulative", headers=_auth_header())

        assert resp.status_code == 200
        data = resp.json()
        assert "total_hours" in data
        assert "sessions" in data
        assert "satisfied" in data
        assert data["total_hours"] == 5.0

    @pytest.mark.asyncio
    async def test_get_cumulative_returns_defaults_when_no_data(self, tmp_path):
        """GET /api/paper/cumulative returns default state when no sessions recorded."""
        ctx = _make_context()
        app = create_app(ctx)
        state_dir = tmp_path / ".omc" / "state"
        state_dir.mkdir(parents=True)
        cum_file = state_dir / "paper-cumulative-hours.json"  # does not exist yet

        with (
            patch("src.api.routes.paper._STATE_DIR", state_dir),
            patch("src.api.routes.paper._CUMULATIVE_FILE", cum_file),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get("/api/paper/cumulative", headers=_auth_header())

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_hours"] == 0.0
        assert data["satisfied"] is False
        assert data["target_hours"] == 24.0


# ---------------------------------------------------------------------------
# US-372: GET /api/paper/result/{session_id}
# ---------------------------------------------------------------------------

class TestPaperResultById:
    @pytest.mark.asyncio
    async def test_get_result_by_session_id_returns_saved_data(self, tmp_path):
        """GET /api/paper/result/{session_id} returns previously saved result."""
        ctx = _make_context()
        app = create_app(ctx)
        state_dir = tmp_path / ".omc" / "state"
        state_dir.mkdir(parents=True)
        cum_file = state_dir / "paper-cumulative-hours.json"

        with (
            patch("src.api.routes.paper._STATE_DIR", state_dir),
            patch("src.api.routes.paper._CUMULATIVE_FILE", cum_file),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                await client.post(
                    "/api/paper/complete",
                    json={"session_id": "lookup99", "duration_hours": 3.0, "pnl_usd": 42.0},
                    headers=_auth_header(),
                )
                resp = await client.get("/api/paper/result/lookup99", headers=_auth_header())

        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "lookup99"
        assert data["pnl_usd"] == 42.0

    @pytest.mark.asyncio
    async def test_get_result_by_session_id_returns_404_when_missing(self, tmp_path):
        """GET /api/paper/result/{session_id} returns 404 when session not found."""
        ctx = _make_context()
        app = create_app(ctx)
        state_dir = tmp_path / ".omc" / "state"
        state_dir.mkdir(parents=True)
        cum_file = state_dir / "paper-cumulative-hours.json"

        with (
            patch("src.api.routes.paper._STATE_DIR", state_dir),
            patch("src.api.routes.paper._CUMULATIVE_FILE", cum_file),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get("/api/paper/result/nonexistent", headers=_auth_header())

        assert resp.status_code == 404
        assert resp.json()["error"] == "session_not_found"

    @pytest.mark.asyncio
    async def test_invalid_session_id_returns_400(self, tmp_path):
        """GET /api/paper/result/{session_id} returns 400 for path traversal attempts."""
        ctx = _make_context()
        app = create_app(ctx)
        state_dir = tmp_path / ".omc" / "state"
        state_dir.mkdir(parents=True)
        cum_file = state_dir / "paper-cumulative-hours.json"

        with (
            patch("src.api.routes.paper._STATE_DIR", state_dir),
            patch("src.api.routes.paper._CUMULATIVE_FILE", cum_file),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                # Dots-only path traversal (no slash — reaches handler, fails regex)
                resp = await client.get(
                    "/api/paper/result/..etc..passwd",
                    headers=_auth_header(),
                )
                # URL-encoded slash traversal is rejected at router level (404) — also safe
                resp2 = await client.get(
                    "/api/paper/result/..%2Fetc%2Fpasswd",
                    headers=_auth_header(),
                )

        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_session_id"
        assert resp2.status_code in (400, 404)  # rejected at router or handler level
