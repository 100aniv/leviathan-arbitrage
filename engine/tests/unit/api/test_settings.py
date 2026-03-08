"""Tests for GET /api/v1/settings and PUT /api/v1/settings endpoints."""
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


class TestSettingsGetBasicResponse:
    def test_get_settings_returns_200(self, client, auth_headers):
        assert client.get("/api/v1/settings", headers=auth_headers).status_code == 200

    def test_get_settings_returns_dict(self, client, auth_headers):
        assert isinstance(client.get("/api/v1/settings", headers=auth_headers).json(), dict)

    def test_get_settings_requires_auth(self, client):
        assert client.get("/api/v1/settings").status_code == 401

    def test_get_settings_includes_min_edge_bps(self, client, auth_headers):
        data = client.get("/api/v1/settings", headers=auth_headers).json()
        assert "min_edge_bps" in data

    def test_get_settings_includes_active_exchanges(self, client, auth_headers):
        data = client.get("/api/v1/settings", headers=auth_headers).json()
        assert "active_exchanges" in data

    def test_get_settings_default_min_edge_bps_is_5(self, client, auth_headers):
        data = client.get("/api/v1/settings", headers=auth_headers).json()
        assert data["min_edge_bps"] == 5

    def test_get_settings_default_active_exchanges_contains_binance(self, client, auth_headers):
        data = client.get("/api/v1/settings", headers=auth_headers).json()
        assert "binance" in data["active_exchanges"]

    def test_get_settings_reflects_context_runtime_settings(self, client, context, auth_headers):
        context.runtime_settings["min_edge_bps"] = 42
        data = client.get("/api/v1/settings", headers=auth_headers).json()
        assert data["min_edge_bps"] == 42


class TestSettingsPutUpdate:
    def test_put_settings_returns_200(self, client, auth_headers):
        response = client.put("/api/v1/settings", json={"min_edge_bps": 10}, headers=auth_headers)
        assert response.status_code == 200

    def test_put_settings_requires_auth(self, client):
        assert client.put("/api/v1/settings", json={"min_edge_bps": 10}).status_code == 401

    def test_put_settings_returns_dict(self, client, auth_headers):
        response = client.put("/api/v1/settings", json={"min_edge_bps": 10}, headers=auth_headers)
        assert isinstance(response.json(), dict)

    def test_put_settings_empty_body_returns_200(self, client, auth_headers):
        response = client.put("/api/v1/settings", json={}, headers=auth_headers)
        assert response.status_code == 200


class TestSettingsMinEdgeBpsChange:
    def test_put_min_edge_bps_updates_value(self, client, context, auth_headers):
        client.put("/api/v1/settings", json={"min_edge_bps": 15}, headers=auth_headers)
        assert context.runtime_settings["min_edge_bps"] == 15

    def test_put_min_edge_bps_reflected_in_subsequent_get(self, client, auth_headers):
        client.put("/api/v1/settings", json={"min_edge_bps": 20}, headers=auth_headers)
        data = client.get("/api/v1/settings", headers=auth_headers).json()
        assert data["min_edge_bps"] == 20

    def test_put_min_edge_bps_response_contains_updated_value(self, client, auth_headers):
        response = client.put("/api/v1/settings", json={"min_edge_bps": 30}, headers=auth_headers)
        assert response.json()["min_edge_bps"] == 30

    def test_put_min_edge_bps_does_not_affect_active_exchanges(self, client, context, auth_headers):
        original_exchanges = list(context.runtime_settings["active_exchanges"])
        client.put("/api/v1/settings", json={"min_edge_bps": 8}, headers=auth_headers)
        assert context.runtime_settings["active_exchanges"] == original_exchanges


class TestSettingsActiveExchangesChange:
    def test_put_active_exchanges_updates_list(self, client, context, auth_headers):
        client.put("/api/v1/settings", json={"active_exchanges": ["binance", "bybit"]}, headers=auth_headers)
        assert context.runtime_settings["active_exchanges"] == ["binance", "bybit"]

    def test_put_active_exchanges_reflected_in_subsequent_get(self, client, auth_headers):
        client.put("/api/v1/settings", json={"active_exchanges": ["okx"]}, headers=auth_headers)
        data = client.get("/api/v1/settings", headers=auth_headers).json()
        assert data["active_exchanges"] == ["okx"]

    def test_put_active_exchanges_response_contains_updated_list(self, client, auth_headers):
        new_exchanges = ["binance", "coinone"]
        response = client.put("/api/v1/settings", json={"active_exchanges": new_exchanges}, headers=auth_headers)
        assert response.json()["active_exchanges"] == new_exchanges

    def test_put_active_exchanges_does_not_affect_min_edge_bps(self, client, context, auth_headers):
        original_bps = context.runtime_settings["min_edge_bps"]
        client.put("/api/v1/settings", json={"active_exchanges": ["binance"]}, headers=auth_headers)
        assert context.runtime_settings["min_edge_bps"] == original_bps

    def test_put_active_exchanges_accepts_all_eight_exchanges(self, client, context, auth_headers):
        all_exchanges = ["binance", "binance_futures", "bybit", "okx", "bitget", "upbit", "bithumb", "coinone"]
        client.put("/api/v1/settings", json={"active_exchanges": all_exchanges}, headers=auth_headers)
        assert context.runtime_settings["active_exchanges"] == all_exchanges


class TestSettingsPartialUpdate:
    def test_put_partial_update_preserves_unmentioned_fields(self, client, context, auth_headers):
        context.runtime_settings["active_exchanges"] = ["binance"]
        client.put("/api/v1/settings", json={"min_edge_bps": 99}, headers=auth_headers)
        assert context.runtime_settings["active_exchanges"] == ["binance"]

    def test_put_both_fields_updates_both(self, client, context, auth_headers):
        client.put(
            "/api/v1/settings",
            json={"min_edge_bps": 7, "active_exchanges": ["bybit", "okx"]},
            headers=auth_headers,
        )
        assert context.runtime_settings["min_edge_bps"] == 7
        assert context.runtime_settings["active_exchanges"] == ["bybit", "okx"]
