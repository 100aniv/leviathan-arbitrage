"""Tests for GET /api/v1/trades endpoint."""
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


class TestTradesBasicResponse:
    def test_trades_returns_200(self, client, auth_headers):
        assert client.get("/api/v1/trades", headers=auth_headers).status_code == 200

    def test_trades_returns_list(self, client, auth_headers):
        assert isinstance(client.get("/api/v1/trades", headers=auth_headers).json(), list)

    def test_trades_empty_by_default(self, client, auth_headers):
        assert client.get("/api/v1/trades", headers=auth_headers).json() == []

    def test_trades_requires_auth(self, client):
        assert client.get("/api/v1/trades").status_code == 401

    def test_trades_returns_context_trade_history(self, client, context, auth_headers):
        context.trade_history = [{"id": "t1", "strategy_id": "cross_exchange", "timestamp": "2024-01-01T00:00:00"}]
        data = client.get("/api/v1/trades", headers=auth_headers).json()
        assert len(data) == 1
        assert data[0]["id"] == "t1"


class TestTradesStrategyFilter:
    def test_strategy_filter_returns_matching_trades(self, client, context, auth_headers):
        context.trade_history = [
            {"id": "t1", "strategy_id": "cross_exchange", "timestamp": "2024-01-01T00:01:00"},
            {"id": "t2", "strategy_id": "funding_rate", "timestamp": "2024-01-01T00:02:00"},
        ]
        data = client.get("/api/v1/trades?strategy=cross_exchange", headers=auth_headers).json()
        assert len(data) == 1
        assert data[0]["id"] == "t1"

    def test_strategy_filter_excludes_non_matching_trades(self, client, context, auth_headers):
        context.trade_history = [
            {"id": "t1", "strategy_id": "cross_exchange", "timestamp": "2024-01-01T00:00:00"},
            {"id": "t2", "strategy_id": "funding_rate", "timestamp": "2024-01-01T00:01:00"},
        ]
        data = client.get("/api/v1/trades?strategy=funding_rate", headers=auth_headers).json()
        assert all(t["strategy_id"] == "funding_rate" for t in data)

    def test_strategy_filter_returns_empty_when_no_match(self, client, context, auth_headers):
        context.trade_history = [
            {"id": "t1", "strategy_id": "cross_exchange", "timestamp": "2024-01-01T00:00:00"},
        ]
        data = client.get("/api/v1/trades?strategy=nonexistent", headers=auth_headers).json()
        assert data == []

    def test_no_strategy_filter_returns_all_trades(self, client, context, auth_headers):
        context.trade_history = [
            {"id": "t1", "strategy_id": "cross_exchange", "timestamp": "2024-01-01T00:00:00"},
            {"id": "t2", "strategy_id": "funding_rate", "timestamp": "2024-01-01T00:01:00"},
        ]
        data = client.get("/api/v1/trades", headers=auth_headers).json()
        assert len(data) == 2


class TestTradesLimitParameter:
    def test_limit_restricts_number_of_results(self, client, context, auth_headers):
        context.trade_history = [
            {"id": f"t{i}", "strategy_id": "cross_exchange", "timestamp": f"2024-01-01T00:{i:02d}:00"}
            for i in range(10)
        ]
        data = client.get("/api/v1/trades?limit=3", headers=auth_headers).json()
        assert len(data) == 3

    def test_limit_default_is_50(self, client, context, auth_headers):
        context.trade_history = [
            {"id": f"t{i}", "strategy_id": "cross_exchange", "timestamp": f"2024-01-01T00:{i:02d}:00"}
            for i in range(60)
        ]
        data = client.get("/api/v1/trades", headers=auth_headers).json()
        assert len(data) == 50

    def test_limit_returns_all_when_fewer_than_limit(self, client, context, auth_headers):
        context.trade_history = [
            {"id": "t1", "strategy_id": "cross_exchange", "timestamp": "2024-01-01T00:00:00"},
            {"id": "t2", "strategy_id": "cross_exchange", "timestamp": "2024-01-01T00:01:00"},
        ]
        data = client.get("/api/v1/trades?limit=50", headers=auth_headers).json()
        assert len(data) == 2


class TestTradesOrdering:
    def test_trades_sorted_by_timestamp_descending(self, client, context, auth_headers):
        context.trade_history = [
            {"id": "t1", "strategy_id": "cross_exchange", "timestamp": "2024-01-01T00:01:00"},
            {"id": "t2", "strategy_id": "cross_exchange", "timestamp": "2024-01-01T00:03:00"},
            {"id": "t3", "strategy_id": "cross_exchange", "timestamp": "2024-01-01T00:02:00"},
        ]
        data = client.get("/api/v1/trades", headers=auth_headers).json()
        timestamps = [t["timestamp"] for t in data]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_most_recent_trade_appears_first(self, client, context, auth_headers):
        context.trade_history = [
            {"id": "old", "strategy_id": "cross_exchange", "timestamp": "2024-01-01T00:00:00"},
            {"id": "new", "strategy_id": "cross_exchange", "timestamp": "2024-01-02T00:00:00"},
        ]
        data = client.get("/api/v1/trades", headers=auth_headers).json()
        assert data[0]["id"] == "new"

    def test_limit_applied_after_sorting(self, client, context, auth_headers):
        """Limit should return the N most recent trades, not the first N stored."""
        context.trade_history = [
            {"id": "oldest", "strategy_id": "cross_exchange", "timestamp": "2024-01-01T00:00:00"},
            {"id": "newest", "strategy_id": "cross_exchange", "timestamp": "2024-01-03T00:00:00"},
            {"id": "middle", "strategy_id": "cross_exchange", "timestamp": "2024-01-02T00:00:00"},
        ]
        data = client.get("/api/v1/trades?limit=1", headers=auth_headers).json()
        assert data[0]["id"] == "newest"
