"""Tests for US-219: Telegram Bot commands (/pnl /strategies /risk /pause /resume /alerts).

Covers: all 6 new commands with EngineContext mock, no-context fallback,
pause/resume state toggle, alerts list rendering.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.infra.telegram_bot import TelegramCommandHandler


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_alerter() -> MagicMock:
    alerter = MagicMock()
    alerter.bot_token = "bot123:TOKEN"
    alerter.enabled = True
    alerter.send_alert = AsyncMock(return_value=True)
    return alerter


@pytest.fixture
def mock_context() -> MagicMock:
    """Mock EngineContext with shadow_mode.get_snapshot()."""
    ctx = MagicMock()
    ctx.kill_switch_active = False
    ctx.pending_alerts = []
    ctx.shadow_mode = MagicMock()
    ctx.shadow_mode.get_snapshot.return_value = {
        "active": True,
        "total_pnl": 42.123456,
        "win_rate": 0.75,
        "trades_executed": 100,
        "max_drawdown_pct": 0.015,
        "by_strategy": [
            {"strategy_id": "cross_exchange", "pnl": 30.0, "trades": 60, "win_rate": 0.8},
            {"strategy_id": "funding_rate", "pnl": 12.1, "trades": 40, "win_rate": 0.65},
        ],
    }
    return ctx


@pytest.fixture
def handler(mock_alerter: MagicMock, mock_context: MagicMock) -> TelegramCommandHandler:
    return TelegramCommandHandler(
        alerter=mock_alerter,
        engine_context=mock_context,
    )


# ---------------------------------------------------------------------------
# /pnl
# ---------------------------------------------------------------------------


class TestPnlCommand:
    async def test_pnl_returns_current_pnl(self, handler: TelegramCommandHandler) -> None:
        resp = await handler.process_command("/pnl")
        assert "42.123456" in resp
        assert "75.0%" in resp
        assert "100건" in resp

    async def test_pnl_no_context_returns_error(self, mock_alerter: MagicMock) -> None:
        h = TelegramCommandHandler(alerter=mock_alerter, engine_context=None)
        resp = await h.process_command("/pnl")
        assert "미연결" in resp


# ---------------------------------------------------------------------------
# /strategies
# ---------------------------------------------------------------------------


class TestStrategiesCommand:
    async def test_strategies_shows_all(self, handler: TelegramCommandHandler) -> None:
        resp = await handler.process_command("/strategies")
        assert "cross_exchange" in resp
        assert "funding_rate" in resp
        assert "$+30.0000" in resp

    async def test_strategies_no_context(self, mock_alerter: MagicMock) -> None:
        h = TelegramCommandHandler(alerter=mock_alerter, engine_context=None)
        resp = await h.process_command("/strategies")
        assert "미연결" in resp

    async def test_strategies_empty_list(self, mock_alerter: MagicMock) -> None:
        ctx = MagicMock()
        ctx.shadow_mode = MagicMock()
        ctx.shadow_mode.get_snapshot.return_value = {"by_strategy": []}
        h = TelegramCommandHandler(alerter=mock_alerter, engine_context=ctx)
        resp = await h.process_command("/strategies")
        assert "없습니다" in resp


# ---------------------------------------------------------------------------
# /risk
# ---------------------------------------------------------------------------


class TestRiskCommand:
    async def test_risk_shows_mdd_and_kill_switch(self, handler: TelegramCommandHandler) -> None:
        resp = await handler.process_command("/risk")
        assert "1.50%" in resp
        assert "비활성" in resp

    async def test_risk_kill_switch_active(
        self, handler: TelegramCommandHandler, mock_context: MagicMock
    ) -> None:
        mock_context.kill_switch_active = True
        resp = await handler.process_command("/risk")
        assert "활성" in resp


# ---------------------------------------------------------------------------
# /pause
# ---------------------------------------------------------------------------


class TestPauseCommand:
    async def test_pause_activates_kill_switch(
        self, handler: TelegramCommandHandler, mock_context: MagicMock
    ) -> None:
        resp = await handler.process_command("/pause")
        assert "일시중단" in resp
        assert mock_context.kill_switch_active is True

    async def test_pause_no_context(self, mock_alerter: MagicMock) -> None:
        h = TelegramCommandHandler(alerter=mock_alerter, engine_context=None)
        resp = await h.process_command("/pause")
        assert "미연결" in resp


# ---------------------------------------------------------------------------
# /resume
# ---------------------------------------------------------------------------


class TestResumeCommand:
    async def test_resume_deactivates_kill_switch(
        self, handler: TelegramCommandHandler, mock_context: MagicMock
    ) -> None:
        mock_context.kill_switch_active = True
        resp = await handler.process_command("/resume")
        assert "재개" in resp
        assert mock_context.kill_switch_active is False

    async def test_resume_no_context(self, mock_alerter: MagicMock) -> None:
        h = TelegramCommandHandler(alerter=mock_alerter, engine_context=None)
        resp = await h.process_command("/resume")
        assert "미연결" in resp


# ---------------------------------------------------------------------------
# /alerts
# ---------------------------------------------------------------------------


class TestAlertsCommand:
    async def test_alerts_empty(self, handler: TelegramCommandHandler) -> None:
        resp = await handler.process_command("/alerts")
        assert "없음" in resp

    async def test_alerts_with_items(
        self, handler: TelegramCommandHandler, mock_context: MagicMock
    ) -> None:
        mock_context.pending_alerts = ["DB 연결 불안정", "Bithumb stale data"]
        resp = await handler.process_command("/alerts")
        assert "2건" in resp
        assert "DB 연결 불안정" in resp
        assert "Bithumb stale data" in resp

    async def test_alerts_caps_at_ten(
        self, handler: TelegramCommandHandler, mock_context: MagicMock
    ) -> None:
        mock_context.pending_alerts = [f"alert_{i}" for i in range(15)]
        resp = await handler.process_command("/alerts")
        assert "+5건" in resp

    async def test_alerts_no_context(self, mock_alerter: MagicMock) -> None:
        h = TelegramCommandHandler(alerter=mock_alerter, engine_context=None)
        resp = await h.process_command("/alerts")
        assert "미연결" in resp


# ---------------------------------------------------------------------------
# Help text includes new commands
# ---------------------------------------------------------------------------


class TestHelpIncludesNewCommands:
    async def test_help_lists_pnl(self, handler: TelegramCommandHandler) -> None:
        resp = await handler.process_command("/help")
        assert "/pnl" in resp

    async def test_help_lists_strategies(self, handler: TelegramCommandHandler) -> None:
        resp = await handler.process_command("/help")
        assert "/strategies" in resp

    async def test_help_lists_risk(self, handler: TelegramCommandHandler) -> None:
        resp = await handler.process_command("/help")
        assert "/risk" in resp

    async def test_help_lists_pause(self, handler: TelegramCommandHandler) -> None:
        resp = await handler.process_command("/help")
        assert "/pause" in resp

    async def test_help_lists_resume(self, handler: TelegramCommandHandler) -> None:
        resp = await handler.process_command("/help")
        assert "/resume" in resp

    async def test_help_lists_alerts(self, handler: TelegramCommandHandler) -> None:
        resp = await handler.process_command("/help")
        assert "/alerts" in resp
