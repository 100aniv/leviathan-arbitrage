"""PositionSizeLeakListener — Phase 5.2.4 listener #11 (HIGH risk, 2026-04-26).

success status에서 BUY/SELL net 후 _position_sizes 업데이트.
RiskGuardian Check #1 (directional exposure) 의존.

원본: engine/src/runtime/risk_execution.py:533-548

⚠️ NOT idempotent — replay 시 double-count. Phase 5.3+ fill_id dedup 필요.

Codex final review 정합 (2026-04-26):
- Alert/elevation 복원: 에러 누적 5회 초과 시 Telegram alert (legacy parity)
- helpers (get_side, is_status_success) 사용
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Any

from src.listeners._helpers import get_side, is_status_success

logger = logging.getLogger(__name__)

_ERROR_THRESHOLD = 5  # alert after this many failures


class PositionSizeLeakListener:
    """Single-responsibility: position_sizes BUY/SELL net + error escalation.

    DI:
    - position_sizes: mutable EngineState dict
    - state: EngineState (for error counter)
    - alert_bot: optional alert sender (TelegramBot or None) — Codex final review parity
    """

    name = "position_size_leak"

    def __init__(
        self,
        position_sizes: dict[str, Decimal],
        state: Any = None,
        alert_bot: Any = None,
    ) -> None:
        self._sizes = position_sizes
        self._state = state
        self._alert_bot = alert_bot

    def on_execution_result(self, request: Any, result: Any) -> None:
        if not is_status_success(result):
            return
        try:
            for leg in getattr(result, "legs", []):
                trade = getattr(leg, "trade", None)
                order = getattr(leg, "order", None)
                if trade is None or order is None:
                    continue
                symbol = order.symbol
                pos_value = trade.price * trade.amount
                side = get_side(order)
                if side == "BUY":
                    self._sizes[symbol] = self._sizes.get(symbol, Decimal("0")) + pos_value
                else:
                    current = self._sizes.get(symbol, Decimal("0"))
                    updated = max(Decimal("0"), current - pos_value)
                    if updated == Decimal("0"):
                        self._sizes.pop(symbol, None)
                    else:
                        self._sizes[symbol] = updated
        except Exception as exc:
            logger.error(
                "position_size_leak.error strategy=%s error=%s",
                getattr(request, "strategy_id", "unknown"), exc,
            )
            self._maybe_escalate_alert()

    def _maybe_escalate_alert(self) -> None:
        """Legacy parity: position_tracking_errors > _ERROR_THRESHOLD → Telegram alert."""
        if self._state is None:
            return
        self._state.position_tracking_errors = (
            getattr(self._state, "position_tracking_errors", 0) + 1
        )
        count = self._state.position_tracking_errors
        if count > _ERROR_THRESHOLD and self._alert_bot is not None:
            try:
                asyncio.ensure_future(
                    self._alert_bot.send_alert_kr(
                        "position_tracking_fail",
                        {"error_count": count},
                    )
                )
            except Exception:
                pass  # non-critical alert failure
