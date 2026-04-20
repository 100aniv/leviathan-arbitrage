"""Day 11 — leg2 fills, leg1 TTL expires → STRANDED_LEG2 mirror path."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.execution.cross_exchange_v2 import (
    CrossExchangeV2Executor,
    ExecutionStatusV2,
)
from src.execution.router import OrderRouter
from src.execution.stranded import StrandedPositionTracker
from tests.unit.execution._parallel_legs_conftest import (  # type: ignore[import-not-found]
    FakeAdapter,
    enable_all_flags,
    make_trade_request,
)


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    enable_all_flags(monkeypatch)


@pytest.mark.asyncio
async def test_leg2_fill_leg1_ttl_stranded_mirror(enabled: None) -> None:
    """Leg1 IOC TTL, leg2 fills → STRANDED_LEG2 (mirror of leg1-only)."""
    calls: list[int] = []

    class _FakeAtomic:
        async def try_ioc(
            self,
            adapter,
            symbol,
            side,
            price,
            size,
            ttl_ms=None,
        ):  # noqa: ANN001
            leg_index = len(calls)
            calls.append(leg_index)
            if leg_index == 1:
                return True, size, price, 1.0
            # leg 0 times out.
            return False, Decimal("0"), price, 50.0

    adapter_a = FakeAdapter()
    adapter_b = FakeAdapter()
    router = OrderRouter()
    stranded = StrandedPositionTracker(halt_threshold_usd=100_000_000.0)

    executor = CrossExchangeV2Executor(
        router=router,
        stranded=stranded,
        atomic=_FakeAtomic(),  # type: ignore[arg-type]
        ttl_ms=500,
    )

    req = make_trade_request(size=Decimal("1.0"))
    result = await executor.execute(req, adapter_a, adapter_b)

    assert result.status == ExecutionStatusV2.STRANDED_LEG2
    assert result.legs[0].filled is False
    assert result.legs[1].filled is True
    assert result.error == "leg1_ioc_ttl_expired"
    assert stranded.total_stranded_usd > 0.0
