"""Unit tests for src/api/server.py and src/api/routes/strategies.py.

Covers:
- create_app factory returns a FastAPI instance
- /api/auth/login: success, wrong credentials
- /status short-path endpoint
- /kill short-path endpoint sets kill_switch_active
- /strategies short-path list
- /strategies/{id}/toggle short-path 404 / toggle
- /metrics short-path (prometheus fallback)
- API routes: GET /api/v1/strategies (dict fallback and StrategyManager path)
- POST /api/v1/strategies/{id}/toggle (dict fallback, not found, StrategyManager path)
- POST /api/v1/strategies/{id}/config (dict fallback, not found, StrategyManager path)

All tests use httpx.AsyncClient with the FastAPI test transport — no real
network or engine subsystems required.
"""
from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from src.api.server import EngineContext, create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_context(**kwargs) -> EngineContext:
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


@pytest.fixture
def app():
    ctx = _make_context(
        strategies={
            "arb1": {"id": "arb1", "type": "cross_exchange", "enabled": True},
        }
    )
    return create_app(ctx)


@pytest.fixture
def transport(app):
    return ASGITransport(app=app)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TestAuthLogin:
    @pytest.mark.asyncio
    async def test_login_with_correct_credentials_returns_token(self, transport, app):
        from src.api.auth import DASHBOARD_USER
        # Password defaults to "leviathan" when DASHBOARD_PASSWORD env not set
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/auth/login",
                json={"username": DASHBOARD_USER, "password": os.environ.get("DASHBOARD_PASSWORD", "leviathan")},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_login_with_wrong_password_returns_401(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "wrong_password_xyz"},
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_login_with_wrong_username_returns_401(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/auth/login",
                json={"username": "not_a_user", "password": "any"},
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /status short-path
# ---------------------------------------------------------------------------

class TestStatusEndpoint:
    @pytest.mark.asyncio
    async def test_status_returns_200(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/status")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_status_body_contains_running_field(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/status")
        body = resp.json()
        assert "running" in body
        assert body["running"] is True

    @pytest.mark.asyncio
    async def test_status_body_contains_kill_switch_active(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/status")
        assert resp.json()["kill_switch_active"] is False

    @pytest.mark.asyncio
    async def test_status_body_contains_strategy_count(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/status")
        assert resp.json()["strategy_count"] == 1


# ---------------------------------------------------------------------------
# /kill short-path
# ---------------------------------------------------------------------------

class TestKillEndpoint:
    @pytest.mark.asyncio
    async def test_kill_returns_halted_status(self):
        ctx = _make_context()
        app = create_app(ctx)
        transport = ASGITransport(app=app)
        with patch("src.risk.kill_switch.halt_local", MagicMock()):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/kill", json={"reason": "test"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "halted"

    @pytest.mark.asyncio
    async def test_kill_sets_kill_switch_active_on_context(self):
        ctx = _make_context()
        app = create_app(ctx)
        transport = ASGITransport(app=app)
        with patch("src.risk.kill_switch.halt_local", MagicMock()):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.post("/kill", json={"reason": "manual"})
        assert ctx.kill_switch_active is True
        assert ctx.running is False

    @pytest.mark.asyncio
    async def test_kill_uses_default_reason_when_not_provided(self):
        ctx = _make_context()
        app = create_app(ctx)
        transport = ASGITransport(app=app)
        with patch("src.risk.kill_switch.halt_local", MagicMock()):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/kill", json={})
        assert resp.json()["reason"] == "manual"


# ---------------------------------------------------------------------------
# /strategies short-path
# ---------------------------------------------------------------------------

class TestStrategiesShortPath:
    @pytest.mark.asyncio
    async def test_list_strategies_returns_list(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/strategies")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @pytest.mark.asyncio
    async def test_list_strategies_contains_registered_strategy(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/strategies")
        strategies = resp.json()
        ids = [s["id"] for s in strategies]
        assert "arb1" in ids

    @pytest.mark.asyncio
    async def test_toggle_strategy_returns_toggled_state(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/strategies/arb1/toggle")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "arb1"
        assert body["enabled"] is False  # was True, now toggled

    @pytest.mark.asyncio
    async def test_toggle_nonexistent_strategy_returns_404(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/strategies/nonexistent/toggle")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /metrics short-path
# ---------------------------------------------------------------------------

class TestMetricsEndpoint:
    @pytest.mark.asyncio
    async def test_metrics_returns_200(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/metrics")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_metrics_fallback_when_prometheus_not_available(self):
        ctx = _make_context()
        app = create_app(ctx)
        transport = ASGITransport(app=app)
        with patch("prometheus_client.generate_latest", side_effect=ImportError):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/metrics")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# API v1 strategy routes — dict fallback path
# ---------------------------------------------------------------------------

class TestApiV1StrategiesDict:
    @pytest.mark.asyncio
    async def test_get_api_strategies_returns_200(self):
        ctx = _make_context(
            strategies={"s1": {"id": "s1", "enabled": True}},
            strategy_manager=None,
        )
        app = create_app(ctx)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/strategies")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_get_api_strategies_returns_dict_values(self):
        ctx = _make_context(
            strategies={"s1": {"id": "s1", "enabled": True}},
            strategy_manager=None,
        )
        app = create_app(ctx)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/strategies")
        data = resp.json()
        assert any(s.get("id") == "s1" for s in data)

    @pytest.mark.asyncio
    async def test_toggle_api_strategy_dict_path(self):
        ctx = _make_context(
            strategies={"s1": {"id": "s1", "enabled": True}},
            strategy_manager=None,
        )
        app = create_app(ctx)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/strategies/s1/toggle")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    @pytest.mark.asyncio
    async def test_toggle_api_strategy_not_found_returns_404(self):
        ctx = _make_context(strategies={}, strategy_manager=None)
        app = create_app(ctx)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/strategies/missing/toggle")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_config_api_strategy_dict_path(self):
        ctx = _make_context(
            strategies={"s1": {"id": "s1", "enabled": True}},
            strategy_manager=None,
        )
        app = create_app(ctx)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/strategies/s1/config",
                json={"min_spread_bps": 15},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["config"]["min_spread_bps"] == 15

    @pytest.mark.asyncio
    async def test_config_api_strategy_not_found_returns_404(self):
        ctx = _make_context(strategies={}, strategy_manager=None)
        app = create_app(ctx)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/strategies/missing/config",
                json={"key": "val"},
            )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# API v1 strategy routes — StrategyManager path
# ---------------------------------------------------------------------------

class TestApiV1StrategiesManagerPath:
    def _make_mock_manager(self, strategy_ids: list[str], active: bool = True):
        manager = MagicMock()
        manager.list_strategies.return_value = strategy_ids

        def get_strategy(sid):
            s = MagicMock()
            s.is_active = active
            s.STRATEGY_TYPE = "cross_exchange"
            s.metrics = MagicMock()
            s.metrics.model_dump.return_value = {"pnl": 0.0}
            return s

        manager.get_strategy.side_effect = get_strategy
        manager.stop_strategy = AsyncMock()
        manager.start_strategy = AsyncMock()
        return manager

    @pytest.mark.asyncio
    async def test_get_strategies_via_manager_returns_list(self):
        manager = self._make_mock_manager(["strat_a"])
        ctx = _make_context(strategy_manager=manager)
        app = create_app(ctx)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/strategies")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "strat_a"

    @pytest.mark.asyncio
    async def test_toggle_active_strategy_calls_stop(self):
        manager = self._make_mock_manager(["s1"], active=True)
        ctx = _make_context(strategy_manager=manager)
        app = create_app(ctx)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/strategies/s1/toggle")
        assert resp.status_code == 200
        manager.stop_strategy.assert_called_once_with("s1")

    @pytest.mark.asyncio
    async def test_toggle_inactive_strategy_calls_start(self):
        manager = self._make_mock_manager(["s1"], active=False)
        ctx = _make_context(strategy_manager=manager)
        app = create_app(ctx)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/strategies/s1/toggle")
        assert resp.status_code == 200
        manager.start_strategy.assert_called_once_with("s1")

    @pytest.mark.asyncio
    async def test_toggle_nonexistent_strategy_via_manager_returns_404(self):
        manager = MagicMock()
        manager.get_strategy.return_value = None
        ctx = _make_context(strategy_manager=manager)
        app = create_app(ctx)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/strategies/missing/toggle")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_config_via_manager_calls_reconfigure(self):
        manager = MagicMock()
        strat = MagicMock()
        strat.is_active = True
        manager.get_strategy.return_value = strat
        manager.reconfigure = MagicMock()
        ctx = _make_context(strategy_manager=manager)
        app = create_app(ctx)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/strategies/s1/config",
                json={"param": 42},
            )
        assert resp.status_code == 200
        manager.reconfigure.assert_called_once_with("s1", {"param": 42})

    @pytest.mark.asyncio
    async def test_config_nonexistent_strategy_via_manager_returns_404(self):
        manager = MagicMock()
        manager.get_strategy.return_value = None
        ctx = _make_context(strategy_manager=manager)
        app = create_app(ctx)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/strategies/missing/config",
                json={},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_strategies_manager_exception_falls_back_to_dict(self):
        manager = MagicMock()
        manager.list_strategies.side_effect = RuntimeError("boom")
        ctx = _make_context(
            strategies={"fallback": {"id": "fallback"}},
            strategy_manager=manager,
        )
        app = create_app(ctx)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/strategies")
        assert resp.status_code == 200
        data = resp.json()
        assert any(s.get("id") == "fallback" for s in data)


# ---------------------------------------------------------------------------
# EngineContext dataclass
# ---------------------------------------------------------------------------

class TestEngineContext:
    def test_default_context_has_paper_mode(self):
        ctx = EngineContext()
        assert ctx.execution_mode == "paper"

    def test_default_context_running_is_false(self):
        ctx = EngineContext()
        assert ctx.running is False

    def test_default_context_strategies_is_empty_dict(self):
        ctx = EngineContext()
        assert ctx.strategies == {}

    def test_default_context_positions_is_empty_list(self):
        ctx = EngineContext()
        assert ctx.positions == []

    def test_realized_pnl_default_is_zero(self):
        from decimal import Decimal
        ctx = EngineContext()
        assert ctx.realized_pnl == Decimal("0")


# ---------------------------------------------------------------------------
# verify_ws_token unit tests (US-106)
# ---------------------------------------------------------------------------

class TestVerifyWsToken:
    """Unit tests for verify_ws_token function."""

    def test_valid_token_in_query_params(self):
        from src.api.auth import verify_ws_token, create_token
        token = create_token("testuser")
        mock_ws = MagicMock()
        mock_ws.query_params = {"token": token}
        mock_ws.cookies = {}
        result = verify_ws_token(mock_ws)
        assert result == "testuser"

    def test_valid_token_in_cookie(self):
        from src.api.auth import verify_ws_token, create_token
        token = create_token("testuser")
        mock_ws = MagicMock()
        mock_ws.query_params = {}
        mock_ws.cookies = {"leviathan_token": token}
        result = verify_ws_token(mock_ws)
        assert result == "testuser"

    def test_query_param_takes_priority_over_cookie(self):
        from src.api.auth import verify_ws_token, create_token
        token1 = create_token("user1")
        token2 = create_token("user2")
        mock_ws = MagicMock()
        mock_ws.query_params = {"token": token1}
        mock_ws.cookies = {"leviathan_token": token2}
        result = verify_ws_token(mock_ws)
        assert result == "user1"

    def test_no_token_returns_none(self):
        from src.api.auth import verify_ws_token
        mock_ws = MagicMock()
        mock_ws.query_params = {}
        mock_ws.cookies = {}
        result = verify_ws_token(mock_ws)
        assert result is None

    def test_invalid_token_returns_none(self):
        from src.api.auth import verify_ws_token
        mock_ws = MagicMock()
        mock_ws.query_params = {"token": "not.a.valid.jwt"}
        mock_ws.cookies = {}
        result = verify_ws_token(mock_ws)
        assert result is None

    def test_expired_token_returns_none(self):
        import jwt as pyjwt
        from datetime import datetime, timedelta, timezone
        from src.api.auth import verify_ws_token, _JWT_SECRET, _JWT_ALGORITHM
        expired = pyjwt.encode(
            {"sub": "user", "exp": datetime.now(timezone.utc) - timedelta(hours=1)},
            _JWT_SECRET, algorithm=_JWT_ALGORITHM
        )
        mock_ws = MagicMock()
        mock_ws.query_params = {"token": expired}
        mock_ws.cookies = {}
        result = verify_ws_token(mock_ws)
        assert result is None


# ---------------------------------------------------------------------------
# WebSocket JWT Authentication (US-106)
# ---------------------------------------------------------------------------

class TestWebSocketAuth:
    """Test JWT authentication for WebSocket endpoints."""

    def _get_valid_token(self) -> str:
        """Generate a valid JWT token for testing."""
        from src.api.auth import create_token
        return create_token("admin")

    def _get_expired_token(self) -> str:
        """Generate an expired JWT token for testing."""
        import jwt as pyjwt
        from datetime import datetime, timedelta, timezone
        from src.api.auth import _JWT_SECRET, _JWT_ALGORITHM
        payload = {
            "sub": "admin",
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
            "iat": datetime.now(timezone.utc) - timedelta(hours=2),
        }
        return pyjwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)

    def test_ws_with_valid_token_query_param(self):
        """Valid token via ?token= query param allows WS connection."""
        ctx = _make_context()
        app = create_app(ctx)
        token = self._get_valid_token()
        from starlette.testclient import TestClient
        client = TestClient(app)
        with client.websocket_connect(f"/ws?token={token}") as ws:
            ws.send_text("hello")
            data = ws.receive_json()
            assert data["type"] == "ack"

    def test_ws_with_valid_token_cookie(self):
        """Valid token via leviathan_token cookie allows WS connection."""
        ctx = _make_context()
        app = create_app(ctx)
        token = self._get_valid_token()
        from starlette.testclient import TestClient
        client = TestClient(app, cookies={"leviathan_token": token})
        with client.websocket_connect("/ws") as ws:
            ws.send_text("hello")
            data = ws.receive_json()
            assert data["type"] == "ack"

    def test_ws_without_token_rejected(self):
        """WS connection without token is rejected with close code 4003."""
        ctx = _make_context()
        app = create_app(ctx)
        from starlette.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect
        client = TestClient(app)
        with pytest.raises(Exception):
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()

    def test_ws_with_invalid_token_rejected(self):
        """WS connection with invalid token is rejected."""
        ctx = _make_context()
        app = create_app(ctx)
        from starlette.testclient import TestClient
        client = TestClient(app)
        with pytest.raises(Exception):
            with client.websocket_connect("/ws?token=invalid.token.here") as ws:
                ws.receive_json()

    def test_ws_with_expired_token_rejected(self):
        """WS connection with expired token is rejected."""
        ctx = _make_context()
        app = create_app(ctx)
        token = self._get_expired_token()
        from starlette.testclient import TestClient
        client = TestClient(app)
        with pytest.raises(Exception):
            with client.websocket_connect(f"/ws?token={token}") as ws:
                ws.receive_json()

    def test_ws_feed_with_valid_token(self):
        """/ws/feed accepts authenticated connections."""
        ctx = _make_context()
        app = create_app(ctx)
        token = self._get_valid_token()
        from starlette.testclient import TestClient
        client = TestClient(app)
        with client.websocket_connect(f"/ws/feed?token={token}") as ws:
            ws.send_text("ping")
            data = ws.receive_json()
            assert data["type"] == "ack"

    def test_ws_feed_without_token_rejected(self):
        """/ws/feed rejects unauthenticated connections."""
        ctx = _make_context()
        app = create_app(ctx)
        from starlette.testclient import TestClient
        client = TestClient(app)
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/feed") as ws:
                ws.receive_json()

    def test_ws_strategies_with_valid_token(self):
        """/ws/strategies accepts authenticated connections."""
        ctx = _make_context(strategies={"s1": {"id": "s1", "enabled": True}})
        app = create_app(ctx)
        token = self._get_valid_token()
        from starlette.testclient import TestClient
        client = TestClient(app)
        with client.websocket_connect(f"/ws/strategies?token={token}") as ws:
            data = ws.receive_json()
            assert data["type"] == "state_update"

    def test_ws_strategies_without_token_rejected(self):
        """/ws/strategies rejects unauthenticated connections."""
        ctx = _make_context()
        app = create_app(ctx)
        from starlette.testclient import TestClient
        client = TestClient(app)
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/strategies") as ws:
                ws.receive_json()
