"""LifecycleManager — Phase 5.3 (2026-04-26).

start_background_tasks 270 LOC if-elif → register pattern.

원본: engine/src/runtime/background_loops.py:start_background_tasks (Phase 4-6).
- if-elif EngineMode.BACKTEST/PAPER/LIVE 분기
- asyncio.create_task 직접 호출 (depends_on 추적 없음)
- shutdown 순서 보장 X

설계 (Nautilus Component lifecycle 미러):
- register(name, factory, depends_on=[...], priority=N)
- start_all(): topological sort + asyncio.create_task
- stop_all(): graceful shutdown 역순 (depends_on 역순)
- get_task(name) — task lookup
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass
class _LifecycleEntry:
    name: str
    factory: Callable[[], Awaitable[None]]
    depends_on: list[str] = field(default_factory=list)
    priority: int = 0
    task: asyncio.Task | None = None


class LifecycleManager:
    """Background task lifecycle 통합 관리자.

    Phase 4-6 background_loops.start_background_tasks (270 LOC if-elif) → register pattern.

    사용:
        mgr = LifecycleManager()
        mgr.register("paper_mode", lambda: paper_mode_loop(engine))
        mgr.register("dashboard_feed", lambda: dashboard_feed_loop(engine), depends_on=["paper_mode"])
        await mgr.start_all()
        # ... 운영 ...
        await mgr.stop_all()
    """

    def __init__(self) -> None:
        self._entries: dict[str, _LifecycleEntry] = {}
        self._started: bool = False

    def register(
        self,
        name: str,
        factory: Callable[[], Awaitable[None]],
        depends_on: list[str] | None = None,
        priority: int = 0,
    ) -> None:
        """Background task 등록.

        Args:
            name: 고유 식별자.
            factory: () → Awaitable[None]. 실제 task 본문.
            depends_on: 이 task 시작 전 시작되어야 하는 task names.
            priority: 동등 의존성 시 정렬 키 (낮을수록 먼저).
        """
        if self._started:
            raise RuntimeError(
                f"Cannot register '{name}' after start_all(). Phase 5.3 contract."
            )
        if name in self._entries:
            raise ValueError(f"Duplicate task name: '{name}'")
        self._entries[name] = _LifecycleEntry(
            name=name,
            factory=factory,
            depends_on=list(depends_on or []),
            priority=priority,
        )
        logger.debug(
            "LifecycleManager.registered name=%s depends_on=%s priority=%d",
            name, depends_on or [], priority,
        )

    @property
    def task_count(self) -> int:
        return len(self._entries)

    @property
    def task_names(self) -> list[str]:
        return list(self._entries.keys())

    def _topological_order(self) -> list[str]:
        """Kahn's algorithm: depends_on 위반 없이 시작 순서 결정.

        priority 동률 시 등록 순서 유지.
        Cycle 감지 → ValueError.
        """
        # in-degree 계산
        in_degree = {n: 0 for n in self._entries}
        for entry in self._entries.values():
            for dep in entry.depends_on:
                if dep not in self._entries:
                    raise ValueError(f"'{entry.name}' depends on unknown '{dep}'")
                in_degree[entry.name] += 1

        # priority 정렬 + ready queue
        ready = sorted(
            [n for n, deg in in_degree.items() if deg == 0],
            key=lambda n: (self._entries[n].priority, list(self._entries.keys()).index(n)),
        )
        order: list[str] = []
        while ready:
            current = ready.pop(0)
            order.append(current)
            # 이 task에 depends_on 했던 다른 task in-degree 감소
            for n, entry in self._entries.items():
                if current in entry.depends_on:
                    in_degree[n] -= 1
                    if in_degree[n] == 0:
                        # priority 정렬 위치 삽입
                        inserted = False
                        for i, r in enumerate(ready):
                            if (self._entries[n].priority, list(self._entries.keys()).index(n)) < (
                                self._entries[r].priority, list(self._entries.keys()).index(r)
                            ):
                                ready.insert(i, n)
                                inserted = True
                                break
                        if not inserted:
                            ready.append(n)
        if len(order) != len(self._entries):
            remaining = set(self._entries) - set(order)
            raise ValueError(f"Dependency cycle detected: {remaining}")
        return order

    async def start_all(self) -> list[asyncio.Task]:
        """topological 순서로 background task 시작. 시작된 Task 리스트 반환.

        Idempotent하지 않음: 한 번만 호출 가능 (register lock).
        """
        if self._started:
            raise RuntimeError("LifecycleManager.start_all() already called.")
        order = self._topological_order()
        tasks: list[asyncio.Task] = []
        for name in order:
            entry = self._entries[name]
            try:
                entry.task = asyncio.create_task(entry.factory(), name=name)
                tasks.append(entry.task)
                logger.info("LifecycleManager.started name=%s", name)
            except Exception as exc:
                logger.error("LifecycleManager.start_failed name=%s error=%s", name, exc)
                raise
        self._started = True
        return tasks

    async def stop_all(self, timeout: float = 30.0) -> None:
        """역순으로 graceful shutdown. depends_on 역순.

        cancel + wait. timeout 초과 시 강제 cancel.
        """
        if not self._started:
            return
        order = self._topological_order()
        for name in reversed(order):
            entry = self._entries[name]
            if entry.task and not entry.task.done():
                entry.task.cancel()
                try:
                    await asyncio.wait_for(entry.task, timeout=timeout)
                except asyncio.CancelledError:
                    pass
                except asyncio.TimeoutError:
                    logger.warning("LifecycleManager.stop_timeout name=%s", name)
                except Exception as exc:
                    logger.warning("LifecycleManager.stop_error name=%s error=%s", name, exc)
                logger.info("LifecycleManager.stopped name=%s", name)
        self._started = False

    def get_task(self, name: str) -> asyncio.Task | None:
        entry = self._entries.get(name)
        return entry.task if entry else None
