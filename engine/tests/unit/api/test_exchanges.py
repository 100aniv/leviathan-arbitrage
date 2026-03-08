"""Tests for GET /api/v1/exchanges endpoint."""
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


class TestExchangesBasicResponse:
    def test_returns_200(self, client, auth_headers):
        assert client.get("/api/v1/exchanges", headers=auth_headers).status_code == 200

    def test_returns_dict(self, client, auth_headers):
        assert isinstance(client.get("/api/v1/exchanges", headers=auth_headers).json(), dict)

    def test_requires_auth(self, client):
        assert client.get("/api/v1/exchanges").status_code == 401

    def test_empty_by_default(self, client, auth_headers):
        assert client.get("/api/v1/exchanges", headers=auth_headers).json() == {}


class TestExchangesWithData:
    def test_returns_context_exchange_status(self, client, context, auth_headers):
        context.exchange_status = {"binance": {"connected": True}}
        data = client.get("/api/v1/exchanges", headers=auth_headers).json()
        assert "binance" in data

    def test_connected_field_returned(self, client, context, auth_headers):
        context.exchange_status = {"bybit": {"connected": False, "latency_ms": 120}}
        data = client.get("/api/v1/exchanges", headers=auth_headers).json()
        assert data["bybit"]["connected"] is False

    def test_latency_ms_field_returned(self, client, context, auth_headers):
        context.exchange_status = {"okx": {"connected": True, "latency_ms": 45}}
        data = client.get("/api/v1/exchanges", headers=auth_headers).json()
        assert data["okx"]["latency_ms"] == 45

    def test_orderbook_depth_field_returned(self, client, context, auth_headers):
        context.exchange_status = {"binance": {"connected": True, "orderbook_depth": 20}}
        data = client.get("/api/v1/exchanges", headers=auth_headers).json()
        assert data["binance"]["orderbook_depth"] == 20

    def test_symbols_count_field_returned(self, client, context, auth_headers):
        context.exchange_status = {"bitget": {"connected": True, "symbols_count": 175}}
        data = client.get("/api/v1/exchanges", headers=auth_headers).json()
        assert data["bitget"]["symbols_count"] == 175

    def test_last_update_field_returned(self, client, context, auth_headers):
        context.exchange_status = {
            "upbit": {"connected": True, "last_update": "2024-01-01T00:00:00"}
        }
        data = client.get("/api/v1/exchanges", headers=auth_headers).json()
        assert data["upbit"]["last_update"] == "2024-01-01T00:00:00"

    def test_balance_field_returned(self, client, context, auth_headers):
        context.exchange_status = {
            "coinone": {"connected": True, "balance": {"USDT": 1000.0}}
        }
        data = client.get("/api/v1/exchanges", headers=auth_headers).json()
        assert data["coinone"]["balance"]["USDT"] == 1000.0

    def test_returns_multiple_exchanges(self, client, context, auth_headers):
        context.exchange_status = {
            "binance": {"connected": True},
            "bybit": {"connected": False},
            "upbit": {"connected": True},
        }
        data = client.get("/api/v1/exchanges", headers=auth_headers).json()
        assert len(data) == 3

    def test_returns_all_eight_exchanges(self, client, context, auth_headers):
        all_exchanges = ["binance", "binance_futures", "bybit", "okx", "bitget", "upbit", "bithumb", "coinone"]
        context.exchange_status = {ex: {"connected": True} for ex in all_exchanges}
        data = client.get("/api/v1/exchanges", headers=auth_headers).json()
        assert set(data.keys()) == set(all_exchanges)

    def test_disconnected_exchange_returned(self, client, context, auth_headers):
        context.exchange_status = {"bithumb": {"connected": False, "latency_ms": None}}
        data = client.get("/api/v1/exchanges", headers=auth_headers).json()
        assert data["bithumb"]["connected"] is False
