"""Tests for GET /api/v1/funding-rates endpoint."""
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


class TestFundingRatesBasicResponse:
    def test_funding_rates_returns_200(self, client, auth_headers):
        assert client.get("/api/v1/funding-rates", headers=auth_headers).status_code == 200

    def test_funding_rates_returns_dict(self, client, auth_headers):
        assert isinstance(client.get("/api/v1/funding-rates", headers=auth_headers).json(), dict)

    def test_funding_rates_requires_auth(self, client):
        assert client.get("/api/v1/funding-rates").status_code == 401

    def test_funding_rates_empty_by_default(self, client, auth_headers):
        assert client.get("/api/v1/funding-rates", headers=auth_headers).json() == {}


class TestFundingRatesWithData:
    def test_returns_context_funding_rates(self, client, context, auth_headers):
        context.funding_rates = {
            "binance_futures": {
                "BTC/USDT": {"rate": 0.0001, "next_funding_time": "2024-01-01T08:00:00", "updated_at": "2024-01-01T00:00:00"},
            }
        }
        data = client.get("/api/v1/funding-rates", headers=auth_headers).json()
        assert "binance_futures" in data

    def test_returns_correct_rate_value(self, client, context, auth_headers):
        context.funding_rates = {
            "bybit": {
                "ETH/USDT": {"rate": 0.0003, "next_funding_time": "2024-01-01T08:00:00", "updated_at": "2024-01-01T00:00:00"},
            }
        }
        data = client.get("/api/v1/funding-rates", headers=auth_headers).json()
        assert data["bybit"]["ETH/USDT"]["rate"] == 0.0003

    def test_returns_multiple_exchanges(self, client, context, auth_headers):
        context.funding_rates = {
            "binance_futures": {"BTC/USDT": {"rate": 0.0001}},
            "bybit": {"BTC/USDT": {"rate": 0.0002}},
        }
        data = client.get("/api/v1/funding-rates", headers=auth_headers).json()
        assert len(data) == 2

    def test_returns_multiple_symbols_per_exchange(self, client, context, auth_headers):
        context.funding_rates = {
            "binance_futures": {
                "BTC/USDT": {"rate": 0.0001},
                "ETH/USDT": {"rate": -0.0002},
                "SOL/USDT": {"rate": 0.0005},
            }
        }
        data = client.get("/api/v1/funding-rates", headers=auth_headers).json()
        assert len(data["binance_futures"]) == 3

    def test_negative_funding_rate_returned_correctly(self, client, context, auth_headers):
        context.funding_rates = {
            "okx": {
                "BTC/USDT": {"rate": -0.0001, "updated_at": "2024-01-01T00:00:00"},
            }
        }
        data = client.get("/api/v1/funding-rates", headers=auth_headers).json()
        assert data["okx"]["BTC/USDT"]["rate"] == -0.0001

    def test_next_funding_time_included_in_response(self, client, context, auth_headers):
        context.funding_rates = {
            "binance_futures": {
                "BTC/USDT": {
                    "rate": 0.0001,
                    "next_funding_time": "2024-01-01T08:00:00",
                    "updated_at": "2024-01-01T00:00:00",
                },
            }
        }
        data = client.get("/api/v1/funding-rates", headers=auth_headers).json()
        assert data["binance_futures"]["BTC/USDT"]["next_funding_time"] == "2024-01-01T08:00:00"

    def test_updated_at_included_in_response(self, client, context, auth_headers):
        context.funding_rates = {
            "binance_futures": {
                "BTC/USDT": {"rate": 0.0001, "updated_at": "2024-01-01T07:55:00"},
            }
        }
        data = client.get("/api/v1/funding-rates", headers=auth_headers).json()
        assert data["binance_futures"]["BTC/USDT"]["updated_at"] == "2024-01-01T07:55:00"
