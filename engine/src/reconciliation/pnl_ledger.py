"""PnLLedger — single authority for operator-facing PnL.

This module wraps :class:`ExchangePnLSnapshot` + :class:`PnLReconciler` so the
API/dashboard layer reads from ONE place instead of the engine's internal
``_stats.total_pnl`` (which is what today's divergence is caused by).

``get_live_pnl_usd()`` returns the canonical dict:

.. code-block:: python

    {
        "exchange_pnl_usd": Decimal,
        "engine_pnl_usd":   Decimal,
        "divergence_usd":   Decimal,
        "status":           "verified" | "pending" | "diverged",
        "last_reconciled_ts": datetime | None,
    }

Status transitions
------------------

- ``pending`` — the snapshot has not yet produced data (fresh boot) OR the
  most recent reconcile resulted in ``severity == "pending"``. Divergence
  is reported but considered unreliable.
- ``verified`` — last reconcile returned ``|divergence| <
  verified_threshold_usd`` (default $0.10) AND snapshot has data.
- ``diverged`` — last reconcile returned ``|divergence| >
  warn_threshold_usd`` (default $0.50).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from src.reconciliation.exchange_pnl_snapshot import ExchangePnLSnapshot


class PnLStatus(str, Enum):
    """String enum — subclasses ``str`` so JSON serialisers don't need hints."""

    VERIFIED = "verified"
    PENDING = "pending"
    DIVERGED = "diverged"


@dataclass(slots=True)
class LedgerConfig:
    """Ledger thresholds. Must match the reconciler's gates for consistency."""

    verified_threshold_usd: Decimal = Decimal("0.10")
    divergence_threshold_usd: Decimal = Decimal("0.50")
    lookback_hours: int = 24


@dataclass(slots=True)
class LedgerState:
    engine_pnl_usd: Decimal = Decimal("0")
    exchange_pnl_usd: Decimal = Decimal("0")
    divergence_usd: Decimal = Decimal("0")
    status: PnLStatus = PnLStatus.PENDING
    last_reconciled_ts: datetime | None = None


class PnLLedger:
    """SINGLE SOURCE OF TRUTH for operator-facing PnL.

    The ledger is fed by :class:`PnLReconciler` via
    :meth:`update_from_reconcile`. It is also capable of computing a PnL
    snapshot on-demand (``get_live_pnl_usd()``) by reading directly from the
    :class:`ExchangePnLSnapshot` — useful when the reconciler hasn't run yet.

    Args:
        snapshot: :class:`ExchangePnLSnapshot` providing exchange-side PnL.
        engine_pnl_getter: Callable returning the engine's cumulative TCA PnL.
        config: see :class:`LedgerConfig`.
    """

    def __init__(
        self,
        snapshot: "ExchangePnLSnapshot",
        engine_pnl_getter: Any,
        *,
        config: LedgerConfig | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._engine_pnl_getter = engine_pnl_getter
        self._config = config or LedgerConfig()
        self._state = LedgerState()

    # ------------------------------------------------------------------
    # Reconciler push path
    # ------------------------------------------------------------------

    def update_from_reconcile(
        self,
        *,
        engine_pnl: Decimal,
        exchange_pnl: Decimal,
        divergence: Decimal,
        status: str,
        ts: datetime,
    ) -> None:
        """Called by :class:`PnLReconciler` after each reconcile cycle."""
        try:
            status_enum = PnLStatus(status)
        except ValueError:
            status_enum = PnLStatus.PENDING
        self._state = LedgerState(
            engine_pnl_usd=self._to_decimal(engine_pnl),
            exchange_pnl_usd=self._to_decimal(exchange_pnl),
            divergence_usd=self._to_decimal(divergence),
            status=status_enum,
            last_reconciled_ts=ts,
        )

    # ------------------------------------------------------------------
    # Pull path (API/dashboard)
    # ------------------------------------------------------------------

    async def get_live_pnl_usd(self) -> dict[str, Any]:
        """Return the canonical operator-facing PnL dict.

        If the reconciler has never published a state, we compute an on-
        demand snapshot so the dashboard is never blank. The status will be
        ``pending`` in that case.
        """
        if self._state.last_reconciled_ts is None:
            await self._refresh_from_snapshot()

        return {
            "exchange_pnl_usd": self._state.exchange_pnl_usd,
            "engine_pnl_usd": self._state.engine_pnl_usd,
            "divergence_usd": self._state.divergence_usd,
            "status": self._state.status.value,
            "last_reconciled_ts": self._state.last_reconciled_ts,
        }

    async def _refresh_from_snapshot(self) -> None:
        """Recompute state directly from the snapshot + engine getter."""
        engine_pnl = self._to_decimal(self._call_engine_getter())
        exchange_pnl = Decimal("0")
        try:
            until = datetime.now(timezone.utc)
            since = until - timedelta(hours=self._config.lookback_hours)
            exchange_pnl = await self._snapshot.get_cumulative_pnl_usd(
                since, until,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("pnl_ledger.snapshot_read_failed err=%s", exc)

        divergence = engine_pnl - exchange_pnl
        status = self._resolve_status(divergence, has_data=self._snapshot.has_data())
        self._state = LedgerState(
            engine_pnl_usd=engine_pnl,
            exchange_pnl_usd=exchange_pnl,
            divergence_usd=divergence,
            status=status,
            last_reconciled_ts=datetime.now(timezone.utc) if status != PnLStatus.PENDING else None,
        )

    def _resolve_status(self, divergence: Decimal, *, has_data: bool) -> PnLStatus:
        if not has_data:
            return PnLStatus.PENDING
        abs_div = abs(divergence)
        if abs_div > self._config.divergence_threshold_usd:
            return PnLStatus.DIVERGED
        if abs_div < self._config.verified_threshold_usd:
            return PnLStatus.VERIFIED
        return PnLStatus.PENDING

    def _call_engine_getter(self) -> Any:
        getter = self._engine_pnl_getter
        if getter is None:
            return Decimal("0")
        try:
            return getter() if callable(getter) else getter
        except Exception as exc:  # noqa: BLE001
            logger.debug("pnl_ledger.engine_getter_failed err=%s", exc)
            return Decimal("0")

    @staticmethod
    def _to_decimal(value: Any) -> Decimal:
        try:
            if isinstance(value, Decimal):
                return value
            if value is None:
                return Decimal("0")
            return Decimal(str(value))
        except Exception:  # noqa: BLE001
            return Decimal("0")

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def state(self) -> LedgerState:
        """Read-only snapshot of the current ledger state (tests)."""
        return LedgerState(
            engine_pnl_usd=self._state.engine_pnl_usd,
            exchange_pnl_usd=self._state.exchange_pnl_usd,
            divergence_usd=self._state.divergence_usd,
            status=self._state.status,
            last_reconciled_ts=self._state.last_reconciled_ts,
        )


__all__ = [
    "LedgerConfig",
    "LedgerState",
    "PnLLedger",
    "PnLStatus",
]
