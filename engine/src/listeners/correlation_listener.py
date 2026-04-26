"""CorrelationListener — Phase 5.2.4 listener #5 (2026-04-26).

ExecutionResult → CorrelationMonitor.record_trade_pnl.
원본: engine/src/runtime/risk_execution.py:733-739
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class CorrelationListener:
    """Single-responsibility: per-strategy correlation matrix feed."""

    name = "correlation"

    def __init__(self, correlation_monitor: Any) -> None:
        self._mon = correlation_monitor

    def on_execution_result(self, request: Any, result: Any) -> None:
        if self._mon is None:
            return
        try:
            pnl = (float(result.pnl) if hasattr(result, "pnl") and result.pnl is not None
                   else float(getattr(request, "expected_profit_usdt", 0)))
            self._mon.record_trade_pnl(request.strategy_id, pnl)
        except Exception as exc:
            logger.debug("correlation_listener.error %s", exc)
