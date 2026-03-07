"""Tests for FastAPI REST server."""
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
    """Authorization headers with a valid JWT for protected endpoints."""
    return {"Authorization": f"Bearer {create_token('test')}"}


class TestHealthRoute:
    def test_health_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_includes_status_ok(self, client):
        data = client.get("/health").json()
        assert data["status"] == "ok"

    def test_health_includes_engine_running(self, client, context):
        context.running = False
        data = client.get("/health").json()
        assert "engine_running" in data
        assert data["engine_running"] is False

    def test_health_running_true(self, client, context):
        context.running = True
        data = client.get("/health").json()
        assert data["engine_running"] is True


class TestStatusRoute:
    def test_status_returns_200(self, client):
        assert client.get("/api/v1/status").status_code == 200

    def test_status_includes_required_fields(self, client):
        data = client.get("/api/v1/status").json()
        assert "running" in data
        assert "kill_switch_active" in data
        assert "environment" in data

    def test_status_reflects_context_state(self, client, context):
        context.running = True
        context.kill_switch_active = True
        data = client.get("/api/v1/status").json()
        assert data["running"] is True
        assert data["kill_switch_active"] is True


class TestPositionsRoute:
    def test_positions_returns_200(self, client, auth_headers):
        assert client.get("/api/v1/positions", headers=auth_headers).status_code == 200

    def test_positions_returns_list(self, client, auth_headers):
        assert isinstance(client.get("/api/v1/positions", headers=auth_headers).json(), list)

    def test_positions_empty_by_default(self, client, auth_headers):
        assert client.get("/api/v1/positions", headers=auth_headers).json() == []

    def test_positions_returns_context_data(self, client, context, auth_headers):
        context.positions = [{"symbol": "BTC/USDT", "side": "LONG", "qty": "1.0"}]
        data = client.get("/api/v1/positions", headers=auth_headers).json()
        assert len(data) == 1
        assert data[0]["symbol"] == "BTC/USDT"


class TestStrategiesRoute:
    def test_strategies_returns_200(self, client):
        assert client.get("/api/v1/strategies").status_code == 200

    def test_strategies_returns_list(self, client):
        assert isinstance(client.get("/api/v1/strategies").json(), list)

    def test_strategies_returns_registered(self, client, context):
        context.strategies["arb_v1"] = {"id": "arb_v1", "enabled": True, "trades": 0}
        data = client.get("/api/v1/strategies").json()
        ids = [s["id"] for s in data]
        assert "arb_v1" in ids

    def test_toggle_existing_strategy(self, client, context):
        context.strategies["arb_v1"] = {"id": "arb_v1", "enabled": True, "trades": 0}
        response = client.post("/api/v1/strategies/arb_v1/toggle")
        assert response.status_code == 200

    def test_toggle_flips_enabled(self, client, context):
        context.strategies["arb_v1"] = {"id": "arb_v1", "enabled": True, "trades": 0}
        client.post("/api/v1/strategies/arb_v1/toggle")
        assert context.strategies["arb_v1"]["enabled"] is False

    def test_toggle_nonexistent_returns_404(self, client):
        assert client.post("/api/v1/strategies/missing/toggle").status_code == 404

    def test_config_update_returns_200(self, client, context):
        context.strategies["arb_v1"] = {"id": "arb_v1", "enabled": True, "config": {}}
        response = client.post(
            "/api/v1/strategies/arb_v1/config",
            json={"min_spread": "0.001"},
        )
        assert response.status_code == 200

    def test_config_update_nonexistent_returns_404(self, client):
        response = client.post("/api/v1/strategies/missing/config", json={})
        assert response.status_code == 404


class TestKillSwitchRoute:
    def test_kill_switch_returns_200(self, client, auth_headers):
        response = client.post("/api/v1/kill-switch", json={"reason": "test"}, headers=auth_headers)
        assert response.status_code == 200

    def test_kill_switch_sets_context_flag(self, client, context, auth_headers):
        client.post("/api/v1/kill-switch", json={"reason": "manual_test"}, headers=auth_headers)
        assert context.kill_switch_active is True

    def test_kill_switch_response_includes_reason(self, client, auth_headers):
        data = client.post("/api/v1/kill-switch", json={"reason": "emergency"}, headers=auth_headers).json()
        assert "reason" in data or "status" in data


class TestPnLRoute:
    def test_pnl_returns_200(self, client, auth_headers):
        assert client.get("/api/v1/pnl", headers=auth_headers).status_code == 200

    def test_pnl_includes_fields(self, client, auth_headers):
        data = client.get("/api/v1/pnl", headers=auth_headers).json()
        assert "realized_pnl" in data
        assert "unrealized_pnl" in data
        assert "total_pnl" in data

    def test_pnl_reflects_context(self, client, context, auth_headers):
        from decimal import Decimal
        context.realized_pnl = Decimal("123.45")
        data = client.get("/api/v1/pnl", headers=auth_headers).json()
        assert float(data["realized_pnl"]) == pytest.approx(123.45)


class TestMetricsRoute:
    def test_metrics_returns_200(self, client):
        assert client.get("/api/v1/metrics").status_code == 200

    def test_metrics_content_type_is_text(self, client):
        response = client.get("/api/v1/metrics")
        assert "text/plain" in response.headers["content-type"]
