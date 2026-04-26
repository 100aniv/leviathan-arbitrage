"""TradeHistoryListener — Phase 5.2.4 listener #7 (2026-04-26).

ExecutionResult → context.trade_history append (dashboard API feed).
원본: engine/src/runtime/risk_execution.py:778-797
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from src.listeners._helpers import effective_pnl

logger = logging.getLogger(__name__)


class TradeHistoryListener:
    """Single-responsibility: in-memory trade history (dashboard API)."""

    name = "trade_history"

    def __init__(self, context: Any) -> None:
        self._ctx = context

    def on_execution_result(self, request: Any, result: Any) -> None:
        try:
            legs = getattr(request, "legs", [])
            entry = {
                "id": str(uuid4()),
                "strategy_id": request.strategy_id,
                "symbol": legs[0].symbol if legs else "UNKNOWN",
                "buy_exchange": next((l.exchange_id for l in legs if l.side.value == "buy"), ""),
                "sell_exchange": next((l.exchange_id for l in legs if l.side.value == "sell"), ""),
                "side": "arbitrage",
                "size": float(legs[0].size) if legs else 0,
                "entry_price": float(legs[0].price or 0) if legs else 0,
                "exit_price": float(legs[-1].price or 0) if legs else 0,
                "pnl": effective_pnl(request, result),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": getattr(getattr(result, "status", None), "value", "unknown"),
            }
            self._ctx.trade_history.append(entry)
        except Exception as exc:
            logger.debug("Failed to record trade to context: %s", exc)
