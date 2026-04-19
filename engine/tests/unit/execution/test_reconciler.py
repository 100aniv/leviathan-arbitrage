"""Unit tests for position reconciler."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from src.core.models import Position
from src.execution.reconciler import PositionReconciler, ReconciliationResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_position(
    exchange_id: str = "binance",
    symbol: str = "BTC/USDT",
    size: Decimal = Decimal("0.1"),
    entry_price: Decimal = Decimal("50000"),
) -> Position:
    return Position(
        exchange_id=exchange_id,
        symbol=symbol,
        size=size,
        entry_price=entry_price,
    )


@pytest.fixture
def mock_exchange_a() -> MagicMock:
    ex = MagicMock()
    ex.exchange_id = "binance"
    _positions = [make_position("binance", "BTC/USDT", Decimal("0.1"))]
    ex.get_positions = AsyncMock(return_value=_positions)
    ex.get_positions_strict = AsyncMock(return_value=_positions)  # BUG-184
    return ex


@pytest.fixture
def mock_exchange_b() -> MagicMock:
    ex = MagicMock()
    ex.exchange_id = "okx"
    _positions = [make_position("okx", "BTC/USDT", Decimal("-0.1"))]
    ex.get_positions = AsyncMock(return_value=_positions)
    ex.get_positions_strict = AsyncMock(return_value=_positions)  # BUG-184
    return ex


@pytest.fixture
def reconciler(mock_exchange_a: MagicMock, mock_exchange_b: MagicMock) -> PositionReconciler:
    return PositionReconciler(exchanges=[mock_exchange_a, mock_exchange_b])


# ---------------------------------------------------------------------------
# ReconciliationResult tests
# ---------------------------------------------------------------------------


def test_reconciliation_result_no_discrepancy() -> None:
    result = ReconciliationResult(
        has_discrepancy=False,
        discrepancies=[],
        engine_positions={},
        exchange_positions={},
    )
    assert result.has_discrepancy is False
    assert result.discrepancies == []


def test_reconciliation_result_with_discrepancy() -> None:
    result = ReconciliationResult(
        has_discrepancy=True,
        discrepancies=["binance:BTC/USDT: engine=0.1, exchange=0.05"],
        engine_positions={},
        exchange_positions={},
    )
    assert result.has_discrepancy is True
    assert len(result.discrepancies) == 1


# ---------------------------------------------------------------------------
# PositionReconciler.reconcile tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_no_discrepancy(reconciler: PositionReconciler) -> None:
    """No discrepancy when engine state matches exchange state."""
    engine_positions = {
        "binance:BTC/USDT": make_position("binance", "BTC/USDT", Decimal("0.1")),
        "okx:BTC/USDT": make_position("okx", "BTC/USDT", Decimal("-0.1")),
    }
    result = await reconciler.reconcile(engine_positions)
    assert not result.has_discrepancy


@pytest.mark.asyncio
async def test_reconcile_detects_size_discrepancy(reconciler: PositionReconciler) -> None:
    """Discrepancy detected when sizes differ."""
    engine_positions = {
        "binance:BTC/USDT": make_position("binance", "BTC/USDT", Decimal("0.5")),  # wrong!
    }
    result = await reconciler.reconcile(engine_positions)
    assert result.has_discrepancy
    assert any("BTC/USDT" in d for d in result.discrepancies)


@pytest.mark.asyncio
async def test_reconcile_detects_missing_engine_position(reconciler: PositionReconciler) -> None:
    """Discrepancy when exchange has position but engine doesn't (after 2-cycle guard).

    BUG-202: first cycle is transient (race window with PositionManager.register);
    only persistent unrecorded keys on the second cycle escalate.
    """
    engine_positions: dict = {}
    # Cycle 1: transient — no discrepancy yet
    result1 = await reconciler.reconcile(engine_positions)
    assert not result1.has_discrepancy
    # Cycle 2: persistent — now escalates
    result2 = await reconciler.reconcile(engine_positions)
    assert result2.has_discrepancy


@pytest.mark.asyncio
async def test_reconcile_detects_missing_exchange_position(reconciler: PositionReconciler) -> None:
    """Discrepancy when engine has position but exchange doesn't."""
    mock_a = MagicMock()
    mock_a.exchange_id = "binance"
    mock_a.get_positions = AsyncMock(return_value=[])  # empty!
    mock_a.get_positions_strict = AsyncMock(return_value=[])  # BUG-184
    r = PositionReconciler(exchanges=[mock_a])
    engine_positions = {
        "binance:BTC/USDT": make_position("binance", "BTC/USDT", Decimal("0.1")),
    }
    result = await r.reconcile(engine_positions)
    assert result.has_discrepancy


# ---------------------------------------------------------------------------
# Stranded position recovery tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_stranded_position_alert(reconciler: PositionReconciler) -> None:
    """Stranded position triggers alert flag in result."""
    reconciler.mark_stranded("binance:BTC/USDT")
    engine_positions: dict = {}
    result = await reconciler.reconcile(engine_positions)
    assert result.has_discrepancy or len(reconciler.stranded_positions) > 0


def test_mark_stranded_records_position(reconciler: PositionReconciler) -> None:
    reconciler.mark_stranded("binance:ETH/USDT")
    assert "binance:ETH/USDT" in reconciler.stranded_positions


def test_clear_stranded_removes_position(reconciler: PositionReconciler) -> None:
    reconciler.mark_stranded("binance:ETH/USDT")
    reconciler.clear_stranded("binance:ETH/USDT")
    assert "binance:ETH/USDT" not in reconciler.stranded_positions


# ---------------------------------------------------------------------------
# Pausing strategy on discrepancy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_triggers_pause_callback(reconciler: PositionReconciler) -> None:
    """When discrepancy found, pause callback is invoked."""
    paused = []
    reconciler.on_discrepancy = lambda result: paused.append(True)

    engine_positions = {
        "binance:BTC/USDT": make_position("binance", "BTC/USDT", Decimal("0.5")),
    }
    await reconciler.reconcile(engine_positions)
    assert len(paused) > 0


# ---------------------------------------------------------------------------
# BUG-01: Fetch failure handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_fetch_failure_sets_field_not_discrepancy() -> None:
    """Fetch failure sets fetch_failed_exchanges but does NOT call on_discrepancy."""
    mock_ex = MagicMock()
    mock_ex.exchange_id = "binance"
    mock_ex.get_positions = AsyncMock(side_effect=Exception("API timeout"))
    mock_ex.get_positions_strict = AsyncMock(side_effect=Exception("API timeout"))  # BUG-184

    callback_called = []
    r = PositionReconciler(
        exchanges=[mock_ex],
        on_discrepancy=lambda result: callback_called.append(True),
    )
    result = await r.reconcile({})

    assert result.has_discrepancy is False  # fetch failure ≠ real position mismatch
    assert "binance" in result.fetch_failed_exchanges  # fetch failure는 별도 필드로 전달
    assert result.discrepancies == []  # no real position mismatch
    assert callback_called == []  # on_discrepancy NOT fired for fetch failure


@pytest.mark.asyncio
async def test_reconcile_fetch_failure_and_real_discrepancy_fires_callback() -> None:
    """When both fetch failure AND real discrepancy, on_discrepancy still fires.

    BUG-202: unrecorded-type discrepancies require 2 cycles to escalate. So we
    reconcile twice; callback fires on cycle 2 when the orphan persists.
    """
    mock_a = MagicMock()
    mock_a.exchange_id = "binance"
    mock_a.get_positions = AsyncMock(side_effect=Exception("timeout"))
    mock_a.get_positions_strict = AsyncMock(side_effect=Exception("timeout"))  # BUG-184
    mock_b = MagicMock()
    mock_b.exchange_id = "okx"
    _b_positions = [make_position("okx", "BTC/USDT", Decimal("0.5"))]
    mock_b.get_positions = AsyncMock(return_value=_b_positions)
    mock_b.get_positions_strict = AsyncMock(return_value=_b_positions)  # BUG-184

    callback_called = []
    r = PositionReconciler(
        exchanges=[mock_a, mock_b],
        on_discrepancy=lambda result: callback_called.append(True),
    )
    engine_positions = {}  # okx has position, engine doesn't know about it
    # Cycle 1: transient — no callback
    result1 = await r.reconcile(engine_positions)
    assert result1.has_discrepancy is False
    assert "binance" in result1.fetch_failed_exchanges
    assert callback_called == []
    # Cycle 2: persistent — callback fires
    result2 = await r.reconcile(engine_positions)
    assert result2.has_discrepancy is True
    assert "binance" in result2.fetch_failed_exchanges
    assert len(result2.discrepancies) > 0
    assert callback_called == [True]  # on_discrepancy fires for real mismatch


# ---------------------------------------------------------------------------
# BUG-202: Race guard for unrecorded type (extends BUG-164)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bug202_unrecorded_first_cycle_is_transient() -> None:
    """BUG-202: first-cycle unrecorded positions (exchange has, engine doesn't)
    are treated as transient (race window between exchange fill and
    PositionManager.register). No CRITICAL log, no callback. Only escalates
    on the second cycle if the orphan persists.
    """
    mock_ex = MagicMock()
    mock_ex.exchange_id = "bitget_futures"
    _positions = [make_position("bitget_futures", "CYBER/USDT", Decimal("20.1"))]
    mock_ex.get_positions = AsyncMock(return_value=_positions)
    mock_ex.get_positions_strict = AsyncMock(return_value=_positions)

    callback_called = []
    r = PositionReconciler(
        exchanges=[mock_ex],
        on_discrepancy=lambda result: callback_called.append(True),
    )

    # Cycle 1: exchange reports position, engine hasn't registered yet (race)
    result1 = await r.reconcile({})
    assert result1.has_discrepancy is False, "First-cycle unrecorded must be transient"
    assert result1.discrepancies == []
    assert callback_called == [], "Callback must NOT fire on first cycle"

    # Cycle 2: same orphan still present → persistent, escalate
    result2 = await r.reconcile({})
    assert result2.has_discrepancy is True, "Second-cycle unrecorded must escalate"
    assert any("CYBER/USDT" in d for d in result2.discrepancies)
    assert callback_called == [True], "Callback must fire on persistent orphan"


@pytest.mark.asyncio
async def test_bug202_unrecorded_resolved_before_second_cycle() -> None:
    """BUG-202: if engine catches up between cycle 1 and cycle 2 (normal race
    resolution), the orphan is cleared and never escalates. This is the
    expected behavior for newly-opened positions: exchange fill precedes
    PositionManager.register by a few seconds.
    """
    mock_ex = MagicMock()
    mock_ex.exchange_id = "bitget_futures"
    _positions = [make_position("bitget_futures", "CYBER/USDT", Decimal("20.1"))]
    mock_ex.get_positions = AsyncMock(return_value=_positions)
    mock_ex.get_positions_strict = AsyncMock(return_value=_positions)

    callback_called = []
    r = PositionReconciler(
        exchanges=[mock_ex],
        on_discrepancy=lambda result: callback_called.append(True),
    )

    # Cycle 1: race — exchange has, engine doesn't (yet)
    result1 = await r.reconcile({})
    assert result1.has_discrepancy is False
    assert callback_called == []

    # Cycle 2: engine caught up — position registered
    engine_now = {
        "bitget_futures:CYBER/USDT": make_position(
            "bitget_futures", "CYBER/USDT", Decimal("20.1")
        ),
    }
    result2 = await r.reconcile(engine_now)
    assert result2.has_discrepancy is False, "Race resolved — no discrepancy"
    assert callback_called == [], "No callback when race self-resolves"


# ---------------------------------------------------------------------------
# BUG-210: per-exchange 2-cycle guard roll-forward (fetch-failure resilience)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bug210_prev_unrecorded_preserved_on_fetch_failure() -> None:
    """BUG-210: when one exchange's fetch fails between cycles, its prior
    unrecorded keys must be preserved (not wiped by the other exchange's
    successful fetch). Meanwhile, persistent orphans on the successful
    exchange must still escalate to CRITICAL on cycle 2.

    Scenario:
      Cycle 1: A succeeds with unrecorded K_A; B succeeds with unrecorded K_B.
      Cycle 2: A's fetch fails (HTTP/2 RemoteProtocolError); B succeeds with
               K_B still unrecorded.
    Expected:
      After cycle 2, _prev_unrecorded_keys still contains K_A (preserved,
      since A failed) AND K_B escalated to CRITICAL discrepancy (persistent).
    """
    # Exchange A: binance
    ex_a = MagicMock()
    ex_a.exchange_id = "binance"
    a_pos = [make_position("binance", "BTC/USDT", Decimal("0.1"))]
    ex_a.get_positions_strict = AsyncMock(return_value=a_pos)
    ex_a.get_positions = AsyncMock(return_value=a_pos)

    # Exchange B: okx
    ex_b = MagicMock()
    ex_b.exchange_id = "okx"
    b_pos = [make_position("okx", "ETH/USDT", Decimal("1.0"))]
    ex_b.get_positions_strict = AsyncMock(return_value=b_pos)
    ex_b.get_positions = AsyncMock(return_value=b_pos)

    callback_called: list[bool] = []
    r = PositionReconciler(
        exchanges=[ex_a, ex_b],
        on_discrepancy=lambda result: callback_called.append(True),
    )

    # Cycle 1: both exchanges succeed; engine has neither position
    result1 = await r.reconcile({})
    assert result1.has_discrepancy is False, "First-cycle unrecorded must be transient"
    assert callback_called == [], "No callback on first cycle"
    # Both keys tracked for next-cycle escalation
    assert "binance:BTC/USDT" in r._prev_unrecorded_keys
    assert "okx:ETH/USDT" in r._prev_unrecorded_keys

    # Cycle 2: A fetch fails (HTTP/2 RemoteProtocolError), B still has K_B
    ex_a.get_positions_strict = AsyncMock(
        side_effect=RuntimeError("RemoteProtocolError: HTTP/2 peer closed connection"),
    )
    # B continues reporting its orphan
    ex_b.get_positions_strict = AsyncMock(return_value=b_pos)

    result2 = await r.reconcile({})

    # A's prior entry must NOT be wiped by B's successful overwrite
    assert "binance:BTC/USDT" in r._prev_unrecorded_keys, (
        "BUG-210: prior entry for failed-fetch exchange must be preserved"
    )
    # A must be in fetch_failed_exchanges
    assert "binance" in result2.fetch_failed_exchanges

    # B's K_B must still be present (successful this cycle)
    assert "okx:ETH/USDT" in r._prev_unrecorded_keys

    # K_B escalates to CRITICAL discrepancy on cycle 2 (persistent)
    assert result2.has_discrepancy is True, "Persistent K_B must escalate"
    assert any("okx:ETH/USDT" in d for d in result2.discrepancies)
    assert callback_called == [True], "Callback must fire for persistent K_B"
