"""Path-B Day-3 — APScheduler hook for the daily reconciliation report.

Fires ``run_daily_report_job(reporter)`` once per day at UTC 00:05 so the
operator has a fresh morning view of the prior day (complete UTC window:
``day_utc = yesterday``).

Design notes:

- 00:05 (not 00:00) gives the last trades of the prior day time to settle
  on the exchange side — Binance income endpoints lag the clock by 1–2s
  during high activity.
- The job body is *defensive*: any exception is caught + logged so the next
  day's run is never blocked by a transient failure.
- APScheduler is an optional dependency: when unavailable we log + return
  ``None`` rather than crashing the engine (same pattern as the existing
  weekly-report scheduler in ``src.infra.telegram``).
- Engine lifecycle integration is intentionally tiny — 3 lines in
  ``src.main`` are enough (start on engine up, stop on engine down).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import structlog

from src.reconciliation.daily_report import (
    DailyReconciliationReport,
    run_daily_report_job,
)

logger = structlog.get_logger(__name__)


_DAILY_REPORT_JOB_ID = "leviathan_daily_recon_report"


def start_daily_report_scheduler(
    reporter: DailyReconciliationReport,
    *,
    scheduler: Any | None = None,
    hour: int = 0,
    minute: int = 5,
) -> Any | None:
    """Register the UTC 00:05 cron job.

    Args:
        reporter: Configured :class:`DailyReconciliationReport` with all
            dependencies injected.
        scheduler: Optional pre-built ``AsyncIOScheduler`` for composition
            with other cron jobs (e.g. weekly report). When ``None`` a
            dedicated scheduler is created + started.
        hour: UTC hour (default 0). Override in tests via mock clock.
        minute: UTC minute (default 5).

    Returns:
        The scheduler instance (so callers can ``shutdown()`` it), or
        ``None`` when APScheduler is not installed.
    """
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning("daily_report_scheduler.apscheduler_not_available")
        return None

    sched = scheduler or AsyncIOScheduler()

    async def _job() -> None:
        try:
            # day_utc = the day that just ended in UTC (yesterday at 00:05).
            day_utc: date = (
                datetime.now(timezone.utc) - timedelta(days=1)
            ).date()
            await run_daily_report_job(reporter, day_utc=day_utc)
        except Exception:
            logger.error(
                "daily_report_scheduler.job_failed", exc_info=True,
            )

    trigger = CronTrigger(hour=hour, minute=minute, timezone="UTC")
    sched.add_job(_job, trigger, id=_DAILY_REPORT_JOB_ID, replace_existing=True)
    if scheduler is None and not sched.running:
        sched.start()
    logger.info(
        "daily_report_scheduler.started",
        schedule=f"UTC {hour:02d}:{minute:02d} daily",
    )
    return sched


def stop_daily_report_scheduler(scheduler: Any | None) -> None:
    """Tear-down helper invoked from the engine shutdown path.

    No-op when ``scheduler`` is ``None`` (APScheduler wasn't available at
    start time) so callers never have to null-check.
    """
    if scheduler is None:
        return
    try:
        if getattr(scheduler, "running", False):
            scheduler.shutdown(wait=False)
        logger.info("daily_report_scheduler.stopped")
    except Exception:
        logger.error(
            "daily_report_scheduler.stop_failed", exc_info=True,
        )
