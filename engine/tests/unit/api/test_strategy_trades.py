"""Tests for GET /api/v1/strategies/{strategy_id}/trades endpoint."""
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


class TestStrategyTradesBasicResponse:
    def test_returns_200_for_existing_strategy(self, client, context, auth_headers):
        context.trade_history.append(
            {"id": "t1", "strategy_id": "cross_exchange", "timestamp": "2024-01-01T00:00:00"}
        )
        resp = client.get("/api/v1/strategies/cross_exchange/trades", headers=auth_headers)
        assert resp.status_code == 200

    def test_returns_list(self, client, context, auth_headers):
        context.trade_history.append(
            {"id": "t1", "strategy_id": "cross_exchange", "timestamp": "2024-01-01T00:00:00"}
        )
        data = client.get("/api/v1/strategies/cross_exchange/trades", headers=auth_headers).json()
        assert isinstance(data, list)

    def test_requires_auth(self, client, context):
        context.trade_history.append(
            {"id": "t1", "strategy_id": "cross_exchange", "timestamp": "2024-01-01T00:00:00"}
        )
        assert client.get("/api/v1/strategies/cross_exchange/trades").status_code == 401

    def test_returns_only_matching_strategy_trades(self, client, context, auth_headers):
        context.trade_history.extend([
            {"id": "t1", "strategy_id": "cross_exchange", "timestamp": "2024-01-01T00:01:00"},
            {"id": "t2", "strategy_id": "funding_rate", "timestamp": "2024-01-01T00:02:00"},
        ])
        data = client.get("/api/v1/strategies/cross_exchange/trades", headers=auth_headers).json()
        assert all(t["strategy_id"] == "cross_exchange" for t in data)

    def test_trade_fields_preserved(self, client, context, auth_headers):
        context.trade_history.append(
            {"id": "t1", "strategy_id": "cross_exchange", "timestamp": "2024-01-01T00:00:00"}
        )
        data = client.get("/api/v1/strategies/cross_exchange/trades", headers=auth_headers).json()
        assert data[0]["id"] == "t1"
        assert data[0]["strategy_id"] == "cross_exchange"


class TestStrategyTradesFilter:
    def test_filters_by_strategy_id_path_param(self, client, context, auth_headers):
        context.trade_history.extend([
            {"id": "t1", "strategy_id": "cross_exchange", "timestamp": "2024-01-01T00:01:00"},
            {"id": "t2", "strategy_id": "funding_rate", "timestamp": "2024-01-01T00:02:00"},
            {"id": "t3", "strategy_id": "triangular", "timestamp": "2024-01-01T00:03:00"},
        ])
        data = client.get("/api/v1/strategies/funding_rate/trades", headers=auth_headers).json()
        assert len(data) == 1
        assert data[0]["id"] == "t2"

    def test_excludes_trades_from_other_strategies(self, client, context, auth_headers):
        context.trade_history.extend([
            {"id": "t1", "strategy_id": "cross_exchange", "timestamp": "2024-01-01T00:01:00"},
            {"id": "t2", "strategy_id": "funding_rate", "timestamp": "2024-01-01T00:02:00"},
        ])
        data = client.get("/api/v1/strategies/cross_exchange/trades", headers=auth_headers).json()
        assert not any(t["strategy_id"] == "funding_rate" for t in data)

    def test_multiple_trades_for_same_strategy_all_returned(self, client, context, auth_headers):
        context.trade_history.extend([
            {"id": f"t{i}", "strategy_id": "cross_exchange", "timestamp": f"2024-01-01T00:0{i}:00"}
            for i in range(1, 4)
        ])
        data = client.get("/api/v1/strategies/cross_exchange/trades", headers=auth_headers).json()
        assert len(data) == 3


class TestStrategyTradesNonexistentStrategy:
    def test_nonexistent_strategy_returns_200(self, client, auth_headers):
        resp = client.get("/api/v1/strategies/nonexistent_strategy/trades", headers=auth_headers)
        assert resp.status_code == 200

    def test_nonexistent_strategy_returns_empty_list(self, client, auth_headers):
        data = client.get("/api/v1/strategies/nonexistent_strategy/trades", headers=auth_headers).json()
        assert data == []

    def test_empty_trade_history_returns_empty_list(self, client, auth_headers):
        data = client.get("/api/v1/strategies/cross_exchange/trades", headers=auth_headers).json()
        assert data == []

    def test_no_matching_trades_returns_empty_list(self, client, context, auth_headers):
        context.trade_history.append(
            {"id": "t1", "strategy_id": "funding_rate", "timestamp": "2024-01-01T00:00:00"}
        )
        data = client.get("/api/v1/strategies/cross_exchange/trades", headers=auth_headers).json()
        assert data == []


class TestStrategyTradesLimitParameter:
    def test_limit_restricts_number_of_results(self, client, context, auth_headers):
        context.trade_history.extend([
            {"id": f"t{i}", "strategy_id": "cross_exchange", "timestamp": f"2024-01-0{i+1}T00:00:00"}
            for i in range(10)
        ])
        data = client.get("/api/v1/strategies/cross_exchange/trades?limit=3", headers=auth_headers).json()
        assert len(data) == 3

    def test_limit_default_returns_up_to_50(self, client, context, auth_headers):
        context.trade_history.extend([
            {"id": f"t{i}", "strategy_id": "cross_exchange", "timestamp": f"2024-01-01T{i:02d}:00:00"}
            for i in range(60)
        ])
        data = client.get("/api/v1/strategies/cross_exchange/trades", headers=auth_headers).json()
        assert len(data) == 50

    def test_limit_returns_all_when_fewer_than_limit(self, client, context, auth_headers):
        context.trade_history.extend([
            {"id": "t1", "strategy_id": "cross_exchange", "timestamp": "2024-01-01T00:01:00"},
            {"id": "t2", "strategy_id": "cross_exchange", "timestamp": "2024-01-01T00:02:00"},
        ])
        data = client.get("/api/v1/strategies/cross_exchange/trades?limit=50", headers=auth_headers).json()
        assert len(data) == 2

    def test_limit_1_returns_single_most_recent_trade(self, client, context, auth_headers):
        context.trade_history.extend([
            {"id": "old", "strategy_id": "cross_exchange", "timestamp": "2024-01-01T00:00:00"},
            {"id": "new", "strategy_id": "cross_exchange", "timestamp": "2024-01-02T00:00:00"},
        ])
        data = client.get("/api/v1/strategies/cross_exchange/trades?limit=1", headers=auth_headers).json()
        assert len(data) == 1
        assert data[0]["id"] == "new"
