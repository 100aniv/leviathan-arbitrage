"""CrossHedgeListener — Phase 5.2.4 listener #12 (HIGH risk, 2026-04-26).

cross-exchange hedged positions (funding_rate, spot_futures) 추적.
RiskGuardian Check #3 (total exposure) + Check #10 (max concurrent) 의존.

원본: engine/src/runtime/risk_execution.py:595-627

⚠️ NOT idempotent — replay 시 double-count.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)


class CrossHedgeListener:
    """Single-responsibility: cross-exchange hedged position tracking.

    DI:
    - cross_exchange_positions: set (mutable EngineState)
    - cross_gross_exposure_holder: list[Decimal] of length 1 (보존을 위한 mutable wrap)
    """

    name = "cross_hedge"

    def __init__(
        self,
        cross_exchange_positions: set[str],
        cross_gross_exposure_holder: list,
    ) -> None:
        self._positions = cross_exchange_positions
        self._gross_holder = cross_gross_exposure_holder

    def on_execution_result(self, request: Any, result: Any) -> None:
        status_val = getattr(getattr(result, "status", None), "value",
                             str(getattr(result, "status", "")))
        if status_val != "success":
            return
        try:
            legs_info = [
                (getattr(leg, "trade", None), getattr(leg, "order", None))
                for leg in getattr(result, "legs", [])
            ]
            buy_exchanges = {
                order.exchange_id for _, order in legs_info
                if order and getattr(order.side, "value", str(order.side)).upper() == "BUY"
            }
            sell_exchanges = {
                order.exchange_id for _, order in legs_info
                if order and getattr(order.side, "value", str(order.side)).upper() == "SELL"
            }
            symbols_in_exec = {order.symbol for _, order in legs_info if order}
            _is_cross = bool(buy_exchanges and sell_exchanges and buy_exchanges != sell_exchanges)
            _is_close = any(
                isinstance(getattr(order, "metadata", None), dict) and (
                    order.metadata.get("reduceOnly") is True
                    or str(order.metadata.get("leg_type", "")).startswith("settlement_close")
                )
                for _, order in legs_info if order
            )
            for sym in symbols_in_exec:
                if _is_cross and not _is_close:
                    self._positions.add(sym)
                elif _is_close or not _is_cross:
                    self._positions.discard(sym)

            if _is_cross:
                _leg_gross = sum(
                    trade.price * trade.amount
                    for trade, order in legs_info
                    if trade is not None and order is not None
                )
                if _is_close:
                    self._gross_holder[0] = max(Decimal("0"), self._gross_holder[0] - _leg_gross)
                else:
                    self._gross_holder[0] += _leg_gross
        except Exception as exc:
            logger.debug("cross_hedge.error %s", exc)
