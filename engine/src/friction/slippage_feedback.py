"""Slippage Feedback Loop — record predicted vs actual slippage, adjust predictions.

US-283: Collects fill-time slippage delta and provides exchange/pair-level correction.
"""
from __future__ import annotations

import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque

logger = logging.getLogger(__name__)

_ENABLED_DEFAULT = os.getenv("SLIPPAGE_FEEDBACK_ENABLED", "true").lower() != "false"


@dataclass
class SlippageFeedbackRecord:
    timestamp: float
    exchange: str
    pair: str
    predicted_bps: float
    actual_bps: float
    delta_bps: float = field(init=False)

    def __post_init__(self) -> None:
        self.delta_bps = self.actual_bps - self.predicted_bps


class SlippageFeedbackCollector:
    """Records predicted vs actual slippage and provides per-exchange/pair adjustment."""

    _MAX_RECORDS = 1000
    _ADJUSTMENT_WINDOW = 100
    _CLAMP_MIN_RATIO = -0.5   # max reduction: -50% of predicted
    _CLAMP_MAX_RATIO = 1.0    # max increase: +100% of predicted

    def __init__(self, enabled: bool = _ENABLED_DEFAULT) -> None:
        self._records: Deque[SlippageFeedbackRecord] = deque(maxlen=self._MAX_RECORDS)
        self._enabled = enabled

    def record(
        self,
        exchange: str,
        pair: str,
        predicted_bps: float,
        actual_bps: float,
    ) -> None:
        """Record a fill's predicted vs actual slippage."""
        if not self._enabled:
            return
        rec = SlippageFeedbackRecord(
            timestamp=time.time(),
            exchange=exchange,
            pair=pair,
            predicted_bps=predicted_bps,
            actual_bps=actual_bps,
        )
        self._records.append(rec)
        logger.debug(
            "slippage_feedback.recorded exchange=%s pair=%s predicted=%.2f actual=%.2f delta=%.2f",
            exchange, pair, predicted_bps, actual_bps, rec.delta_bps,
        )

    def get_adjustment_bps(self, exchange: str, pair: str) -> float:
        """Return mean delta_bps for the given exchange/pair (last 100 records), clamped."""
        if not self._enabled or not self._records:
            return 0.0

        relevant = [
            r.delta_bps
            for r in list(self._records)[-self._ADJUSTMENT_WINDOW:]
            if r.exchange == exchange and r.pair == pair
        ]
        if not relevant:
            return 0.0

        mean_delta = sum(relevant) / len(relevant)

        # Clamp adjustment to [-50%, +100%] of the mean actual_bps as reference
        mean_actual = sum(
            r.actual_bps
            for r in list(self._records)[-self._ADJUSTMENT_WINDOW:]
            if r.exchange == exchange and r.pair == pair
        ) / len(relevant)

        lower = self._CLAMP_MIN_RATIO * mean_actual
        upper = self._CLAMP_MAX_RATIO * mean_actual
        return max(lower, min(upper, mean_delta))

    async def persist_to_db(self, pool) -> None:
        """Persist recent records to DB (fire-and-forget, never crashes engine)."""
        if not self._enabled or not self._records:
            return
        try:
            async with pool.acquire() as conn:
                rows = list(self._records)[-100:]
                await conn.executemany(
                    """
                    INSERT INTO slippage_feedback
                        (ts, exchange, pair, predicted_bps, actual_bps, delta_bps)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT DO NOTHING
                    """,
                    [
                        (r.timestamp, r.exchange, r.pair,
                         r.predicted_bps, r.actual_bps, r.delta_bps)
                        for r in rows
                    ],
                )
        except Exception as exc:
            logger.debug("slippage_feedback.persist_failed (non-fatal): %s", exc)
