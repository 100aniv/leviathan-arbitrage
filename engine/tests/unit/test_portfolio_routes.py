"""Unit tests for portfolio routes (US-108).

Covers:
- GET /api/v1/portfolio/equity-curve -> 200 + {"curve": [...]}
- GET /api/v1/portfolio/equity-curve with shadow_mode -> includes shadow PnL data
- GET /api/v1/portfolio/metrics -> 200 + sharpe_ratio, max_drawdown_pct, calmar_ratio, win_rate
- GET /api/v1/portfolio/metrics with shadow_mode -> reflects snapshot data
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock
from httpx import AsyncClient, ASGITransport

from src.api.server import EngineContext, create_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(**kwargs) -> EngineContext:
    ctx = EngineContext(
        running=True,
        kill_switch_active=False,
        environment="test",
        execution_mode="shadow",
        strategies={},
        strategy_manager=None,
    )
    for k, v in kwargs.items():
        setattr(ctx, k, v)
    return ctx


def _auth_header() -> dict[str, str]:
    from src.api.auth import create_token
    return {"Authorization": f"Bearer {create_token('testuser')}"}


def _make_shadow_mock(total_pnl: float = 21.10, win_rate: float = 1.0,
                      total_trades: int = 3110, max_drawdown: float = 0.0007) -> MagicMock:
    shadow = MagicMock()
    shadow.get_snapshot.return_value = {
        "total_pnl": total_pnl,
        "win_rate": win_rate,
        "total_trades": total_trades,
        "max_drawdown": max_drawdown,
    }
    return shadow


# ---------------------------------------------------------------------------
# GET /api/v1/portfolio/equity-curve
# ---------------------------------------------------------------------------

class TestGetEquityCurve:
    @pytest.mark.asyncio
    async def test_get_equity_curve_returns_200(self):
        """Equity curve endpoint returns 200 OK."""
        app = create_app(_make_ctx())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/portfolio/equity-curve", headers=_auth_header())
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_get_equity_curve_returns_curve_list(self):
        """Response body contains a 'curve' list with at least one data point."""
        app = create_app(_make_ctx())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/portfolio/equity-curve", headers=_auth_header())
        body = resp.json()
        assert "curve" in body
        assert isinstance(body["curve"], list)
        assert len(body["curve"]) >= 1

    @pytest.mark.asyncio
    async def test_get_equity_curve_point_has_required_fields(self):
        """Each curve data point contains date, equity, pnl, btc_benchmark."""
        app = create_app(_make_ctx())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/portfolio/equity-curve", headers=_auth_header())
        point = resp.json()["curve"][0]
        assert "date" in point
        assert "equity" in point
        assert "pnl" in point
        assert "btc_benchmark" in point

    @pytest.mark.asyncio
    async def test_get_equity_curve_with_shadow(self):
        """With shadow_mode, equity curve reflects shadow snapshot PnL."""
        shadow = _make_shadow_mock(total_pnl=21.10)
        ctx = _make_ctx(shadow_mode=shadow)
        app = create_app(ctx)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/portfolio/equity-curve", headers=_auth_header())
        assert resp.status_code == 200
        curve = resp.json()["curve"]
        assert len(curve) >= 1
        # equity = base(100000) + shadow pnl
        assert curve[0]["equity"] == pytest.approx(100021.10, abs=0.01)
        assert curve[0]["pnl"] == pytest.approx(21.10, abs=0.001)

    @pytest.mark.asyncio
    async def test_get_equity_curve_requires_auth(self):
        """Equity curve endpoint returns 401/403 without token."""
        app = create_app(_make_ctx())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/portfolio/equity-curve")
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_get_equity_curve_fallback_when_no_shadow(self):
        """Without shadow_mode, equity curve uses realized+unrealized PnL as fallback."""
        from decimal import Decimal
        ctx = _make_ctx(realized_pnl=Decimal("5.0"), unrealized_pnl=Decimal("2.5"))
        app = create_app(ctx)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/portfolio/equity-curve", headers=_auth_header())
        curve = resp.json()["curve"]
        assert curve[0]["pnl"] == pytest.approx(7.5, abs=0.001)
        assert curve[0]["equity"] == pytest.approx(100007.5, abs=0.01)


# ---------------------------------------------------------------------------
# GET /api/v1/portfolio/metrics
# ---------------------------------------------------------------------------

class TestGetPortfolioMetrics:
    @pytest.mark.asyncio
    async def test_get_portfolio_metrics_returns_200(self):
        """Portfolio metrics endpoint returns 200 OK."""
        app = create_app(_make_ctx())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/portfolio/metrics", headers=_auth_header())
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_get_portfolio_metrics_has_required_fields(self):
        """Response contains sharpe_ratio, max_drawdown_pct, calmar_ratio, win_rate."""
        app = create_app(_make_ctx())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/portfolio/metrics", headers=_auth_header())
        body = resp.json()
        assert "sharpe_ratio" in body
        assert "max_drawdown_pct" in body
        assert "calmar_ratio" in body
        assert "win_rate" in body
        assert "total_trades" in body
        assert "total_pnl" in body

    @pytest.mark.asyncio
    async def test_get_portfolio_metrics_defaults_to_zero(self):
        """Without shadow_mode, all metrics default to zero."""
        app = create_app(_make_ctx())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/portfolio/metrics", headers=_auth_header())
        body = resp.json()
        assert body["sharpe_ratio"] == 0.0
        assert body["max_drawdown_pct"] == 0.0
        assert body["calmar_ratio"] == 0.0
        assert body["win_rate"] == 0.0
        assert body["total_trades"] == 0

    @pytest.mark.asyncio
    async def test_get_portfolio_metrics_with_shadow(self):
        """With shadow_mode, metrics reflect snapshot win_rate and trade count."""
        shadow = _make_shadow_mock(total_pnl=21.10, win_rate=1.0, total_trades=3110)
        ctx = _make_ctx(shadow_mode=shadow)
        app = create_app(ctx)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/portfolio/metrics", headers=_auth_header())
        body = resp.json()
        assert body["win_rate"] == pytest.approx(1.0, abs=0.001)
        assert body["total_trades"] == 3110
        assert body["total_pnl"] == pytest.approx(21.10, abs=0.001)

    @pytest.mark.asyncio
    async def test_get_portfolio_metrics_max_drawdown_converted_to_pct(self):
        """max_drawdown from snapshot (fraction) is converted to percentage."""
        # max_drawdown=0.0007 -> max_drawdown_pct = 0.0007 * 100 = 0.07
        shadow = _make_shadow_mock(max_drawdown=0.0007)
        ctx = _make_ctx(shadow_mode=shadow)
        app = create_app(ctx)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/portfolio/metrics", headers=_auth_header())
        body = resp.json()
        assert body["max_drawdown_pct"] == pytest.approx(0.07, abs=0.001)

    @pytest.mark.asyncio
    async def test_get_portfolio_metrics_requires_auth(self):
        """Portfolio metrics endpoint returns 401/403 without token."""
        app = create_app(_make_ctx())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/portfolio/metrics")
        assert resp.status_code in (401, 403)
