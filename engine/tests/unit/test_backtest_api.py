"""Unit tests for backtest API routes — US-361/368~371.

Covers:
- GET /api/backtest/result: returns 404 when no result available
- POST /api/backtest/start: triggers backtest run
- POST /api/backtest/start: returns 409 when backtest already running (_running=True)
- POST /api/backtest/start: returns 503 when backtest_mode not initialized
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.auth import create_token
from src.api.server import EngineContext, create_app


def _auth_header() -> dict:
    return {"Authorization": f"Bearer {create_token('test_user')}"}


def _make_context(**kwargs) -> EngineContext:
    ctx = EngineContext(
        running=True,
        kill_switch_active=False,
        environment="test",
        execution_mode="backtest",
        strategies={},
        strategy_manager=None,
    )
    ctx.engine = kwargs.pop("engine", MagicMock())
    for k, v in kwargs.items():
        setattr(ctx, k, v)
    return ctx


# ---------------------------------------------------------------------------
# GET /api/backtest/result
# ---------------------------------------------------------------------------

class TestBacktestResult:
    @pytest.mark.asyncio
    async def test_get_result_returns_404_when_no_result(self, tmp_path):
        """GET /api/backtest/result returns 404 when no backtest result available."""
        ctx = _make_context()
        # no backtest_result attribute, no file
        app = create_app(ctx)

        with patch("src.api.routes.backtest._RESULTS_FILE", tmp_path / "nonexistent.json"):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get("/api/backtest/result", headers=_auth_header())

        assert resp.status_code == 404
        assert resp.json()["error"] == "no_backtest_result"

    @pytest.mark.asyncio
    async def test_get_result_returns_data_from_context(self):
        """GET /api/backtest/result returns result from engine context."""
        ctx = _make_context()
        mock_result = MagicMock()
        mock_result.snapshots_replayed = 100
        mock_result.signals_generated = 50
        mock_result.trades_executed = 10
        mock_result.total_pnl = 5.0
        mock_result.sharpe_ratio = 1.5
        mock_result.max_drawdown_pct = 0.02
        mock_result.win_rate = 0.6
        mock_result.profit_factor = 1.8
        mock_result.duration_s = 120.0
        mock_result.by_strategy = {}
        mock_result.error = ""
        mock_result.strategy_ids = []
        mock_result.exchange_ids = []
        mock_result.seed_capital = 1000.0
        mock_result.period_label = "4H"
        mock_result.by_exchange = {}
        ctx.backtest_result = mock_result
        app = create_app(ctx)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/backtest/result", headers=_auth_header())

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_pnl"] == 5.0
        assert data["sharpe_ratio"] == 1.5


# ---------------------------------------------------------------------------
# POST /api/backtest/start — race condition guard
# ---------------------------------------------------------------------------

class TestBacktestStart:
    @pytest.mark.asyncio
    async def test_start_returns_503_when_backtest_mode_not_initialized(self):
        """POST /api/backtest/start returns 503 when backtest_mode is None."""
        ctx = _make_context()
        # backtest_mode not set
        app = create_app(ctx)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/backtest/start",
                json={"seed_capital": 1000.0},
                headers=_auth_header(),
            )

        assert resp.status_code == 503
        assert resp.json()["error"] == "backtest_mode_not_initialized"

    @pytest.mark.asyncio
    async def test_concurrent_start_returns_409(self):
        """POST /api/backtest/start returns 409 when backtest already running (_running=True)."""
        ctx = _make_context()
        mock_backtest = MagicMock()
        mock_backtest._running = True  # simulate already running
        mock_backtest.run = AsyncMock()
        ctx.backtest_mode = mock_backtest
        app = create_app(ctx)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/backtest/start",
                json={"seed_capital": 1000.0},
                headers=_auth_header(),
            )

        assert resp.status_code == 409
        assert resp.json()["error"] == "backtest_already_running"
        # run() must NOT have been called
        mock_backtest.run.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_triggers_backtest_when_not_running(self):
        """POST /api/backtest/start returns 200 and schedules run when _running=False."""
        ctx = _make_context()
        mock_backtest = MagicMock()
        mock_backtest._running = False
        mock_backtest.run = AsyncMock(return_value=None)
        ctx.backtest_mode = mock_backtest
        app = create_app(ctx)

        with patch("asyncio.create_task"):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/api/backtest/start",
                    json={"seed_capital": 500.0, "strategy_ids": ["triangular"]},
                    headers=_auth_header(),
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert data["params"]["seed_capital"] == 500.0
