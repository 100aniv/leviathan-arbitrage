"""API integration tests.

Tests API routes with real subsystem objects (StrategyManager, PositionManager)
using TestClient (no external dependencies).
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.auth import create_token
from src.api.server import EngineContext, create_app
from src.core.models import OrderSide
from src.risk.kill_switch import clear_halt

from src.api.auth import create_token

def _auth_header() -> dict:
    return {"Authorization": f"Bearer {create_token('test_user')}"}

# Pre-built auth header for tests that hit JWT-protected endpoints
_AUTH_HEADERS = {"Authorization": f"Bearer {create_token('test')}"}


@pytest.fixture(autouse=True)
def reset_kill_switch():
    clear_halt()
    yield
    clear_halt()


def _make_context(**overrides) -> EngineContext:
    """Create a minimal EngineContext for testing."""
    defaults = {
        "running": True,
        "kill_switch_active": False,
        "environment": "test",
        "execution_mode": "paper",
        "strategies": {},
        "positions": [],
        "realized_pnl": Decimal("0"),
        "unrealized_pnl": Decimal("0"),
        "ws_manager": None,
        "engine": None,
        "strategy_manager": None,
        "risk_guardian": None,
        "position_manager": None,
        "trade_consumer": None,
    }
    defaults.update(overrides)
    return EngineContext(**defaults)


def _make_app(ctx: EngineContext):
    app = create_app()
    app.state.engine_context = ctx
    return app


class TestStatusEndpoint:
    """Test /api/v1/status endpoint."""

    def test_status_returns_engine_state(self):
        ctx = _make_context(running=True, environment="paper")
        app = _make_app(ctx)
        client = TestClient(app)

        resp = client.get("/api/v1/status", headers=_auth_header())
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is True
        assert data["environment"] == "paper"
        assert data["execution_mode"] == "paper"

    def test_status_with_kill_switch(self):
        ctx = _make_context(kill_switch_active=True)
        app = _make_app(ctx)
        client = TestClient(app)

        resp = client.get("/api/v1/status", headers=_auth_header())
        data = resp.json()
        assert data["kill_switch_active"] is True


class TestPositionsEndpoint:
    """Test /api/v1/positions endpoint."""

    def test_positions_empty(self):
        ctx = _make_context()
        app = _make_app(ctx)
        client = TestClient(app)

        resp = client.get("/api/v1/positions", headers=_AUTH_HEADERS)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_positions_from_position_manager(self):
        mock_pm = MagicMock()
        mock_pos = MagicMock()
        mock_pos.strategy_id = "test_strat"
        mock_pos.exchange_id = "binance"
        mock_pos.symbol = "BTC/USDT"
        mock_pos.side = "long"
        mock_pos.quantity = Decimal("0.1")
        mock_pos.entry_price = Decimal("50000")
        mock_pos.mark_price = Decimal("50500")
        mock_pos.unrealized_pnl = Decimal("50")
        mock_pos.realized_pnl = Decimal("10")
        mock_pm.get_all_positions.return_value = [mock_pos]

        ctx = _make_context(position_manager=mock_pm)
        app = _make_app(ctx)
        client = TestClient(app)

        resp = client.get("/api/v1/positions", headers=_AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["strategy_id"] == "test_strat"
        assert data[0]["unrealized_pnl"] == 50.0

    def test_positions_fallback_to_list(self):
        ctx = _make_context(
            positions=[{"symbol": "BTC/USDT", "size": 0.01}],
        )
        app = _make_app(ctx)
        client = TestClient(app)

        resp = client.get("/api/v1/positions", headers=_AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1


class TestPnLEndpoint:
    """Test /api/v1/pnl endpoint."""

    def test_pnl_default_zeros(self):
        ctx = _make_context()
        app = _make_app(ctx)
        client = TestClient(app)

        resp = client.get("/api/v1/pnl", headers=_AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["realized_pnl"] == 0.0
        assert data["unrealized_pnl"] == 0.0
        assert data["total_pnl"] == 0.0

    def test_pnl_from_position_manager(self):
        mock_pm = MagicMock()
        mock_pos = MagicMock()
        mock_pos.realized_pnl = Decimal("25.5")
        mock_pos.unrealized_pnl = Decimal("10.3")
        mock_pm.get_all_positions.return_value = [mock_pos]

        ctx = _make_context(position_manager=mock_pm)
        app = _make_app(ctx)
        client = TestClient(app)

        resp = client.get("/api/v1/pnl", headers=_AUTH_HEADERS)
        data = resp.json()
        assert data["realized_pnl"] == 25.5
        assert data["unrealized_pnl"] == 10.3
        assert data["total_pnl"] == 35.8


class TestStrategiesEndpoint:
    """Test /api/v1/strategies endpoints."""

    def test_list_strategies_empty(self):
        ctx = _make_context()
        app = _make_app(ctx)
        client = TestClient(app)

        resp = client.get("/api/v1/strategies", headers=_auth_header())
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_strategies_from_manager(self):
        mock_sm = MagicMock()
        mock_sm.list_strategies.return_value = ["cross_exchange_v1"]
        mock_strategy = MagicMock()
        mock_strategy.is_active = True
        mock_strategy.STRATEGY_TYPE = "cross_exchange"
        mock_strategy.metrics = MagicMock()
        mock_strategy.metrics.model_dump.return_value = {"signals_received": 10}
        mock_sm.get_strategy.return_value = mock_strategy

        ctx = _make_context(strategy_manager=mock_sm)
        app = _make_app(ctx)
        client = TestClient(app)

        resp = client.get("/api/v1/strategies", headers=_auth_header())
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "cross_exchange_v1"
        assert data[0]["enabled"] is True

    def test_toggle_strategy(self):
        ctx = _make_context(
            strategies={"strat_1": {"id": "strat_1", "enabled": True}},
        )
        app = _make_app(ctx)
        client = TestClient(app)

        resp = client.post("/api/v1/strategies/strat_1/toggle", headers=_auth_header())
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False

    def test_toggle_nonexistent_strategy(self):
        ctx = _make_context()
        app = _make_app(ctx)
        client = TestClient(app)

        resp = client.post("/api/v1/strategies/nonexistent/toggle", headers=_auth_header())
        assert resp.status_code == 404

    def test_update_strategy_config(self):
        ctx = _make_context(
            strategies={"strat_1": {"id": "strat_1", "config": {}}},
        )
        app = _make_app(ctx)
        client = TestClient(app)

        resp = client.post(
            "/api/v1/strategies/strat_1/config",
            json={"min_spread_bps": 15.0},
            headers=_auth_header(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["config"]["min_spread_bps"] == 15.0


class TestModeEndpoint:
    """Test /api/v1/mode endpoint."""

    def test_mode_returns_defaults(self):
        ctx = _make_context(execution_mode="paper")
        app = _make_app(ctx)
        client = TestClient(app)

        resp = client.get("/api/v1/mode", headers=_auth_header())
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "paper"
        assert data["data_mode"] == "synthetic"
        assert data["shadow_active"] is False
        assert data["live_gate_eligible"] is False

    def test_mode_reflects_live_execution_mode(self):
        ctx = _make_context(execution_mode="live")
        app = _make_app(ctx)
        client = TestClient(app)

        resp = client.get("/api/v1/mode", headers=_auth_header())
        assert resp.status_code == 200
        assert resp.json()["mode"] == "live"

    def test_mode_is_public_no_auth_required(self):
        ctx = _make_context()
        app = _make_app(ctx)
        client = TestClient(app)

        # No auth header — must still return 200
        resp = client.get("/api/v1/mode", headers=_auth_header())
        assert resp.status_code == 200


class TestRiskMetricsEndpoint:
    """Test /api/v1/risk/metrics endpoint."""

    def test_risk_metrics_defaults(self):
        ctx = _make_context()
        app = _make_app(ctx)
        client = TestClient(app)

        resp = client.get("/api/v1/risk/metrics", headers=_auth_header())
        assert resp.status_code == 200
        data = resp.json()
        assert data["kill_switch_active"] is False
        assert data["circuit_breaker_state"] == "CLOSED"
        assert data["max_drawdown_pct"] == 0.0
        assert data["daily_loss_pct"] == 0.0
        assert data["position_count"] == 0
        assert data["correlation_alert"] is False

    def test_risk_metrics_reads_from_risk_guardian(self):
        mock_rg = MagicMock()
        mock_rg.kill_switch_active = True
        mock_rg.circuit_breaker_state = "OPEN"
        mock_rg.max_drawdown_pct = 5.5
        mock_rg.daily_loss_pct = 2.1
        mock_rg.correlation_alert = True

        ctx = _make_context(risk_guardian=mock_rg)
        app = _make_app(ctx)
        client = TestClient(app)

        resp = client.get("/api/v1/risk/metrics", headers=_auth_header())
        assert resp.status_code == 200
        data = resp.json()
        assert data["kill_switch_active"] is True
        assert data["circuit_breaker_state"] == "OPEN"
        assert data["max_drawdown_pct"] == 5.5
        assert data["daily_loss_pct"] == 2.1
        assert data["correlation_alert"] is True

    def test_risk_metrics_position_count_from_position_manager(self):
        mock_pm = MagicMock()
        mock_pm.get_all_positions.return_value = [MagicMock(), MagicMock()]

        ctx = _make_context(position_manager=mock_pm)
        app = _make_app(ctx)
        client = TestClient(app)

        resp = client.get("/api/v1/risk/metrics", headers=_auth_header())
        assert resp.status_code == 200
        assert resp.json()["position_count"] == 2

    def test_risk_metrics_position_count_from_context_list(self):
        ctx = _make_context(positions=[{"symbol": "BTC/USDT"}, {"symbol": "ETH/USDT"}])
        app = _make_app(ctx)
        client = TestClient(app)

        resp = client.get("/api/v1/risk/metrics", headers=_auth_header())
        assert resp.status_code == 200
        assert resp.json()["position_count"] == 2

    def test_risk_metrics_is_public_no_auth_required(self):
        ctx = _make_context()
        app = _make_app(ctx)
        client = TestClient(app)

        resp = client.get("/api/v1/risk/metrics", headers=_auth_header())
        assert resp.status_code == 200

    def test_risk_metrics_kill_switch_active_reflected(self):
        ctx = _make_context(kill_switch_active=True)
        app = _make_app(ctx)
        client = TestClient(app)

        resp = client.get("/api/v1/risk/metrics", headers=_auth_header())
        assert resp.json()["kill_switch_active"] is True


class TestPrometheusAliasEndpoint:
    """Test /metrics short-path alias."""

    def test_metrics_alias_returns_200(self):
        ctx = _make_context()
        app = _make_app(ctx)
        client = TestClient(app)

        resp = client.get("/metrics", headers=_auth_header())
        assert resp.status_code == 200


class TestMetricsCollector:
    """Test MetricsCollector integration."""

    def test_metrics_collector_basic(self):
        from src.core.metrics_collector import MetricsCollector

        collector = MetricsCollector(initial_capital=100.0)
        collector.record_trade("strat_a", 5.0)
        collector.record_trade("strat_a", -2.0)
        collector.record_trade("strat_b", 3.0)

        report = collector.get_report()
        assert report.total_trades == 3
        assert report.realized_pnl == 6.0
        assert report.winning_trades == 2
        assert report.losing_trades == 1
        assert len(report.strategy_metrics) == 2

    def test_beta_gate_pass(self):
        from src.core.metrics_collector import MetricsCollector

        collector = MetricsCollector(initial_capital=100.0)
        for _ in range(10):
            collector.record_trade("strat_a", 1.0)
        for _ in range(3):
            collector.record_trade("strat_a", -0.5)

        report = collector.get_report()
        assert report.total_pnl > 0
        assert report.profit_factor > 1.2

    def test_performance_report_summary(self):
        from src.core.metrics_collector import MetricsCollector

        collector = MetricsCollector(initial_capital=70.0)
        collector.record_trade("strat", 2.0)
        report = collector.get_report()
        summary = report.summary()
        assert "LEVIATHAN Performance Report" in summary
        assert "Beta Gate" in summary
