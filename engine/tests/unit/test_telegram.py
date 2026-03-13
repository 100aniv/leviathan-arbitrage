"""Tests for engine/src/infra/telegram.py (TelegramAlerter).

Covers: init with/without env vars, send_alert formatting, rate limiting,
disabled mode, send_kill_switch_event, send_daily_summary, send_signal_found,
_send HTTP error handling, _check_rate_limit sliding window.
"""
from __future__ import annotations

import time
from collections import deque
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.infra.telegram import TelegramAlerter, WorkflowTelegramAlerter, get_telegram_alerter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def enabled_alerter() -> TelegramAlerter:
    """Fully configured, enabled alerter with fake credentials."""
    return TelegramAlerter(
        bot_token="bot123:TOKEN",
        chat_id="-100123456",
        enabled=True,
    )


@pytest.fixture
def disabled_alerter() -> TelegramAlerter:
    """Alerter in disabled mode."""
    return TelegramAlerter(
        bot_token="bot123:TOKEN",
        chat_id="-100123456",
        enabled=False,
    )


@pytest.fixture
def mock_signal():
    """Minimal Signal-like object for send_signal_found tests."""
    sig = MagicMock()
    sig.strategy_id = "cross_exchange_spot_v1"
    sig.symbol = "BTC/USDT"
    sig.buy_exchange = "binance"
    sig.sell_exchange = "okx"
    sig.buy_price = Decimal("50000.00")
    sig.sell_price = Decimal("50150.00")
    sig.spread_pct = Decimal("0.003")
    sig.confidence = 0.85
    sig.volume = Decimal("0.5")
    sig.metadata = {"net_profit": "75.00", "net_edge_pct": "0.15"}
    return sig


@pytest.fixture
def mock_kill_switch_event():
    """Minimal KillSwitchEvent-like object."""
    evt = MagicMock()
    evt.tier1_latency_ms = 0.42
    evt.tier2_latency_ms = 12.5
    evt.tier3_latency_ms = None
    evt.cancelled_orders = ["ord1", "ord2"]
    evt.closed_positions = []
    evt.redis_halt_set = True
    evt.errors = []
    return evt


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


class TestTelegramAlerterInit:
    def test_init_with_explicit_credentials_stores_values(self):
        alerter = TelegramAlerter(
            bot_token="botABC:XYZ",
            chat_id="-999",
            enabled=True,
        )
        assert alerter._bot_token == "botABC:XYZ"
        assert alerter._chat_id == "-999"
        assert alerter._enabled is True

    def test_init_without_args_reads_env_vars(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env_token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "env_chat")
        monkeypatch.setenv("TELEGRAM_ENABLED", "true")
        alerter = TelegramAlerter()
        assert alerter._bot_token == "env_token"
        assert alerter._chat_id == "env_chat"
        assert alerter._enabled is True

    def test_init_defaults_to_disabled_when_env_not_set(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_ENABLED", raising=False)
        alerter = TelegramAlerter()
        assert alerter._enabled is False

    def test_init_send_times_queue_starts_empty(self):
        alerter = TelegramAlerter()
        assert len(alerter._send_times) == 0

    def test_get_telegram_alerter_factory_reads_env(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "factory_tok")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "factory_chat")
        monkeypatch.setenv("TELEGRAM_ENABLED", "true")
        alerter = get_telegram_alerter()
        assert alerter._bot_token == "factory_tok"
        assert alerter._enabled is True


# ---------------------------------------------------------------------------
# Disabled mode
# ---------------------------------------------------------------------------


class TestDisabledMode:
    async def test_send_alert_returns_false_when_disabled(self, disabled_alerter):
        result = await disabled_alerter.send_alert("hello", level="INFO")
        assert result is False

    async def test_disabled_does_not_call_http(self, disabled_alerter):
        with patch("httpx.AsyncClient") as mock_client:
            await disabled_alerter.send_alert("should not send")
            mock_client.assert_not_called()


# ---------------------------------------------------------------------------
# Misconfiguration guard
# ---------------------------------------------------------------------------


class TestMisconfiguredAlerter:
    async def test_returns_false_when_token_missing(self):
        alerter = TelegramAlerter(bot_token=None, chat_id="-999", enabled=True)
        result = await alerter.send_alert("no token")
        assert result is False

    async def test_returns_false_when_chat_id_missing(self):
        alerter = TelegramAlerter(bot_token="tok", chat_id=None, enabled=True)
        result = await alerter.send_alert("no chat id")
        assert result is False


# ---------------------------------------------------------------------------
# send_alert formatting
# ---------------------------------------------------------------------------


class TestSendAlertFormatting:
    async def test_send_alert_info_prefix_contains_emoji(self, enabled_alerter):
        captured: list[str] = []

        async def fake_send(text: str, parse_mode: str = "HTML") -> bool:
            captured.append(text)
            return True

        enabled_alerter._send = fake_send  # type: ignore[method-assign]
        await enabled_alerter.send_alert("engine started", level="INFO")
        assert "ℹ️" in captured[0]
        assert "INFO" in captured[0]
        assert "engine started" in captured[0]

    async def test_send_alert_critical_prefix_contains_siren_emoji(self, enabled_alerter):
        captured: list[str] = []

        async def fake_send(text: str, parse_mode: str = "HTML") -> bool:
            captured.append(text)
            return True

        enabled_alerter._send = fake_send  # type: ignore[method-assign]
        await enabled_alerter.send_alert("system failure", level="CRITICAL")
        assert "🚨" in captured[0]
        assert "CRITICAL" in captured[0]

    async def test_send_alert_unknown_level_falls_back_to_info_emoji(self, enabled_alerter):
        captured: list[str] = []

        async def fake_send(text: str, parse_mode: str = "HTML") -> bool:
            captured.append(text)
            return True

        enabled_alerter._send = fake_send  # type: ignore[method-assign]
        await enabled_alerter.send_alert("test", level="TRACE")
        assert "ℹ️" in captured[0]


# ---------------------------------------------------------------------------
# send_kill_switch_event formatting
# ---------------------------------------------------------------------------


class TestSendKillSwitchEventFormatting:
    async def test_message_contains_kill_switch_header(
        self, enabled_alerter, mock_kill_switch_event
    ):
        captured: list[str] = []

        async def fake_send(text: str, parse_mode: str = "HTML") -> bool:
            captured.append(text)
            return True

        enabled_alerter._send = fake_send  # type: ignore[method-assign]
        await enabled_alerter.send_kill_switch_event(mock_kill_switch_event)
        assert "KILL SWITCH ACTIVATED" in captured[0]

    async def test_message_includes_tier_latencies(
        self, enabled_alerter, mock_kill_switch_event
    ):
        captured: list[str] = []

        async def fake_send(text: str, parse_mode: str = "HTML") -> bool:
            captured.append(text)
            return True

        enabled_alerter._send = fake_send  # type: ignore[method-assign]
        await enabled_alerter.send_kill_switch_event(mock_kill_switch_event)
        assert "0.42 ms" in captured[0]
        assert "12.50 ms" in captured[0]
        assert "N/A" in captured[0]  # tier3 is None

    async def test_message_shows_error_list_when_errors_present(
        self, enabled_alerter, mock_kill_switch_event
    ):
        mock_kill_switch_event.errors = ["Redis timeout", "API error"]
        captured: list[str] = []

        async def fake_send(text: str, parse_mode: str = "HTML") -> bool:
            captured.append(text)
            return True

        enabled_alerter._send = fake_send  # type: ignore[method-assign]
        await enabled_alerter.send_kill_switch_event(mock_kill_switch_event)
        assert "Redis timeout" in captured[0]
        assert "API error" in captured[0]

    async def test_message_caps_error_list_at_five(
        self, enabled_alerter, mock_kill_switch_event
    ):
        mock_kill_switch_event.errors = [f"err{i}" for i in range(10)]
        captured: list[str] = []

        async def fake_send(text: str, parse_mode: str = "HTML") -> bool:
            captured.append(text)
            return True

        enabled_alerter._send = fake_send  # type: ignore[method-assign]
        await enabled_alerter.send_kill_switch_event(mock_kill_switch_event)
        assert "5 more" in captured[0]


# ---------------------------------------------------------------------------
# send_daily_summary formatting
# ---------------------------------------------------------------------------


class TestSendDailySummaryFormatting:
    async def test_positive_pnl_uses_up_arrow_emoji(self, enabled_alerter):
        captured: list[str] = []

        async def fake_send(text: str, parse_mode: str = "HTML") -> bool:
            captured.append(text)
            return True

        enabled_alerter._send = fake_send  # type: ignore[method-assign]
        await enabled_alerter.send_daily_summary(
            {"date": "2026-03-06", "total_pnl": 250.0, "trades": 12}
        )
        assert "📈" in captured[0]
        assert "$+250.00" in captured[0]

    async def test_negative_pnl_uses_down_arrow_emoji(self, enabled_alerter):
        captured: list[str] = []

        async def fake_send(text: str, parse_mode: str = "HTML") -> bool:
            captured.append(text)
            return True

        enabled_alerter._send = fake_send  # type: ignore[method-assign]
        await enabled_alerter.send_daily_summary({"total_pnl": -100.0})
        assert "📉" in captured[0]
        assert "$-100.00" in captured[0]

    async def test_missing_keys_render_as_na(self, enabled_alerter):
        captured: list[str] = []

        async def fake_send(text: str, parse_mode: str = "HTML") -> bool:
            captured.append(text)
            return True

        enabled_alerter._send = fake_send  # type: ignore[method-assign]
        await enabled_alerter.send_daily_summary({})
        assert "N/A" in captured[0]

    async def test_win_rate_formatted_as_percentage(self, enabled_alerter):
        captured: list[str] = []

        async def fake_send(text: str, parse_mode: str = "HTML") -> bool:
            captured.append(text)
            return True

        enabled_alerter._send = fake_send  # type: ignore[method-assign]
        await enabled_alerter.send_daily_summary({"win_rate": 0.75})
        assert "75.0%" in captured[0]


# ---------------------------------------------------------------------------
# send_signal_found formatting
# ---------------------------------------------------------------------------


class TestSendSignalFoundFormatting:
    async def test_message_contains_arbitrage_signal_header(
        self, enabled_alerter, mock_signal
    ):
        captured: list[str] = []

        async def fake_send(text: str, parse_mode: str = "HTML") -> bool:
            captured.append(text)
            return True

        enabled_alerter._send = fake_send  # type: ignore[method-assign]
        await enabled_alerter.send_signal_found(mock_signal)
        assert "ARBITRAGE SIGNAL" in captured[0]

    async def test_message_contains_exchange_prices(
        self, enabled_alerter, mock_signal
    ):
        captured: list[str] = []

        async def fake_send(text: str, parse_mode: str = "HTML") -> bool:
            captured.append(text)
            return True

        enabled_alerter._send = fake_send  # type: ignore[method-assign]
        await enabled_alerter.send_signal_found(mock_signal)
        assert "binance" in captured[0]
        assert "okx" in captured[0]
        assert "50000.00" in captured[0]

    async def test_spread_pct_formatted_to_four_decimal_places(
        self, enabled_alerter, mock_signal
    ):
        captured: list[str] = []

        async def fake_send(text: str, parse_mode: str = "HTML") -> bool:
            captured.append(text)
            return True

        enabled_alerter._send = fake_send  # type: ignore[method-assign]
        await enabled_alerter.send_signal_found(mock_signal)
        # spread_pct=0.003, so spread_pct*100 = 0.3 -> formatted as "0.3000%"
        assert "0.3000%" in captured[0]

    async def test_confidence_formatted_as_percentage(
        self, enabled_alerter, mock_signal
    ):
        captured: list[str] = []

        async def fake_send(text: str, parse_mode: str = "HTML") -> bool:
            captured.append(text)
            return True

        enabled_alerter._send = fake_send  # type: ignore[method-assign]
        await enabled_alerter.send_signal_found(mock_signal)
        assert "85.0%" in captured[0]


# ---------------------------------------------------------------------------
# _send HTTP error handling
# ---------------------------------------------------------------------------


class TestSendHttpErrors:
    async def test_http_status_error_returns_false(self, enabled_alerter):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "400", request=MagicMock(), response=mock_response
            )
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await enabled_alerter._send("test message")

        assert result is False

    async def test_timeout_exception_returns_false(self, enabled_alerter):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(
            side_effect=httpx.TimeoutException("timed out")
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await enabled_alerter._send("test message")

        assert result is False

    async def test_unexpected_exception_returns_false(self, enabled_alerter):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=RuntimeError("unexpected"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await enabled_alerter._send("test message")

        assert result is False

    async def test_successful_post_returns_true(self, enabled_alerter):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await enabled_alerter._send("test message")

        assert result is True


# ---------------------------------------------------------------------------
# _check_rate_limit sliding window
# ---------------------------------------------------------------------------


class TestCheckRateLimit:
    def test_first_message_is_allowed(self, enabled_alerter):
        assert enabled_alerter._check_rate_limit() is True

    def test_exactly_twenty_messages_are_allowed(self, enabled_alerter):
        for _ in range(20):
            result = enabled_alerter._check_rate_limit()
        assert result is True

    def test_twenty_first_message_is_blocked(self, enabled_alerter):
        for _ in range(20):
            enabled_alerter._check_rate_limit()
        result = enabled_alerter._check_rate_limit()
        assert result is False

    def test_old_timestamps_are_evicted_from_window(self, enabled_alerter):
        # Inject 20 timestamps that are > 60s old
        old_time = time.monotonic() - 61.0
        enabled_alerter._send_times = deque([old_time] * 20)
        # Window is now empty after eviction — message should be allowed
        result = enabled_alerter._check_rate_limit()
        assert result is True

    def test_rate_limit_is_per_minute_window(self, enabled_alerter):
        # Fill 19 slots with old timestamps (expired) and 1 recent
        old_time = time.monotonic() - 61.0
        enabled_alerter._send_times = deque([old_time] * 19)
        # One recent message already sent
        enabled_alerter._check_rate_limit()  # adds current timestamp → 1 in window
        # 19 more should be allowed before the 21st is blocked
        for _ in range(19):
            enabled_alerter._check_rate_limit()
        # Now window has 20 → next should be blocked
        result = enabled_alerter._check_rate_limit()
        assert result is False


# ---------------------------------------------------------------------------
# WorkflowTelegramAlerter — context warning & clear success
# ---------------------------------------------------------------------------


@pytest.fixture
def workflow_alerter() -> WorkflowTelegramAlerter:
    """Enabled workflow alerter with fake credentials."""
    return WorkflowTelegramAlerter(
        bot_token="bot123:WORKFLOW",
        chat_id="-100CEO",
        enabled=True,
    )


class TestWorkflowContextWarning:
    async def test_context_warning_contains_header(self, workflow_alerter):
        captured: list[str] = []

        async def fake_send(text: str, parse_mode: str = "HTML") -> bool:
            captured.append(text)
            return True

        workflow_alerter._send = fake_send  # type: ignore[method-assign]
        await workflow_alerter.send_context_warning(stage="B", context_pct=62)
        assert "CONTEXT WARNING" in captured[0]

    async def test_context_warning_contains_stage_and_pct(self, workflow_alerter):
        captured: list[str] = []

        async def fake_send(text: str, parse_mode: str = "HTML") -> bool:
            captured.append(text)
            return True

        workflow_alerter._send = fake_send  # type: ignore[method-assign]
        await workflow_alerter.send_context_warning(stage="C", context_pct=75)
        assert "Stage C" in captured[0] or "C" in captured[0]
        assert "75%" in captured[0]


class TestWorkflowContextClearSuccess:
    async def test_clear_success_contains_header(self, workflow_alerter):
        captured: list[str] = []

        async def fake_send(text: str, parse_mode: str = "HTML") -> bool:
            captured.append(text)
            return True

        workflow_alerter._send = fake_send  # type: ignore[method-assign]
        await workflow_alerter.send_context_clear_success(stage="B", next_stage="C")
        assert "CONTEXT CLEARED" in captured[0]

    async def test_clear_success_contains_resume_info(self, workflow_alerter):
        captured: list[str] = []

        async def fake_send(text: str, parse_mode: str = "HTML") -> bool:
            captured.append(text)
            return True

        workflow_alerter._send = fake_send  # type: ignore[method-assign]
        await workflow_alerter.send_context_clear_success(stage="D", next_stage="E")
        assert "D" in captured[0]
        assert "E" in captured[0]
        assert "progress.json" in captured[0]
