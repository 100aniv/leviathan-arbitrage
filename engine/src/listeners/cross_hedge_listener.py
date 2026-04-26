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

from src.listeners._helpers import (
    extract_legs_info,
    get_side,
    is_close_execution,
    is_status_success,
)

logger = logging.getLogger(__name__)


class CrossHedgeListener:
    """Single-responsibility: cross-exchange hedged position tracking.

    DI:
    - cross_exchange_positions: set (mutable EngineState)
    - cross_gross_exposure_holder: list[Decimal] of length 1 (보존을 위한 mutable wrap)

    Codex SUGGEST (2026-04-26): legs_info / close-detection 로직은 _helpers로 추출 (DRY).
    """

    name = "cross_hedge"

    def __init__(self, state: Any) -> None:
        """DI: EngineState 인스턴스 (Phase 5.2.1).

        Mutate state.cross_exchange_positions (set) + state.cross_gross_exposure (Decimal).
        Phase 5.2.6 holder list[Decimal] 패턴 제거 — EngineState 단일 진실 소스.
        """
        self._state = state

    def on_execution_result(self, request: Any, result: Any) -> None:
        if not is_status_success(result):
            return
        try:
            legs_info = extract_legs_info(result)
            buy_exchanges = {
                order.exchange_id for _, order in legs_info
                if order and get_side(order) == "BUY"
            }
            sell_exchanges = {
                order.exchange_id for _, order in legs_info
                if order and get_side(order) == "SELL"
            }
            symbols_in_exec = {order.symbol for _, order in legs_info if order}
            _is_cross = bool(buy_exchanges and sell_exchanges and buy_exchanges != sell_exchanges)
            _is_close = is_close_execution(legs_info)
            for sym in symbols_in_exec:
                if _is_cross and not _is_close:
                    self._state.cross_exchange_positions.add(sym)
                elif _is_close or not _is_cross:
                    self._state.cross_exchange_positions.discard(sym)

            if _is_cross:
                _leg_gross = sum(
                    trade.price * trade.amount
                    for trade, order in legs_info
                    if trade is not None and order is not None
                )
                if _is_close:
                    self._state.cross_gross_exposure = max(
                        Decimal("0"), self._state.cross_gross_exposure - _leg_gross,
                    )
                else:
                    self._state.cross_gross_exposure += _leg_gross
        except Exception as exc:
            logger.debug("cross_hedge.error %s", exc)
