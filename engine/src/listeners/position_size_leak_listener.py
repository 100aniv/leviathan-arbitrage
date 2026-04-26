"""PositionSizeLeakListener — Phase 5.2.4 listener #11 (HIGH risk, 2026-04-26).

success status에서 BUY/SELL net 후 _position_sizes 업데이트.
RiskGuardian Check #1 (directional exposure) 의존.

원본: engine/src/runtime/risk_execution.py:533-548

⚠️ NOT idempotent — replay 시 double-count. Phase 5.3+ fill_id dedup 필요.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)


class PositionSizeLeakListener:
    """Single-responsibility: position_sizes BUY/SELL net.

    DI: position_sizes (mutable EngineState dict).
    """

    name = "position_size_leak"

    def __init__(self, position_sizes: dict[str, Decimal]) -> None:
        self._sizes = position_sizes

    def on_execution_result(self, request: Any, result: Any) -> None:
        status_val = getattr(getattr(result, "status", None), "value",
                             str(getattr(result, "status", "")))
        if status_val != "success":
            return
        try:
            for leg in getattr(result, "legs", []):
                trade = getattr(leg, "trade", None)
                order = getattr(leg, "order", None)
                if trade is None or order is None:
                    continue
                symbol = order.symbol
                pos_value = trade.price * trade.amount
                side = getattr(order.side, "value", str(order.side)).upper()
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
            logger.debug("position_size_leak.error %s", exc)
