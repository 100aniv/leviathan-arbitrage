"""Day 11 — outer task cancellation during asyncio.gather."""
from __future__ import annotations

import asyncio
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
async def test_outer_task_cancellation_propagates(
    enabled: None, tmp_path: Path,
) -> None:
    """If the outer task is cancelled mid-gather, CancelledError propagates."""

    class _SlowAtomic:
        async def try_ioc(
            self, adapter, symbol, side, price, size, ttl_ms=None
        ):  # noqa: ANN001
            await asyncio.sleep(5.0)
            return False, Decimal("0"), price, 0.0

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
            atomic=_SlowAtomic(),  # type: ignore[arg-type]
            ttl_ms=5000,
        )

        req = make_trade_request(size=Decimal("1.0"))
        task = asyncio.create_task(executor.execute(req, adapter_a, adapter_b))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        await journal.stop()


@pytest.mark.asyncio
async def test_flag_off_returns_disabled_without_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag off → DISABLED sentinel, zero adapter calls, no ConfigError."""
    # Explicitly leave EXECUTION_PARALLEL_LEGS_ENABLED unset (default false).
    monkeypatch.delenv("EXECUTION_PARALLEL_LEGS_ENABLED", raising=False)

    adapter_a = FakeAdapter()
    adapter_b = FakeAdapter()
    router = OrderRouter()
    stranded = StrandedPositionTracker()

    executor = CrossExchangeV2Executor(
        router=router,
        stranded=stranded,
        ttl_ms=500,
    )

    req = make_trade_request(size=Decimal("1.0"))
    result = await executor.execute(req, adapter_a, adapter_b)

    assert result.status == ExecutionStatusV2.DISABLED
    assert adapter_a.ioc_calls == []
    assert adapter_b.ioc_calls == []


@pytest.mark.asyncio
async def test_flag_on_without_dependency_flags_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§22.3: parallel flag requires journal+state-machine+router all true."""
    from src.execution.cross_exchange_v2 import (
        ConfigError,
        FLAG_ENV_VAR as PARALLEL_FLAG_ENV_VAR,
    )

    monkeypatch.setenv(PARALLEL_FLAG_ENV_VAR, "true")
    # Ensure deps are off.
    monkeypatch.delenv("EXECUTION_JOURNAL_ENABLED", raising=False)
    monkeypatch.delenv("EXECUTION_STATE_MACHINE_ENABLED", raising=False)
    monkeypatch.delenv("EXECUTION_ROUTER_ENABLED", raising=False)

    with pytest.raises(ConfigError, match="§22.3"):
        CrossExchangeV2Executor(
            router=OrderRouter(),
            stranded=StrandedPositionTracker(),
            ttl_ms=500,
        )
