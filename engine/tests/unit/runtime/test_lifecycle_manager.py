"""Phase 5.3 LifecycleManager 검증."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.runtime.lifecycle_manager import LifecycleManager


async def _stub_task(events: list, name: str) -> None:
    events.append(f"{name}_start")
    try:
        await asyncio.sleep(60)
    except asyncio.CancelledError:
        events.append(f"{name}_stop")
        raise


class TestLifecycleManager:
    def test_register_increments_count(self) -> None:
        mgr = LifecycleManager()
        mgr.register("a", lambda: asyncio.sleep(0))
        assert mgr.task_count == 1
        assert "a" in mgr.task_names

    def test_register_duplicate_raises(self) -> None:
        mgr = LifecycleManager()
        mgr.register("a", lambda: asyncio.sleep(0))
        with pytest.raises(ValueError, match="Duplicate"):
            mgr.register("a", lambda: asyncio.sleep(0))

    def test_unknown_dependency_raises(self) -> None:
        mgr = LifecycleManager()
        mgr.register("a", lambda: asyncio.sleep(0), depends_on=["unknown"])
        with pytest.raises(ValueError, match="unknown"):
            mgr._topological_order()

    def test_topological_order_respects_depends_on(self) -> None:
        mgr = LifecycleManager()
        mgr.register("c", lambda: asyncio.sleep(0), depends_on=["b"])
        mgr.register("b", lambda: asyncio.sleep(0), depends_on=["a"])
        mgr.register("a", lambda: asyncio.sleep(0))
        order = mgr._topological_order()
        assert order == ["a", "b", "c"]

    def test_cycle_detection(self) -> None:
        mgr = LifecycleManager()
        mgr.register("a", lambda: asyncio.sleep(0), depends_on=["b"])
        mgr.register("b", lambda: asyncio.sleep(0), depends_on=["a"])
        with pytest.raises(ValueError, match="cycle"):
            mgr._topological_order()

    @pytest.mark.asyncio
    async def test_start_all_creates_tasks(self) -> None:
        events: list = []
        mgr = LifecycleManager()
        mgr.register("a", lambda: _stub_task(events, "a"))
        mgr.register("b", lambda: _stub_task(events, "b"), depends_on=["a"])
        tasks = await mgr.start_all()
        assert len(tasks) == 2
        await asyncio.sleep(0.1)
        assert "a_start" in events
        assert "b_start" in events
        await mgr.stop_all(timeout=2.0)

    @pytest.mark.asyncio
    async def test_start_all_idempotent_blocked(self) -> None:
        mgr = LifecycleManager()
        mgr.register("a", lambda: asyncio.sleep(60))
        await mgr.start_all()
        with pytest.raises(RuntimeError, match="already"):
            await mgr.start_all()
        await mgr.stop_all(timeout=2.0)

    @pytest.mark.asyncio
    async def test_register_after_start_blocked(self) -> None:
        mgr = LifecycleManager()
        mgr.register("a", lambda: asyncio.sleep(60))
        await mgr.start_all()
        with pytest.raises(RuntimeError, match="after start_all"):
            mgr.register("b", lambda: asyncio.sleep(60))
        await mgr.stop_all(timeout=2.0)

    @pytest.mark.asyncio
    async def test_stop_all_reverse_order(self) -> None:
        events: list = []
        mgr = LifecycleManager()
        mgr.register("a", lambda: _stub_task(events, "a"))
        mgr.register("b", lambda: _stub_task(events, "b"), depends_on=["a"])
        await mgr.start_all()
        await asyncio.sleep(0.1)
        await mgr.stop_all(timeout=2.0)
        # b stops first (역순), then a
        b_idx = events.index("b_stop")
        a_idx = events.index("a_stop")
        assert b_idx < a_idx
