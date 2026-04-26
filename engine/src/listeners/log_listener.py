"""LogListener — Phase 5.2.4 listener #1 (trivial, 2026-04-26).

on_execution_result 첫 줄 (`logger.info("Execution result: ...")`) 분리.
사이드이펙트 0, idempotent, mock 매우 단순.

원본: engine/src/runtime/risk_execution.py:521-525
"""
from __future__ import annotations

import logging
from typing import Any

from src.listeners._helpers import get_status_value

logger = logging.getLogger(__name__)


class LogListener:
    """Single-responsibility execution result header trace.

    구현:
        ExecutionResultListener Protocol (src.ports.listener_port).
    """

    name = "log"

    def on_execution_result(self, request: Any, result: Any) -> None:
        """단일 INFO 로그.

        - request.strategy_id: 전략 식별자
        - result.status.value: SUCCESS / FAILURE / PARTIAL / ROLLED_BACK
        """
        try:
            strategy_id = getattr(request, "strategy_id", "unknown")
            status_value = get_status_value(result) or "unknown"
            logger.info(
                "Execution result: strategy=%s status=%s",
                strategy_id, status_value,
            )
        except Exception as exc:
            # Listener 계약: never raise. 로깅 실패 시 silent (dispatcher 안전망 외).
            logger.debug("LogListener internal error (silent): %s", exc)
