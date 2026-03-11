"""Slippage feedback loop — EMA-based model parameter adjustment.

US-115: Track actual vs expected slippage and auto-adjust via EMA.
NOTE: Adjustment factor is ONLY for parameter calibration.
      NEVER apply get_adjusted_slippage() result to fill_price directly.
"""
from __future__ import annotations

from collections import deque
from decimal import Decimal

import structlog

from src.infra.metrics import SLIPPAGE_ADJUSTMENT, SLIPPAGE_ERROR

logger = structlog.get_logger(__name__)


class SlippageFeedbackLoop:
    """Track actual vs expected slippage and auto-adjust via EMA.

    Positive adjustment → model underestimates slippage (actual > predicted).
    Negative adjustment → model overestimates slippage (actual < predicted).
    """

    def __init__(self, alpha: float = 0.1, window: int = 100) -> None:
        self._alpha = alpha
        self._window = window
        self._errors: deque[float] = deque(maxlen=window)
        self._adjustment: float = 0.0
        self._count: int = 0

    def record_fill(
        self,
        expected_price: Decimal,
        actual_price: Decimal,
        side: str,
    ) -> None:
        """Record actual fill vs expected, update EMA adjustment.

        Args:
            expected_price: Price predicted by slippage model.
            actual_price:   Price actually received on fill.
            side:           "BUY" or "SELL".
        """
        if expected_price <= 0:
            return

        if side.upper() == "BUY":
            # Positive error = paid more than expected (under-estimated slippage)
            error = float((actual_price - expected_price) / expected_price)
        else:
            # Positive error = received less than expected (under-estimated slippage)
            error = float((expected_price - actual_price) / expected_price)

        # Clamp error to ±1% (100bps) to prevent outlier corruption
        error = max(-0.01, min(0.01, error))

        self._errors.append(error)
        self._count += 1

        if self._count == 1:
            self._adjustment = error
        else:
            self._adjustment = self._alpha * error + (1 - self._alpha) * self._adjustment

        SLIPPAGE_ADJUSTMENT.set(self._adjustment)
        SLIPPAGE_ERROR.observe(error)
        logger.debug(
            "slippage_feedback",
            error=error,
            adjustment=self._adjustment,
            count=self._count,
        )

    def get_adjusted_slippage(self, base_slippage_bps: float) -> float:
        """Return calibrated slippage bps for BookWalkSlippage._fallback_bps.

        NOTE: This is ONLY for parameter calibration. NEVER apply to fill_price directly.
        """
        adjusted = base_slippage_bps * (1.0 + self._adjustment)
        return max(0.0, adjusted)  # Never return negative slippage

    @property
    def adjustment_factor(self) -> float:
        return self._adjustment

    @property
    def sample_count(self) -> int:
        return self._count
