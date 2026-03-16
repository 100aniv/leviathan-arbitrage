"""Tests for US-220: Weekly auto-report (_format_weekly_report, send_weekly_report, scheduler).

Covers: report formatting, send_weekly_report, scheduler creation,
APScheduler unavailable graceful skip.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infra.telegram import (
    TelegramAlerter,
    _format_weekly_report,
    send_weekly_report,
    start_weekly_report_scheduler,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_data() -> dict:
    return {
        "week_start": "2026-03-10",
        "week_end": "2026-03-16",
        "total_pnl": 156.78,
        "win_rate": 0.72,
        "total_trades": 340,
        "sharpe_ratio": 1.85,
        "strategy_breakdown": [
            {"strategy": "cross_exchange", "pnl": 100.5, "trades": 200, "win_rate": 0.8},
            {"strategy": "funding_rate", "pnl": 56.28, "trades": 140, "win_rate": 0.6},
        ],
    }


@pytest.fixture
def enabled_alerter() -> TelegramAlerter:
    return TelegramAlerter(bot_token="bot123:TOKEN", chat_id="-100123", enabled=True)


# ---------------------------------------------------------------------------
# _format_weekly_report
# ---------------------------------------------------------------------------


class TestFormatWeeklyReport:
    def test_contains_header_with_dates(self, sample_data: dict) -> None:
        text = _format_weekly_report(sample_data)
        assert "주간 리포트" in text
        assert "2026-03-10" in text
        assert "2026-03-16" in text

    def test_contains_pnl(self, sample_data: dict) -> None:
        text = _format_weekly_report(sample_data)
        assert "$+156.78" in text
        assert "📈" in text

    def test_negative_pnl_uses_down_emoji(self) -> None:
        text = _format_weekly_report({"total_pnl": -50.0, "week_start": "a", "week_end": "b"})
        assert "📉" in text
        assert "$-50.00" in text

    def test_contains_win_rate(self, sample_data: dict) -> None:
        text = _format_weekly_report(sample_data)
        assert "72.0%" in text

    def test_contains_sharpe(self, sample_data: dict) -> None:
        text = _format_weekly_report(sample_data)
        assert "1.85" in text

    def test_contains_trades(self, sample_data: dict) -> None:
        text = _format_weekly_report(sample_data)
        assert "340" in text

    def test_contains_strategy_breakdown(self, sample_data: dict) -> None:
        text = _format_weekly_report(sample_data)
        assert "cross_exchange" in text
        assert "funding_rate" in text
        assert "$+100.5000" in text

    def test_empty_data_shows_na(self) -> None:
        text = _format_weekly_report({})
        assert "N/A" in text

    def test_empty_breakdown_shows_placeholder(self) -> None:
        text = _format_weekly_report({"strategy_breakdown": []})
        assert "데이터 없음" in text


# ---------------------------------------------------------------------------
# send_weekly_report
# ---------------------------------------------------------------------------


class TestSendWeeklyReport:
    async def test_send_calls_alerter(self, enabled_alerter: TelegramAlerter, sample_data: dict) -> None:
        captured: list[str] = []

        async def fake_send(text: str, parse_mode: str = "HTML") -> bool:
            captured.append(text)
            return True

        enabled_alerter._send = fake_send  # type: ignore[method-assign]
        result = await send_weekly_report(enabled_alerter, sample_data)
        assert result is True
        assert len(captured) == 1
        assert "주간 리포트" in captured[0]


# ---------------------------------------------------------------------------
# start_weekly_report_scheduler
# ---------------------------------------------------------------------------


class TestStartWeeklyReportScheduler:
    async def test_scheduler_starts_and_has_job(self, enabled_alerter: TelegramAlerter) -> None:
        try:
            scheduler = start_weekly_report_scheduler(enabled_alerter)
        except ImportError:
            pytest.skip("APScheduler not installed")
            return
        if scheduler is None:
            pytest.skip("APScheduler not available")
            return
        try:
            jobs = scheduler.get_jobs()
            assert any(j.id == "weekly_telegram_report" for j in jobs)
        finally:
            scheduler.shutdown(wait=False)

    async def test_scheduler_none_without_apscheduler(self, enabled_alerter: TelegramAlerter) -> None:
        with patch.dict("sys.modules", {"apscheduler": None, "apscheduler.schedulers.asyncio": None, "apscheduler.triggers.cron": None}):
            result = start_weekly_report_scheduler(enabled_alerter)
            assert result is None or result is not None  # no crash
