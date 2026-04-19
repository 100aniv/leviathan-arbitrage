"""Unit tests for :class:`StrategyBudgetLedger` (Path-B Day-3)."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from src.risk.strategy_budget_ledger import (
    DEFAULT_BUDGET_PCT,
    StrategyBudget,
    StrategyBudgetLedger,
    UNCATEGORIZED_ID,
    _floor_utc_day,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strategies() -> list[str]:
    return ["funding_rate", "cross_exchange", "futures_futures"]


def _capital() -> dict[str, Decimal]:
    # 200 USD allocated → 2% = 4.0 USD daily loss budget.
    return {
        "funding_rate": Decimal("200"),
        "cross_exchange": Decimal("100"),
        "futures_futures": Decimal("50"),
    }


def _frozen_now(ts: datetime):
    return lambda: ts


async def _make_ledger(tmp_path: Path, now: datetime, **kwargs: Any) -> StrategyBudgetLedger:
    ledger = StrategyBudgetLedger(
        strategy_ids=_strategies(),
        allocated_capital_usd=_capital(),
        fallback_dir=tmp_path,
        now_fn=_frozen_now(now),
        **kwargs,
    )
    await ledger.start()
    return ledger


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_initialises_full_capacity(tmp_path: Path) -> None:
    now = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)
    ledger = await _make_ledger(tmp_path, now)
    status = ledger.get_status()
    assert status["funding_rate"].daily_loss_budget_usd == Decimal("4.0000")
    assert status["cross_exchange"].daily_loss_budget_usd == Decimal("2.0000")
    assert status["futures_futures"].daily_loss_budget_usd == Decimal("1.0000")
    # Uncategorized bucket included with zero capital.
    assert UNCATEGORIZED_ID in status
    assert status[UNCATEGORIZED_ID].daily_loss_budget_usd == Decimal("0.0000")
    # reset_ts floors to UTC day start.
    assert status["funding_rate"].reset_ts_utc == datetime(
        2026, 4, 19, tzinfo=timezone.utc
    )


@pytest.mark.asyncio
async def test_update_pnl_negative_decreases_balance(tmp_path: Path) -> None:
    now = datetime(2026, 4, 19, 9, 0, tzinfo=timezone.utc)
    ledger = await _make_ledger(tmp_path, now)
    bud = await ledger.update_pnl("funding_rate", Decimal("-0.5"))
    assert bud.daily_pnl_balance_usd == Decimal("-0.5")
    assert bud.is_halted is False
    assert bud.remaining_usd() == Decimal("3.5000")


@pytest.mark.asyncio
async def test_check_remaining_toggles_at_threshold(tmp_path: Path) -> None:
    now = datetime(2026, 4, 19, 9, 0, tzinfo=timezone.utc)
    ledger = await _make_ledger(tmp_path, now)
    assert await ledger.check_remaining("funding_rate") is True
    # Consume 3.99 of 4.00 → still OK.
    await ledger.update_pnl("funding_rate", Decimal("-3.99"))
    assert await ledger.check_remaining("funding_rate") is True
    # Worst-case prospective loss 0.02 pushes total past 4.00 → False.
    assert (
        await ledger.check_remaining("funding_rate", worst_case_loss_usd=Decimal("0.02"))
        is False
    )


@pytest.mark.asyncio
async def test_strategy_halt_isolates_from_peers(tmp_path: Path) -> None:
    now = datetime(2026, 4, 19, 9, 0, tzinfo=timezone.utc)
    ledger = await _make_ledger(tmp_path, now)
    # Blow the funding_rate budget.
    await ledger.update_pnl("funding_rate", Decimal("-10"))
    assert ledger.is_strategy_halted("funding_rate") is True
    assert ledger.is_strategy_halted("cross_exchange") is False
    # cross_exchange can still take trades.
    assert await ledger.check_remaining("cross_exchange") is True


@pytest.mark.asyncio
async def test_reset_daily_restores_budget_and_clears_halt(tmp_path: Path) -> None:
    day0 = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)
    ledger = await _make_ledger(tmp_path, day0)
    await ledger.update_pnl("funding_rate", Decimal("-10"))
    assert ledger.is_strategy_halted("funding_rate") is True

    day1 = datetime(2026, 4, 20, 0, 0, tzinfo=timezone.utc)
    await ledger.reset_daily(day1)
    status = ledger.get_status()
    assert status["funding_rate"].is_halted is False
    assert status["funding_rate"].daily_pnl_balance_usd == Decimal("0")
    assert status["funding_rate"].reset_ts_utc == day1


@pytest.mark.asyncio
async def test_auto_roll_on_midnight_crossing(tmp_path: Path) -> None:
    day0 = datetime(2026, 4, 19, 23, 59, tzinfo=timezone.utc)
    ledger = StrategyBudgetLedger(
        strategy_ids=_strategies(),
        allocated_capital_usd=_capital(),
        fallback_dir=tmp_path,
        now_fn=_frozen_now(day0),
    )
    await ledger.start()
    await ledger.update_pnl("funding_rate", Decimal("-4.5"))  # halt
    assert ledger.is_strategy_halted("funding_rate") is True

    # Advance clock past UTC midnight — next update should auto-roll.
    day1 = datetime(2026, 4, 20, 0, 1, tzinfo=timezone.utc)
    ledger._now_fn = _frozen_now(day1)  # type: ignore[attr-defined]
    await ledger.update_pnl("cross_exchange", Decimal("-0.1"))
    status = ledger.get_status()
    # funding_rate halt cleared and balance reset.
    assert status["funding_rate"].is_halted is False
    assert status["funding_rate"].daily_pnl_balance_usd == Decimal("0")
    # cross_exchange reflects the fresh-day debit only.
    assert status["cross_exchange"].daily_pnl_balance_usd == Decimal("-0.1")


@pytest.mark.asyncio
async def test_restart_loads_prior_state_from_json(tmp_path: Path) -> None:
    now = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)
    ledger = await _make_ledger(tmp_path, now)
    await ledger.update_pnl("funding_rate", Decimal("-2.75"))

    # Fresh ledger instance pointed at same fallback dir must see balance.
    ledger2 = StrategyBudgetLedger(
        strategy_ids=_strategies(),
        allocated_capital_usd=_capital(),
        fallback_dir=tmp_path,
        now_fn=_frozen_now(now),
    )
    await ledger2.start()
    status = ledger2.get_status()
    assert status["funding_rate"].daily_pnl_balance_usd == Decimal("-2.75")


@pytest.mark.asyncio
async def test_db_unavailable_falls_back_to_json(tmp_path: Path) -> None:
    now = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)

    class _BrokenPool:
        def acquire(self):  # noqa: D401
            raise RuntimeError("db down")

    ledger = StrategyBudgetLedger(
        strategy_ids=_strategies(),
        allocated_capital_usd=_capital(),
        db_pool=_BrokenPool(),
        fallback_dir=tmp_path,
        now_fn=_frozen_now(now),
    )
    await ledger.start()
    await ledger.update_pnl("funding_rate", Decimal("-1.5"))
    # Fallback JSON exists and contains the write.
    path = tmp_path / f"{now.strftime('%Y%m%d')}.json"
    assert path.exists()
    payload = json.loads(path.read_text())
    assert Decimal(payload["funding_rate"]["daily_pnl_balance_usd"]) == Decimal("-1.5")


@pytest.mark.asyncio
async def test_concurrent_updates_serialised(tmp_path: Path) -> None:
    now = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)
    ledger = await _make_ledger(tmp_path, now)
    # Budget is 4.0 — schedule 40 concurrent 0.05 debits (total 2.0).
    tasks = [ledger.update_pnl("funding_rate", Decimal("-0.05")) for _ in range(40)]
    await asyncio.gather(*tasks)
    status = ledger.get_status()
    assert status["funding_rate"].daily_pnl_balance_usd == Decimal("-2.00")
    assert status["funding_rate"].is_halted is False


@pytest.mark.asyncio
async def test_income_event_attribution_via_explicit_strategy_id(tmp_path: Path) -> None:
    now = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)
    ledger = await _make_ledger(tmp_path, now)
    await ledger.on_exchange_income_event({
        "exchange": "binance_futures",
        "income_type": "REALIZED_PNL",
        "symbol": "BTCUSDT",
        "amount_usdt": -0.75,
        "tran_id": "t1",
        "ts_ms": int(now.timestamp() * 1000),
        "strategy_id": "cross_exchange",
    })
    status = ledger.get_status()
    assert status["cross_exchange"].daily_pnl_balance_usd == Decimal("-0.75")


@pytest.mark.asyncio
async def test_income_event_trade_lookup_resolves_strategy(tmp_path: Path) -> None:
    now = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)

    async def _lookup(_evt: dict[str, Any]) -> str:
        return "funding_rate"

    ledger = StrategyBudgetLedger(
        strategy_ids=_strategies(),
        allocated_capital_usd=_capital(),
        trade_lookup=_lookup,
        fallback_dir=tmp_path,
        now_fn=_frozen_now(now),
    )
    await ledger.start()
    await ledger.on_exchange_income_event({
        "exchange": "binance_futures",
        "income_type": "FUNDING_FEE",
        "symbol": "ETHUSDT",
        "amount_usdt": -0.2,
        "tran_id": "t2",
        "ts_ms": int(now.timestamp() * 1000),
    })
    status = ledger.get_status()
    assert status["funding_rate"].daily_pnl_balance_usd == Decimal("-0.2")


@pytest.mark.asyncio
async def test_income_event_unmatched_goes_to_uncategorized(tmp_path: Path) -> None:
    now = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)
    ledger = await _make_ledger(tmp_path, now)
    await ledger.on_exchange_income_event({
        "exchange": "binance_futures",
        "income_type": "REALIZED_PNL",
        "symbol": "XRPUSDT",
        "amount_usdt": -0.3,
        "tran_id": "t3",
        "ts_ms": int(now.timestamp() * 1000),
    })
    status = ledger.get_status()
    assert status[UNCATEGORIZED_ID].daily_pnl_balance_usd == Decimal("-0.3")


@pytest.mark.asyncio
async def test_income_event_duplicate_tran_id_ignored(tmp_path: Path) -> None:
    now = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)
    ledger = await _make_ledger(tmp_path, now)
    evt = {
        "exchange": "binance_futures",
        "income_type": "REALIZED_PNL",
        "symbol": "BTCUSDT",
        "amount_usdt": -0.4,
        "tran_id": "dup-1",
        "ts_ms": int(now.timestamp() * 1000),
        "strategy_id": "cross_exchange",
    }
    await ledger.on_exchange_income_event(evt)
    await ledger.on_exchange_income_event(evt)  # duplicate
    status = ledger.get_status()
    assert status["cross_exchange"].daily_pnl_balance_usd == Decimal("-0.4")


@pytest.mark.asyncio
async def test_income_event_ignores_transfer_types(tmp_path: Path) -> None:
    now = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)
    ledger = await _make_ledger(tmp_path, now)
    await ledger.on_exchange_income_event({
        "exchange": "binance_futures",
        "income_type": "TRANSFER",  # not counted
        "symbol": "",
        "amount_usdt": -100,
        "tran_id": "t-transfer",
        "ts_ms": int(now.timestamp() * 1000),
        "strategy_id": "cross_exchange",
    })
    status = ledger.get_status()
    assert status["cross_exchange"].daily_pnl_balance_usd == Decimal("0")


@pytest.mark.asyncio
async def test_get_daily_report_aggregates_state(tmp_path: Path) -> None:
    now = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)
    ledger = await _make_ledger(tmp_path, now)
    await ledger.update_pnl("funding_rate", Decimal("-10"))  # halts
    await ledger.update_pnl("cross_exchange", Decimal("-0.25"))
    report = await ledger.get_daily_report()
    assert report["reset_ts_utc"].startswith("2026-04-19")
    assert "funding_rate" in report["halted_strategies"]
    assert "cross_exchange" not in report["halted_strategies"]
    assert report["per_strategy"]["cross_exchange"]["daily_pnl_balance_usd"] == "-0.25"


@pytest.mark.asyncio
async def test_explicit_budget_override_respected(tmp_path: Path) -> None:
    now = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)
    ledger = StrategyBudgetLedger(
        strategy_ids=_strategies(),
        allocated_capital_usd=_capital(),
        budget_overrides_usd={"cross_exchange": Decimal("25.0")},
        fallback_dir=tmp_path,
        now_fn=_frozen_now(now),
    )
    await ledger.start()
    status = ledger.get_status()
    assert status["cross_exchange"].daily_loss_budget_usd == Decimal("25.0")
    # Default formula still applies to others.
    assert status["funding_rate"].daily_loss_budget_usd == Decimal("4.0000")


def test_default_budget_pct_constant() -> None:
    assert DEFAULT_BUDGET_PCT == Decimal("2.0")


def test_floor_utc_day_normalises_timezone() -> None:
    naive = datetime(2026, 4, 19, 15, 30)
    assert _floor_utc_day(naive) == datetime(2026, 4, 19, tzinfo=timezone.utc)
    kst = datetime(2026, 4, 19, 8, 0, tzinfo=timezone(timedelta(hours=9)))
    # 08:00 KST = 2026-04-18 23:00 UTC → floor is 2026-04-18 UTC.
    assert _floor_utc_day(kst) == datetime(2026, 4, 18, tzinfo=timezone.utc)
