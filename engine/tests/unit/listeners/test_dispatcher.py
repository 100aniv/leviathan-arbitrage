"""Phase 5.2.5 ExecutionResultDispatcher 검증."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.listeners.dispatcher import ExecutionResultDispatcher
from src.listeners.log_listener import LogListener


class _SyncListener:
    name = "sync"

    def __init__(self) -> None:
        self.calls: list = []

    def on_execution_result(self, request, result) -> None:
        self.calls.append((request, result))


class _AsyncListener:
    name = "async"

    def __init__(self) -> None:
        self.calls: list = []

    async def on_execution_result(self, request, result) -> None:
        self.calls.append((request, result))


class _RaisingListener:
    name = "raising"

    def on_execution_result(self, request, result) -> None:
        raise RuntimeError("intentional fail")


class TestExecutionResultDispatcher:
    def test_register_listener(self) -> None:
        d = ExecutionResultDispatcher()
        d.register(LogListener())
        assert d.listener_count == 1
        assert d.listener_names == ["log"]

    def test_register_invalid_listener_raises(self) -> None:
        d = ExecutionResultDispatcher()
        # int does not satisfy Protocol
        with pytest.raises(TypeError):
            d.register(123)  # type: ignore

    def test_unregister(self) -> None:
        d = ExecutionResultDispatcher()
        listener = LogListener()
        d.register(listener)
        assert d.unregister(listener) is True
        assert d.listener_count == 0
        assert d.unregister(listener) is False  # already removed

    @pytest.mark.asyncio
    async def test_dispatch_sequential(self) -> None:
        d = ExecutionResultDispatcher()
        sync = _SyncListener()
        async_l = _AsyncListener()
        d.register(sync)
        d.register(async_l)
        request = SimpleNamespace(strategy_id="x")
        result = SimpleNamespace(status=SimpleNamespace(value="success"))
        await d.dispatch(request, result)
        assert len(sync.calls) == 1
        assert len(async_l.calls) == 1

    @pytest.mark.asyncio
    async def test_dispatch_isolates_failures(self) -> None:
        """한 listener 실패가 다음 listener 실행 차단하지 않음."""
        d = ExecutionResultDispatcher()
        d.register(_RaisingListener())
        sync = _SyncListener()
        d.register(sync)
        await d.dispatch(SimpleNamespace(), SimpleNamespace())
        assert len(sync.calls) == 1  # raising 후에도 호출됨
