"""ExposureListener — Phase 5.2.4 listener #3 (2026-04-26).

ExecutionResult → asyncio.create_task(exposure_tracker.update_exposure(...))
RiskGuardian Check #4e (net exposure per asset, Amendment 7) consumer.

원본: engine/src/runtime/risk_execution.py:694-719

설계:
- async fire-and-forget (asyncio.create_task)
- task exception은 add_done_callback에서 로깅, 전파하지 않음
- exposure_tracker None이면 silent skip
- success status only
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Any

from src.listeners._helpers import get_side, is_status_success

logger = logging.getLogger(__name__)


class ExposureListener:
    """Single-responsibility: ExposureTracker async update.

    Dependencies:
    - exposure_tracker: ExposureTracker instance (또는 None)

    Idempotency: depends on tracker impl. Redis SET semantics 가정 = idempotent.
    """

    name = "exposure"

    def __init__(self, exposure_tracker: Any) -> None:
        self._tracker = exposure_tracker

    def on_execution_result(self, request: Any, result: Any) -> None:
        """async create_task for each filled leg. exception은 callback에서 로깅."""
        try:
            if not is_status_success(result) or self._tracker is None:
                return

            for leg in getattr(result, "legs", []):
                order = getattr(leg, "order", None)
                trade = getattr(leg, "trade", None)
                if order is None or trade is None:
                    continue
                if "/" not in getattr(order, "symbol", ""):
                    continue
                base_asset = order.symbol.split("/")[0]
                side = get_side(order)
                delta = trade.amount if side == "BUY" else -trade.amount
                _ex_id = (order.exchange_id if hasattr(order, "exchange_id")
                          else getattr(leg, "exchange_id", "unknown"))
                _task = asyncio.create_task(
                    self._tracker.update_exposure(_ex_id, base_asset, Decimal(str(delta)))
                )

                def _on_exp_done(t: asyncio.Task, _ex=_ex_id, _ba=base_asset) -> None:
                    if not t.cancelled() and t.exception() is not None:
                        logger.warning(
                            "exposure_tracker.update_failed ex=%s asset=%s err=%s",
                            _ex, _ba, t.exception(),
                        )
                _task.add_done_callback(_on_exp_done)
        except Exception as exc:
            logger.debug("exposure_tracking.loop_error %s", exc)
