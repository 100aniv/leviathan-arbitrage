"""Day 11 — pre-gather edge re-check rejects stale signals before submit."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

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
async def test_edge_evaporated_rejects_without_submit(
    enabled: None, tmp_path: Path,
) -> None:
    """Edge check returns False → no adapter calls, status EDGE_EVAPORATED."""
    adapter_a = FakeAdapter()
    adapter_b = FakeAdapter()
    router = OrderRouter()
    stranded = StrandedPositionTracker()
    sm, journal = await make_state_machine(tmp_path)

    try:
        executor = CrossExchangeV2Executor(
            router=router,
            stranded=stranded,
            state_machine=sm,
            ttl_ms=500,
            edge_check_fn=lambda _req: False,
        )

        req = make_trade_request(size=Decimal("1.0"))
        result = await executor.execute(req, adapter_a, adapter_b)

        assert result.status == ExecutionStatusV2.EDGE_EVAPORATED
        assert result.error == "pre_gather_edge_invalid"
        # Neither adapter called.
        assert adapter_a.ioc_calls == []
        assert adapter_b.ioc_calls == []
    finally:
        await journal.stop()


@pytest.mark.asyncio
async def test_edge_check_exception_rejects_defensively(
    enabled: None, tmp_path: Path,
) -> None:
    """Edge check raising → treated as invalid → EDGE_EVAPORATED."""
    adapter_a = FakeAdapter()
    adapter_b = FakeAdapter()
    router = OrderRouter()
    stranded = StrandedPositionTracker()
    sm, journal = await make_state_machine(tmp_path)

    def _boom(_req):  # noqa: ANN001
        raise RuntimeError("orderbook_unavailable")

    try:
        executor = CrossExchangeV2Executor(
            router=router,
            stranded=stranded,
            state_machine=sm,
            ttl_ms=500,
            edge_check_fn=_boom,
        )

        req = make_trade_request(size=Decimal("1.0"))
        result = await executor.execute(req, adapter_a, adapter_b)

        assert result.status == ExecutionStatusV2.EDGE_EVAPORATED
        assert adapter_a.ioc_calls == []
        assert adapter_b.ioc_calls == []
    finally:
        await journal.stop()
