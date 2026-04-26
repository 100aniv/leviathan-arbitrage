"""PnLPeakListener — Phase 5.2.4 listener #13 (HIGH risk, 2026-04-26).

PnL + peak equity 업데이트. RiskGuardian Check #2 (drawdown) 의존.

원본: engine/src/runtime/risk_execution.py:628-647

⚠️ NOT idempotent — replay 시 double-count. Phase 5.3+ PnLLedger와 통합 필요.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Callable

logger = logging.getLogger(__name__)


class PnLPeakListener:
    """Single-responsibility: total_pnl + peak_equity update.

    DI:
    - state: EngineState 인스턴스 (mutable runtime state)
    - capital_total_supplier: Callable[[], Decimal]
    """

    name = "pnl_peak"

    def __init__(
        self,
        state: Any,
        capital_total_supplier: Callable[[], Decimal] | None = None,
    ) -> None:
        self._state = state
        self._capital_total = capital_total_supplier or (lambda: Decimal("0"))

    def on_execution_result(self, request: Any, result: Any) -> None:
        status_val = getattr(getattr(result, "status", None), "value",
                             str(getattr(result, "status", "")))
        if status_val != "success":
            return
        try:
            pnl_raw = getattr(result, "pnl", None)
            if pnl_raw is None:
                # Estimate from fill prices
                pnl_estimate = Decimal("0")
                for leg in getattr(result, "legs", []):
                    t = getattr(leg, "trade", None)
                    o = getattr(leg, "order", None)
                    if t and o:
                        val = t.price * t.amount
                        s = getattr(o.side, "value", str(o.side)).upper()
                        pnl_estimate += val if s == "SELL" else -val
                pnl_raw = pnl_estimate

            self._state.total_pnl += Decimal(str(pnl_raw))
            capital_total = self._capital_total()
            current_equity = capital_total + self._state.total_pnl
            if self._state.peak_equity is not None and current_equity > self._state.peak_equity:
                self._state.peak_equity = current_equity
        except Exception as exc:
            logger.debug("pnl_peak.error %s", exc)
