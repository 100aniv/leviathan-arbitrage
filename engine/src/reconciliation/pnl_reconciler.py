"""PnLReconciler — compares engine TCA PnL vs exchange-reported PnL.

Gate logic
----------

- |Δ| > ``warn_threshold_usd`` (default $0.50) for ``consecutive_breaches``
  (default 3) cycles → WARN: bump
  ``leviathan_pnl_divergence_breach_total{severity="warn"}`` +
  Telegram alert (if configured).
- |Δ| > ``critical_threshold_usd`` (default $1.00) for ``consecutive_breaches``
  cycles → CRITICAL: bump
  ``leviathan_pnl_divergence_breach_total{severity="critical"}``, call
  :func:`src.risk.kill_switch.halt_local` and send a CRITICAL Telegram alert.

The reconciler exposes three Prometheus gauges —
``leviathan_pnl_engine_usd``, ``leviathan_pnl_exchange_usd``,
``leviathan_pnl_divergence_usd`` — the dashboard and Grafana wire directly
into.

Implementation notes
--------------------

- Consecutive-breach counters are tracked per severity. A cycle that falls
  below a threshold resets that severity's counter (warn-only breach still
  ticks the warn counter).
- The reconciler is intentionally non-blocking: a transient snapshot error
  logs a warning and increments no counter.
- Threshold-level state transitions are reported to
  :class:`src.reconciliation.pnl_ledger.PnLLedger` so ``get_live_pnl_usd()``
  reflects the current ``verified | pending | diverged`` status.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Callable

from prometheus_client import Counter, Gauge

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from src.reconciliation.exchange_pnl_snapshot import ExchangePnLSnapshot
    from src.reconciliation.pnl_ledger import PnLLedger


# ---------------------------------------------------------------------------
# Prometheus surface (registered once at import time)
# ---------------------------------------------------------------------------

PNL_ENGINE_USD = Gauge(
    "leviathan_pnl_engine_usd",
    "Engine-reported cumulative PnL (TCA) in USD",
)
PNL_EXCHANGE_USD = Gauge(
    "leviathan_pnl_exchange_usd",
    "Exchange-reported cumulative PnL in USD (sum of realized + commission + funding)",
)
PNL_DIVERGENCE_USD = Gauge(
    "leviathan_pnl_divergence_usd",
    "Signed divergence = engine_pnl - exchange_pnl (USD)",
)
PNL_DIVERGENCE_BREACH_TOTAL = Counter(
    "leviathan_pnl_divergence_breach_total",
    "Count of reconciliation checks whose divergence exceeded a threshold",
    ["severity"],  # warn / critical
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ReconcilerConfig:
    """Reconciler tuning knobs."""

    interval_s: float = 60.0
    warn_threshold_usd: Decimal = Decimal("0.50")
    critical_threshold_usd: Decimal = Decimal("1.00")
    verified_threshold_usd: Decimal = Decimal("0.10")
    consecutive_breaches: int = 3
    lookback_hours: int = 24


@dataclass(slots=True)
class ReconciliationResult:
    """Snapshot produced by :meth:`PnLReconciler.run_check`."""

    engine_pnl_usd: Decimal
    exchange_pnl_usd: Decimal
    divergence_usd: Decimal
    warn_breach_count: int
    critical_breach_count: int
    severity: str  # ok | warn | critical | pending
    halted: bool
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# PnLReconciler
# ---------------------------------------------------------------------------


class PnLReconciler:
    """Compare engine vs exchange PnL on a cadence and promote divergences.

    Args:
        snapshot: :class:`ExchangePnLSnapshot` used to fetch the truth-side
            cumulative PnL.
        engine_pnl_getter: Callable returning the engine's cumulative TCA PnL
            in USD (``Decimal | float | int``). The reconciler never touches
            engine internals directly.
        ledger: optional :class:`PnLLedger` to push status updates into.
        telegram: optional alerter duck-typed as
            ``await send_alert(message, level=..., mode='live')``.
        config: see :class:`ReconcilerConfig`.
        halt_callable: optional override for ``halt_local`` (tests).
    """

    def __init__(
        self,
        snapshot: "ExchangePnLSnapshot",
        engine_pnl_getter: Callable[[], Any],
        *,
        ledger: "PnLLedger | None" = None,
        telegram: Any | None = None,
        config: ReconcilerConfig | None = None,
        halt_callable: Callable[[], None] | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._engine_pnl_getter = engine_pnl_getter
        self._ledger = ledger
        self._telegram = telegram
        self._config = config or ReconcilerConfig()
        self._halt = halt_callable
        self._task: asyncio.Task[Any] | None = None
        self._running = False
        self._warn_count = 0
        self._critical_count = 0
        self._last_result: ReconciliationResult | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="pnl_reconciler")
        logger.info(
            "pnl_reconciler.started interval_s=%.0f warn=$%s critical=$%s",
            self._config.interval_s,
            self._config.warn_threshold_usd,
            self._config.critical_threshold_usd,
        )

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._task = None
        logger.info("pnl_reconciler.stopped")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self.run_check()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("pnl_reconciler.check_failed err=%s", exc)
            try:
                await asyncio.sleep(self._config.interval_s)
            except asyncio.CancelledError:
                break

    # ------------------------------------------------------------------
    # Core comparator
    # ------------------------------------------------------------------

    async def run_check(self) -> ReconciliationResult:
        """One reconciliation pass. Emits metrics + alerts."""
        engine_pnl = self._coerce_decimal(self._engine_pnl_getter())
        until = datetime.now(timezone.utc)
        since = until - timedelta(hours=self._config.lookback_hours)

        try:
            exchange_pnl = await self._snapshot.get_cumulative_pnl_usd(since, until)
        except Exception as exc:  # noqa: BLE001
            logger.warning("pnl_reconciler.snapshot_read_failed err=%s", exc)
            # Pending → no breach promotion this cycle.
            result = ReconciliationResult(
                engine_pnl_usd=engine_pnl,
                exchange_pnl_usd=Decimal("0"),
                divergence_usd=Decimal("0"),
                warn_breach_count=self._warn_count,
                critical_breach_count=self._critical_count,
                severity="pending",
                halted=False,
            )
            self._publish_status(result, pending=True)
            return result

        # If the snapshot hasn't seen any data yet (freshly booted, prime
        # hasn't run), treat as pending — never diverge from nothing.
        pending = not self._snapshot.has_data()
        divergence = engine_pnl - exchange_pnl

        severity = "ok"
        halted = False
        abs_div = abs(divergence)
        if not pending:
            warn_hit = abs_div > self._config.warn_threshold_usd
            crit_hit = abs_div > self._config.critical_threshold_usd

            if crit_hit:
                self._critical_count += 1
                self._warn_count += 1  # a critical is also a warn-level event
            elif warn_hit:
                self._warn_count += 1
                self._critical_count = 0
            else:
                self._warn_count = 0
                self._critical_count = 0

            if self._critical_count >= self._config.consecutive_breaches:
                severity = "critical"
                halted = await self._escalate_critical(divergence)
            elif self._warn_count >= self._config.consecutive_breaches:
                severity = "warn"
                await self._escalate_warn(divergence)
            elif warn_hit or crit_hit:
                severity = "tracking"  # within the streak but not yet escalated
        else:
            self._warn_count = 0
            self._critical_count = 0

        # Prometheus emit — always, even in pending (zeros are meaningful).
        try:
            PNL_ENGINE_USD.set(float(engine_pnl))
            PNL_EXCHANGE_USD.set(float(exchange_pnl))
            PNL_DIVERGENCE_USD.set(float(divergence))
        except (TypeError, ValueError):
            pass

        result = ReconciliationResult(
            engine_pnl_usd=engine_pnl,
            exchange_pnl_usd=exchange_pnl,
            divergence_usd=divergence,
            warn_breach_count=self._warn_count,
            critical_breach_count=self._critical_count,
            severity=severity if not pending else "pending",
            halted=halted,
        )
        self._last_result = result
        self._publish_status(result, pending=pending)
        return result

    # ------------------------------------------------------------------
    # Escalations
    # ------------------------------------------------------------------

    async def _escalate_warn(self, divergence: Decimal) -> None:
        try:
            PNL_DIVERGENCE_BREACH_TOTAL.labels(severity="warn").inc()
        except ValueError:
            pass
        logger.warning(
            "pnl_reconciler.warn divergence=$%.4f streak=%d",
            float(divergence), self._warn_count,
        )
        if self._telegram is not None:
            try:
                await self._telegram.send_alert(
                    f"[PnL WARN] engine vs exchange divergence "
                    f"${divergence:+.4f} — streak {self._warn_count}",
                    level="WARN",
                    mode="live",
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("pnl_reconciler.warn_telegram_failed err=%s", exc)

    async def _escalate_critical(self, divergence: Decimal) -> bool:
        try:
            PNL_DIVERGENCE_BREACH_TOTAL.labels(severity="critical").inc()
        except ValueError:
            pass
        logger.critical(
            "pnl_reconciler.critical divergence=$%.4f streak=%d — HALT",
            float(divergence), self._critical_count,
        )
        halted = False
        try:
            halt_fn = self._halt or self._default_halt
            halt_fn()
            halted = True
        except Exception as exc:  # noqa: BLE001
            logger.error("pnl_reconciler.halt_failed err=%s", exc)

        if self._telegram is not None:
            try:
                await self._telegram.send_alert(
                    f"[PnL CRITICAL] divergence ${divergence:+.4f} for "
                    f"{self._critical_count} cycles — halt_local() invoked.",
                    level="CRITICAL",
                    mode="live",
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("pnl_reconciler.crit_telegram_failed err=%s", exc)
        return halted

    @staticmethod
    def _default_halt() -> None:
        from src.risk.kill_switch import halt_local
        halt_local()

    # ------------------------------------------------------------------
    # Ledger publication
    # ------------------------------------------------------------------

    def _publish_status(
        self,
        result: ReconciliationResult,
        *,
        pending: bool,
    ) -> None:
        if self._ledger is None:
            return
        if pending:
            status = "pending"
        elif abs(result.divergence_usd) > self._config.warn_threshold_usd:
            status = "diverged"
        elif abs(result.divergence_usd) < self._config.verified_threshold_usd:
            status = "verified"
        else:
            status = "pending"  # in-range but not tight enough to claim verified
        try:
            self._ledger.update_from_reconcile(
                engine_pnl=result.engine_pnl_usd,
                exchange_pnl=result.exchange_pnl_usd,
                divergence=result.divergence_usd,
                status=status,
                ts=result.ts,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("pnl_reconciler.ledger_publish_failed err=%s", exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _coerce_decimal(value: Any) -> Decimal:
        try:
            if isinstance(value, Decimal):
                return value
            if value is None:
                return Decimal("0")
            return Decimal(str(value))
        except Exception:  # noqa: BLE001
            return Decimal("0")

    @property
    def last_result(self) -> ReconciliationResult | None:
        """Most recent reconciliation snapshot (tests, dashboard)."""
        return self._last_result

    @property
    def warn_count(self) -> int:
        return self._warn_count

    @property
    def critical_count(self) -> int:
        return self._critical_count


__all__ = [
    "PNL_DIVERGENCE_BREACH_TOTAL",
    "PNL_DIVERGENCE_USD",
    "PNL_ENGINE_USD",
    "PNL_EXCHANGE_USD",
    "PnLReconciler",
    "ReconcilerConfig",
    "ReconciliationResult",
]
