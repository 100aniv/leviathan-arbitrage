"""Tests for GET /api/v1/strategy-metrics endpoint."""
import pytest
from unittest.mock import MagicMock
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


class TestStrategyMetricsBasicResponse:
    def test_strategy_metrics_returns_200(self, client, auth_headers):
        assert client.get("/api/v1/strategy-metrics", headers=auth_headers).status_code == 200

    def test_strategy_metrics_returns_dict(self, client, auth_headers):
        assert isinstance(client.get("/api/v1/strategy-metrics", headers=auth_headers).json(), dict)

    def test_strategy_metrics_requires_auth(self, client):
        assert client.get("/api/v1/strategy-metrics").status_code == 401

    def test_strategy_metrics_contains_strategies_key(self, client, auth_headers):
        data = client.get("/api/v1/strategy-metrics", headers=auth_headers).json()
        assert "strategies" in data

    def test_strategy_metrics_empty_when_no_strategies(self, client, auth_headers):
        data = client.get("/api/v1/strategy-metrics", headers=auth_headers).json()
        assert data["strategies"] == {}


class TestStrategyMetricsWithoutStrategyManager:
    def test_fallback_uses_context_strategies_dict(self, client, context, auth_headers):
        context.strategies = {
            "cross_exchange": {"type": "cross_exchange", "enabled": True, "pnl": 1.5},
        }
        data = client.get("/api/v1/strategy-metrics", headers=auth_headers).json()
        assert "cross_exchange" in data["strategies"]

    def test_fallback_includes_strategy_id(self, client, context, auth_headers):
        context.strategies = {
            "funding_rate": {"type": "funding_rate", "enabled": True},
        }
        data = client.get("/api/v1/strategy-metrics", headers=auth_headers).json()
        assert data["strategies"]["funding_rate"]["id"] == "funding_rate"

    def test_fallback_includes_strategy_type(self, client, context, auth_headers):
        context.strategies = {
            "triangular": {"type": "triangular", "enabled": True},
        }
        data = client.get("/api/v1/strategy-metrics", headers=auth_headers).json()
        assert data["strategies"]["triangular"]["type"] == "triangular"

    def test_fallback_includes_enabled_flag(self, client, context, auth_headers):
        context.strategies = {
            "s1": {"type": "cross_exchange", "enabled": False},
        }
        data = client.get("/api/v1/strategy-metrics", headers=auth_headers).json()
        assert data["strategies"]["s1"]["enabled"] is False

    def test_fallback_includes_pnl(self, client, context, auth_headers):
        context.strategies = {
            "s1": {"type": "cross_exchange", "enabled": True, "pnl": 42.0},
        }
        data = client.get("/api/v1/strategy-metrics", headers=auth_headers).json()
        assert data["strategies"]["s1"]["pnl"] == 42.0

    def test_fallback_defaults_signals_received_to_zero(self, client, context, auth_headers):
        context.strategies = {
            "s1": {"type": "cross_exchange", "enabled": True},
        }
        data = client.get("/api/v1/strategy-metrics", headers=auth_headers).json()
        assert data["strategies"]["s1"]["signals_received"] == 0

    def test_fallback_multiple_strategies_all_present(self, client, context, auth_headers):
        context.strategies = {
            "s1": {"type": "cross_exchange", "enabled": True},
            "s2": {"type": "funding_rate", "enabled": False},
        }
        data = client.get("/api/v1/strategy-metrics", headers=auth_headers).json()
        assert len(data["strategies"]) == 2


class TestStrategyMetricsWithStrategyManager:
    def test_uses_strategy_manager_when_present(self, client, context, auth_headers):
        mock_manager = MagicMock()
        mock_manager.get_all_metrics_summary.return_value = {
            "cross_exchange": {"total_pnl": 100.0, "trade_count": 10},
        }
        context.strategy_manager = mock_manager
        data = client.get("/api/v1/strategy-metrics", headers=auth_headers).json()
        assert "cross_exchange" in data["strategies"]

    def test_strategy_manager_metrics_returned_directly(self, client, context, auth_headers):
        mock_manager = MagicMock()
        mock_manager.get_all_metrics_summary.return_value = {
            "s1": {"total_pnl": 55.5, "win_rate": 0.72},
        }
        context.strategy_manager = mock_manager
        data = client.get("/api/v1/strategy-metrics", headers=auth_headers).json()
        assert data["strategies"]["s1"]["total_pnl"] == 55.5

    def test_falls_back_to_context_when_manager_raises(self, client, context, auth_headers):
        mock_manager = MagicMock()
        mock_manager.get_all_metrics_summary.side_effect = RuntimeError("manager down")
        context.strategy_manager = mock_manager
        context.strategies = {
            "s1": {"type": "cross_exchange", "enabled": True},
        }
        data = client.get("/api/v1/strategy-metrics", headers=auth_headers).json()
        # Should not raise, should fall back gracefully
        assert "strategies" in data
