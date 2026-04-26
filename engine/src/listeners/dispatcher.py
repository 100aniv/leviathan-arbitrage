"""ExecutionResultDispatcher — Phase 5.2.5 (2026-04-26).

14 listeners 등록 + 순차 실행 + 예외 격리 (한 listener 실패 → 다음 listener 계속).

설계 원칙 (Phase 5.0 listener-decomposition.md §1):
- Listeners run sequentially in registration order.
- Failure in one listener MUST NOT prevent later listeners.
- async listeners detected via inspect.iscoroutinefunction → asyncio.ensure_future.
- sync listeners: 직접 호출.

원본: engine/src/runtime/risk_execution.py:519-877 (358 LOC) → 이 dispatcher가 대체.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Callable

from src.ports.listener_port import ExecutionResultListener

logger = logging.getLogger(__name__)


class ExecutionResultDispatcher:
    """Manages 14 ExecutionResultListener registrations + sequential dispatch.

    Phase 5.3+ 진화 가능:
    - parallel dispatch (async asyncio.gather)
    - dedup (fill_id 기반)
    - replay tooling (journal-based)
    """

    def __init__(self) -> None:
        self._listeners: list[ExecutionResultListener] = []

    def register(self, listener: ExecutionResultListener) -> None:
        """Listener 등록. order = registration order."""
        if not isinstance(listener, ExecutionResultListener):
            raise TypeError(
                f"listener must implement ExecutionResultListener Protocol, got {type(listener)}"
            )
        self._listeners.append(listener)
        logger.debug("Listener registered: %s", listener.name)

    def unregister(self, listener: ExecutionResultListener) -> bool:
        """Listener 등록 해제. True if removed."""
        try:
            self._listeners.remove(listener)
            return True
        except ValueError:
            return False

    @property
    def listener_count(self) -> int:
        return len(self._listeners)

    @property
    def listener_names(self) -> list[str]:
        return [l.name for l in self._listeners]

    async def dispatch(self, request: Any, result: Any) -> None:
        """모든 listener에 (request, result) 전달. 순차 실행 + 예외 격리.

        async listener는 await, sync는 직접 호출. 한 listener 예외는 catch + log,
        다음 listener 계속. 호출자에게 예외 전파 X (god-function 동작 보존).
        """
        for listener in self._listeners:
            try:
                callback = listener.on_execution_result
                _result_ret = callback(request, result)
                if inspect.iscoroutine(_result_ret):
                    await _result_ret
            except Exception as exc:
                logger.warning(
                    "listener.dispatch_failed name=%s error=%s",
                    listener.name, exc,
                )
                # 다음 listener 계속

    def dispatch_sync(self, request: Any, result: Any) -> None:
        """동기 호출자용. async listener는 ensure_future + done-callback (exception 가시화).

        Codex SUGGEST (2026-04-26): asyncio.ensure_future만으로는 unobserved task
        exception이 silent로 사라짐. done-callback 부착하여 모든 async failure를 log.
        """
        for listener in self._listeners:
            try:
                callback = listener.on_execution_result
                _result_ret = callback(request, result)
                if inspect.iscoroutine(_result_ret):
                    task = asyncio.ensure_future(_result_ret)
                    task.add_done_callback(self._log_async_exception(listener.name))
            except Exception as exc:
                logger.warning(
                    "listener.dispatch_sync_failed name=%s error=%s",
                    listener.name, exc,
                )

    @staticmethod
    def _log_async_exception(listener_name: str) -> Callable[[asyncio.Task[Any]], None]:
        """Done-callback factory: log async listener exceptions (Codex SUGGEST)."""
        def _on_done(task: asyncio.Task[Any]) -> None:
            if task.cancelled():
                return
            exc = task.exception()
            if exc is not None:
                logger.warning(
                    "listener.async_dispatch_failed name=%s error=%s",
                    listener_name, exc,
                )
        return _on_done
