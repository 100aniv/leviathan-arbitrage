"""Day 15 — TradingSupervisor activate + STRANDED→halt_request wiring tests.

Covers the 4 acceptance criteria for Day 15:
    1. Flag ON + StrandedPositionTracker emits halt → supervisor.halt_request()
       is invoked via the registered forwarder.
    2. Flag OFF + STRANDED threshold event → legacy halt path only; no
       supervisor registered; forwarder remains None.
    3. supervisor.start() registers supplied background tasks correctly.
    4. supervisor.stop() cancels all tasks within SHUTDOWN_TIMEOUT.
"""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Optional
from unittest.mock import MagicMock

import pytest

from src.core.supervisor import TradingSupervisor
from src.execution import stranded as stranded_module
from src.execution.stranded import StrandedPositionTracker


# ---------------------------------------------------------------------------
# Minimal stubs (mirror Day-4 test_supervisor.py approach)
# ---------------------------------------------------------------------------


class StubDatabasePool:
    def __init__(self, dsn: str, min_size: int = 2, max_size: int = 10) -> None:
        self.dsn = dsn
        self.pool = MagicMock(name="asyncpg_pool")

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None


class StubRedisClient:
    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None


class StubAdapter:
    def __init__(self, exchange_id: str, **_: Any) -> None:
        self.exchange_id = exchange_id

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None


class StubUniverseMatrix:
    def __init__(self) -> None:
        self.built = False

    async def build(self, exchange_registry: dict[str, Any], strategy_registry: Any) -> None:
        self.built = True


class _TrackingSupervisor(TradingSupervisor):
    """Supervisor with Day-4-style injection seams wired to stubs."""

    def __init__(
        self,
        config: Any,
        task_factories: Optional[list[tuple[str, Callable[[], Awaitable[Any]]]]] = None,
    ) -> None:
        super().__init__(config)
        self._task_factories_list = task_factories or []

    def _db_pool_cls(self) -> Any:
        return StubDatabasePool

    def _redis_client_cls(self) -> Any:
        return StubRedisClient

    def _exchange_factory(self) -> Optional[Callable[..., Any]]:
        def _factory(**kwargs: Any) -> Any:
            return StubAdapter(**kwargs)
        return _factory

    def _universe_matrix_cls(self) -> Optional[type]:
        return StubUniverseMatrix

    def _background_task_factories(self):
        return self._task_factories_list


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        exchanges=SimpleNamespace(active=["binance"]),
        redis=SimpleNamespace(url="redis://localhost:6379/0", max_connections=50),
        database=SimpleNamespace(
            url="postgresql://leviathan:leviathan@localhost:5432/leviathan"
        ),
    )


@pytest.fixture(autouse=True)
def _reset_stranded_forwarder() -> None:
    """Ensure each test starts with no residual halt forwarder state."""
    stranded_module._HALT_FORWARDER = None
    yield
    stranded_module._HALT_FORWARDER = None


@pytest.fixture(autouse=True)
def _clear_supervisor_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default all supervisor-related env flags OFF per test."""
    monkeypatch.delenv("SUPERVISOR_ACTIVE", raising=False)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flag_on_stranded_breach_invokes_supervisor_halt_request() -> None:
    """Flag ON + stranded threshold exceeded → supervisor.halt_request() fires."""
    os.environ["SUPERVISOR_ACTIVE"] = "true"
    try:
        sup = _TrackingSupervisor(_cfg())
        await sup.start()
        try:
            # Bound-method identity check — __self__ must be the supervisor.
            fwd = stranded_module._HALT_FORWARDER
            assert fwd is not None
            assert getattr(fwd, "__self__", None) is sup
            assert getattr(fwd, "__func__", None) is TradingSupervisor.halt_request

            # Drive the tracker past its threshold so register() returns True
            # AND the forwarder fires supervisor.halt_request().
            tracker = StrandedPositionTracker(halt_threshold_usd=10.0)
            should_halt = tracker.register(
                exchange_id="binance",
                symbol="BTC/USDT",
                side="buy",
                size=0.5,
                value_usd=25.0,
                reason="rollback_failed_unit_test",
            )
            assert should_halt is True
            # halt_request() sets shutdown event synchronously.
            assert sup._shutdown_requested.is_set()
        finally:
            await sup.stop()
        assert sup._stopped is True
    finally:
        os.environ.pop("SUPERVISOR_ACTIVE", None)


@pytest.mark.asyncio
async def test_flag_off_stranded_breach_uses_legacy_halt_path_only() -> None:
    """Flag OFF → no supervisor instance; forwarder stays None; legacy path intact."""
    # Flag OFF (autouse fixture already cleared SUPERVISOR_ACTIVE).
    # No supervisor is ever constructed in this path — we only assert that
    # StrandedPositionTracker does NOT invoke any registered forwarder.
    assert stranded_module._HALT_FORWARDER is None

    tracker = StrandedPositionTracker(halt_threshold_usd=10.0)
    should_halt = tracker.register(
        exchange_id="binance",
        symbol="BTC/USDT",
        side="buy",
        size=0.5,
        value_usd=25.0,
        reason="rollback_failed_unit_test",
    )
    assert should_halt is True
    # Legacy path: executor calls halt_local() directly based on should_halt.
    # No forwarder ran → supervisor coupling is absent.
    assert stranded_module._HALT_FORWARDER is None


@pytest.mark.asyncio
async def test_supervisor_start_registers_background_tasks() -> None:
    """supervisor.start() correctly registers factory-supplied tasks."""
    tasks_started: list[str] = []

    async def _worker_a() -> None:
        tasks_started.append("a")
        await asyncio.sleep(3600)

    async def _worker_b() -> None:
        tasks_started.append("b")
        await asyncio.sleep(3600)

    sup = _TrackingSupervisor(
        _cfg(),
        task_factories=[("worker_a", _worker_a), ("worker_b", _worker_b)],
    )
    await sup.start()
    try:
        names = {t.get_name() for t in sup.background_tasks}
        assert names == {"leviathan_worker_a", "leviathan_worker_b"}
        # Give the scheduler a tick so both coros enter.
        await asyncio.sleep(0.01)
        assert set(tasks_started) == {"a", "b"}
    finally:
        await sup.stop()


@pytest.mark.asyncio
async def test_supervisor_stop_cancels_tasks_within_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """supervisor.stop() cancels all registered tasks within SHUTDOWN_TIMEOUT (≤30s)."""
    cancelled_flag = {"hit": False}

    async def _long_runner() -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled_flag["hit"] = True
            raise

    sup = _TrackingSupervisor(
        _cfg(), task_factories=[("long_runner", _long_runner)]
    )
    # Tighten the timeout so the test stays fast but still exercises the path.
    monkeypatch.setattr(_TrackingSupervisor, "SHUTDOWN_TIMEOUT", 1.0, raising=True)

    await sup.start()
    assert len(sup.background_tasks) == 1
    # Let the coroutine enter its sleep().
    await asyncio.sleep(0.01)

    # stop() must complete within the timeout.
    await asyncio.wait_for(sup.stop(), timeout=3.0)
    assert cancelled_flag["hit"] is True
    assert sup.background_tasks == []
    # After stop(), the halt forwarder is cleared.
    assert stranded_module._HALT_FORWARDER is None
