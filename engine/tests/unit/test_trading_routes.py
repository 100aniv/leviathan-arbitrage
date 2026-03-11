"""Unit tests for trading routes (US-111 + US-112).

Covers:
US-111 - GET /api/v1/trades/{trade_id}:
- Returns 200 + detail (reason, spread_bps, fee_usd, net_pnl) when trade found
- Returns 404 when trade_id not in history
- Returns 401/403 without JWT

US-112 - GET /api/v1/trades with filter params:
- strategy: filter by strategy_id
- exchange: filter by buy_exchange or sell_exchange
- symbol: filter by symbol
- from/to: ISO date range filter
- Combined multiple filters
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Any

import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import MagicMock

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


def _make_trade(
    trade_id: str = "t1",
    strategy_id: str = "cross_exchange",
    buy_exchange: str = "binance",
    sell_exchange: str = "coinone",
    symbol: str = "BTC/USDT",
    timestamp: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()
    return {
        "id": trade_id,
        "strategy_id": strategy_id,
        "buy_exchange": buy_exchange,
        "sell_exchange": sell_exchange,
        "symbol": symbol,
        "timestamp": timestamp,
        "pnl": 1.5,
        "reason": "spread exceeds min_edge",
        "spread_bps": 12.5,
        "fee_usd": 0.30,
        "net_pnl": 1.20,
        **extra,
    }


# ---------------------------------------------------------------------------
# US-111: GET /api/v1/trades/{trade_id}
# ---------------------------------------------------------------------------

class TestGetTradeDetail:
    @pytest.mark.asyncio
    async def test_get_trade_detail_found_returns_200(self):
        """Returns 200 when trade_id exists in trade_history."""
        trade = _make_trade(trade_id="abc123")
        ctx = _make_ctx(trade_history=deque([trade], maxlen=10_000))
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/trades/abc123", headers=_auth_header())
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_get_trade_detail_returns_required_fields(self):
        """Response contains reason, spread_bps, fee_usd, net_pnl fields."""
        trade = _make_trade(
            trade_id="abc123",
            reason="spread exceeds min_edge",
            spread_bps=12.5,
            fee_usd=0.30,
            net_pnl=1.20,
        )
        ctx = _make_ctx(trade_history=deque([trade], maxlen=10_000))
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/trades/abc123", headers=_auth_header())
        body = resp.json()
        assert body["id"] == "abc123"
        assert "reason" in body
        assert "spread_bps" in body
        assert "fee_usd" in body
        assert "net_pnl" in body

    @pytest.mark.asyncio
    async def test_get_trade_detail_values_correct(self):
        """Response values match the stored trade data."""
        trade = _make_trade(
            trade_id="xyz789",
            strategy_id="funding_rate",
            spread_bps=8.3,
            fee_usd=0.15,
            net_pnl=0.85,
            reason="funding rate capture",
        )
        ctx = _make_ctx(trade_history=deque([trade], maxlen=10_000))
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/trades/xyz789", headers=_auth_header())
        body = resp.json()
        assert body["spread_bps"] == pytest.approx(8.3, abs=0.01)
        assert body["fee_usd"] == pytest.approx(0.15, abs=0.001)
        assert body["net_pnl"] == pytest.approx(0.85, abs=0.001)
        assert body["reason"] == "funding rate capture"

    @pytest.mark.asyncio
    async def test_get_trade_detail_not_found_returns_404(self):
        """Returns 404 when trade_id does not exist in trade_history."""
        ctx = _make_ctx(trade_history=deque([], maxlen=10_000))
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/trades/nonexistent", headers=_auth_header())
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_trade_detail_auth_required(self):
        """Returns 401/403 without JWT token."""
        trade = _make_trade(trade_id="abc123")
        ctx = _make_ctx(trade_history=deque([trade], maxlen=10_000))
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/trades/abc123")
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_get_trade_detail_from_multiple_trades(self):
        """Finds correct trade by ID among multiple trades."""
        trades = deque([
            _make_trade(trade_id="t1", symbol="BTC/USDT"),
            _make_trade(trade_id="t2", symbol="ETH/USDT"),
            _make_trade(trade_id="t3", symbol="XRP/USDT"),
        ], maxlen=10_000)
        ctx = _make_ctx(trade_history=trades)
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/trades/t2", headers=_auth_header())
        assert resp.status_code == 200
        assert resp.json()["symbol"] == "ETH/USDT"


# ---------------------------------------------------------------------------
# US-112: GET /api/v1/trades with filter parameters
# ---------------------------------------------------------------------------

class TestListTradesFilters:
    @pytest.mark.asyncio
    async def test_list_trades_filter_by_strategy(self):
        """strategy filter returns only trades matching strategy_id."""
        trades = deque([
            _make_trade(trade_id="t1", strategy_id="cross_exchange"),
            _make_trade(trade_id="t2", strategy_id="funding_rate"),
            _make_trade(trade_id="t3", strategy_id="cross_exchange"),
        ], maxlen=10_000)
        ctx = _make_ctx(trade_history=trades)
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(
                "/api/v1/trades",
                params={"strategy": "cross_exchange"},
                headers=_auth_header(),
            )
        assert resp.status_code == 200
        result = resp.json()
        assert all(t["strategy_id"] == "cross_exchange" for t in result)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_list_trades_filter_by_exchange_buy_side(self):
        """exchange filter matches trades where buy_exchange or sell_exchange equals the value."""
        trades = deque([
            _make_trade(trade_id="t1", buy_exchange="binance", sell_exchange="coinone"),
            _make_trade(trade_id="t2", buy_exchange="bybit", sell_exchange="upbit"),
            _make_trade(trade_id="t3", buy_exchange="okx", sell_exchange="binance"),
        ], maxlen=10_000)
        ctx = _make_ctx(trade_history=trades)
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(
                "/api/v1/trades",
                params={"exchange": "binance"},
                headers=_auth_header(),
            )
        assert resp.status_code == 200
        result = resp.json()
        # t1 (buy=binance) and t3 (sell=binance) should be included
        ids = {t["id"] for t in result}
        assert "t1" in ids
        assert "t3" in ids
        assert "t2" not in ids

    @pytest.mark.asyncio
    async def test_list_trades_filter_by_symbol(self):
        """symbol filter returns only trades matching the symbol."""
        trades = deque([
            _make_trade(trade_id="t1", symbol="BTC/USDT"),
            _make_trade(trade_id="t2", symbol="ETH/USDT"),
            _make_trade(trade_id="t3", symbol="BTC/USDT"),
        ], maxlen=10_000)
        ctx = _make_ctx(trade_history=trades)
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(
                "/api/v1/trades",
                params={"symbol": "BTC/USDT"},
                headers=_auth_header(),
            )
        assert resp.status_code == 200
        result = resp.json()
        assert all(t["symbol"] == "BTC/USDT" for t in result)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_list_trades_filter_date_range_from(self):
        """from filter excludes trades before the given date."""
        now = datetime.now(timezone.utc)
        trades = deque([
            _make_trade(trade_id="old", timestamp=(now - timedelta(days=2)).isoformat()),
            _make_trade(trade_id="new", timestamp=now.isoformat()),
        ], maxlen=10_000)
        ctx = _make_ctx(trade_history=trades)
        app = create_app(ctx)
        from_date = (now - timedelta(hours=1)).strftime("%Y-%m-%d")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(
                "/api/v1/trades",
                params={"from": from_date},
                headers=_auth_header(),
            )
        assert resp.status_code == 200
        result = resp.json()
        ids = {t["id"] for t in result}
        assert "new" in ids
        assert "old" not in ids

    @pytest.mark.asyncio
    async def test_list_trades_filter_date_range_to(self):
        """to filter excludes trades after the given date."""
        now = datetime.now(timezone.utc)
        trades = deque([
            _make_trade(trade_id="old", timestamp=(now - timedelta(days=2)).isoformat()),
            _make_trade(trade_id="new", timestamp=now.isoformat()),
        ], maxlen=10_000)
        ctx = _make_ctx(trade_history=trades)
        app = create_app(ctx)
        to_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(
                "/api/v1/trades",
                params={"to": to_date},
                headers=_auth_header(),
            )
        assert resp.status_code == 200
        result = resp.json()
        ids = {t["id"] for t in result}
        assert "old" in ids
        assert "new" not in ids

    @pytest.mark.asyncio
    async def test_list_trades_combined_filters(self):
        """Combined strategy + symbol filters are applied together (AND logic)."""
        trades = deque([
            _make_trade(trade_id="t1", strategy_id="cross_exchange", symbol="BTC/USDT"),
            _make_trade(trade_id="t2", strategy_id="cross_exchange", symbol="ETH/USDT"),
            _make_trade(trade_id="t3", strategy_id="funding_rate", symbol="BTC/USDT"),
        ], maxlen=10_000)
        ctx = _make_ctx(trade_history=trades)
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(
                "/api/v1/trades",
                params={"strategy": "cross_exchange", "symbol": "BTC/USDT"},
                headers=_auth_header(),
            )
        assert resp.status_code == 200
        result = resp.json()
        assert len(result) == 1
        assert result[0]["id"] == "t1"

    @pytest.mark.asyncio
    async def test_list_trades_no_filters_returns_all(self):
        """Without filters, all trades in history are returned (up to limit)."""
        trades = deque([_make_trade(trade_id=f"t{i}") for i in range(5)], maxlen=10_000)
        ctx = _make_ctx(trade_history=trades)
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/trades", headers=_auth_header())
        assert resp.status_code == 200
        assert len(resp.json()) == 5

    @pytest.mark.asyncio
    async def test_list_trades_filter_auth_required(self):
        """Returns 401/403 without JWT token."""
        ctx = _make_ctx(trade_history=deque([], maxlen=10_000))
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/trades", params={"symbol": "BTC/USDT"})
        assert resp.status_code in (401, 403)
