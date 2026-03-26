"""Continuous LiveGate monitor — US-280.

Periodically evaluates LiveGate and triggers a risk halt on failure.
Disabled via LIVE_GATE_CONTINUOUS=0 env var.
"""
from __future__ import annotations

from collections import deque

import asyncio
import logging
import os

logger = logging.getLogger(__name__)


class ContinuousLiveGateMonitor:
    """Runs live-gate evaluation on a fixed interval.

    Args:
        live_gate:        LiveGate instance with async evaluate().
        risk_guardian:    Optional — called .trigger_halt() on gate failure.
        interval_seconds: Evaluation period (default 300s = 5 min).
        strategy_id:      Strategy to evaluate.
    """

    def __init__(
        self,
        live_gate,
        risk_guardian=None,
        interval_seconds: float = 300.0,
        strategy_id: str = "cross_exchange_arb_v1",
    ) -> None:
        self._live_gate = live_gate
        self._risk_guardian = risk_guardian
        self._interval = interval_seconds
        self._strategy_id = strategy_id
        self._task: asyncio.Task | None = None
        self._results: deque = deque(maxlen=500)

    @property
    def enabled(self) -> bool:
        return os.getenv("LIVE_GATE_CONTINUOUS", "1").strip().lower() not in (
            "0",
            "false",
            "no",
        )

    async def start(self) -> None:
        if not self.enabled:
            logger.info("live_gate_continuous.disabled_by_env")
            return
        self._task = asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _monitor_loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            await self._evaluate()

    async def _evaluate(self) -> None:
        try:
            result = await self._live_gate.evaluate(self._strategy_id)
            self._results.append(result)
            logger.info(
                "live_gate_continuous.evaluated: eligible=%s", result.eligible
            )
            if not result.eligible and self._risk_guardian is not None:
                self._risk_guardian.trigger_halt("live_gate_failed")
        except Exception as exc:  # noqa: BLE001
            logger.error("live_gate_continuous.error: %s", exc)

    @property
    def results(self) -> list:
        return list(self._results)
