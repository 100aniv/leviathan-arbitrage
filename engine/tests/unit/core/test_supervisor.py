"""Unit tests for TradingSupervisor (Path-B Day-4 lifecycle module).

These tests use stubbed DB/Redis/exchange/UniverseMatrix to verify the
supervisor orchestrates the boot/stop sequence correctly without any
network, filesystem, or real asyncpg/redis IO.
"""
from __future__ import annotations

import asyncio
import signal
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.supervisor import SupervisorHealth, TradingSupervisor


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class StubDatabasePool:
    """Tracks initialize/close calls and exposes a .pool handle."""

    instances: list["StubDatabasePool"] = []

    def __init__(self, dsn: str, min_size: int = 2, max_size: int = 10) -> None:
        self.dsn = dsn
        self.min_size = min_size
        self.max_size = max_size
        self.initialize_called = False
        self.close_called = False
        self.pool = MagicMock(name="asyncpg_pool")
        StubDatabasePool.instances.append(self)

    async def initialize(self) -> None:
        self.initialize_called = True

    async def close(self) -> None:
        self.close_called = True


class FailingDatabasePool(StubDatabasePool):
    async def initialize(self) -> None:
        raise ConnectionError("db boom")


class StubRedisClient:
    instances: list["StubRedisClient"] = []

    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        self.connect_called = False
        self.disconnect_called = False
        StubRedisClient.instances.append(self)

    async def connect(self) -> None:
        self.connect_called = True

    async def disconnect(self) -> None:
        self.disconnect_called = True


class StubAdapter:
    def __init__(self, exchange_id: str, **_: Any) -> None:
        self.exchange_id = exchange_id
        self.connected = False
        self.disconnected = False

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True


class StubUniverseMatrix:
    instances: list["StubUniverseMatrix"] = []

    def __init__(self) -> None:
        self.built = False
        self.build_args: Optional[dict[str, Any]] = None
        StubUniverseMatrix.instances.append(self)

    async def build(
        self, exchange_registry: dict[str, Any], strategy_registry: Any
    ) -> None:
        self.built = True
        self.build_args = {
            "exchange_registry": dict(exchange_registry),
            "strategy_registry": list(strategy_registry),
        }


def _make_config(
    active: list[str] | None = None,
    redis_url: str = "redis://localhost:6379/0",
    db_url: str = "postgresql://leviathan:leviathan@localhost:5432/leviathan",
) -> SimpleNamespace:
    return SimpleNamespace(
        exchanges=SimpleNamespace(active=active if active is not None else ["binance"]),
        redis=SimpleNamespace(url=redis_url, max_connections=50),
        database=SimpleNamespace(url=db_url),
    )


class TrackingSupervisor(TradingSupervisor):
    """Supervisor subclass with injection seams pre-wired to stubs."""

    def __init__(
        self,
        config: Any,
        task_factories: Optional[list[tuple[str, Callable[[], Awaitable[Any]]]]] = None,
        db_pool_cls: type = StubDatabasePool,
        redis_cls: type = StubRedisClient,
        matrix_cls: type = StubUniverseMatrix,
        adapter_cls: type = StubAdapter,
    ) -> None:
        super().__init__(config)
        self._task_factories_list = task_factories or []
        self._db_cls_override = db_pool_cls
        self._redis_cls_override = redis_cls
        self._matrix_cls_override = matrix_cls
        self._adapter_cls_override = adapter_cls
        self.boot_call_order: list[str] = []

    def _db_pool_cls(self) -> Any:
        self.boot_call_order.append("db")
        return self._db_cls_override

    def _redis_client_cls(self) -> Any:
        self.boot_call_order.append("redis")
        return self._redis_cls_override

    def _exchange_factory(self) -> Optional[Callable[..., Any]]:
        def _factory(**kwargs: Any) -> Any:
            self.boot_call_order.append(f"exchange:{kwargs['exchange_id']}")
            return self._adapter_cls_override(**kwargs)
        return _factory

    def _universe_matrix_cls(self) -> Optional[type]:
        self.boot_call_order.append("matrix")
        return self._matrix_cls_override

    def _background_task_factories(self):
        return self._task_factories_list


@pytest.fixture(autouse=True)
def _reset_stub_instances() -> None:
    StubDatabasePool.instances.clear()
    StubRedisClient.instances.clear()
    StubUniverseMatrix.instances.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_invokes_initializers_in_order() -> None:
    """supervisor.start() calls DB → Redis → exchanges → matrix → tasks → signals."""
    async def _noop() -> None:
        await asyncio.sleep(3600)

    cfg = _make_config(active=["binance", "okx"])
    sup = TrackingSupervisor(
        cfg, task_factories=[("noop", _noop)]
    )
    try:
        await sup.start()
        # The ordering of injection-seam calls reveals the boot order.
        order = sup.boot_call_order
        assert order[0] == "db"
        assert order[1] == "redis"
        # exchanges:<eid> appear for each
        assert "exchange:binance" in order
        assert "exchange:okx" in order
        assert "matrix" in order
        # matrix comes after exchanges
        assert order.index("matrix") > order.index("exchange:binance")

        # Subsystems wired.
        assert StubDatabasePool.instances[-1].initialize_called
        assert StubRedisClient.instances[-1].connect_called
        assert set(sup._exchanges.keys()) == {"binance", "okx"}
        assert StubUniverseMatrix.instances[-1].built
        assert sup.is_ready is True
    finally:
        await sup.stop()


@pytest.mark.asyncio
async def test_signal_handler_triggers_stop() -> None:
    """SIGTERM / SIGINT handler must invoke stop() via the running loop."""
    cfg = _make_config()
    sup = TrackingSupervisor(cfg)
    await sup.start()
    try:
        # Invoke the handler directly (equivalent to what loop.add_signal_handler does).
        sup._on_signal()
        # Let the scheduled stop() task run.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        # _shutdown_requested must be set even if stop() is still scheduling.
        assert sup._shutdown_requested.is_set()
    finally:
        await sup.stop()
    assert sup._stopped is True


@pytest.mark.asyncio
async def test_register_background_task_tags_name() -> None:
    """register_background_task() prefixes the asyncio.Task name with leviathan_."""
    cfg = _make_config()
    sup = TrackingSupervisor(cfg)

    async def _sleeper() -> None:
        await asyncio.sleep(3600)

    task = await sup.register_background_task("my_loop", _sleeper())
    try:
        assert task.get_name() == "leviathan_my_loop"
        assert task in sup.background_tasks
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_failed_db_init_raises_and_blocks_downstream() -> None:
    """If DB init fails, Redis/exchange/matrix init must NOT be attempted."""
    cfg = _make_config()
    sup = TrackingSupervisor(cfg, db_pool_cls=FailingDatabasePool)
    with pytest.raises(ConnectionError, match="db boom"):
        await sup.start()

    # Redis and exchange seams were never called.
    assert "redis" not in sup.boot_call_order
    assert not any(o.startswith("exchange:") for o in sup.boot_call_order)
    assert sup.is_ready is False
    assert any("supervisor.boot_failed" in e for e in sup._errors)

    # Safe to call stop().
    await sup.stop()


@pytest.mark.asyncio
async def test_empty_active_exchanges_raises() -> None:
    """config.exchanges.active = [] must raise during start()."""
    cfg = _make_config(active=[])
    sup = TrackingSupervisor(cfg)
    with pytest.raises(RuntimeError, match="exchanges_empty"):
        await sup.start()
    assert sup.is_ready is False
    await sup.stop()


@pytest.mark.asyncio
async def test_shutdown_cancels_background_tasks() -> None:
    """stop() cancels all registered background tasks inside the timeout."""
    cfg = _make_config()
    cancelled_flag = {"hit": False}

    async def _long_running() -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled_flag["hit"] = True
            raise

    sup = TrackingSupervisor(
        cfg, task_factories=[("long_running", _long_running)]
    )
    await sup.start()
    assert len(sup.background_tasks) == 1
    # Give the scheduler a tick so the coroutine enters its sleep() before we cancel.
    await asyncio.sleep(0.01)
    await sup.stop()
    assert cancelled_flag["hit"] is True
    assert sup.background_tasks == []
    assert StubRedisClient.instances[-1].disconnect_called
    assert StubDatabasePool.instances[-1].close_called


@pytest.mark.asyncio
async def test_shutdown_timeout_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    """stop() must honor SHUTDOWN_TIMEOUT — even uncancellable tasks don't hang it."""
    cfg = _make_config()

    async def _stubborn() -> None:
        # Shield from cancellation to simulate a badly-behaved task.
        try:
            while True:
                await asyncio.shield(asyncio.sleep(0.05))
        except asyncio.CancelledError:
            # Keep looping to defeat stop()'s cancel — but only briefly so the
            # test doesn't actually hang. The SHUTDOWN_TIMEOUT is the escape hatch.
            await asyncio.sleep(5)

    sup = TrackingSupervisor(
        cfg, task_factories=[("stubborn", _stubborn)]
    )
    # Tighten the timeout so the test stays fast.
    monkeypatch.setattr(TrackingSupervisor, "SHUTDOWN_TIMEOUT", 0.3, raising=True)
    await sup.start()

    stop_coro = sup.stop()
    # stop() must return within a few seconds regardless of stubborn task.
    await asyncio.wait_for(stop_coro, timeout=2.0)
    # Supervisor marks itself stopped even on timeout.
    assert sup._stopped is True


@pytest.mark.asyncio
async def test_get_health_reflects_state() -> None:
    cfg = _make_config()

    async def _ticker() -> None:
        await asyncio.sleep(3600)

    sup = TrackingSupervisor(cfg, task_factories=[("ticker", _ticker)])

    # Pre-start: not ready.
    h0 = sup.get_health()
    assert isinstance(h0, SupervisorHealth)
    assert h0.is_ready is False
    assert h0.background_tasks_count == 0

    await sup.start()
    try:
        h1 = sup.get_health()
        assert h1.is_ready is True
        assert h1.background_tasks_count == 1
        assert h1.errors == []
    finally:
        await sup.stop()

    h2 = sup.get_health()
    assert h2.is_ready is False
    assert h2.background_tasks_count == 0


@pytest.mark.asyncio
async def test_shutdown_request_triggers_stop() -> None:
    """shutdown_request() fires the shutdown event and invokes stop()."""
    cfg = _make_config()
    sup = TrackingSupervisor(cfg)
    await sup.start()
    await sup.shutdown_request()
    assert sup._shutdown_requested.is_set()
    assert sup._stopped is True
    assert sup.is_ready is False


@pytest.mark.asyncio
async def test_stop_is_idempotent() -> None:
    """Calling stop() twice is a no-op on the second call."""
    cfg = _make_config()
    sup = TrackingSupervisor(cfg)
    await sup.start()
    await sup.stop()
    # Second call should not raise / double-close.
    await sup.stop()
    assert StubDatabasePool.instances[-1].close_called is True


@pytest.mark.asyncio
async def test_background_task_factories_executed_once() -> None:
    """Each factory yields exactly one task, and names are tagged."""
    cfg = _make_config()
    call_counter = {"a": 0, "b": 0}

    async def _a() -> None:
        call_counter["a"] += 1
        await asyncio.sleep(3600)

    async def _b() -> None:
        call_counter["b"] += 1
        await asyncio.sleep(3600)

    sup = TrackingSupervisor(
        cfg, task_factories=[("worker_a", _a), ("worker_b", _b)]
    )
    await sup.start()
    try:
        names = {t.get_name() for t in sup.background_tasks}
        assert names == {"leviathan_worker_a", "leviathan_worker_b"}
        # Give the scheduler a moment to enter each coro.
        await asyncio.sleep(0.01)
        assert call_counter["a"] == 1
        assert call_counter["b"] == 1
    finally:
        await sup.stop()


@pytest.mark.asyncio
async def test_signal_handlers_installed_for_sigterm_sigint() -> None:
    """_installed_signals records SIGTERM + SIGINT after start()."""
    cfg = _make_config()
    sup = TrackingSupervisor(cfg)
    await sup.start()
    try:
        # At least one of SIGTERM/SIGINT must be installed (both on Unix).
        assert signal.SIGTERM in sup._installed_signals or signal.SIGINT in sup._installed_signals
    finally:
        await sup.stop()
    # After stop(), the handler list is cleared.
    assert sup._installed_signals == []
