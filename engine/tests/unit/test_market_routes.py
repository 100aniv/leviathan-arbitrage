"""Unit tests for market data routes (US-142).

Covers:
US-142 - GET /api/v1/symbols:
- Returns 200 with 'symbols' list and 'count' integer
- Returns symbols from runtime_settings when engine not available
- Returns symbols from env var TRADING_SYMBOLS as fallback
- count field equals len(symbols)
- Returns empty list when no symbol source configured
- Returns symbols from engine.collector_manager.get_active_symbols()
- Returns 401/403 without JWT

US-142 - GET /api/v1/spreads:
- Returns 200 with list
- Returns empty list when no engine attached
- Returns spread data from engine.signal_generator.get_spread_snapshot()
- Each spread item has symbol, exchange_a, exchange_b, spread_bps, timestamp
- Returns multiple spread items
- Falls back to engine.price_hub.get_snapshot() when SignalGenerator has no data
- spread_bps is positive when price difference exists
- Returns 401/403 without JWT
"""
from __future__ import annotations

import os
import pytest
from unittest.mock import MagicMock, patch
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
    # runtime_settings is always injected by main.py; provide empty default for tests
    ctx.runtime_settings = {}
    for k, v in kwargs.items():
        setattr(ctx, k, v)
    return ctx


def _auth_header() -> dict[str, str]:
    from src.api.auth import create_token
    return {"Authorization": f"Bearer {create_token('testuser')}"}


# ---------------------------------------------------------------------------
# GET /api/v1/symbols
# ---------------------------------------------------------------------------

class TestGetSymbols:
    @pytest.mark.asyncio
    async def test_symbols_returns_200_with_auth(self):
        """Returns 200 when called with valid JWT."""
        ctx = _make_ctx()
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/symbols", headers=_auth_header())
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_symbols_response_has_symbols_and_count(self):
        """Response contains 'symbols' list and 'count' integer."""
        ctx = _make_ctx()
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/symbols", headers=_auth_header())
        assert resp.status_code == 200
        body = resp.json()
        assert "symbols" in body
        assert "count" in body
        assert isinstance(body["symbols"], list)
        assert isinstance(body["count"], int)

    @pytest.mark.asyncio
    async def test_symbols_from_runtime_settings(self):
        """Returns symbols from ctx.runtime_settings when engine is not available."""
        ctx = _make_ctx(runtime_settings={"trading_symbols": ["BTC/USDT", "ETH/USDT", "XRP/USDT"]})
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/symbols", headers=_auth_header())
        body = resp.json()
        assert body["symbols"] == ["BTC/USDT", "ETH/USDT", "XRP/USDT"]
        assert body["count"] == 3

    @pytest.mark.asyncio
    async def test_symbols_count_equals_list_length(self):
        """count field always equals len(symbols)."""
        symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT"]
        ctx = _make_ctx(runtime_settings={"trading_symbols": symbols})
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/symbols", headers=_auth_header())
        body = resp.json()
        assert body["count"] == len(body["symbols"])
        assert body["count"] == 5

    @pytest.mark.asyncio
    async def test_symbols_empty_when_no_source_configured(self):
        """Returns empty list when runtime_settings is empty and TRADING_SYMBOLS not set."""
        ctx = _make_ctx(runtime_settings={})
        app = create_app(ctx)
        env_backup = os.environ.pop("TRADING_SYMBOLS", None)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/v1/symbols", headers=_auth_header())
            body = resp.json()
            assert body["symbols"] == []
            assert body["count"] == 0
        finally:
            if env_backup is not None:
                os.environ["TRADING_SYMBOLS"] = env_backup

    @pytest.mark.asyncio
    async def test_symbols_from_trading_symbols_env_var(self):
        """Returns symbols from TRADING_SYMBOLS env var when runtime_settings has no symbols."""
        ctx = _make_ctx(runtime_settings={})
        app = create_app(ctx)
        with patch.dict(os.environ, {"TRADING_SYMBOLS": "BTC/USDT,ETH/USDT,SOL/USDT"}):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/v1/symbols", headers=_auth_header())
        body = resp.json()
        assert "BTC/USDT" in body["symbols"]
        assert "ETH/USDT" in body["symbols"]
        assert "SOL/USDT" in body["symbols"]
        assert body["count"] == 3

    @pytest.mark.asyncio
    async def test_symbols_from_collector_manager_takes_priority(self):
        """Returns symbols from engine.collector_manager.get_active_symbols() over runtime_settings."""
        mock_cm = MagicMock()
        mock_cm.get_active_symbols.return_value = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        mock_engine = MagicMock()
        mock_engine.collector_manager = mock_cm
        ctx = _make_ctx(
            engine=mock_engine,
            runtime_settings={"trading_symbols": ["SHOULD_NOT_APPEAR"]},
        )
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/symbols", headers=_auth_header())
        body = resp.json()
        assert "BTC/USDT" in body["symbols"]
        assert body["count"] == 3

    @pytest.mark.asyncio
    async def test_symbols_requires_auth(self):
        """Returns 401/403 without JWT token."""
        ctx = _make_ctx()
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/symbols")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# GET /api/v1/spreads
# ---------------------------------------------------------------------------

class TestGetSpreads:
    @pytest.mark.asyncio
    async def test_spreads_returns_200_with_auth(self):
        """Returns 200 when called with valid JWT."""
        ctx = _make_ctx()
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/spreads", headers=_auth_header())
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_spreads_response_is_list(self):
        """Response is a JSON list."""
        ctx = _make_ctx()
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/spreads", headers=_auth_header())
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @pytest.mark.asyncio
    async def test_spreads_empty_when_engine_not_attached(self):
        """Returns empty list (not 500) when no engine is attached to context."""
        ctx = _make_ctx()  # No engine attribute
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/spreads", headers=_auth_header())
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_spreads_from_signal_generator_snapshot(self):
        """Returns spread data from engine.signal_generator.get_spread_snapshot()."""
        mock_snapshot = {
            "BTC/USDT:binance-coinone": {
                "symbol": "BTC/USDT",
                "exchange_a": "binance",
                "exchange_b": "coinone",
                "spread_bps": 12.5,
                "timestamp": "2026-03-14T10:00:00Z",
            }
        }
        mock_sg = MagicMock()
        mock_sg.get_spread_snapshot.return_value = mock_snapshot
        mock_engine = MagicMock()
        mock_engine.signal_generator = mock_sg
        ctx = _make_ctx(engine=mock_engine)
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/spreads", headers=_auth_header())
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        item = body[0]
        assert item["symbol"] == "BTC/USDT"
        assert item["exchange_a"] == "binance"
        assert item["exchange_b"] == "coinone"
        assert item["spread_bps"] == pytest.approx(12.5, abs=0.01)

    @pytest.mark.asyncio
    async def test_spreads_item_has_required_fields(self):
        """Each spread item contains symbol, exchange_a, exchange_b, spread_bps, timestamp."""
        mock_snapshot = {
            "ETH/USDT:bybit-upbit": {
                "symbol": "ETH/USDT",
                "exchange_a": "bybit",
                "exchange_b": "upbit",
                "spread_bps": 8.3,
                "timestamp": "2026-03-14T10:01:00Z",
            }
        }
        mock_sg = MagicMock()
        mock_sg.get_spread_snapshot.return_value = mock_snapshot
        mock_engine = MagicMock()
        mock_engine.signal_generator = mock_sg
        ctx = _make_ctx(engine=mock_engine)
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/spreads", headers=_auth_header())
        item = resp.json()[0]
        for field in ("symbol", "exchange_a", "exchange_b", "spread_bps", "timestamp"):
            assert field in item, f"Missing required field: {field}"

    @pytest.mark.asyncio
    async def test_spreads_returns_multiple_entries(self):
        """Returns all spread entries from SignalGenerator snapshot."""
        mock_snapshot = {
            "BTC/USDT:binance-coinone": {
                "symbol": "BTC/USDT", "exchange_a": "binance", "exchange_b": "coinone",
                "spread_bps": 12.5, "timestamp": "",
            },
            "ETH/USDT:bybit-upbit": {
                "symbol": "ETH/USDT", "exchange_a": "bybit", "exchange_b": "upbit",
                "spread_bps": 7.2, "timestamp": "",
            },
            "XRP/USDT:okx-bithumb": {
                "symbol": "XRP/USDT", "exchange_a": "okx", "exchange_b": "bithumb",
                "spread_bps": 15.1, "timestamp": "",
            },
        }
        mock_sg = MagicMock()
        mock_sg.get_spread_snapshot.return_value = mock_snapshot
        mock_engine = MagicMock()
        mock_engine.signal_generator = mock_sg
        ctx = _make_ctx(engine=mock_engine)
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/spreads", headers=_auth_header())
        body = resp.json()
        assert len(body) == 3
        symbols = {item["symbol"] for item in body}
        assert "BTC/USDT" in symbols
        assert "ETH/USDT" in symbols
        assert "XRP/USDT" in symbols

    @pytest.mark.asyncio
    async def test_spreads_falls_back_to_price_hub(self):
        """Falls back to engine.price_hub.get_snapshot() when SignalGenerator has no data."""
        mock_sg = MagicMock()
        mock_sg.get_spread_snapshot.return_value = {}  # Empty → triggers PriceHub fallback

        mock_price_hub = MagicMock()
        mock_price_hub.get_snapshot.return_value = {
            "BTC/USDT": {
                "binance": 65000.0,
                "coinone": 65050.0,
            }
        }
        mock_engine = MagicMock()
        mock_engine.signal_generator = mock_sg
        mock_engine.price_hub = mock_price_hub
        ctx = _make_ctx(engine=mock_engine)
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/spreads", headers=_auth_header())
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) >= 1
        item = body[0]
        assert item["symbol"] == "BTC/USDT"
        assert item["spread_bps"] > 0  # Price diff → positive spread

    @pytest.mark.asyncio
    async def test_spreads_spread_bps_positive_when_price_diff_exists(self):
        """spread_bps is correctly computed as positive when exchange prices differ."""
        # p_a=65000, p_b=65100 → mid=65050 → spread = 100/65050*10000 ≈ 15.37 bps
        mock_sg = MagicMock()
        mock_sg.get_spread_snapshot.return_value = {}
        mock_price_hub = MagicMock()
        mock_price_hub.get_snapshot.return_value = {
            "ETH/USDT": {"binance": 3500.0, "upbit": 3510.0},
        }
        mock_engine = MagicMock()
        mock_engine.signal_generator = mock_sg
        mock_engine.price_hub = mock_price_hub
        ctx = _make_ctx(engine=mock_engine)
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/spreads", headers=_auth_header())
        item = resp.json()[0]
        assert item["spread_bps"] > 0

    @pytest.mark.asyncio
    async def test_spreads_requires_auth(self):
        """Returns 401/403 without JWT token."""
        ctx = _make_ctx()
        app = create_app(ctx)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/spreads")
        assert resp.status_code in (401, 403)
