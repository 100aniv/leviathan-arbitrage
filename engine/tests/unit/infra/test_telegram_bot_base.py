"""Tests for TelegramBotBase (US-291-a)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.infra.telegram_bot_base import TelegramBotBase, InlineKeyboard


class TestInlineKeyboard:
    def test_empty(self):
        kb = InlineKeyboard()
        assert kb.to_markup() == {"inline_keyboard": []}

    def test_single_row(self):
        kb = InlineKeyboard().row(("A", "a"), ("B", "b"))
        markup = kb.to_markup()
        assert len(markup["inline_keyboard"]) == 1
        assert markup["inline_keyboard"][0][0] == {"text": "A", "callback_data": "a"}

    def test_multiple_rows(self):
        kb = InlineKeyboard().row(("A", "a")).row(("B", "b"))
        assert len(kb.to_markup()["inline_keyboard"]) == 2


class TestTelegramBotBase:
    def test_init_defaults(self):
        bot = TelegramBotBase(bot_token="test", chat_id="123", enabled=True, bot_name="Test")
        assert bot.enabled is True
        assert bot.bot_name == "Test"

    def test_disabled(self):
        bot = TelegramBotBase(enabled=False, bot_name="Test")
        assert bot.enabled is False

    def test_register_command(self):
        bot = TelegramBotBase(bot_token="t", chat_id="1", enabled=True, bot_name="T")

        async def handler(text, chat_id, message):
            return "ok"

        bot.register_command("/test", handler)
        assert "/test" in bot._commands

    def test_register_callback(self):
        bot = TelegramBotBase(bot_token="t", chat_id="1", enabled=True, bot_name="T")

        async def handler(cq):
            return "ok"

        bot.register_callback("prefix_", handler)
        assert "prefix_" in bot._callbacks

    def test_rate_limit(self):
        bot = TelegramBotBase(bot_token="t", chat_id="1", enabled=True, bot_name="T")
        for _ in range(20):
            assert bot._check_rate_limit() is True
        assert bot._check_rate_limit() is False

    def test_authorize_allowed(self):
        bot = TelegramBotBase(bot_token="t", chat_id="123", enabled=True, bot_name="T")
        assert bot._authorize(123) is True

    def test_authorize_denied(self):
        bot = TelegramBotBase(bot_token="t", chat_id="123", enabled=True, bot_name="T")
        assert bot._authorize(999) is False

    @pytest.mark.asyncio
    async def test_send_message_disabled(self):
        bot = TelegramBotBase(enabled=False, bot_name="T")
        result = await bot.send_message("test")
        assert result is None

    @pytest.mark.asyncio
    async def test_send_message_no_token(self):
        with patch.dict("os.environ", {}, clear=True):
            bot = TelegramBotBase(bot_token="", enabled=True, bot_name="T")
            result = await bot.send_message("test")
            assert result is None

    @pytest.mark.asyncio
    async def test_close(self):
        bot = TelegramBotBase(bot_token="t", chat_id="1", enabled=True, bot_name="T")
        await bot.close()
        assert bot._running is False
