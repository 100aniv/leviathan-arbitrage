"""Day 11 — parallel cross-exchange executor: both legs fill path.

Verifies the happy path — both IOC limits fill within TTL, ``ExecResultV2``
reports ``SUCCESS`` with two populated ``LegState`` entries.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.execution.cross_exchange_v2 import (
    CrossExchangeV2Executor,
    ExecutionStatusV2,
    LegState,
)
from src.execution.router import OrderRouter
from src.execution.stranded import StrandedPositionTracker
from tests.unit.execution._parallel_legs_conftest import (  # type: ignore[import-not-found]
    FakeAdapter,
    FakeIOCResult,
    enable_all_flags,
    make_trade_request,
)


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    enable_all_flags(monkeypatch)


@pytest.mark.asyncio
async def test_both_legs_fill_returns_success(enabled: None) -> None:
    """Both IOC limits fill within TTL → SUCCESS + two LegState entries."""
    adapter_a = FakeAdapter(
        ioc_result=FakeIOCResult(
            filled_size=Decimal("1.0"),
            avg_price=Decimal("30000"),
        )
    )
    adapter_b = FakeAdapter(
        ioc_result=FakeIOCResult(
            filled_size=Decimal("1.0"),
            avg_price=Decimal("30050"),
        )
    )
    router = OrderRouter()
    stranded = StrandedPositionTracker()

    executor = CrossExchangeV2Executor(
        router=router,
        stranded=stranded,
        atomic=SimpleNamespace(try_ioc=_ioc_fn(True, Decimal("1.0"), Decimal("30000"))),
        ttl_ms=5000,
    )

    req = make_trade_request(size=Decimal("1.0"))
    result = await executor.execute(req, adapter_a, adapter_b)

    assert result.status == ExecutionStatusV2.SUCCESS
    assert len(result.legs) == 2
    assert all(leg.filled for leg in result.legs)
    assert result.legs[0].client_order_id == f"{result.trace_id}.0"
    assert result.legs[1].client_order_id == f"{result.trace_id}.1"
    assert stranded.total_stranded_usd == 0.0


def _ioc_fn(filled: bool, size: Decimal, price: Decimal):
    """Shared helper: returns an async try_ioc replacement."""

    async def _fn(adapter, symbol, side, pr, sz, ttl_ms=None):  # noqa: ANN001
        return filled, size, price, 1.0

    return _fn
