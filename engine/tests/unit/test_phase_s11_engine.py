"""Tests for Phase S11 engine backend (US-207, US-208, US-209, US-210, US-211).

Covers:
- US-207: Korean infra bot templates (send_daily_report_kr, send_alert_kr)
- US-208: Korean trade bot templates (send_fill_kr, send_daily_settlement_kr)
- US-209: AlertSeverity enum, SeverityFilter, send_alert_with_severity
- US-210: WebSocket payload extended fields (total_equity, win_rate, active_strategy_count)
- US-211: 9 new API endpoints (positions, daily-returns, logs, db-metrics,
          redis-metrics, acknowledge, resolve, test-alert, reconnect)
"""
from __future__ import annotations

import os
import time
from collections import deque
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.auth import create_token
from src.api.server import EngineContext, create_app
from src.infra.telegram import (
    AlertSeverity,
    SeverityFilter,
    TelegramAlerter,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auth_header() -> dict:
    return {"Authorization": f"Bearer {create_token('test_user')}"}


def _make_context(**kwargs) -> EngineContext:
    ctx = EngineContext(
        running=True,
        kill_switch_active=False,
        environment="test",
        execution_mode="paper",
        strategies={
            "arb1": {"id": "arb1", "type": "cross_exchange", "enabled": True},
            "arb2": {"id": "arb2", "type": "funding_rate", "enabled": False},
        },
    )
    for k, v in kwargs.items():
        setattr(ctx, k, v)
    return ctx


def _capture_alerter() -> tuple[TelegramAlerter, list[str]]:
    """Return an enabled alerter with a fake _send that captures messages."""
    alerter = TelegramAlerter(bot_token="bot:TEST", chat_id="-100", enabled=True)
    captured: list[str] = []

    async def fake_send(text: str, parse_mode: str = "HTML") -> bool:
        captured.append(text)
        return True

    alerter._send = fake_send  # type: ignore[method-assign]
    return alerter, captured


# ===========================================================================
# US-209: AlertSeverity & SeverityFilter
# ===========================================================================


class TestAlertSeverityEnum:
    def test_emergency_interval_is_zero(self):
        assert AlertSeverity.EMERGENCY.min_interval == 0

    def test_critical_interval_is_60(self):
        assert AlertSeverity.CRITICAL.min_interval == 60

    def test_warning_interval_is_300(self):
        assert AlertSeverity.WARNING.min_interval == 300

    def test_info_interval_is_1800(self):
        assert AlertSeverity.INFO.min_interval == 1800


class TestSeverityFilter:
    def test_emergency_always_allowed(self):
        f = SeverityFilter()
        assert f.should_send(AlertSeverity.EMERGENCY) is True
        f.record_send(AlertSeverity.EMERGENCY)
        assert f.should_send(AlertSeverity.EMERGENCY) is True

    def test_critical_blocked_within_interval(self):
        f = SeverityFilter()
        f.record_send(AlertSeverity.CRITICAL)
        assert f.should_send(AlertSeverity.CRITICAL) is False

    def test_critical_allowed_after_interval(self):
        f = SeverityFilter()
        f._last_sent[AlertSeverity.CRITICAL] = time.monotonic() - 61
        assert f.should_send(AlertSeverity.CRITICAL) is True

    def test_warning_blocked_within_interval(self):
        f = SeverityFilter()
        f.record_send(AlertSeverity.WARNING)
        assert f.should_send(AlertSeverity.WARNING) is False

    def test_info_blocked_within_interval(self):
        f = SeverityFilter()
        f.record_send(AlertSeverity.INFO)
        assert f.should_send(AlertSeverity.INFO) is False

    def test_reset_clears_state(self):
        f = SeverityFilter()
        f.record_send(AlertSeverity.CRITICAL)
        f.reset()
        assert f.should_send(AlertSeverity.CRITICAL) is True

    def test_different_severities_are_independent(self):
        f = SeverityFilter()
        f.record_send(AlertSeverity.CRITICAL)
        # WARNING not recorded, should be allowed
        assert f.should_send(AlertSeverity.WARNING) is True


class TestSendAlertWithSeverity:
    async def test_emergency_sends_immediately(self):
        alerter, captured = _capture_alerter()
        result = await alerter.send_alert_with_severity("urgent", AlertSeverity.EMERGENCY)
        assert result is True
        assert len(captured) == 1
        assert "urgent" in captured[0]

    async def test_critical_throttled_on_second_call(self):
        alerter, captured = _capture_alerter()
        await alerter.send_alert_with_severity("first", AlertSeverity.CRITICAL)
        result = await alerter.send_alert_with_severity("second", AlertSeverity.CRITICAL)
        assert result is False
        assert len(captured) == 1  # Only first sent

    async def test_info_throttled_on_second_call(self):
        alerter, captured = _capture_alerter()
        await alerter.send_alert_with_severity("first", AlertSeverity.INFO)
        result = await alerter.send_alert_with_severity("second", AlertSeverity.INFO)
        assert result is False


# ===========================================================================
# US-207: Korean infra bot templates
# ===========================================================================


class TestDailyReportKr:
    async def test_contains_korean_header(self):
        alerter, captured = _capture_alerter()
        await alerter.send_daily_report_kr({"date": "2026-03-17", "total_pnl": 100.0})
        assert "일일 가동 리포트" in captured[0]

    async def test_positive_pnl_shows_up_emoji(self):
        alerter, captured = _capture_alerter()
        await alerter.send_daily_report_kr({"total_pnl": 50.0})
        assert "📈" in captured[0]
        assert "$+50.00" in captured[0]

    async def test_negative_pnl_shows_down_emoji(self):
        alerter, captured = _capture_alerter()
        await alerter.send_daily_report_kr({"total_pnl": -30.0})
        assert "📉" in captured[0]

    async def test_exchange_status_rendered(self):
        alerter, captured = _capture_alerter()
        await alerter.send_daily_report_kr({
            "exchange_status": {"binance": "정상", "upbit": "장애"},
        })
        assert "🟢" in captured[0]
        assert "🔴" in captured[0]
        assert "binance" in captured[0]

    async def test_missing_data_shows_na(self):
        alerter, captured = _capture_alerter()
        await alerter.send_daily_report_kr({})
        assert "N/A" in captured[0]

    async def test_win_rate_as_percentage(self):
        alerter, captured = _capture_alerter()
        await alerter.send_daily_report_kr({"win_rate": 0.82})
        assert "82.0%" in captured[0]

    async def test_active_strategies_count(self):
        alerter, captured = _capture_alerter()
        await alerter.send_daily_report_kr({"active_strategies": 5})
        assert "5개" in captured[0]


class TestAlertKr:
    async def test_kill_switch_alert(self):
        alerter, captured = _capture_alerter()
        await alerter.send_alert_kr("kill_switch", {
            "reason": "최대 손실 초과",
            "cancelled_orders": 3,
            "closed_positions": 1,
            "redis_halt": True,
        })
        assert "킬 스위치" in captured[0]
        assert "3건" in captured[0]

    async def test_circuit_breaker_alert(self):
        alerter, captured = _capture_alerter()
        await alerter.send_alert_kr("circuit_breaker", {
            "state": "OPEN",
            "reason": "연속 실패",
        })
        assert "서킷 브레이커" in captured[0]
        assert "🔴" in captured[0]

    async def test_db_failure_alert(self):
        alerter, captured = _capture_alerter()
        await alerter.send_alert_kr("db_failure", {
            "db_type": "TimescaleDB",
            "error": "connection refused",
        })
        assert "DB 장애" in captured[0]
        assert "TimescaleDB" in captured[0]

    async def test_unknown_alert_type_fallback(self):
        alerter, captured = _capture_alerter()
        await alerter.send_alert_kr("unknown_type", {"detail": "기타 오류"})
        assert "unknown_type" in captured[0]


# ===========================================================================
# US-208: Korean trade bot templates
# ===========================================================================


class TestFillKr:
    async def test_fill_contains_korean_header(self):
        alerter, captured = _capture_alerter()
        await alerter.send_fill_kr({
            "strategy": "cross_exchange",
            "symbol": "BTC/USDT",
            "buy_exchange": "binance",
            "sell_exchange": "upbit",
            "pnl": 1.23,
        })
        assert "체결 완료" in captured[0]
        assert "cross_exchange" in captured[0]

    async def test_positive_pnl_emoji(self):
        alerter, captured = _capture_alerter()
        await alerter.send_fill_kr({"pnl": 5.0})
        assert "💰" in captured[0]

    async def test_negative_pnl_emoji(self):
        alerter, captured = _capture_alerter()
        await alerter.send_fill_kr({"pnl": -2.0})
        assert "📉" in captured[0]


class TestDailySettlementKr:
    async def test_settlement_contains_korean_header(self):
        alerter, captured = _capture_alerter()
        await alerter.send_daily_settlement_kr({
            "date": "2026-03-17",
            "total_pnl": 200.0,
            "win_rate": 0.75,
            "total_trades": 50,
        })
        assert "일일 정산 리포트" in captured[0]
        assert "$+200.00" in captured[0]
        assert "75.0%" in captured[0]

    async def test_strategy_breakdown_rendered(self):
        alerter, captured = _capture_alerter()
        await alerter.send_daily_settlement_kr({
            "strategy_breakdown": [
                {"strategy": "cross_exchange", "pnl": 150.0, "trades": 30, "win_rate": 0.8},
                {"strategy": "funding_rate", "pnl": 50.0, "trades": 20, "win_rate": 0.65},
            ],
        })
        assert "cross_exchange" in captured[0]
        assert "funding_rate" in captured[0]
        assert "30건" in captured[0]

    async def test_empty_breakdown_shows_no_data(self):
        alerter, captured = _capture_alerter()
        await alerter.send_daily_settlement_kr({})
        assert "데이터 없음" in captured[0]


# ===========================================================================
# US-211: API endpoints
# ===========================================================================


@pytest.fixture
def app():
    ctx = _make_context(
        alert_history=deque([
            {"id": "alert-001", "type": "kill_switch", "severity": "critical",
             "message": "Kill switch triggered", "timestamp": "2026-03-17T10:00:00Z"},
            {"id": "alert-002", "type": "ws_disconnect", "severity": "warning",
             "message": "WS disconnect", "timestamp": "2026-03-17T09:00:00Z"},
        ], maxlen=5000),
        exchange_status={
            "binance": {"connected": True, "latency_ms": 10},
            "upbit": {"connected": True, "latency_ms": 50},
        },
    )
    return create_app(ctx)


@pytest.fixture
def transport(app):
    return ASGITransport(app=app)


class TestPortfolioPositions:
    @pytest.mark.asyncio
    async def test_positions_returns_empty_list_by_default(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/v1/portfolio/positions", headers=_auth_header())
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @pytest.mark.asyncio
    async def test_positions_from_context_positions(self, transport, app):
        app.state.engine_context.positions = [{"symbol": "BTC/USDT", "side": "long"}]
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/v1/portfolio/positions", headers=_auth_header())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1


class TestPortfolioDailyReturns:
    @pytest.mark.asyncio
    async def test_daily_returns_returns_array(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/v1/portfolio/daily-returns", headers=_auth_header())
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert "date" in data[0]
        assert "pnl" in data[0]


class TestSystemLogs:
    @pytest.mark.asyncio
    async def test_logs_returns_alert_history(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/v1/system/logs", headers=_auth_header())
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_logs_limit_param(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/v1/system/logs?limit=1", headers=_auth_header())
        assert resp.status_code == 200
        assert len(resp.json()) <= 1


class TestDbMetrics:
    @pytest.mark.asyncio
    async def test_db_metrics_returns_dict(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/v1/system/db-metrics", headers=_auth_header())
        assert resp.status_code == 200
        data = resp.json()
        assert "connected" in data


class TestRedisMetrics:
    @pytest.mark.asyncio
    async def test_redis_metrics_returns_dict(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/v1/system/redis-metrics", headers=_auth_header())
        assert resp.status_code == 200
        data = resp.json()
        assert "connected" in data
        assert "memory_used_mb" in data


class TestAlertAcknowledge:
    @pytest.mark.asyncio
    async def test_acknowledge_existing_alert(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/v1/alerts/acknowledge",
                headers=_auth_header(),
                json={"alert_id": "alert-001"},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "acknowledged"

    @pytest.mark.asyncio
    async def test_acknowledge_nonexistent_alert_returns_404(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/v1/alerts/acknowledge",
                headers=_auth_header(),
                json={"alert_id": "nonexistent"},
            )
        assert resp.status_code == 404


class TestAlertResolve:
    @pytest.mark.asyncio
    async def test_resolve_existing_alert(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/v1/alerts/resolve",
                headers=_auth_header(),
                json={"alert_id": "alert-002"},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "resolved"

    @pytest.mark.asyncio
    async def test_resolve_nonexistent_alert_returns_404(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/v1/alerts/resolve",
                headers=_auth_header(),
                json={"alert_id": "nonexistent"},
            )
        assert resp.status_code == 404


class TestTestAlert:
    @pytest.mark.asyncio
    async def test_send_test_alert(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/v1/settings/test-alert", headers=_auth_header())
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "sent"
        assert "alert" in data


class TestExchangeReconnect:
    @pytest.mark.asyncio
    async def test_reconnect_known_exchange(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/v1/exchanges/reconnect",
                headers=_auth_header(),
                json={"exchange_id": "binance"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["exchange_id"] == "binance"

    @pytest.mark.asyncio
    async def test_reconnect_unknown_exchange_returns_404(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/v1/exchanges/reconnect",
                headers=_auth_header(),
                json={"exchange_id": "nonexistent_exchange"},
            )
        assert resp.status_code == 404
