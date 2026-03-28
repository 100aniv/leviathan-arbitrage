"""Tests for US-146: ScheduledTuner wiring into Engine via ENABLE_INLINE_TUNER.

Verifies:
- ScheduledTuner.start_scheduler() creates an AsyncIOScheduler and starts it
- ScheduledTuner.stop() shuts down scheduler gracefully
- ENABLE_INLINE_TUNER guard activates/deactivates tuner in Engine._init_tuner()
- 'Scheduled tuner started' log emitted on successful init

Run:
    cd engine && python -m pytest tests/test_scheduled_tuner_wiring.py -x --tb=short -v
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from src.tuning.scheduled_tuner import ScheduledTuner


# ===========================================================================
# ScheduledTuner.start_scheduler() unit tests
# ===========================================================================


def _patch_apscheduler(mock_scheduler: MagicMock):
    """Context manager that injects a mock AsyncIOScheduler into the module namespace.

    Works whether or not apscheduler is actually installed (create=True allows
    patching attributes that don't yet exist on the module).
    """
    return (
        patch("src.tuning.scheduled_tuner._APSCHEDULER_AVAILABLE", True),
        patch("src.tuning.scheduled_tuner.AsyncIOScheduler",
              return_value=mock_scheduler, create=True),
    )


class TestScheduledTunerStartScheduler:
    """start_scheduler() creates and starts an AsyncIOScheduler."""

    def test_start_scheduler_creates_scheduler_instance(self):
        """start_scheduler creates an AsyncIOScheduler assigned to self._scheduler."""
        mock_scheduler = MagicMock()
        tuner = ScheduledTuner(strategies=["cross_exchange"])

        avail_patch, sched_patch = _patch_apscheduler(mock_scheduler)
        with avail_patch, sched_patch:
            tuner.start_scheduler()

        assert tuner._scheduler is mock_scheduler

    def test_start_scheduler_adds_weekly_cron_job(self):
        """start_scheduler registers a weekly Sunday 02:00 UTC cron job."""
        mock_scheduler = MagicMock()
        tuner = ScheduledTuner(strategies=["cross_exchange"])

        avail_patch, sched_patch = _patch_apscheduler(mock_scheduler)
        with avail_patch, sched_patch:
            tuner.start_scheduler()

        # 2 jobs: weekly cron + initial 5-min delayed run (SIT-3 P0-A)
        assert mock_scheduler.add_job.call_count == 2
        cron_call = mock_scheduler.add_job.call_args_list[0]
        assert cron_call[1].get("day_of_week") == "sun"
        assert cron_call[1].get("hour") == 2

    def test_start_scheduler_calls_start(self):
        """start_scheduler calls scheduler.start()."""
        mock_scheduler = MagicMock()
        tuner = ScheduledTuner(strategies=["cross_exchange"])

        avail_patch, sched_patch = _patch_apscheduler(mock_scheduler)
        with avail_patch, sched_patch:
            tuner.start_scheduler()

        mock_scheduler.start.assert_called_once()

    def test_start_scheduler_logs_started_message(self, caplog):
        """start_scheduler emits 'Auto-tuner scheduler started' log."""
        mock_scheduler = MagicMock()
        tuner = ScheduledTuner(strategies=["cross_exchange"])

        avail_patch, sched_patch = _patch_apscheduler(mock_scheduler)
        with avail_patch, sched_patch, \
             caplog.at_level(logging.INFO, logger="src.tuning.scheduled_tuner"):
            tuner.start_scheduler()

        assert any("scheduler started" in r.message.lower() for r in caplog.records)

    def test_start_scheduler_noop_when_apscheduler_unavailable(self, caplog):
        """start_scheduler logs error and skips when apscheduler not installed."""
        tuner = ScheduledTuner(strategies=["cross_exchange"])

        with patch("src.tuning.scheduled_tuner._APSCHEDULER_AVAILABLE", False), \
             caplog.at_level(logging.ERROR, logger="src.tuning.scheduled_tuner"):
            tuner.start_scheduler()

        assert tuner._scheduler is None


# ===========================================================================
# ScheduledTuner.stop() unit tests
# ===========================================================================


class TestScheduledTunerStop:
    """stop() shuts down the scheduler gracefully."""

    def test_stop_calls_shutdown_on_scheduler(self):
        """stop() calls scheduler.shutdown(wait=False)."""
        mock_scheduler = MagicMock()
        tuner = ScheduledTuner(strategies=["cross_exchange"])
        tuner._scheduler = mock_scheduler

        tuner.stop()

        mock_scheduler.shutdown.assert_called_once_with(wait=False)

    def test_stop_is_noop_when_no_scheduler(self):
        """stop() does nothing when scheduler was never started."""
        tuner = ScheduledTuner(strategies=["cross_exchange"])
        assert tuner._scheduler is None
        # Should not raise
        tuner.stop()

    def test_stop_swallows_shutdown_exceptions(self, caplog):
        """stop() logs warning and continues if shutdown raises."""
        mock_scheduler = MagicMock()
        mock_scheduler.shutdown.side_effect = RuntimeError("already stopped")
        tuner = ScheduledTuner(strategies=["cross_exchange"])
        tuner._scheduler = mock_scheduler

        with caplog.at_level(logging.WARNING, logger="src.tuning.scheduled_tuner"):
            tuner.stop()  # Must not raise

        assert any("shutdown error" in r.message.lower() for r in caplog.records)


# ===========================================================================
# Engine._init_tuner() wiring tests (US-146)
# ===========================================================================


class TestEngineInitTunerWiring:
    """Engine._init_tuner() respects ENABLE_INLINE_TUNER env var."""

    @pytest.mark.asyncio
    async def test_init_tuner_starts_scheduler_when_enabled(self, monkeypatch):
        """_init_tuner creates ScheduledTuner and calls start_scheduler when enabled."""
        monkeypatch.setenv("ENABLE_INLINE_TUNER", "true")
        mock_tuner = MagicMock()

        with patch("src.main._HAS_TUNER", True), \
             patch("src.main.ScheduledTuner", return_value=mock_tuner):
            from src.main import Engine
            engine = Engine()
            await engine._init_tuner()

        mock_tuner.start_scheduler.assert_called_once()
        assert engine._scheduled_tuner is mock_tuner

    @pytest.mark.asyncio
    async def test_init_tuner_disabled_when_env_not_set(self, monkeypatch):
        """_init_tuner skips tuner creation when ENABLE_INLINE_TUNER is absent."""
        monkeypatch.delenv("ENABLE_INLINE_TUNER", raising=False)

        with patch("src.main._HAS_TUNER", True), \
             patch("src.main.ScheduledTuner") as mock_cls:
            from src.main import Engine
            engine = Engine()
            await engine._init_tuner()

        mock_cls.assert_not_called()
        assert engine._scheduled_tuner is None

    @pytest.mark.asyncio
    async def test_init_tuner_logs_started_message(self, monkeypatch, caplog):
        """_init_tuner emits 'Scheduled tuner started' log on success."""
        monkeypatch.setenv("ENABLE_INLINE_TUNER", "1")
        mock_tuner = MagicMock()

        with patch("src.main._HAS_TUNER", True), \
             patch("src.main.ScheduledTuner", return_value=mock_tuner), \
             caplog.at_level(logging.INFO, logger="src.main"):
            from src.main import Engine
            engine = Engine()
            await engine._init_tuner()

        assert any("scheduled tuner started" in r.message.lower() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_engine_stop_calls_tuner_stop(self, monkeypatch):
        """Engine.stop() calls _scheduled_tuner.stop() when tuner is active."""
        monkeypatch.setenv("ENABLE_INLINE_TUNER", "true")
        mock_tuner = MagicMock()

        with patch("src.main._HAS_TUNER", True), \
             patch("src.main.ScheduledTuner", return_value=mock_tuner):
            from src.main import Engine
            engine = Engine()
            engine._scheduled_tuner = mock_tuner
            engine.state.running = True  # required for stop() to proceed
            await engine.stop()

        mock_tuner.stop.assert_called_once()
