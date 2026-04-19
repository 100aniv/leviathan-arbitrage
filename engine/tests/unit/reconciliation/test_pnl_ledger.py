"""Unit tests for :class:`PnLLedger` — Path-B Day-1."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.reconciliation.pnl_ledger import (
    LedgerConfig,
    PnLLedger,
    PnLStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snapshot(exchange_pnl: Decimal = Decimal("0"), has_data: bool = True) -> MagicMock:
    snap = MagicMock()
    snap.get_cumulative_pnl_usd = AsyncMock(return_value=exchange_pnl)
    snap.has_data = MagicMock(return_value=has_data)
    return snap


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initial_pending_when_no_reconcile_yet() -> None:
    """Fresh ledger + no snapshot data → status=pending on first read."""
    ledger = PnLLedger(
        snapshot=_snapshot(has_data=False),
        engine_pnl_getter=lambda: Decimal("1.00"),
    )
    r = await ledger.get_live_pnl_usd()
    assert r["status"] == PnLStatus.PENDING.value
    assert r["last_reconciled_ts"] is None


@pytest.mark.asyncio
async def test_on_demand_read_classifies_verified() -> None:
    """Snapshot has data + |Δ| < verified_threshold → status=verified."""
    ledger = PnLLedger(
        snapshot=_snapshot(exchange_pnl=Decimal("1.00"), has_data=True),
        engine_pnl_getter=lambda: Decimal("1.05"),  # Δ=0.05 < 0.10
    )
    r = await ledger.get_live_pnl_usd()
    assert r["status"] == PnLStatus.VERIFIED.value
    assert r["engine_pnl_usd"] == pytest.approx(Decimal("1.05"))
    assert r["exchange_pnl_usd"] == pytest.approx(Decimal("1.00"))
    assert r["divergence_usd"] == pytest.approx(Decimal("0.05"))


@pytest.mark.asyncio
async def test_on_demand_read_classifies_diverged() -> None:
    """|Δ| > divergence_threshold → status=diverged."""
    ledger = PnLLedger(
        snapshot=_snapshot(exchange_pnl=Decimal("1.00"), has_data=True),
        engine_pnl_getter=lambda: Decimal("2.00"),  # Δ=1.00 > 0.50
    )
    r = await ledger.get_live_pnl_usd()
    assert r["status"] == PnLStatus.DIVERGED.value


@pytest.mark.asyncio
async def test_update_from_reconcile_sets_last_ts() -> None:
    """Reconciler push path populates state + last_reconciled_ts."""
    ledger = PnLLedger(
        snapshot=_snapshot(),
        engine_pnl_getter=lambda: Decimal("0"),
    )
    ts = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)
    ledger.update_from_reconcile(
        engine_pnl=Decimal("5.0"),
        exchange_pnl=Decimal("4.95"),
        divergence=Decimal("0.05"),
        status="verified",
        ts=ts,
    )
    r = await ledger.get_live_pnl_usd()
    assert r["status"] == "verified"
    assert r["last_reconciled_ts"] == ts


@pytest.mark.asyncio
async def test_update_rejects_unknown_status_falls_to_pending() -> None:
    """Unknown status strings degrade to pending (defensive)."""
    ledger = PnLLedger(
        snapshot=_snapshot(),
        engine_pnl_getter=lambda: Decimal("0"),
    )
    ledger.update_from_reconcile(
        engine_pnl=Decimal("0"),
        exchange_pnl=Decimal("0"),
        divergence=Decimal("0"),
        status="garbage-value",
        ts=datetime.now(timezone.utc),
    )
    assert ledger.state.status == PnLStatus.PENDING


@pytest.mark.asyncio
async def test_engine_getter_exception_safe() -> None:
    """Engine getter raises → 0 fallback, never surfaces exception."""
    def boom() -> Decimal:
        raise RuntimeError("engine dead")

    ledger = PnLLedger(
        snapshot=_snapshot(has_data=True),
        engine_pnl_getter=boom,
    )
    r = await ledger.get_live_pnl_usd()
    assert r["engine_pnl_usd"] == Decimal("0")
    assert r["status"] in {PnLStatus.VERIFIED.value, PnLStatus.PENDING.value}


@pytest.mark.asyncio
async def test_custom_thresholds() -> None:
    """LedgerConfig thresholds override defaults."""
    ledger = PnLLedger(
        snapshot=_snapshot(exchange_pnl=Decimal("10"), has_data=True),
        engine_pnl_getter=lambda: Decimal("10.25"),  # Δ=0.25
        config=LedgerConfig(
            verified_threshold_usd=Decimal("0.30"),
            divergence_threshold_usd=Decimal("1.00"),
        ),
    )
    r = await ledger.get_live_pnl_usd()
    # Δ=0.25 < 0.30 → verified under the custom config
    assert r["status"] == PnLStatus.VERIFIED.value


def test_state_property_is_snapshot_copy() -> None:
    """ledger.state must be a copy so external mutation is impossible."""
    ledger = PnLLedger(
        snapshot=_snapshot(),
        engine_pnl_getter=lambda: Decimal("0"),
    )
    ledger.update_from_reconcile(
        engine_pnl=Decimal("1"),
        exchange_pnl=Decimal("1"),
        divergence=Decimal("0"),
        status="verified",
        ts=datetime.now(timezone.utc),
    )
    snap = ledger.state
    snap.engine_pnl_usd = Decimal("999")
    assert ledger.state.engine_pnl_usd == Decimal("1")
