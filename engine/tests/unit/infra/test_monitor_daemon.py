"""Tests for engine/src/infra/monitor_daemon.py (MonitorDaemon).

Covers: init defaults, custom interval_sec/failure_threshold, check_redis
(success/failure), check_timescaledb (success/failure), check_engine
(success/failure via httpx mock), check_all (all success / partial failure),
_handle_failure (below/at threshold), _handle_recovery (with/without previous
failure at threshold), run (one cycle + sleep mock).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infra.monitor_daemon import MonitorDaemon


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _daemon(interval_sec: int = 300, failure_threshold: int = 3) -> MonitorDaemon:
    """Create a MonitorDaemon with a mocked TelegramAlerter."""
    d = MonitorDaemon(interval_sec=interval_sec, failure_threshold=failure_threshold)
    d.alerter = MagicMock()
    d.alerter.send_alert = AsyncMock(return_value=True)
    return d


# ---------------------------------------------------------------------------
# 1-2. Initialisation
# ---------------------------------------------------------------------------


class TestMonitorDaemonInit:
    def test_default_initialization_stores_interval_and_threshold(self):
        """MonitorDaemon() with defaults stores interval=300, threshold=3."""
        daemon = MonitorDaemon()
        assert daemon.interval == 300
        assert daemon.threshold == 3

    def test_custom_interval_sec_and_failure_threshold_are_stored(self):
        """MonitorDaemon(interval_sec=10, failure_threshold=2) persists both values."""
        daemon = MonitorDaemon(interval_sec=10, failure_threshold=2)
        assert daemon.interval == 10
        assert daemon.threshold == 2


# ---------------------------------------------------------------------------
# 3-4. check_redis
# ---------------------------------------------------------------------------


class TestCheckRedis:
    async def test_check_redis_returns_true_when_ping_succeeds(self):
        daemon = _daemon()
        mock_client = AsyncMock()
        mock_client.ping = AsyncMock(return_value=True)
        mock_client.aclose = AsyncMock()

        with patch("src.infra.monitor_daemon.aioredis.from_url", return_value=mock_client):
            result = await daemon.check_redis()

        assert result is True

    async def test_check_redis_returns_false_when_connection_raises(self):
        daemon = _daemon()
        with patch(
            "src.infra.monitor_daemon.aioredis.from_url",
            side_effect=OSError("refused"),
        ):
            result = await daemon.check_redis()

        assert result is False


# ---------------------------------------------------------------------------
# 5-6. check_timescaledb
# ---------------------------------------------------------------------------


class TestCheckTimescaleDB:
    async def test_check_timescaledb_returns_true_on_successful_select(self):
        daemon = _daemon()
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=1)
        mock_conn.close = AsyncMock()

        with patch(
            "src.infra.monitor_daemon.asyncpg.connect",
            new_callable=AsyncMock,
            return_value=mock_conn,
        ):
            result = await daemon.check_timescaledb()

        assert result is True

    async def test_check_timescaledb_returns_false_when_connect_raises(self):
        daemon = _daemon()
        with patch(
            "src.infra.monitor_daemon.asyncpg.connect",
            new_callable=AsyncMock,
            side_effect=Exception("DB unreachable"),
        ):
            result = await daemon.check_timescaledb()

        assert result is False


# ---------------------------------------------------------------------------
# 7-8. check_engine
# ---------------------------------------------------------------------------


class TestCheckEngine:
    async def test_check_engine_returns_true_on_200_response(self):
        daemon = _daemon()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.infra.monitor_daemon.httpx.AsyncClient", return_value=mock_client):
            result = await daemon.check_engine()

        assert result is True

    async def test_check_engine_returns_false_when_request_raises(self):
        daemon = _daemon()

        import httpx as _httpx

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=_httpx.ConnectError("engine unreachable")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.infra.monitor_daemon.httpx.AsyncClient", return_value=mock_client):
            result = await daemon.check_engine()

        assert result is False


# ---------------------------------------------------------------------------
# 9-10. check_all
# ---------------------------------------------------------------------------


class TestCheckAll:
    async def test_check_all_returns_all_true_when_all_services_healthy(self):
        daemon = _daemon()
        daemon.check_redis = AsyncMock(return_value=True)
        daemon.check_timescaledb = AsyncMock(return_value=True)
        daemon.check_engine = AsyncMock(return_value=True)
        daemon._handle_failure = AsyncMock()
        daemon._handle_recovery = AsyncMock()

        result = await daemon.check_all()

        assert result == {"redis": True, "timescaledb": True, "engine": True}

    async def test_check_all_returns_false_for_failing_redis_service(self):
        daemon = _daemon()
        daemon.check_redis = AsyncMock(return_value=False)
        daemon.check_timescaledb = AsyncMock(return_value=True)
        daemon.check_engine = AsyncMock(return_value=True)
        daemon._handle_failure = AsyncMock()
        daemon._handle_recovery = AsyncMock()

        result = await daemon.check_all()

        assert result["redis"] is False
        assert result["timescaledb"] is True
        assert result["engine"] is True


# ---------------------------------------------------------------------------
# 11-12. _handle_failure
# ---------------------------------------------------------------------------


class TestHandleFailure:
    async def test_no_telegram_alert_when_count_below_threshold(self):
        """First failure (count=1) with threshold=3 must NOT send alert."""
        daemon = _daemon(failure_threshold=3)
        await daemon._handle_failure("redis", "ping timeout")

        daemon.alerter.send_alert.assert_not_called()

    async def test_telegram_alert_sent_when_failure_count_reaches_threshold(self):
        """After 3 consecutive failures (threshold=3) alert must fire."""
        daemon = _daemon(failure_threshold=3)
        for _ in range(3):
            await daemon._handle_failure("redis", "ping timeout")

        daemon.alerter.send_alert.assert_called_once()
        args, kwargs = daemon.alerter.send_alert.call_args
        message = args[0] if args else kwargs.get("message", "")
        assert "redis" in message.lower()


# ---------------------------------------------------------------------------
# 13-14. _handle_recovery
# ---------------------------------------------------------------------------


class TestHandleRecovery:
    async def test_recovery_alert_sent_when_failure_count_was_at_threshold(self):
        """Service was failing at threshold level — recovery must alert."""
        daemon = _daemon(failure_threshold=3)
        daemon.failure_counts["engine"] = 3  # pre-seed: at threshold

        await daemon._handle_recovery("engine")

        daemon.alerter.send_alert.assert_called_once()
        args, kwargs = daemon.alerter.send_alert.call_args
        message = args[0] if args else kwargs.get("message", "")
        assert "engine" in message.lower()

    async def test_no_alert_when_service_had_no_previous_failure(self):
        """No prior failures recorded → recovery must NOT fire an alert."""
        daemon = _daemon(failure_threshold=3)
        # failure_counts["engine"] == 0 (default)

        await daemon._handle_recovery("engine")

        daemon.alerter.send_alert.assert_not_called()

    async def test_failure_count_reset_to_zero_after_recovery(self):
        """Regardless of alert, failure_counts must be zeroed on recovery."""
        daemon = _daemon(failure_threshold=3)
        daemon.failure_counts["redis"] = 5

        await daemon._handle_recovery("redis")

        assert daemon.failure_counts.get("redis", 0) == 0


# ---------------------------------------------------------------------------
# 15. run — one cycle
# ---------------------------------------------------------------------------


class TestRun:
    async def test_run_invokes_check_all_then_sleeps_for_interval(self):
        """run() must call check_all() then asyncio.sleep(interval) in order."""
        daemon = _daemon(interval_sec=30)
        call_log: list[str] = []

        async def fake_check_all() -> dict[str, bool]:
            call_log.append("check_all")
            return {"redis": True, "timescaledb": True, "engine": True}

        async def fake_sleep(seconds: float) -> None:
            call_log.append(f"sleep:{seconds}")
            raise asyncio.CancelledError  # stop the infinite loop

        daemon.check_all = fake_check_all  # type: ignore[method-assign]

        with patch("src.infra.monitor_daemon.asyncio.sleep", side_effect=fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await daemon.run()

        assert call_log[0] == "check_all"
        assert call_log[1] == "sleep:30"
