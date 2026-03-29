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
        live_gate:          LiveGate instance with async evaluate().
        risk_guardian:      Optional — called .trigger_halt() on gate failure.
        interval_seconds:   Evaluation period (default 300s = 5 min).
        strategy_id:        Strategy to evaluate.
        telegram:           Optional alerter for shadow-fallback notifications.
        mode_switch_fn:     Optional async callable → switches engine to Shadow mode.
        max_mdd:            MDD threshold triggering auto Shadow fallback (default 10%).
        daily_loss_fn:      Optional callable → returns current daily loss USD (negative = loss).
        max_daily_loss_usd: Max absolute daily loss before Shadow fallback (default 5000 USD).
    """

    def __init__(
        self,
        live_gate,
        risk_guardian=None,
        interval_seconds: float = 300.0,
        strategy_id: str = "cross_exchange_arb_v1",
        telegram=None,
        mode_switch_fn=None,
        max_mdd: float = 0.10,
        daily_loss_fn=None,
        max_daily_loss_usd: float = 5000.0,
    ) -> None:
        self._live_gate = live_gate
        self._risk_guardian = risk_guardian
        self._interval = interval_seconds
        self._strategy_id = strategy_id
        self._telegram = telegram
        self._mode_switch_fn = mode_switch_fn
        self._max_mdd = max_mdd
        self._daily_loss_fn = daily_loss_fn
        self._max_daily_loss_usd = max_daily_loss_usd
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

            # --- MDD breach → auto Shadow fallback ---
            mdd_breach = False
            mdd_val = 0.0
            if result.walk_forward is not None:
                try:
                    raw = result.walk_forward.overall_mdd
                    # Only treat as valid if it's a real numeric type
                    if isinstance(raw, (int, float)):
                        mdd_val = float(raw)
                        mdd_breach = mdd_val > self._max_mdd
                except (TypeError, ValueError):
                    pass

            # --- Daily loss breach ---
            daily_loss_breach = False
            if self._daily_loss_fn is not None:
                try:
                    daily_loss = self._daily_loss_fn()
                    daily_loss_breach = abs(float(daily_loss)) > self._max_daily_loss_usd
                except Exception as exc:
                    logger.warning("live_gate_continuous.daily_loss_fn_error: %s", exc)

            if mdd_breach or daily_loss_breach:
                if mdd_breach:
                    reason = (
                        f"MDD {mdd_val * 100:.1f}%"
                        f" > {self._max_mdd * 100:.0f}%"
                    )
                else:
                    reason = f"일일손실 > {self._max_daily_loss_usd:.0f} USD"
                logger.warning(
                    "live_gate_continuous.shadow_fallback_triggered: %s", reason
                )
                await self._send_shadow_fallback_alert(reason)
                if self._mode_switch_fn is not None:
                    try:
                        await self._mode_switch_fn()
                    except Exception as exc:
                        logger.error(
                            "live_gate_continuous.mode_switch_error: %s", exc
                        )
            elif not result.eligible and self._risk_guardian is not None:
                self._risk_guardian.trigger_halt("live_gate_failed")

        except Exception as exc:  # noqa: BLE001
            logger.error("live_gate_continuous.error: %s", exc)

    async def _send_shadow_fallback_alert(self, reason: str) -> None:
        """텔레그램 알림: Live→Shadow 자동 전환."""
        if self._telegram is None:
            return
        try:
            await self._telegram.send_alert(
                f"⚠️ Live→Shadow 자동 전환: {reason}",
                level="warning",
            )
        except Exception as exc:
            logger.warning("live_gate_continuous.telegram_error: %s", exc)

    @property
    def results(self) -> list:
        return list(self._results)
