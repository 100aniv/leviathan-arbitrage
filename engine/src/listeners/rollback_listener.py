"""RollbackListener — Phase 5.2.4 listener #9 (2026-04-26).

ROLLED_BACK / REJECTED 완료 시 strategy._open_positions 해제 + position_sizes 누수 fix.
원본: engine/src/runtime/risk_execution.py:822-860
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from src.listeners._helpers import is_close_leg

logger = logging.getLogger(__name__)


class RollbackListener:
    """Single-responsibility: strategy position release on rollback/reject + position_sizes leak fix.

    BUG-J: ROLLED_BACK 완료 시 strategy._open_positions 해제 → 4H 심볼 차단 방지
    BUG-31: REJECTED도 해제 (주문 미발생)
    BUG-95: entry vs exit rollback 구분 (reduceOnly / leg_type)
    WS-3.3: _position_sizes rollback leak fix

    DI:
    - strategy_manager: StrategyManager (또는 None)
    - position_sizes: dict (mutable state)
    """

    name = "rollback"

    def __init__(
        self,
        strategy_manager: Any,
        position_sizes: dict[str, Decimal],
    ) -> None:
        self._mgr = strategy_manager
        self._position_sizes = position_sizes

    def on_execution_result(self, request: Any, result: Any) -> None:
        status_val = getattr(getattr(result, "status", None), "value", str(getattr(result, "status", "")))
        if status_val not in ("rolled_back", "rejected"):
            return

        # 1. strategy._open_positions 해제
        try:
            if self._mgr is not None:
                strategy = self._mgr.get_strategy(request.strategy_id)
                if strategy is not None and request.legs:
                    symbol = request.legs[0].symbol
                    _is_exit = any(is_close_leg(leg) for leg in request.legs)
                    if _is_exit and hasattr(strategy, "handle_exit_rollback"):
                        strategy.handle_exit_rollback(symbol)
                    elif hasattr(strategy, "handle_entry_rollback"):
                        strategy.handle_entry_rollback(symbol)
        except Exception as exc:
            logger.debug("rollback.position_clear_failed %s", exc)

        # 2. WS-3.3: _position_sizes leak reverse
        try:
            for leg in request.legs:
                if leg.symbol and leg.symbol in self._position_sizes:
                    _val = (leg.price or Decimal("0")) * (leg.size or Decimal("0"))
                    if _val > 0:
                        current = self._position_sizes.get(leg.symbol, Decimal("0"))
                        updated = max(Decimal("0"), current - _val)
                        if updated == Decimal("0"):
                            self._position_sizes.pop(leg.symbol, None)
                        else:
                            self._position_sizes[leg.symbol] = updated
        except Exception as exc:
            logger.debug("rollback.position_sizes_leak_fix_failed %s", exc)
