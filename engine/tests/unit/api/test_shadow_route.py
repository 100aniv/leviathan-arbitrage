"""Unit tests for GET /api/v1/shadow/stats route."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.auth import create_token
from src.api.server import EngineContext, create_app


@pytest.fixture
def context():
    return EngineContext()


@pytest.fixture
def client(context):
    app = create_app(context)
    return TestClient(app)


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {create_token('test')}"}


class TestShadowStatsUnauthorized:
    def test_shadow_stats_unauthorized(self, client):
        """Unauthenticated request returns 401."""
        response = client.get("/api/v1/shadow/stats")
        assert response.status_code == 401


class TestShadowStatsNoShadowMode:
    def test_shadow_stats_no_shadow_mode(self, client, auth_headers):
        """When context.shadow_mode is None, returns active=false."""
        response = client.get("/api/v1/shadow/stats", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["active"] is False


class TestShadowStatsWithData:
    def test_shadow_stats_with_data(self, client, context, auth_headers):
        """When shadow_mode is set, returns snapshot data."""
        mock_shadow = MagicMock()
        mock_shadow.get_snapshot.return_value = {
            "active": True,
            "trades_executed": 42,
            "win_rate": 0.75,
            "total_pnl": 15.5,
            "by_strategy": [],
        }
        context.shadow_mode = mock_shadow
        response = client.get("/api/v1/shadow/stats", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["active"] is True
        assert data["trades_executed"] == 42
        mock_shadow.get_snapshot.assert_called_once()

    def test_shadow_stats_get_snapshot_exception_returns_active_false(
        self, client, context, auth_headers
    ):
        """When get_snapshot() raises, endpoint returns active=false gracefully."""
        mock_shadow = MagicMock()
        mock_shadow.get_snapshot.side_effect = RuntimeError("boom")
        context.shadow_mode = mock_shadow
        response = client.get("/api/v1/shadow/stats", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["active"] is False


class TestShadowStatsResponseFormat:
    def test_shadow_stats_response_format(self, client, context, auth_headers):
        """Response JSON contains all expected fields when shadow is active."""
        mock_shadow = MagicMock()
        mock_shadow.get_snapshot.return_value = {
            "active": True,
            "uptime_seconds": 300.0,
            "signals_detected": 100,
            "trades_executed": 50,
            "trades_won": 38,
            "trades_lost": 12,
            "win_rate": 0.76,
            "total_pnl": 20.5,
            "peak_pnl": 25.0,
            "max_drawdown": 0.05,
            "trades_rejected": 3,
            "trades_partial_fill": 2,
            "trades_rate_limited": 1,
            "by_strategy": [],
        }
        context.shadow_mode = mock_shadow
        data = client.get("/api/v1/shadow/stats", headers=auth_headers).json()
        required_fields = [
            "active", "uptime_seconds", "signals_detected", "trades_executed",
            "trades_won", "trades_lost", "win_rate", "total_pnl",
            "peak_pnl", "max_drawdown", "trades_rejected", "trades_partial_fill",
            "trades_rate_limited", "by_strategy",
        ]
        for field_name in required_fields:
            assert field_name in data, f"Missing field: {field_name}"
