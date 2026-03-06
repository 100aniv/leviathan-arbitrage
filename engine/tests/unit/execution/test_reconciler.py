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
    ex.get_positions = AsyncMock(return_value=[
        make_position("binance", "BTC/USDT", Decimal("0.1"))
    ])
    return ex


@pytest.fixture
def mock_exchange_b() -> MagicMock:
    ex = MagicMock()
    ex.exchange_id = "okx"
    ex.get_positions = AsyncMock(return_value=[
        make_position("okx", "BTC/USDT", Decimal("-0.1"))
    ])
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
    """Discrepancy when exchange has position but engine doesn't."""
    engine_positions: dict = {}
    result = await reconciler.reconcile(engine_positions)
    assert result.has_discrepancy


@pytest.mark.asyncio
async def test_reconcile_detects_missing_exchange_position(reconciler: PositionReconciler) -> None:
    """Discrepancy when engine has position but exchange doesn't."""
    mock_a = MagicMock()
    mock_a.exchange_id = "binance"
    mock_a.get_positions = AsyncMock(return_value=[])  # empty!
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
