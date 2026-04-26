"""TCAListener — Phase 5.2.4 listener #6 (2026-04-26).

ExecutionResult → TCAAnalyzer.record_execution (transaction cost analysis).
원본: engine/src/runtime/risk_execution.py:740-777
"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class TCAListener:
    """Single-responsibility: transaction cost analysis feed."""

    name = "tca"

    def __init__(self, tca_analyzer: Any) -> None:
        self._tca = tca_analyzer

    def on_execution_result(self, request: Any, result: Any) -> None:
        if self._tca is None:
            return
        try:
            legs = getattr(result, "legs", [])
            for idx, leg in enumerate(legs):
                trade = getattr(leg, "trade", None)
                if trade is None:
                    continue
                latency_ms = float(
                    getattr(result, "execution_duration_ms", 0)
                    or getattr(result, "duration_ms", 0)
                    or 0
                )
                expected = 0.0
                if idx < len(request.legs):
                    expected = float(request.legs[idx].price or 0)
                if expected <= 0:
                    expected = float(getattr(getattr(leg, "order", None), "price", 0) or 0)
                if expected <= 0:
                    logger.debug("TCA: skipping leg %d — no expected price", idx)
                    continue
                try:
                    _signal_ts = request.timestamp.timestamp()
                except (AttributeError, TypeError):
                    _signal_ts = 0.0
                self._tca.record_execution(
                    expected_price=expected,
                    fill_price=float(trade.price),
                    latency_ms=latency_ms,
                    filled_ratio=float(getattr(leg, "filled_ratio", 1.0)),
                    strategy_id=request.strategy_id,
                    signal_ts=_signal_ts,
                    fill_ts=time.time(),
                )
        except Exception as exc:
            logger.debug("tca_listener.error %s", exc)
