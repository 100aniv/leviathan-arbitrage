"""Path-B Day-3 — unit tests for DailyReconciliationReport.

Covers report assembly, variance decomposition, Telegram template
formatting, CSV schema stability, scheduler wiring, and delivery skip
behaviour when the day had zero trades.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.reconciliation.daily_report import (
    CSV_COLUMNS,
    DailyReconciliationReport,
    DailyReport,
    VarianceDecomp,
    run_daily_report_job,
)
from src.reconciliation.daily_report_scheduler import (
    start_daily_report_scheduler,
    stop_daily_report_scheduler,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def day_utc() -> date:
    return date(2026, 4, 20)


@pytest.fixture
def snapshot_stub() -> AsyncMock:
    stub = AsyncMock()
    stub.get_daily_attribution = AsyncMock(
        return_value={
            "per_strategy_pnl": {
                "funding_rate": Decimal("0.12"),
                "futures_futures": Decimal("-0.80"),
            },
            "balance_start_usd": Decimal("10.73"),
            "balance_end_usd": Decimal("10.53"),
            "commission_mismatch_usd": Decimal("-0.02"),
            "funding_mismatch_usd": Decimal("0.00"),
            "slippage_mismatch_usd": Decimal("-0.65"),
            "fx_mismatch_usd": Decimal("0.00"),
            "rollback_mismatch_usd": Decimal("-0.01"),
            "unattributed_usd": Decimal("0.00"),
        }
    )
    return stub


@pytest.fixture
def budget_stub() -> MagicMock:
    stub = MagicMock()
    stub.get_per_strategy_stats = MagicMock(
        return_value={
            "funding_rate": {"trades": 3, "wins": 2},
            "futures_futures": {"trades": 5, "wins": 2},
        }
    )
    return stub


@pytest.fixture
def pretrade_stub() -> MagicMock:
    stub = MagicMock()
    stub.get_rejection_counts = MagicMock(
        return_value={
            "SYMBOL_COOLDOWN": 48,
            "NOTIONAL_BUMP_EXCEEDS_RISK": 12,
            "UNIVERSE_MISS": 5,
            "MARGIN_INSUFFICIENT": 1,
        }
    )
    return stub


@pytest.fixture
def engine_stats_stub() -> MagicMock:
    stub = MagicMock()
    stub.get_daily_rollup = MagicMock(
        return_value={
            "divergence_events": 0,
            "circuit_breaker_trips": {"futures_futures": 0},
            "kill_switch_active_minutes": 0,
            "stranded_count": 0,
            "allocation_changes": [],
        }
    )
    return stub


@pytest.fixture
def telegram_stub() -> AsyncMock:
    stub = AsyncMock()
    stub._send = AsyncMock(return_value=True)
    return stub


@pytest.fixture
def reporter(
    snapshot_stub, budget_stub, pretrade_stub, engine_stats_stub,
    telegram_stub, tmp_path,
) -> DailyReconciliationReport:
    return DailyReconciliationReport(
        snapshot=snapshot_stub,
        budget_ledger=budget_stub,
        pre_trade_validator=pretrade_stub,
        engine_stats=engine_stats_stub,
        telegram=telegram_stub,
        csv_dir=tmp_path,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_assembles_report_from_dependencies(reporter, day_utc):
    report = await reporter.generate(day_utc)

    assert report.day_utc == day_utc
    assert report.per_strategy_pnl_verified == {
        "funding_rate": Decimal("0.12"),
        "futures_futures": Decimal("-0.80"),
    }
    assert report.per_strategy_trades_count == {
        "funding_rate": 3,
        "futures_futures": 5,
    }
    # Win rate = wins / trades
    assert report.per_strategy_win_rate["funding_rate"] == pytest.approx(2 / 3)
    assert report.per_strategy_win_rate["futures_futures"] == pytest.approx(2 / 5)
    assert report.exchange_account_balance_usd == Decimal("10.53")
    assert report.exchange_account_balance_prev_usd == Decimal("10.73")
    # Total PnL aggregates per-strategy
    assert report.total_pnl_verified == Decimal("0.12") + Decimal("-0.80")
    assert report.total_trades == 8


@pytest.mark.asyncio
async def test_variance_decomposition_matches_input(reporter, day_utc):
    report = await reporter.generate(day_utc)
    vd = report.variance_decomposition

    assert vd.commission_mismatch_usd == Decimal("-0.02")
    assert vd.funding_mismatch_usd == Decimal("0.00")
    assert vd.slippage_mismatch_usd == Decimal("-0.65")
    assert vd.fx_mismatch_usd == Decimal("0.00")
    assert vd.rollback_mismatch_usd == Decimal("-0.01")
    assert vd.unattributed_usd == Decimal("0.00")

    # Sum of all line items == total divergence
    expected_total = (
        Decimal("-0.02") + Decimal("0") + Decimal("-0.65")
        + Decimal("0") + Decimal("-0.01") + Decimal("0")
    )
    assert vd.total() == expected_total


@pytest.mark.asyncio
async def test_rejections_top10_sorted_and_capped(reporter, day_utc):
    report = await reporter.generate(day_utc)

    # Highest count first
    assert report.rejections_top10[0] == ("SYMBOL_COOLDOWN", 48)
    assert report.rejections_top10[1] == ("NOTIONAL_BUMP_EXCEEDS_RISK", 12)
    # Never more than 10 entries (current fixture has 4)
    assert len(report.rejections_top10) <= 10


@pytest.mark.asyncio
async def test_telegram_template_has_all_sections(reporter, day_utc):
    report = await reporter.generate(day_utc)
    text = reporter.format_telegram_message(report)

    # Header with date
    assert "2026-04-20" in text
    assert "LEVIATHAN Daily Reconciliation" in text
    # Per-strategy lines
    assert "funding_rate" in text
    assert "futures_futures" in text
    # Variance block
    assert "commission:" in text
    assert "unattributed:" in text
    # Rejections block
    assert "SYMBOL_COOLDOWN" in text
    # Account block
    assert "Binance" in text
    # No unescaped raw braces that would break Telegram HTML parse
    assert "{" not in text and "}" not in text


@pytest.mark.asyncio
async def test_telegram_delivery_calls_underlying_send(reporter, day_utc, telegram_stub):
    report = await reporter.generate(day_utc)
    ok = await reporter.deliver_telegram(report)

    assert ok is True
    telegram_stub._send.assert_awaited_once()
    sent_text = telegram_stub._send.await_args.args[0]
    assert "2026-04-20" in sent_text


@pytest.mark.asyncio
async def test_zero_trades_skips_telegram_entirely(
    day_utc, telegram_stub, tmp_path,
):
    # Empty snapshot/budget → 0 trades, 0 pnl
    empty_snap = AsyncMock()
    empty_snap.get_daily_attribution = AsyncMock(
        return_value={
            "per_strategy_pnl": {},
            "balance_start_usd": Decimal("10"),
            "balance_end_usd": Decimal("10"),
        }
    )
    empty_budget = MagicMock()
    empty_budget.get_per_strategy_stats = MagicMock(return_value={})

    reporter = DailyReconciliationReport(
        snapshot=empty_snap,
        budget_ledger=empty_budget,
        telegram=telegram_stub,
        csv_dir=tmp_path,
    )
    report = await reporter.generate(day_utc)
    ok = await reporter.deliver_telegram(report)

    assert report.total_trades == 0
    assert ok is False
    telegram_stub._send.assert_not_awaited()


def test_save_csv_writes_stable_schema(reporter, day_utc, tmp_path):
    report = DailyReport(
        day_utc=day_utc,
        per_strategy_pnl_verified={"funding_rate": Decimal("0.12")},
        per_strategy_trades_count={"funding_rate": 3},
        per_strategy_win_rate={"funding_rate": 0.667},
        variance_decomposition=VarianceDecomp(
            commission_mismatch_usd=Decimal("-0.02")
        ),
        rejections_top10=[("SYMBOL_COOLDOWN", 48)],
        exchange_account_balance_usd=Decimal("10.53"),
        exchange_account_balance_prev_usd=Decimal("10.73"),
        summary_text="test summary",
    )

    path = reporter.save_csv(report)

    assert path.exists()
    assert path.name == "20260420.csv"
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    # Column order + presence frozen
    assert list(rows[0].keys()) == list(CSV_COLUMNS)
    assert rows[0]["day_utc"] == "2026-04-20"
    assert rows[0]["total_trades"] == "3"
    assert rows[0]["commission_mismatch_usd"] == "-0.02"
    # JSON round-trip preserves data
    import json
    assert json.loads(rows[0]["per_strategy_pnl_json"]) == {"funding_rate": "0.12"}
    assert json.loads(rows[0]["rejections_top10_json"]) == [
        ["SYMBOL_COOLDOWN", 48]
    ]


def test_csv_columns_are_frozen_order():
    # Regression guard: downstream consumers rely on column order.
    expected_prefix = (
        "day_utc",
        "total_pnl_verified_usd",
        "total_trades",
    )
    assert CSV_COLUMNS[:3] == expected_prefix
    # 22 fields after all Day-3 sections
    assert len(CSV_COLUMNS) == 22


@pytest.mark.asyncio
async def test_summary_text_tone_detection(reporter):
    # Loss day
    losing = DailyReport(
        day_utc=date(2026, 4, 20),
        per_strategy_pnl_verified={"x": Decimal("-5.00")},
        per_strategy_trades_count={"x": 2},
    )
    summary = reporter._build_summary(losing)
    assert "loss" in summary
    assert "-5.00" in summary
    # Good day
    good = DailyReport(
        day_utc=date(2026, 4, 20),
        per_strategy_pnl_verified={"x": Decimal("1.00")},
        per_strategy_trades_count={"x": 2},
    )
    summary = reporter._build_summary(good)
    assert "good" in summary
    # Flat day
    flat = DailyReport(day_utc=date(2026, 4, 20))
    assert "flat" in reporter._build_summary(flat)


@pytest.mark.asyncio
async def test_email_skipped_when_smtp_not_configured(
    reporter, day_utc, monkeypatch,
):
    # Ensure no SMTP env vars present
    for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "SMTP_TO"):
        monkeypatch.delenv(k, raising=False)
    report = await reporter.generate(day_utc)
    ok = await reporter.deliver_email(report)
    assert ok is False


@pytest.mark.asyncio
async def test_run_daily_report_job_full_cycle(reporter, day_utc):
    # Runs generate + csv + telegram + email in one shot.
    report = await run_daily_report_job(reporter, day_utc=day_utc)
    assert isinstance(report, DailyReport)
    assert report.day_utc == day_utc
    # CSV was written to reporter's csv_dir
    csv_path = reporter._csv_dir / f"{day_utc.strftime('%Y%m%d')}.csv"
    assert csv_path.exists()


def test_scheduler_registers_utc_0005_cron(reporter):
    """Scheduler fires at UTC 00:05 — verified via trigger inspection."""
    pytest.importorskip("apscheduler")
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    # Pass a pre-built scheduler so start() is not called automatically —
    # the test just inspects the registered trigger.
    pre_built = AsyncIOScheduler()
    sched = start_daily_report_scheduler(reporter, scheduler=pre_built)
    assert sched is pre_built
    try:
        job = sched.get_job("leviathan_daily_recon_report")
        assert job is not None
        trig = job.trigger
        # CronTrigger exposes fields via .fields (list of BaseField)
        field_map = {f.name: str(f) for f in trig.fields}
        assert field_map["hour"] == "0"
        assert field_map["minute"] == "5"
        assert str(trig.timezone) == "UTC"
    finally:
        # pre_built was never started, stop is a no-op
        stop_daily_report_scheduler(sched)


def test_scheduler_stop_is_safe_on_none():
    # Does not raise when scheduler was never started (APScheduler missing).
    stop_daily_report_scheduler(None)


@pytest.mark.asyncio
async def test_generate_degrades_gracefully_without_snapshot(
    budget_stub, pretrade_stub, engine_stats_stub, tmp_path, day_utc,
):
    # No snapshot injected — report still assembles with empty PnL section.
    reporter = DailyReconciliationReport(
        budget_ledger=budget_stub,
        pre_trade_validator=pretrade_stub,
        engine_stats=engine_stats_stub,
        csv_dir=tmp_path,
    )
    report = await reporter.generate(day_utc)
    assert report.per_strategy_pnl_verified == {}
    assert report.per_strategy_trades_count == {
        "funding_rate": 3,
        "futures_futures": 5,
    }
    # Variance block stays at zero defaults
    assert report.variance_decomposition.total() == Decimal("0")
