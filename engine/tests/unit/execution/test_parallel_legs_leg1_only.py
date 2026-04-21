"""Day 11 — leg1 fills, leg2 TTL expires → STRANDED_LEG1 + tracker."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

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
    make_state_machine,
    make_trade_request,
)


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    enable_all_flags(monkeypatch)


@pytest.mark.asyncio
async def test_leg1_fill_leg2_ttl_stranded(enabled: None, tmp_path: Path) -> None:
    """Leg1 fills, leg2 IOC times out → STRANDED_LEG1 + StrandedPositionTracker."""
    # Override `try_ioc` on the atomic executor: leg 0 fills, leg 1 does not.
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
            if leg_index == 0:
                return True, size, price, 1.0
            # leg_index == 1 → TTL expired, nothing filled.
            return False, Decimal("0"), price, 50.0

    adapter_a = FakeAdapter()
    adapter_b = FakeAdapter()
    router = OrderRouter()
    stranded = StrandedPositionTracker(halt_threshold_usd=100_000_000.0)
    sm, journal = await make_state_machine(tmp_path)

    try:
        executor = CrossExchangeV2Executor(
            router=router,
            stranded=stranded,
            state_machine=sm,
            atomic=_FakeAtomic(),  # type: ignore[arg-type]
            ttl_ms=500,
        )

        req = make_trade_request(size=Decimal("1.0"))
        result = await executor.execute(req, adapter_a, adapter_b)

        assert result.status == ExecutionStatusV2.STRANDED_LEG1
        assert len(result.legs) == 2
        assert result.legs[0].filled is True
        assert result.legs[1].filled is False
        assert result.error == "leg2_ioc_ttl_expired"
        # Tracker received the stranded leg.
        assert stranded.total_stranded_usd > 0.0
    finally:
        await journal.stop()
