"""CircuitBreakerListener — Phase 5.2.4 listener #8 (2026-04-26).

ExecutionResult → CircuitBreaker.record_win/record_loss.
원본: engine/src/runtime/risk_execution.py:799-820
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Any, Callable

logger = logging.getLogger(__name__)


class CircuitBreakerListener:
    """Single-responsibility: CircuitBreaker consecutive_loss feedback.

    DI:
    - circuit_breaker: CircuitBreaker (또는 None)
    - capital_total_supplier: Callable[[], Decimal] for current capital × exchange count
    - total_pnl_supplier: Callable[[], Decimal] for cumulative PnL
    """

    name = "circuit_breaker"

    def __init__(
        self,
        circuit_breaker: Any,
        capital_total_supplier: Callable[[], Decimal] | None = None,
        total_pnl_supplier: Callable[[], Decimal] | None = None,
    ) -> None:
        self._cb = circuit_breaker
        self._capital_total = capital_total_supplier or (lambda: Decimal("0"))
        self._total_pnl = total_pnl_supplier or (lambda: Decimal("0"))

    def on_execution_result(self, request: Any, result: Any) -> None:
        if self._cb is None:
            return
        try:
            status_val = getattr(getattr(result, "status", None), "value", str(getattr(result, "status", "")))
            if status_val == "success":
                pnl_val = getattr(result, "pnl", None)
                if pnl_val is not None and float(pnl_val) < 0:
                    capital_total = self._capital_total()
                    total_pnl = self._total_pnl()
                    dd_pct = (
                        float(abs(total_pnl) / capital_total)
                        if capital_total > 0 and total_pnl < 0 else 0.0
                    )
                    asyncio.ensure_future(self._cb.record_loss(drawdown_pct=dd_pct))
                else:
                    asyncio.ensure_future(self._cb.record_win())
            elif status_val in ("rolled_back", "rollback_failed", "timeout"):
                asyncio.ensure_future(self._cb.record_loss())
            # status="rejected" → infrastructure reject, NOT counted as consecutive_loss
        except Exception as exc:
            logger.debug("circuit_breaker.feedback error: %s", exc)
