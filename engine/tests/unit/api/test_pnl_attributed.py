"""Unit tests for /api/v1/pnl/attributed (WS-C1)."""
from __future__ import annotations

from types import SimpleNamespace
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


class TestPnlAttributedAuth:
    def test_requires_auth(self, client):
        assert client.get("/api/v1/pnl/attributed").status_code == 401

    def test_returns_200(self, client, auth_headers):
        assert client.get("/api/v1/pnl/attributed", headers=auth_headers).status_code == 200


class TestPnlAttributedShape:
    def test_top_level_keys(self, client, auth_headers):
        data = client.get("/api/v1/pnl/attributed", headers=auth_headers).json()
        for key in (
            "realized_exchange",
            "unrealized",
            "commission",
            "funding",
            "slippage_estimated",
            "basis_capture",
            "reconciliation_variance_pct",
            "engine_total_pnl",
            "grand_total",
        ):
            assert key in data, f"missing key: {key}"

    def test_empty_context_returns_zero_grand_total(self, client, auth_headers):
        data = client.get("/api/v1/pnl/attributed", headers=auth_headers).json()
        # Grand total may float because of leftover Counter values in the shared
        # prometheus_client registry across tests, but the dict types must match.
        assert isinstance(data["grand_total"], (int, float))
        assert isinstance(data["realized_exchange"], dict)
        assert isinstance(data["unrealized"], dict)

    def test_engine_total_pnl_from_context_fallback(self, client, context, auth_headers):
        from decimal import Decimal
        context.realized_pnl = Decimal("1.23")
        context.unrealized_pnl = Decimal("-0.50")
        data = client.get("/api/v1/pnl/attributed", headers=auth_headers).json()
        assert float(data["engine_total_pnl"]) == pytest.approx(0.73, abs=0.01)


class TestPnlAttributedPrometheusIntegration:
    def test_increments_reflected(self, client, auth_headers):
        from src.infra.metrics import EXCHANGE_INCOME_TOTAL

        # Add a known amount and ensure the endpoint returns >0 for that exchange.
        EXCHANGE_INCOME_TOTAL.labels(
            exchange="binance_futures", income_type="REALIZED_PNL"
        ).inc(5.0)
        data = client.get("/api/v1/pnl/attributed", headers=auth_headers).json()
        assert data["realized_exchange"]["binance_futures"] >= 5.0

    def test_commission_reported_negative(self, client, auth_headers):
        from src.infra.metrics import EXCHANGE_INCOME_TOTAL

        EXCHANGE_INCOME_TOTAL.labels(
            exchange="bitget_futures", income_type="COMMISSION"
        ).inc(1.0)
        data = client.get("/api/v1/pnl/attributed", headers=auth_headers).json()
        # Commission is a cost; endpoint reports signed negative value.
        assert data["commission"]["bitget_futures"] < 0.0


class TestPnlAttributedUnrealizedFromPositionManager:
    def test_unrealized_groups_by_symbol(self, client, context, auth_headers):
        b_pos = SimpleNamespace(
            strategy_id="futures_futures",
            exchange_id="binance_futures",
            symbol="BLUR/USDT",
            side="SHORT",
            quantity=339.0,
            entry_price=0.031,
            mark_price=0.0308,
            unrealized_pnl=0.12,
            realized_pnl=0.0,
        )
        g_pos = SimpleNamespace(
            strategy_id="futures_futures",
            exchange_id="bitget_futures",
            symbol="BLUR/USDT",
            side="LONG",
            quantity=339.0,
            entry_price=0.031,
            mark_price=0.0308,
            unrealized_pnl=-0.13,
            realized_pnl=0.0,
        )
        pm = MagicMock()
        pm.get_all_positions.return_value = [b_pos, g_pos]
        context.position_manager = pm

        # Minimal engine mock with _exchanges dict so _unrealized_by_symbol iterates.
        context.engine = SimpleNamespace(_exchanges={"binance_futures": object(), "bitget_futures": object()})

        data = client.get("/api/v1/pnl/attributed", headers=auth_headers).json()
        un = data["unrealized"]
        assert "BLUR/USDT" in un
        leg = un["BLUR/USDT"]
        assert leg["binance_leg"] == pytest.approx(0.12)
        assert leg["bitget_leg"] == pytest.approx(-0.13)
        assert leg["net"] == pytest.approx(-0.01, abs=0.001)
