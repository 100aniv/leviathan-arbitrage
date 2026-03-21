"""Tests for TradeTelegramBot (US-291-c/f/g/h)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.infra.telegram_trade_bot import TradeTelegramBot, AlertLevel


class TestAlertLevel:
    def test_values(self):
        assert AlertLevel.ALL.value == "all"
        assert AlertLevel.IMPORTANT.value == "important"
        assert AlertLevel.CRITICAL_ONLY.value == "critical_only"


class TestTradeTelegramBot:
    def test_init_with_fallback(self):
        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN": "tok",
                "TELEGRAM_CHAT_ID": "123",
                "TELEGRAM_ENABLED": "true",
            },
            clear=True,
        ):
            bot = TradeTelegramBot()
            assert bot.bot_name == "LEVIATHAN-TRADE"
            assert bot.enabled is True

    def test_init_with_trade_token(self):
        with patch.dict(
            "os.environ",
            {
                "TRADE_TELEGRAM_BOT_TOKEN": "trade_tok",
                "TRADE_TELEGRAM_CHAT_ID": "456",
                "TRADE_TELEGRAM_ENABLED": "true",
            },
            clear=True,
        ):
            bot = TradeTelegramBot()
            assert bot._bot_token == "trade_tok"

    def test_commands_registered(self):
        with patch.dict(
            "os.environ",
            {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1", "TELEGRAM_ENABLED": "true"},
            clear=True,
        ):
            bot = TradeTelegramBot()
            for cmd in [
                "/status", "/pnl", "/strategies", "/risk", "/kill",
                "/pause", "/resume", "/alerts", "/menu", "/settings",
                "/chart", "/help",
            ]:
                assert cmd in bot._commands, f"{cmd} not registered"

    def test_callbacks_registered(self):
        with patch.dict(
            "os.environ",
            {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1", "TELEGRAM_ENABLED": "true"},
            clear=True,
        ):
            bot = TradeTelegramBot()
            assert "kill_" in bot._callbacks
            assert "menu_" in bot._callbacks
            assert "settings_" in bot._callbacks

    def test_default_alert_level(self):
        with patch.dict(
            "os.environ",
            {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1", "TELEGRAM_ENABLED": "true"},
            clear=True,
        ):
            bot = TradeTelegramBot()
            assert bot._alert_level == AlertLevel.IMPORTANT

    @pytest.mark.asyncio
    async def test_send_alert_delegates(self):
        with patch.dict(
            "os.environ",
            {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1", "TELEGRAM_ENABLED": "true"},
            clear=True,
        ):
            bot = TradeTelegramBot()
            bot._alerter = AsyncMock()
            bot._alerter.send_alert = AsyncMock(return_value=True)
            result = await bot.send_alert("test", level="WARNING")
            bot._alerter.send_alert.assert_called_once_with("test", level="WARNING")
            assert result is True

    def test_get_shadow_snapshot_no_context(self):
        with patch.dict(
            "os.environ",
            {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1", "TELEGRAM_ENABLED": "true"},
            clear=True,
        ):
            bot = TradeTelegramBot()
            assert bot._get_shadow_snapshot() is None

    @pytest.mark.asyncio
    async def test_get_pnl_text_no_snapshot(self):
        with patch.dict(
            "os.environ",
            {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1", "TELEGRAM_ENABLED": "true"},
            clear=True,
        ):
            bot = TradeTelegramBot()
            result = await bot._get_pnl_text()
            assert "없음" in result
