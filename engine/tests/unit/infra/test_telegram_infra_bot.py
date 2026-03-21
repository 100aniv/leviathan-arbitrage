"""Tests for InfraTelegramBot (US-291-b)."""
import pytest
from unittest.mock import AsyncMock, patch
from src.infra.telegram_infra_bot import InfraTelegramBot


class TestInfraTelegramBot:
    def test_init(self):
        with patch.dict(
            "os.environ",
            {
                "INFRA_TELEGRAM_BOT_TOKEN": "tok",
                "INFRA_TELEGRAM_CHAT_ID": "123",
                "INFRA_TELEGRAM_ENABLED": "true",
            },
        ):
            bot = InfraTelegramBot()
            assert bot.bot_name == "LEVIATHAN-INFRA"
            assert bot.enabled is True

    def test_commands_registered(self):
        with patch.dict(
            "os.environ",
            {
                "INFRA_TELEGRAM_BOT_TOKEN": "tok",
                "INFRA_TELEGRAM_CHAT_ID": "123",
                "INFRA_TELEGRAM_ENABLED": "true",
            },
        ):
            bot = InfraTelegramBot()
            assert "/health" in bot._commands
            assert "/docker" in bot._commands
            assert "/checklist" in bot._commands
            assert "/help" in bot._commands

    def test_disabled_by_default(self):
        with patch.dict("os.environ", {}, clear=True):
            bot = InfraTelegramBot()
            assert bot.enabled is False

    def test_set_startup_checker(self):
        with patch.dict(
            "os.environ",
            {
                "INFRA_TELEGRAM_BOT_TOKEN": "tok",
                "INFRA_TELEGRAM_CHAT_ID": "123",
                "INFRA_TELEGRAM_ENABLED": "true",
            },
        ):
            bot = InfraTelegramBot()
            mock_checker = object()
            bot.set_startup_checker(mock_checker)
            assert bot._startup_checker is mock_checker

    @pytest.mark.asyncio
    async def test_cmd_checklist_no_checker(self):
        with patch.dict(
            "os.environ",
            {
                "INFRA_TELEGRAM_BOT_TOKEN": "tok",
                "INFRA_TELEGRAM_CHAT_ID": "123",
                "INFRA_TELEGRAM_ENABLED": "true",
            },
        ):
            bot = InfraTelegramBot()
            result = await bot._cmd_checklist("", 123, {})
            assert "미설정" in result

    @pytest.mark.asyncio
    async def test_cmd_help(self):
        with patch.dict(
            "os.environ",
            {
                "INFRA_TELEGRAM_BOT_TOKEN": "tok",
                "INFRA_TELEGRAM_CHAT_ID": "123",
                "INFRA_TELEGRAM_ENABLED": "true",
            },
        ):
            bot = InfraTelegramBot()
            result = await bot._cmd_help("", 123, {})
            assert "LEVIATHAN-INFRA" in result
