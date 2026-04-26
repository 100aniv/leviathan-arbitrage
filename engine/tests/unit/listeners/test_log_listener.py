"""Phase 5.2.4 LogListener 검증."""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.listeners.log_listener import LogListener
from src.ports.listener_port import ExecutionResultListener


class TestLogListener:
    def test_implements_listener_port(self) -> None:
        """LogListener satisfies runtime_checkable ExecutionResultListener Protocol."""
        listener = LogListener()
        assert isinstance(listener, ExecutionResultListener)
        assert listener.name == "log"

    def test_on_execution_result_logs_info(self, caplog: pytest.LogCaptureFixture) -> None:
        listener = LogListener()
        request = SimpleNamespace(strategy_id="cross_exchange_v1")
        result = SimpleNamespace(status=SimpleNamespace(value="SUCCESS"))
        with caplog.at_level(logging.INFO, logger="src.listeners.log_listener"):
            listener.on_execution_result(request, result)
        assert any("strategy=cross_exchange_v1" in r.message for r in caplog.records)
        assert any("status=SUCCESS" in r.message for r in caplog.records)

    def test_handles_missing_attributes_gracefully(self, caplog: pytest.LogCaptureFixture) -> None:
        """request/result attribute 부재 시 silent fallback."""
        listener = LogListener()
        request = SimpleNamespace()  # strategy_id 없음
        result = SimpleNamespace()    # status 없음
        with caplog.at_level(logging.INFO, logger="src.listeners.log_listener"):
            listener.on_execution_result(request, result)  # must not raise
        # default fallback "unknown" 로깅
        assert any("strategy=unknown" in r.message for r in caplog.records)
