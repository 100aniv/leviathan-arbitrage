"""Tests for DevTelegramBot (US-291-d)."""
import pytest
from unittest.mock import patch
from src.infra.telegram_dev_bot import DevTelegramBot


class TestDevTelegramBot:
    def test_init_with_fallback(self):
        with patch.dict(
            "os.environ",
            {
                "WORKFLOW_TELEGRAM_BOT_TOKEN": "wf_tok",
                "WORKFLOW_TELEGRAM_CHAT_ID": "789",
                "WORKFLOW_TELEGRAM_ENABLED": "true",
            },
            clear=True,
        ):
            bot = DevTelegramBot()
            assert bot.bot_name == "LEVIATHAN-DEV"
            assert bot.enabled is True

    def test_init_with_dev_token(self):
        with patch.dict(
            "os.environ",
            {
                "DEV_TELEGRAM_BOT_TOKEN": "dev_tok",
                "DEV_TELEGRAM_CHAT_ID": "456",
                "DEV_TELEGRAM_ENABLED": "true",
            },
            clear=True,
        ):
            bot = DevTelegramBot()
            assert bot._bot_token == "dev_tok"

    def test_commands_registered(self):
        with patch.dict(
            "os.environ",
            {
                "WORKFLOW_TELEGRAM_BOT_TOKEN": "t",
                "WORKFLOW_TELEGRAM_CHAT_ID": "1",
                "WORKFLOW_TELEGRAM_ENABLED": "true",
            },
            clear=True,
        ):
            bot = DevTelegramBot()
            assert "/phase" in bot._commands
            assert "/tests" in bot._commands
            assert "/errors" in bot._commands
            assert "/help" in bot._commands

    def test_disabled_when_no_env(self):
        with patch.dict("os.environ", {}, clear=True):
            bot = DevTelegramBot()
            assert bot.enabled is False

    @pytest.mark.asyncio
    async def test_cmd_errors_returns_guidance(self):
        with patch.dict(
            "os.environ",
            {
                "WORKFLOW_TELEGRAM_BOT_TOKEN": "t",
                "WORKFLOW_TELEGRAM_CHAT_ID": "1",
                "WORKFLOW_TELEGRAM_ENABLED": "true",
            },
            clear=True,
        ):
            bot = DevTelegramBot()
            result = await bot._cmd_errors("", 1, {})
            assert "에러" in result

    @pytest.mark.asyncio
    async def test_cmd_help(self):
        with patch.dict(
            "os.environ",
            {
                "WORKFLOW_TELEGRAM_BOT_TOKEN": "t",
                "WORKFLOW_TELEGRAM_CHAT_ID": "1",
                "WORKFLOW_TELEGRAM_ENABLED": "true",
            },
            clear=True,
        ):
            bot = DevTelegramBot()
            result = await bot._cmd_help("", 1, {})
            assert "LEVIATHAN-DEV" in result
