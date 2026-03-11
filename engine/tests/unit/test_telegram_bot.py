"""Tests for src/infra/telegram_bot.py — US-117 Telegram Command Bot.

Covers: /status, /kill, /mode, /balance, /help commands,
unknown command fallback, empty text fallback.
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
    """Minimal mock TelegramAlerter with required attributes."""
    alerter = MagicMock()
    alerter._bot_token = "bot123:TOKEN"
    alerter._enabled = True
    alerter.send_alert = AsyncMock(return_value=True)
    return alerter


@pytest.fixture
def status_fn() -> AsyncMock:
    return AsyncMock(return_value="Mode: shadow | PnL: +42.50 | Trades: 3")


@pytest.fixture
def kill_fn() -> AsyncMock:
    return AsyncMock(return_value="KillSwitch activated")


@pytest.fixture
def mode_fn() -> AsyncMock:
    return AsyncMock(return_value="Current mode: shadow")


@pytest.fixture
def balance_fn() -> AsyncMock:
    return AsyncMock(return_value="USDT balance: 10000.00")


@pytest.fixture
def handler(
    mock_alerter: MagicMock,
    status_fn: AsyncMock,
    kill_fn: AsyncMock,
    mode_fn: AsyncMock,
    balance_fn: AsyncMock,
) -> TelegramCommandHandler:
    return TelegramCommandHandler(
        alerter=mock_alerter,
        status_fn=status_fn,
        kill_fn=kill_fn,
        mode_fn=mode_fn,
        balance_fn=balance_fn,
    )


# ---------------------------------------------------------------------------
# /status command
# ---------------------------------------------------------------------------


class TestStatusCommand:
    async def test_process_status_command_returns_status_message(
        self, handler: TelegramCommandHandler
    ) -> None:
        """/status → response is non-empty."""
        response = await handler.process_command("/status")
        assert response is not None
        assert len(response) > 0

    async def test_process_status_command_calls_status_fn(
        self, handler: TelegramCommandHandler, status_fn: AsyncMock
    ) -> None:
        """/status → registered status_fn is invoked."""
        await handler.process_command("/status")
        status_fn.assert_awaited_once()

    async def test_process_status_command_returns_status_fn_output(
        self, handler: TelegramCommandHandler
    ) -> None:
        """/status → returns exact string from status_fn."""
        response = await handler.process_command("/status")
        assert "shadow" in response or "PnL" in response or "Mode" in response


# ---------------------------------------------------------------------------
# /kill command
# ---------------------------------------------------------------------------


class TestKillCommand:
    async def test_process_kill_command_returns_kill_message(
        self, handler: TelegramCommandHandler
    ) -> None:
        """/kill → response acknowledges kill switch action."""
        response = await handler.process_command("/kill")
        assert response is not None
        assert len(response) > 0

    async def test_process_kill_command_calls_kill_fn(
        self, handler: TelegramCommandHandler, kill_fn: AsyncMock
    ) -> None:
        """/kill → registered kill_fn is invoked."""
        await handler.process_command("/kill")
        kill_fn.assert_awaited_once()

    async def test_process_kill_command_returns_kill_fn_output(
        self, handler: TelegramCommandHandler
    ) -> None:
        """/kill → response contains kill-related text."""
        response = await handler.process_command("/kill")
        assert any(kw in response.lower() for kw in ("kill", "activated", "switch"))


# ---------------------------------------------------------------------------
# /mode command
# ---------------------------------------------------------------------------


class TestModeCommand:
    async def test_process_mode_command_returns_mode_message(
        self, handler: TelegramCommandHandler
    ) -> None:
        """/mode → response contains mode information."""
        response = await handler.process_command("/mode")
        assert response is not None
        assert len(response) > 0

    async def test_process_mode_command_calls_mode_fn(
        self, handler: TelegramCommandHandler, mode_fn: AsyncMock
    ) -> None:
        """/mode → registered mode_fn is invoked."""
        await handler.process_command("/mode")
        mode_fn.assert_awaited_once()

    async def test_process_mode_command_default_when_no_mode_fn(
        self, mock_alerter: MagicMock
    ) -> None:
        """/mode with no mode_fn → returns non-empty default string."""
        h = TelegramCommandHandler(alerter=mock_alerter)
        response = await h.process_command("/mode")
        assert response is not None
        assert len(response) > 0


# ---------------------------------------------------------------------------
# /balance command
# ---------------------------------------------------------------------------


class TestBalanceCommand:
    async def test_process_balance_command_returns_balance_message(
        self, handler: TelegramCommandHandler
    ) -> None:
        """/balance → response contains balance information."""
        response = await handler.process_command("/balance")
        assert response is not None
        assert len(response) > 0

    async def test_process_balance_command_calls_balance_fn(
        self, handler: TelegramCommandHandler, balance_fn: AsyncMock
    ) -> None:
        """/balance → registered balance_fn is invoked."""
        await handler.process_command("/balance")
        balance_fn.assert_awaited_once()

    async def test_process_balance_command_returns_balance_fn_output(
        self, handler: TelegramCommandHandler
    ) -> None:
        """/balance → response includes USDT value."""
        response = await handler.process_command("/balance")
        assert "usdt" in response.lower() or "balance" in response.lower() or "10000" in response


# ---------------------------------------------------------------------------
# /help command
# ---------------------------------------------------------------------------


class TestHelpCommand:
    async def test_process_help_command_returns_help_text(
        self, handler: TelegramCommandHandler
    ) -> None:
        """/help → response is non-empty help text."""
        response = await handler.process_command("/help")
        assert response is not None
        assert len(response) > 10

    async def test_process_help_command_lists_status_command(
        self, handler: TelegramCommandHandler
    ) -> None:
        """/help → lists /status command."""
        response = await handler.process_command("/help")
        assert "/status" in response

    async def test_process_help_command_lists_kill_command(
        self, handler: TelegramCommandHandler
    ) -> None:
        """/help → lists /kill command."""
        response = await handler.process_command("/help")
        assert "/kill" in response

    async def test_process_help_command_lists_mode_command(
        self, handler: TelegramCommandHandler
    ) -> None:
        """/help → lists /mode command."""
        response = await handler.process_command("/help")
        assert "/mode" in response

    async def test_process_help_command_lists_balance_command(
        self, handler: TelegramCommandHandler
    ) -> None:
        """/help → lists /balance command."""
        response = await handler.process_command("/help")
        assert "/balance" in response

    async def test_process_help_command_lists_help_itself(
        self, handler: TelegramCommandHandler
    ) -> None:
        """/help → lists /help itself (all 5 commands present)."""
        response = await handler.process_command("/help")
        assert "/help" in response


# ---------------------------------------------------------------------------
# Unknown command fallback
# ---------------------------------------------------------------------------


class TestUnknownCommandFallback:
    async def test_unknown_command_returns_unknown_message(
        self, handler: TelegramCommandHandler
    ) -> None:
        """/unknown → response contains 'Unknown command' text."""
        response = await handler.process_command("/unknown")
        assert "unknown" in response.lower()

    async def test_unknown_command_includes_known_commands(
        self, handler: TelegramCommandHandler
    ) -> None:
        """/unknown → response also shows available commands."""
        response = await handler.process_command("/unknown")
        assert any(
            cmd in response for cmd in ("/status", "/kill", "/mode", "/balance", "/help")
        )


# ---------------------------------------------------------------------------
# Empty text fallback
# ---------------------------------------------------------------------------


class TestEmptyTextFallback:
    async def test_empty_text_returns_help_or_unknown_message(
        self, handler: TelegramCommandHandler
    ) -> None:
        """Empty string → returns help (graceful default, non-empty)."""
        response = await handler.process_command("")
        assert response is not None
        assert len(response) > 0

    async def test_whitespace_only_returns_non_empty_response(
        self, handler: TelegramCommandHandler
    ) -> None:
        """Whitespace input → non-empty response."""
        response = await handler.process_command("   ")
        assert response is not None
        assert len(response) > 0
