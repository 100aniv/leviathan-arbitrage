"""Day 11 — both legs fill but invariant violated → ROLLED_BACK (both-legs path)."""
from __future__ import annotations

from decimal import Decimal

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
    enable_all_flags,
    make_trade_request,
)


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    enable_all_flags(monkeypatch)


@pytest.mark.asyncio
async def test_both_legs_fill_invariant_violated_triggers_parallel_rollback(
    enabled: None,
) -> None:
    """Both IOC fills but invariant fails → _do_rollback_cross_parallel invoked."""

    class _FakeAtomic:
        async def try_ioc(
            self, adapter, symbol, side, price, size, ttl_ms=None
        ):  # noqa: ANN001
            return True, size, price, 1.0

    adapter_a = FakeAdapter()
    adapter_b = FakeAdapter()
    router = OrderRouter()
    stranded = StrandedPositionTracker(halt_threshold_usd=100_000_000.0)

    def _invariant_always_fails(_legs: list[LegState]) -> bool:
        return False

    executor = CrossExchangeV2Executor(
        router=router,
        stranded=stranded,
        atomic=_FakeAtomic(),  # type: ignore[arg-type]
        ttl_ms=500,
        both_legs_invariant_fn=_invariant_always_fails,
    )

    req = make_trade_request(size=Decimal("1.0"))
    result = await executor.execute(req, adapter_a, adapter_b)

    assert result.status == ExecutionStatusV2.ROLLED_BACK
    assert result.error == "both_legs_invariant_violated"
    # Each adapter received exactly one reverse market order (unwind).
    assert len(adapter_a.market_calls) == 1
    assert len(adapter_b.market_calls) == 1
    # Unwind side is the opposite of the entry side.
    assert adapter_a.market_calls[0]["side"] == "sell"  # leg 0 was buy
    assert adapter_b.market_calls[0]["side"] == "buy"   # leg 1 was sell
