"""TradeHistoryListener — Phase 5.2.4 listener #7 (2026-04-26).

ExecutionResult → context.trade_history append (dashboard API feed).
원본: engine/src/runtime/risk_execution.py:778-797
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from src.listeners._helpers import get_status_value, request_to_summary

logger = logging.getLogger(__name__)


class TradeHistoryListener:
    """Single-responsibility: in-memory trade history (dashboard API).

    Codex SUGGEST (2026-04-27): request_to_summary helper 사용 (DRY).
    """

    name = "trade_history"

    def __init__(self, context: Any) -> None:
        self._ctx = context

    def on_execution_result(self, request: Any, result: Any) -> None:
        try:
            legs = getattr(request, "legs", [])
            entry = request_to_summary(request, result)
            entry.update({
                "id": str(uuid4()),
                "side": "arbitrage",
                "entry_price": float(legs[0].price or 0) if legs else 0,
                "exit_price": float(legs[-1].price or 0) if legs else 0,
                "status": get_status_value(result) or "unknown",
            })
            self._ctx.trade_history.append(entry)
        except Exception as exc:
            logger.debug("Failed to record trade to context: %s", exc)
