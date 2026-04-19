"""Unit tests for :class:`PnLReconciler` — Path-B Day-1."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.reconciliation.pnl_ledger import PnLLedger, PnLStatus
from src.reconciliation.pnl_reconciler import (
    PnLReconciler,
    ReconcilerConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_snapshot(exchange_pnl: Decimal | float, has_data: bool = True) -> MagicMock:
    snap = MagicMock()
    snap.get_cumulative_pnl_usd = AsyncMock(
        return_value=Decimal(str(exchange_pnl)),
    )
    snap.has_data = MagicMock(return_value=has_data)
    return snap


def _config(warn: float = 0.5, critical: float = 1.0, streak: int = 3) -> ReconcilerConfig:
    return ReconcilerConfig(
        interval_s=60.0,
        warn_threshold_usd=Decimal(str(warn)),
        critical_threshold_usd=Decimal(str(critical)),
        verified_threshold_usd=Decimal("0.10"),
        consecutive_breaches=streak,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_warn_escalation_after_three_breaches() -> None:
    """|Δ|=$0.30 (=warn, <critical) for 3 cycles → warn-level escalation."""
    halt_mock = MagicMock()
    snap = _make_snapshot(Decimal("10.00"))
    recon = PnLReconciler(
        snapshot=snap,
        engine_pnl_getter=lambda: Decimal("10.30"),  # Wait — 0.30 is <warn 0.5
        config=_config(),
        halt_callable=halt_mock,
    )
    # Bump divergence above warn to produce warn-level breach.
    recon._engine_pnl_getter = lambda: Decimal("10.70")  # Δ=0.70, between warn & critical
    for _ in range(3):
        r = await recon.run_check()
    assert r.severity == "warn"
    assert recon.warn_count >= 3
    halt_mock.assert_not_called()


@pytest.mark.asyncio
async def test_critical_escalation_after_three_breaches_triggers_halt() -> None:
    """|Δ|=$1.50 for 3 cycles → critical escalation + halt_local()."""
    halt_mock = MagicMock()
    snap = _make_snapshot(Decimal("10.00"))
    recon = PnLReconciler(
        snapshot=snap,
        engine_pnl_getter=lambda: Decimal("11.50"),  # Δ=+1.50
        config=_config(),
        halt_callable=halt_mock,
    )
    for _ in range(3):
        r = await recon.run_check()
    assert r.severity == "critical"
    assert r.halted is True
    assert recon.critical_count >= 3
    halt_mock.assert_called_once()


@pytest.mark.asyncio
async def test_recovery_resets_counters() -> None:
    """Once Δ returns below warn threshold, streak counters reset."""
    halt_mock = MagicMock()
    snap = _make_snapshot(Decimal("10.00"))

    # Mutable getter so we can flip value between cycles.
    state = {"engine": Decimal("11.50")}
    recon = PnLReconciler(
        snapshot=snap,
        engine_pnl_getter=lambda: state["engine"],
        config=_config(),
        halt_callable=halt_mock,
    )
    for _ in range(2):
        await recon.run_check()
    assert recon.warn_count == 2
    assert recon.critical_count == 2
    state["engine"] = Decimal("10.05")  # Δ=0.05 < verified threshold
    r = await recon.run_check()
    assert recon.warn_count == 0
    assert recon.critical_count == 0
    assert r.severity == "ok"


@pytest.mark.asyncio
async def test_pending_when_snapshot_empty() -> None:
    """Fresh boot (no snapshot data) → status=pending, no escalation."""
    halt_mock = MagicMock()
    snap = _make_snapshot(Decimal("0.00"), has_data=False)
    recon = PnLReconciler(
        snapshot=snap,
        engine_pnl_getter=lambda: Decimal("5.00"),  # wildly different from 0
        config=_config(),
        halt_callable=halt_mock,
    )
    for _ in range(5):
        r = await recon.run_check()
    assert r.severity == "pending"
    assert recon.warn_count == 0
    halt_mock.assert_not_called()


@pytest.mark.asyncio
async def test_ledger_status_transitions() -> None:
    """pending → verified → diverged → verified as divergence changes."""
    halt_mock = MagicMock()
    snap = _make_snapshot(Decimal("0.00"), has_data=False)
    ledger = PnLLedger(snapshot=snap, engine_pnl_getter=lambda: Decimal("0"))
    state = {"engine": Decimal("0"), "exchange": Decimal("0"), "has_data": False}

    snap.get_cumulative_pnl_usd = AsyncMock(
        side_effect=lambda *a, **k: state["exchange"],
    )
    snap.has_data = MagicMock(side_effect=lambda: state["has_data"])

    recon = PnLReconciler(
        snapshot=snap,
        engine_pnl_getter=lambda: state["engine"],
        ledger=ledger,
        config=_config(),
        halt_callable=halt_mock,
    )
    # 1: pending (no data)
    await recon.run_check()
    assert ledger.state.status == PnLStatus.PENDING
    # 2: verified
    state["has_data"] = True
    state["engine"] = Decimal("5.00")
    state["exchange"] = Decimal("5.00")
    await recon.run_check()
    assert ledger.state.status == PnLStatus.VERIFIED
    # 3: diverged (|Δ|=0.80 > 0.50)
    state["engine"] = Decimal("5.80")
    await recon.run_check()
    assert ledger.state.status == PnLStatus.DIVERGED


@pytest.mark.asyncio
async def test_prometheus_gauges_updated() -> None:
    """Engine/exchange/divergence gauges emit the current cycle values."""
    from src.reconciliation.pnl_reconciler import (
        PNL_DIVERGENCE_USD,
        PNL_ENGINE_USD,
        PNL_EXCHANGE_USD,
    )
    snap = _make_snapshot(Decimal("2.25"))
    recon = PnLReconciler(
        snapshot=snap,
        engine_pnl_getter=lambda: Decimal("2.00"),
        halt_callable=MagicMock(),
        config=_config(),
    )
    await recon.run_check()
    assert PNL_ENGINE_USD._value.get() == pytest.approx(2.00)
    assert PNL_EXCHANGE_USD._value.get() == pytest.approx(2.25)
    assert PNL_DIVERGENCE_USD._value.get() == pytest.approx(-0.25)


@pytest.mark.asyncio
async def test_snapshot_read_failure_produces_pending_result() -> None:
    """Snapshot read error → pending result, no breach increment."""
    snap = _make_snapshot(Decimal("0"))
    snap.get_cumulative_pnl_usd = AsyncMock(side_effect=RuntimeError("db down"))
    snap.has_data = MagicMock(return_value=True)
    halt_mock = MagicMock()
    recon = PnLReconciler(
        snapshot=snap,
        engine_pnl_getter=lambda: Decimal("99"),
        config=_config(),
        halt_callable=halt_mock,
    )
    r = await recon.run_check()
    assert r.severity == "pending"
    assert recon.warn_count == 0
    halt_mock.assert_not_called()


@pytest.mark.asyncio
async def test_telegram_alert_called_on_critical() -> None:
    """Critical breach triggers Telegram CRITICAL alert."""
    telegram = AsyncMock()
    telegram.send_alert = AsyncMock(return_value=True)
    snap = _make_snapshot(Decimal("0"))
    recon = PnLReconciler(
        snapshot=snap,
        engine_pnl_getter=lambda: Decimal("2.0"),  # Δ=2.0 > critical
        telegram=telegram,
        halt_callable=MagicMock(),
        config=_config(),
    )
    for _ in range(3):
        await recon.run_check()
    assert telegram.send_alert.await_count >= 1
    call = telegram.send_alert.await_args_list[-1]
    assert call.kwargs.get("level") == "CRITICAL"
