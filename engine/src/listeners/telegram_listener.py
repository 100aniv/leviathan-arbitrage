"""TelegramListener — Phase 5.2.4 listener #10 (2026-04-26).

success status → TradeBot.send_fill_kr (Korean fill notification).
원본: engine/src/runtime/risk_execution.py:862-876
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class TelegramListener:
    """Single-responsibility: Korean fill notification via TradeBot."""

    name = "telegram"

    def __init__(self, trade_bot: Any) -> None:
        self._bot = trade_bot

    def on_execution_result(self, request: Any, result: Any) -> None:
        if self._bot is None:
            return
        status_val = getattr(getattr(result, "status", None), "value", str(getattr(result, "status", "")))
        if status_val != "success":
            return
        try:
            legs = getattr(request, "legs", [])
            fill_data = {
                "strategy_id": request.strategy_id,
                "symbol": legs[0].symbol if legs else "UNKNOWN",
                "buy_exchange": next((l.exchange_id for l in legs if l.side.value == "buy"), ""),
                "sell_exchange": next((l.exchange_id for l in legs if l.side.value == "sell"), ""),
                "size": float(legs[0].size) if legs else 0,
                "pnl": (
                    float(result.pnl) if hasattr(result, "pnl") and result.pnl is not None
                    else float(getattr(request, "expected_profit_usdt", 0))
                ),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            asyncio.ensure_future(self._bot.send_fill_kr(fill_data))
        except Exception as exc:
            logger.debug("telegram.fill_notification_failed: %s", exc)
