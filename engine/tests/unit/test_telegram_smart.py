"""Tests for engine/src/infra/telegram_smart.py — US-213 SmartTelegramAlerter.

Covers: dedup via memory fallback, dedup via Redis mock, INFO batching,
flush_batch, buffer_size, non-INFO immediate send.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infra.telegram import AlertSeverity, TelegramAlerter
from src.infra.telegram_smart import SmartTelegramAlerter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_alerter() -> MagicMock:
    """Enabled TelegramAlerter mock."""
    alerter = MagicMock(spec=TelegramAlerter)
    alerter.send_alert = AsyncMock(return_value=True)
    alerter._send = AsyncMock(return_value=True)
    alerter._enabled = True
    alerter._bot_token = "bot123:TOKEN"
    alerter._chat_id = "-100123"
    return alerter


@pytest.fixture
def smart(mock_alerter: MagicMock) -> SmartTelegramAlerter:
    """SmartTelegramAlerter with no Redis (memory fallback)."""
    return SmartTelegramAlerter(
        alerter=mock_alerter,
        redis_client=None,
        dedup_ttl=300,
        batch_interval=1800,
    )


# ---------------------------------------------------------------------------
# Dedup — memory fallback
# ---------------------------------------------------------------------------


class TestMemoryDedup:
    async def test_first_message_not_deduped(self, smart: SmartTelegramAlerter) -> None:
        result = await smart.send_smart("hello", severity=AlertSeverity.WARNING, level="WARNING")
        assert result is True

    async def test_duplicate_message_is_skipped(self, smart: SmartTelegramAlerter) -> None:
        await smart.send_smart("duplicate", severity=AlertSeverity.WARNING, level="WARNING")
        result = await smart.send_smart("duplicate", severity=AlertSeverity.WARNING, level="WARNING")
        assert result is False

    async def test_different_messages_not_deduped(self, smart: SmartTelegramAlerter) -> None:
        await smart.send_smart("msg_a", severity=AlertSeverity.WARNING, level="WARNING")
        result = await smart.send_smart("msg_b", severity=AlertSeverity.WARNING, level="WARNING")
        assert result is True

    async def test_expired_dedup_allows_resend(self, smart: SmartTelegramAlerter) -> None:
        smart._dedup_ttl = 1  # 1 second TTL
        await smart.send_smart("expire_test", severity=AlertSeverity.WARNING, level="WARNING")

        # Manually expire the entry
        for k in list(smart._memory_dedup.keys()):
            smart._memory_dedup[k] = time.monotonic() - 2.0

        result = await smart.send_smart("expire_test", severity=AlertSeverity.WARNING, level="WARNING")
        assert result is True


# ---------------------------------------------------------------------------
# Dedup — Redis mock
# ---------------------------------------------------------------------------


class TestRedisDedup:
    async def test_redis_dedup_check_and_record(self, mock_alerter: MagicMock) -> None:
        mock_redis = MagicMock()
        mock_redis_conn = AsyncMock()
        mock_redis_conn.get = AsyncMock(return_value=None)  # not duplicate
        mock_redis_conn.setex = AsyncMock()
        mock_redis.redis = mock_redis_conn

        smart = SmartTelegramAlerter(alerter=mock_alerter, redis_client=mock_redis)
        result = await smart.send_smart("redis_msg", severity=AlertSeverity.CRITICAL, level="ERROR")
        assert result is True
        mock_redis_conn.setex.assert_awaited_once()

    async def test_redis_dedup_blocks_duplicate(self, mock_alerter: MagicMock) -> None:
        mock_redis = MagicMock()
        mock_redis_conn = AsyncMock()
        mock_redis_conn.get = AsyncMock(return_value=b"1")  # exists → duplicate
        mock_redis.redis = mock_redis_conn

        smart = SmartTelegramAlerter(alerter=mock_alerter, redis_client=mock_redis)
        result = await smart.send_smart("redis_dup", severity=AlertSeverity.CRITICAL, level="ERROR")
        assert result is False

    async def test_redis_failure_falls_back_to_memory(self, mock_alerter: MagicMock) -> None:
        mock_redis = MagicMock()
        mock_redis_conn = AsyncMock()
        mock_redis_conn.get = AsyncMock(side_effect=ConnectionError("Redis down"))
        mock_redis_conn.setex = AsyncMock(side_effect=ConnectionError("Redis down"))
        mock_redis.redis = mock_redis_conn

        smart = SmartTelegramAlerter(alerter=mock_alerter, redis_client=mock_redis)
        # First call should succeed (not in memory)
        result = await smart.send_smart("fallback", severity=AlertSeverity.WARNING, level="WARNING")
        assert result is True
        # Memory fallback should have recorded it
        assert len(smart._memory_dedup) == 1


# ---------------------------------------------------------------------------
# INFO batching
# ---------------------------------------------------------------------------


class TestInfoBatching:
    async def test_info_buffered_not_sent_immediately(
        self, smart: SmartTelegramAlerter, mock_alerter: MagicMock
    ) -> None:
        result = await smart.send_smart("info msg", severity=AlertSeverity.INFO)
        assert result is True  # buffered
        mock_alerter.send_alert.assert_not_awaited()

    async def test_buffer_size_increments(self, smart: SmartTelegramAlerter) -> None:
        assert smart.buffer_size == 0
        await smart.send_smart("a", severity=AlertSeverity.INFO)
        assert smart.buffer_size == 1
        await smart.send_smart("b", severity=AlertSeverity.INFO)
        assert smart.buffer_size == 2

    async def test_flush_batch_sends_combined_message(
        self, smart: SmartTelegramAlerter, mock_alerter: MagicMock
    ) -> None:
        await smart.send_smart("msg1", severity=AlertSeverity.INFO)
        await smart.send_smart("msg2", severity=AlertSeverity.INFO)
        result = await smart.flush_batch()
        assert result is True
        assert smart.buffer_size == 0
        # Check combined message was sent
        call_args = mock_alerter.send_alert.call_args
        sent_text = call_args[0][0]
        assert "2건" in sent_text
        assert "msg1" in sent_text
        assert "msg2" in sent_text

    async def test_flush_empty_buffer_returns_false(self, smart: SmartTelegramAlerter) -> None:
        result = await smart.flush_batch()
        assert result is False

    async def test_non_info_sent_immediately(
        self, smart: SmartTelegramAlerter, mock_alerter: MagicMock
    ) -> None:
        result = await smart.send_smart("urgent", severity=AlertSeverity.EMERGENCY, level="CRITICAL")
        assert result is True
        mock_alerter.send_alert.assert_awaited_once()
        assert smart.buffer_size == 0


# ---------------------------------------------------------------------------
# Stop / cleanup
# ---------------------------------------------------------------------------


class TestStopCleanup:
    async def test_stop_flushes_remaining_buffer(
        self, smart: SmartTelegramAlerter, mock_alerter: MagicMock
    ) -> None:
        await smart.send_smart("leftover", severity=AlertSeverity.INFO)
        assert smart.buffer_size == 1
        await smart.stop()
        assert smart.buffer_size == 0
        mock_alerter.send_alert.assert_awaited_once()


# ---------------------------------------------------------------------------
# Hash function
# ---------------------------------------------------------------------------


class TestHash:
    def test_same_message_same_hash(self) -> None:
        h1 = SmartTelegramAlerter._hash("test")
        h2 = SmartTelegramAlerter._hash("test")
        assert h1 == h2

    def test_different_message_different_hash(self) -> None:
        h1 = SmartTelegramAlerter._hash("aaa")
        h2 = SmartTelegramAlerter._hash("bbb")
        assert h1 != h2
