"""Unit tests for GET /api/v1/portfolio-summary route."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
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


class TestPortfolioAuth:
    def test_requires_auth(self, client):
        """Unauthenticated request returns 401."""
        response = client.get("/api/v1/portfolio-summary")
        assert response.status_code == 401

    def test_returns_200(self, client, auth_headers):
        """Authenticated request returns 200."""
        response = client.get("/api/v1/portfolio-summary", headers=auth_headers)
        assert response.status_code == 200


class TestPortfolioEmptyContext:
    def test_empty_context_returns_defaults(self, client, auth_headers):
        """Empty context returns zero balance and empty exchange_balances."""
        data = client.get("/api/v1/portfolio-summary", headers=auth_headers).json()
        assert data["total_balance_usdt"] == 0.0
        assert data["exchange_balances"] == []
        assert data["active_positions"] == 0
        assert data["total_pnl"] == 0.0


class TestPortfolioShadowMode:
    def test_shadow_balance_tracker(self, client, context, auth_headers):
        """Shadow mode VirtualBalanceTracker data reflected in response."""
        mock_shadow = MagicMock()
        mock_tracker = MagicMock()
        mock_tracker.summary.return_value = {
            "binance": "5000000.00",
            "upbit": "3000000.00",
        }
        mock_shadow._balance_tracker = mock_tracker
        context.shadow_mode = mock_shadow

        data = client.get("/api/v1/portfolio-summary", headers=auth_headers).json()
        assert data["total_balance_usdt"] == 8000000.0
        assert len(data["exchange_balances"]) == 2

        # Check sorted order
        assert data["exchange_balances"][0]["exchange_id"] == "binance"
        assert data["exchange_balances"][0]["balance_usdt"] == 5000000.0
        assert data["exchange_balances"][1]["exchange_id"] == "upbit"
        assert data["exchange_balances"][1]["balance_usdt"] == 3000000.0


class TestPortfolioExchangeStatusFallback:
    def test_exchange_status_fallback(self, client, context, auth_headers):
        """When shadow is None, exchange_status balance is used."""
        context.exchange_status = {
            "binance": {"balance": {"USDT": 1000.0}, "connected": True},
            "bybit": {"balance": {"USDT": 2000.0}, "connected": False},
        }

        data = client.get("/api/v1/portfolio-summary", headers=auth_headers).json()
        assert data["total_balance_usdt"] == 3000.0
        assert len(data["exchange_balances"]) == 2


class TestPortfolioCalculations:
    def test_pct_of_total_calculation(self, client, context, auth_headers):
        """pct_of_total sums to approximately 1.0."""
        mock_shadow = MagicMock()
        mock_tracker = MagicMock()
        mock_tracker.summary.return_value = {
            "binance": "6000.00",
            "upbit": "4000.00",
        }
        mock_shadow._balance_tracker = mock_tracker
        context.shadow_mode = mock_shadow

        data = client.get("/api/v1/portfolio-summary", headers=auth_headers).json()
        total_pct = sum(eb["pct_of_total"] for eb in data["exchange_balances"])
        assert abs(total_pct - 1.0) < 0.01

        # Binance should be 60%
        binance = next(eb for eb in data["exchange_balances"] if eb["exchange_id"] == "binance")
        assert binance["pct_of_total"] == 0.6

    def test_total_balance_sum(self, client, context, auth_headers):
        """total_balance_usdt equals sum of exchange_balances[].balance_usdt."""
        mock_shadow = MagicMock()
        mock_tracker = MagicMock()
        mock_tracker.summary.return_value = {
            "binance": "1234.56",
            "okx": "7890.12",
            "upbit": "5555.55",
        }
        mock_shadow._balance_tracker = mock_tracker
        context.shadow_mode = mock_shadow

        data = client.get("/api/v1/portfolio-summary", headers=auth_headers).json()
        expected = sum(eb["balance_usdt"] for eb in data["exchange_balances"])
        assert abs(data["total_balance_usdt"] - expected) < 0.01


class TestPortfolioFields:
    def test_pnl_fields(self, client, context, auth_headers):
        """PnL fields reflect context realized + unrealized."""
        context.realized_pnl = Decimal("100.50")
        context.unrealized_pnl = Decimal("25.25")

        data = client.get("/api/v1/portfolio-summary", headers=auth_headers).json()
        assert abs(data["total_pnl"] - 125.75) < 0.001
        assert abs(data["daily_pnl"] - 125.75) < 0.001

    def test_mode_field(self, client, context, auth_headers):
        """Mode field reflects execution_mode from context."""
        context.execution_mode = "shadow"
        data = client.get("/api/v1/portfolio-summary", headers=auth_headers).json()
        assert data["mode"] == "shadow"

    def test_last_updated_iso_format(self, client, auth_headers):
        """last_updated is valid ISO 8601 format."""
        data = client.get("/api/v1/portfolio-summary", headers=auth_headers).json()
        # Should parse without error
        ts = datetime.fromisoformat(data["last_updated"])
        assert ts is not None

    def test_position_count_from_manager(self, client, context, auth_headers):
        """Position count comes from position_manager when available."""
        mock_pm = MagicMock()
        mock_pm.get_all_positions.return_value = [{"id": 1}, {"id": 2}, {"id": 3}]
        context.position_manager = mock_pm

        data = client.get("/api/v1/portfolio-summary", headers=auth_headers).json()
        assert data["active_positions"] == 3

    def test_connected_status_merged(self, client, context, auth_headers):
        """Exchange connection status merged from exchange_status into balance response."""
        mock_shadow = MagicMock()
        mock_tracker = MagicMock()
        mock_tracker.summary.return_value = {
            "binance": "1000.00",
            "upbit": "2000.00",
        }
        mock_shadow._balance_tracker = mock_tracker
        context.shadow_mode = mock_shadow
        context.exchange_status = {
            "binance": {"connected": True},
            "upbit": {"connected": False},
        }

        data = client.get("/api/v1/portfolio-summary", headers=auth_headers).json()
        binance = next(eb for eb in data["exchange_balances"] if eb["exchange_id"] == "binance")
        upbit = next(eb for eb in data["exchange_balances"] if eb["exchange_id"] == "upbit")
        assert binance["connected"] is True
        assert upbit["connected"] is False
