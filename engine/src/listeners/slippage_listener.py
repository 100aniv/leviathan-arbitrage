"""SlippageListener — Phase 5.2.4 listener #4 (2026-04-26).

ExecutionResult → SlippageFeedbackCollector.record_fill.
원본: engine/src/runtime/risk_execution.py:721-732
"""
from __future__ import annotations

import logging
from typing import Any

from src.listeners._helpers import get_side

logger = logging.getLogger(__name__)


class SlippageListener:
    """Single-responsibility: slippage feedback feed."""

    name = "slippage"

    def __init__(self, slippage_feedback: Any) -> None:
        self._fb = slippage_feedback

    def on_execution_result(self, request: Any, result: Any) -> None:
        if self._fb is None or not hasattr(result, "legs"):
            return
        try:
            for leg in result.legs:
                if hasattr(leg, "expected_price") and hasattr(leg, "fill_price"):
                    side = get_side(leg.order) if leg.order else "BUY"
                    self._fb.record_fill(
                        expected_price=leg.expected_price,
                        actual_price=leg.fill_price,
                        side=side or "BUY",
                    )
        except Exception as exc:
            logger.debug("slippage_listener.error %s", exc)
