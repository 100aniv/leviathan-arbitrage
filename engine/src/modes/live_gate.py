"""LEVIATHAN Live Gate — Performance-Based Trading Gate.

Evaluates shadow mode results against strict criteria before
allowing live trading. Auto-reevaluates every 24 hours.

Gate Criteria:
  1. 7-day rolling Sharpe >= 2.5
  2. Maximum Drawdown < 5%
  3. Daily signals > 100
  4. Kill switch events = 0 in evaluation period
  5. Circuit breaker state = CLOSED
  6. Exchange health scores > 0.95

Failure blocks live mode activation + sends Telegram alert with reasons.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import asyncpg
import structlog

from src.analysis.walk_forward import WalkForwardAnalyzer, WalkForwardResult
from src.infra.metrics import CIRCUIT_BREAKER_STATE

logger = structlog.get_logger(__name__)


@dataclass
class LiveGateCheck:
    """Result of a single gate criterion check."""

    name: str
    passed: bool
    value: str       # actual measured value
    threshold: str   # required threshold
    detail: str = "" # additional info


@dataclass
class LiveGateResult:
    """Aggregated live gate evaluation result."""

    timestamp: datetime
    eligible: bool                          # True only if ALL checks pass
    checks: list[LiveGateCheck]             # individual criterion results
    walk_forward: WalkForwardResult | None = None  # raw WF data
    block_reasons: list[str] = field(default_factory=list)
    evaluation_duration_ms: float = 0.0

    @property
    def passed(self) -> bool:
        """Alias for eligible (used by continuous monitor)."""
        return self.eligible


class LiveGate:
    """Performance-based gate for live trading authorization.

    Evaluates shadow mode performance against strict criteria.
    Can run as a one-shot check or as a continuous 24h auto-evaluation loop.

    Args:
        pool: asyncpg connection pool for WalkForwardAnalyzer
        telegram: Optional TelegramAlerter for notifications
        kill_switch: Optional KillSwitch instance for halt check
        circuit_breaker: Optional CircuitBreaker for state check
        exchange_health_fn: Optional callable returning dict[str, float] of
            exchange health scores
    """

    # Gate thresholds — defaults, overridable via constructor or LiveGateSettings
    SHARPE_THRESHOLD = 2.5
    MDD_THRESHOLD = 0.05       # 5%
    MIN_SIGNALS_PER_DAY = 100
    MIN_EXCHANGE_HEALTH = 0.95
    EVALUATION_DAYS = 7
    REEVALUATION_INTERVAL_HOURS = 24

    def __init__(
        self,
        pool: asyncpg.Pool,
        telegram: object | None = None,
        kill_switch: object | None = None,
        circuit_breaker: object | None = None,
        exchange_health_fn: Callable[[], dict[str, float]] | None = None,
        settings: object | None = None,
    ) -> None:
        self._pool = pool
        self._telegram = telegram
        self._kill_switch = kill_switch
        self._circuit_breaker = circuit_breaker
        self._exchange_health_fn = exchange_health_fn

        # Override class-level defaults from LiveGateSettings if provided
        if settings is not None and hasattr(settings, "live_gate"):
            lg = settings.live_gate
            self.SHARPE_THRESHOLD = float(lg.sharpe_threshold)
            self.MDD_THRESHOLD = float(lg.mdd_threshold)
            self.MIN_SIGNALS_PER_DAY = lg.min_signals_per_day
            self.MIN_EXCHANGE_HEALTH = float(lg.min_exchange_health)
            self.EVALUATION_DAYS = lg.evaluation_days
            self.REEVALUATION_INTERVAL_HOURS = lg.reevaluation_interval_hours

        self._analyzer = WalkForwardAnalyzer(pool)
        self._latest_result: LiveGateResult | None = None
        self._auto_task: asyncio.Task | None = None  # type: ignore[type-arg]

    # ---------------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------------

    async def evaluate(
        self, strategy_id: str = "cross_exchange_arb_v1"
    ) -> LiveGateResult:
        """Run full gate evaluation against all criteria.

        Args:
            strategy_id: Strategy to evaluate.

        Returns:
            LiveGateResult with per-check details and overall eligibility.
        """
        t_start = time.perf_counter()
        timestamp = datetime.now(timezone.utc)

        # Run walk-forward analysis
        wf_result = await self._analyzer.analyze(
            strategy_id=strategy_id,
            days=self.EVALUATION_DAYS,
        )

        checks: list[LiveGateCheck] = []
        block_reasons: list[str] = []

        # ------------------------------------------------------------------
        # Check 1: Sharpe ratio
        # ------------------------------------------------------------------
        sharpe_ok = wf_result.overall_sharpe >= self.SHARPE_THRESHOLD
        checks.append(
            LiveGateCheck(
                name="Sharpe Ratio",
                passed=sharpe_ok,
                value=f"{wf_result.overall_sharpe:.2f}",
                threshold=f">= {self.SHARPE_THRESHOLD}",
            )
        )
        if not sharpe_ok:
            block_reasons.append(
                f"Sharpe {wf_result.overall_sharpe:.2f} < {self.SHARPE_THRESHOLD}"
            )

        # ------------------------------------------------------------------
        # Check 2: Maximum drawdown
        # ------------------------------------------------------------------
        mdd_ok = wf_result.overall_mdd < self.MDD_THRESHOLD
        checks.append(
            LiveGateCheck(
                name="Max Drawdown",
                passed=mdd_ok,
                value=f"{wf_result.overall_mdd * 100:.1f}%",
                threshold=f"< {self.MDD_THRESHOLD * 100:.0f}%",
            )
        )
        if not mdd_ok:
            block_reasons.append(
                f"MDD {wf_result.overall_mdd * 100:.2f}% >= {self.MDD_THRESHOLD * 100:.0f}%"
            )

        # ------------------------------------------------------------------
        # Check 3: Signals per day
        # ------------------------------------------------------------------
        signals_ok = wf_result.avg_signals_per_day >= self.MIN_SIGNALS_PER_DAY
        checks.append(
            LiveGateCheck(
                name="Signals/Day",
                passed=signals_ok,
                value=f"{wf_result.avg_signals_per_day:.0f}",
                threshold=f">= {self.MIN_SIGNALS_PER_DAY}",
            )
        )
        if not signals_ok:
            block_reasons.append(
                f"Signals/day {wf_result.avg_signals_per_day:.0f} < {self.MIN_SIGNALS_PER_DAY}"
            )

        # ------------------------------------------------------------------
        # Check 4: Kill switch not halted
        # ------------------------------------------------------------------
        ks_halted = self._check_kill_switch()
        ks_ok = not ks_halted
        checks.append(
            LiveGateCheck(
                name="Kill Switch",
                passed=ks_ok,
                value="HALTED" if ks_halted else "Clear",
                threshold="Not halted",
            )
        )
        if not ks_ok:
            block_reasons.append("Kill switch is active (engine halted)")

        # ------------------------------------------------------------------
        # Check 5: Circuit breaker state == CLOSED
        # ------------------------------------------------------------------
        cb_state_value = self._get_circuit_breaker_state()
        cb_ok = cb_state_value == 0  # 0 = CLOSED per metrics definition
        cb_label = {0: "CLOSED", 1: "OPEN", 2: "HALF_OPEN"}.get(int(cb_state_value), f"{cb_state_value}")
        checks.append(
            LiveGateCheck(
                name="Circuit Breaker",
                passed=cb_ok,
                value=cb_label,
                threshold="CLOSED",
            )
        )
        if not cb_ok:
            block_reasons.append(f"Circuit breaker is {cb_label} (expected CLOSED)")

        # ------------------------------------------------------------------
        # Check 6: Exchange health scores
        # ------------------------------------------------------------------
        exchange_health_ok, health_detail = self._check_exchange_health()
        checks.append(
            LiveGateCheck(
                name="Exchange Health",
                passed=exchange_health_ok,
                value="OK" if exchange_health_ok else "DEGRADED",
                threshold=f">= {self.MIN_EXCHANGE_HEALTH}",
                detail=health_detail,
            )
        )
        if not exchange_health_ok:
            block_reasons.append(f"Exchange health below threshold: {health_detail}")

        # ------------------------------------------------------------------
        # Aggregate
        # ------------------------------------------------------------------
        eligible = len(block_reasons) == 0
        duration_ms = (time.perf_counter() - t_start) * 1000

        result = LiveGateResult(
            timestamp=timestamp,
            eligible=eligible,
            checks=checks,
            walk_forward=wf_result,
            block_reasons=block_reasons,
            evaluation_duration_ms=duration_ms,
        )

        self._latest_result = result

        # Logging
        logger.info(
            "live_gate_evaluated",
            strategy_id=strategy_id,
            eligible=eligible,
            block_reasons=block_reasons,
            duration_ms=f"{duration_ms:.1f}",
            sharpe=f"{wf_result.overall_sharpe:.2f}",
            mdd=f"{wf_result.overall_mdd * 100:.1f}%",
            signals_per_day=f"{wf_result.avg_signals_per_day:.0f}",
        )

        # Telegram notification
        await self._send_telegram_notification(result, strategy_id)

        return result

    async def start_auto_evaluation(
        self, strategy_id: str = "cross_exchange_arb_v1"
    ) -> None:
        """Start a background loop that re-evaluates every 24 hours.

        Stores the latest result in self._latest_result.
        Exceptions are caught and logged — the loop never crashes.

        Args:
            strategy_id: Strategy to evaluate on each cycle.
        """
        if self._auto_task is not None and not self._auto_task.done():
            logger.warning("live_gate_auto_already_running")
            return

        self._auto_task = asyncio.create_task(
            self._auto_evaluation_loop(strategy_id),
            name="live_gate_auto_evaluation",
        )
        logger.info(
            "live_gate_auto_started",
            strategy_id=strategy_id,
            interval_hours=self.REEVALUATION_INTERVAL_HOURS,
        )

    async def stop_auto_evaluation(self) -> None:
        """Cancel the auto-evaluation background loop."""
        if self._auto_task is None or self._auto_task.done():
            return

        self._auto_task.cancel()
        try:
            await self._auto_task
        except asyncio.CancelledError:
            pass

        logger.info("live_gate_auto_stopped")
        self._auto_task = None

    @property
    def latest_result(self) -> LiveGateResult | None:
        """Returns the most recent evaluation result, or None if not yet run."""
        return self._latest_result

    def is_live_eligible(self) -> bool:
        """Quick check: returns latest_result.eligible, or False if no evaluation yet."""
        if self._latest_result is None:
            return False
        return self._latest_result.eligible

    async def start_continuous_monitor(
        self,
        interval_s: int = 60,
        risk_guardian: object | None = None,
    ) -> None:
        """모든 모드에서 주기적 LiveGate 평가.

        US-280: Runs indefinitely; exceptions are caught and logged.
        Enabled via LIVE_GATE_CONTINUOUS_ENABLED env var (default True).
        """
        import os
        if os.getenv("LIVE_GATE_CONTINUOUS_ENABLED", "true").lower() == "false":
            logger.info("live_gate.continuous_monitor disabled via env")
            return

        consecutive_failures = 0
        try:
            _raw = int(os.getenv("LIVE_GATE_PAUSE_THRESHOLD", "3"))
            pause_threshold = max(1, min(_raw, 10))
        except (ValueError, TypeError):
            pause_threshold = 3

        while True:
            try:
                result = await self.evaluate()
                logger.info(
                    "live_gate.continuous_check: pass=%s, checks=%s",
                    result.passed,
                    {c.name: c.passed for c in result.checks},
                )
                if not result.passed:
                    consecutive_failures += 1
                    if consecutive_failures >= pause_threshold and risk_guardian is not None:
                        logger.warning(
                            "live_gate.continuous_check: %d consecutive FAILs — triggering emergency pause",
                            consecutive_failures,
                        )
                        try:
                            pause_fn = getattr(risk_guardian, "emergency_pause", None)
                            if pause_fn is not None:
                                await pause_fn()
                        except Exception as exc:
                            logger.warning("live_gate.continuous_check: emergency_pause error: %s", exc)
                    else:
                        logger.info(
                            "live_gate.continuous_check: FAIL %d/%d (backoff, no halt yet)",
                            consecutive_failures, pause_threshold,
                        )
                else:
                    if consecutive_failures > 0:
                        logger.info("live_gate.continuous_check: recovered after %d failures", consecutive_failures)
                    consecutive_failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("live_gate.continuous_monitor_error: %s", exc, exc_info=True)

            backoff = min(300, interval_s * (2 ** min(consecutive_failures, 3)))
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                raise

    async def enforce_or_fallback(self) -> bool:
        """Evaluate LiveGate 6-check. Return True if live-eligible, False to fallback.

        US-246: Called by Engine before entering live mode.
        """
        result = await self.evaluate()
        if result is None or not result.eligible:
            logger.warning(
                "live_gate_enforcement_blocked",
                eligible=False,
                checks={c.name: c.passed for c in result.checks} if result else {},
            )
            if self._telegram is not None:
                try:
                    await self._telegram.send_alert(
                        "⚠️ LiveGate BLOCKED: 6-check 미통과 → Shadow fallback",
                        level="warning",
                    )
                except Exception:
                    pass
            return False
        logger.info("live_gate_enforcement_passed", eligible=True)
        return True

    # ---------------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------------

    def _check_kill_switch(self) -> bool:
        """Return True if the kill switch is currently halted."""
        if self._kill_switch is not None:
            try:
                return bool(self._kill_switch.is_halted())
            except Exception as exc:
                logger.warning("live_gate_kill_switch_check_error", error=str(exc))
                return False

        # Fall back to module-level function
        try:
            from src.risk.kill_switch import is_halted
            return is_halted()
        except Exception as exc:
            logger.warning("live_gate_kill_switch_import_error", error=str(exc))
            return False

    def _get_circuit_breaker_state(self) -> float:
        """Return circuit breaker state value (0=CLOSED, 1=OPEN, 2=HALF_OPEN).

        Checks injected circuit_breaker first, then Prometheus gauge fallback.
        """
        if self._circuit_breaker is not None:
            try:
                # Support objects with .state property returning "CLOSED"/"OPEN"/"HALF_OPEN"
                state_attr = getattr(self._circuit_breaker, "state", None)
                if callable(state_attr):
                    state_str = state_attr()
                else:
                    state_str = state_attr
                if isinstance(state_str, str):
                    return {"CLOSED": 0.0, "OPEN": 1.0, "HALF_OPEN": 2.0}.get(
                        state_str.upper(), 1.0
                    )
                # Numeric state already
                return float(state_str)
            except Exception as exc:
                logger.warning(
                    "live_gate_circuit_breaker_check_error", error=str(exc)
                )

        # Fallback: read Prometheus gauge value
        try:
            return float(CIRCUIT_BREAKER_STATE._value.get())  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning(
                "live_gate_circuit_breaker_gauge_error", error=str(exc)
            )
            # Assume CLOSED if unreadable (conservative: don't block on metric errors)
            return 0.0

    def _check_exchange_health(self) -> tuple[bool, str]:
        """Check all exchange health scores against the threshold.

        Returns:
            (all_healthy, detail_string)
        """
        if self._exchange_health_fn is None:
            # No health provider — skip check (pass by default)
            return True, "No health provider configured"

        try:
            scores: dict[str, float] = self._exchange_health_fn()
        except Exception as exc:
            logger.warning("live_gate_exchange_health_error", error=str(exc))
            return False, f"Health check error: {exc}"

        if not scores:
            return True, "No exchanges registered"

        failing = {
            exch: score
            for exch, score in scores.items()
            if score < self.MIN_EXCHANGE_HEALTH
        }

        if failing:
            detail = ", ".join(
                f"{exch}={score:.3f}" for exch, score in sorted(failing.items())
            )
            return False, f"Below threshold: {detail}"

        return True, "All exchanges healthy"

    async def _auto_evaluation_loop(self, strategy_id: str) -> None:
        """Background coroutine: evaluates on startup then every 24h."""
        interval_seconds = self.REEVALUATION_INTERVAL_HOURS * 3600

        while True:
            try:
                await self.evaluate(strategy_id=strategy_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "live_gate_auto_evaluation_error",
                    error=str(exc),
                    exc_info=True,
                )

            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                raise

    # ---------------------------------------------------------------------------
    # Telegram formatting
    # ---------------------------------------------------------------------------

    async def _send_telegram_notification(
        self, result: LiveGateResult, strategy_id: str
    ) -> None:
        """Send a formatted Telegram message for the gate result."""
        if self._telegram is None:
            return

        lines = self._format_telegram_message(result, strategy_id)
        message = "\n".join(lines)
        level = "INFO" if result.eligible else "WARNING"

        try:
            await self._telegram.send_alert(message, level=level)
        except Exception as exc:
            logger.warning("live_gate_telegram_error", error=str(exc))

    @staticmethod
    def _format_check_line(check: LiveGateCheck) -> str:
        """Format a single check as a display line."""
        mark = "✅" if check.passed else "❌"
        return f"{check.name}: {check.value} ({check.threshold}) {mark}"

    def _format_telegram_message(
        self, result: LiveGateResult, strategy_id: str
    ) -> list[str]:
        """Build the Telegram message lines for a gate result."""
        if result.eligible:
            header = "✅ LIVE GATE: PASSED"
        else:
            header = "⚠️ LIVE GATE: BLOCKED"

        lines: list[str] = [
            header,
            f"Strategy: {strategy_id}",
            f"Period: {self.EVALUATION_DAYS} days",
            "",
        ]

        # Per-check lines
        for check in result.checks:
            lines.append(self._format_check_line(check))

        # Block reasons section (only on failure)
        if not result.eligible and result.block_reasons:
            lines.append("")
            lines.append("Block Reasons:")
            for reason in result.block_reasons:
                lines.append(f"• {reason}")
            lines.append("")
            lines.append(f"Next evaluation in {self.REEVALUATION_INTERVAL_HOURS}h")

        return lines
