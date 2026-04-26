"""TelegramListener — Phase 5.2.4 listener #10 (2026-04-26).

success status → TradeBot.send_fill_kr (Korean fill notification).
원본: engine/src/runtime/risk_execution.py:862-876
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from src.listeners._helpers import is_status_success, request_to_summary
from src.ports import AlertPort

logger = logging.getLogger(__name__)


class TelegramListener:
    """Single-responsibility: Korean fill notification via AlertPort.

    Codex SUGGEST (2026-04-27): AlertPort actual adoption — vendor-neutral
    (Telegram/Discord/Slack 무관 substitutable).
    """

    name = "telegram"

    def __init__(self, trade_bot: Optional[AlertPort]) -> None:
        self._bot: Optional[AlertPort] = trade_bot

    def on_execution_result(self, request: Any, result: Any) -> None:
        if self._bot is None:
            return
        if not is_status_success(result):
            return
        try:
            asyncio.ensure_future(self._bot.send_fill_kr(request_to_summary(request, result)))
        except Exception as exc:
            logger.debug("telegram.fill_notification_failed: %s", exc)
