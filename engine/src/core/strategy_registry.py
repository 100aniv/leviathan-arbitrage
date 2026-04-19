"""Phoenix Path-B Day-4: StrategyRegistry — strategy lifecycle + universe binding.

This module owns **strategy lifecycle** (instantiation, activation, runtime
deactivation) and **universe binding** (pre-filters strategies against the
boot-time UniverseMatrix so invalid leg combinations never reach signal
generation).

Design boundaries (clarified on Day-5 when main.py migrates):

- ``StrategyRegistry`` (this module): config-driven lifecycle. Which strategies
  exist, are they active, how much capital are they allocated, and when should
  they be deactivated (budget exhaustion, per-strategy CB trip)?
- ``StrategyManager`` (``src/strategies/manager.py``): signal routing. Given a
  live signal, which active strategies should process it?

Integration (prepared for Day-5):

- ``load_active_from_config()`` reads ``config/strategy_activation.json`` and
  instantiates strategies whose ids appear in ``active_strategies`` and NOT in
  ``disabled_strategies``.
- ``subscribe_budget_ledger()`` attaches the registry to a
  :class:`src.risk.strategy_budget_ledger.StrategyBudgetLedger` so budget-
  exhaustion halts flip ``is_active=False`` here.
- ``subscribe_circuit_breaker()`` attaches the registry to
  :class:`src.risk.per_strategy_cb.PerStrategyCB` so CB trips (HALTED /
  SUSPENDED) flip ``is_active=False`` here.
- ``apply_universe_filter()`` consults ``UniverseMatrix.has_entry`` to ensure
  each active strategy has at least one validated symbol-pair. Strategies with
  zero valid pairs are deactivated with reason ``NO_VALID_UNIVERSE``.

Defense-in-depth note: ``PreTradeValidator`` also consults the matrix at order
time. The registry filter is a *coarser* cutoff — it prevents a strategy from
being marked active when its entire universe is empty (configuration drift,
adapter failure, listing removal). PreTradeValidator catches the per-signal
case.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger(__name__)

# Prometheus metrics (optional — skip if prometheus_client unavailable).
try:  # pragma: no cover - metric registration is a side-effect
    from prometheus_client import Counter, Gauge

    STRATEGY_REGISTRY_ACTIVE = Gauge(
        "leviathan_strategy_registry_active",
        "1 if strategy is currently active in the registry, else 0",
        ["strategy"],
    )
    STRATEGY_DEACTIVATED_TOTAL = Counter(
        "leviathan_strategy_deactivated_total",
        "Runtime deactivation events, labelled by deactivation reason",
        ["strategy", "reason"],
    )
    STRATEGY_ERROR_COUNT = Counter(
        "leviathan_strategy_registry_errors_total",
        "Per-strategy error count tracked by the registry",
        ["strategy"],
    )
except Exception:  # pragma: no cover - metrics optional
    STRATEGY_REGISTRY_ACTIVE = None  # type: ignore[assignment]
    STRATEGY_DEACTIVATED_TOTAL = None  # type: ignore[assignment]
    STRATEGY_ERROR_COUNT = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Deactivation reasons (string-typed for metric-label stability)
# ---------------------------------------------------------------------------

REASON_BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
REASON_CB_TRIP = "CB_TRIP"
REASON_NO_VALID_UNIVERSE = "NO_VALID_UNIVERSE"
REASON_DISABLED_CONFIG = "DISABLED_CONFIG"
REASON_OPERATOR = "OPERATOR"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class StrategyEntry:
    """Per-strategy lifecycle record managed by :class:`StrategyRegistry`.

    ``is_active`` is the single source of truth consulted by
    StrategyManager.get_active() during Day-5 migration. Day-5 replaces
    StrategyManager._strategies iteration with a registry lookup.
    """

    strategy_id: str
    instance: Any  # BaseStrategy — loosely typed here to avoid a hard import cycle
    is_active: bool = False
    allocation_pct: Decimal = Decimal("0")
    daily_loss_budget_usd: Decimal = Decimal("0")
    last_health_ts: Optional[datetime] = None
    error_count: int = 0
    deactivation_reason: Optional[str] = None
    # Optional: per-strategy universe size cached by apply_universe_filter()
    universe_entry_count: int = 0

    def to_health_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "is_active": self.is_active,
            "allocation_pct": str(self.allocation_pct),
            "daily_loss_budget_usd": str(self.daily_loss_budget_usd),
            "last_health_ts": (
                self.last_health_ts.isoformat() if self.last_health_ts else None
            ),
            "error_count": self.error_count,
            "deactivation_reason": self.deactivation_reason,
            "universe_entry_count": self.universe_entry_count,
        }


# ---------------------------------------------------------------------------
# Factory protocol
# ---------------------------------------------------------------------------

StrategyFactory = Callable[[str, "StrategyRegistry"], Any]
"""Callable that builds a ``BaseStrategy`` given a strategy_id and a back-reference
to the registry for dependency access. Used by ``load_active_from_config`` so
main.py owns construction rules (cost calc, regime detector, adapter lookup,
strategy_params wiring) while the registry owns lifecycle.
"""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class StrategyRegistry:
    """Strategy lifecycle + universe binding + health tracking.

    Day-4 scope (this module): standalone, no main.py integration. Day-5 wires
    StrategyManager.get_active() to read from this registry instead of its
    own internal dict.

    Thread-safety: registry mutations happen during boot, deactivation hooks,
    and operator calls. All are sync and assumed to run on the engine's main
    event loop. Health reads are lock-free — callers tolerate stale data.
    """

    def __init__(
        self,
        config: Any,  # EngineConfig or dict — loosely typed to decouple
        universe_matrix: Any,
        budget_ledger: Any,
        cost_calculator: Any,
    ) -> None:
        self._config = config
        self._universe_matrix = universe_matrix
        self._budget_ledger = budget_ledger
        self._cost_calculator = cost_calculator
        self._entries: dict[str, StrategyEntry] = {}
        # Hook ids allowing unsubscribe during shutdown/tests.
        self._budget_unsub: Optional[Callable[[], None]] = None
        self._cb_unsub: Optional[Callable[[], None]] = None

    # ------------------------------------------------------------------
    # Registration API
    # ------------------------------------------------------------------

    def register(self, entry: StrategyEntry) -> None:
        """Add (or replace) a strategy entry. Logs allocation info."""
        prior = self._entries.get(entry.strategy_id)
        self._entries[entry.strategy_id] = entry
        if prior is None:
            logger.info(
                "strategy_registered strategy_id=%s allocation_pct=%s is_active=%s",
                entry.strategy_id,
                entry.allocation_pct,
                entry.is_active,
            )
        else:
            logger.info(
                "strategy_replaced strategy_id=%s allocation_pct=%s is_active=%s",
                entry.strategy_id,
                entry.allocation_pct,
                entry.is_active,
            )
        self._emit_active_metric(entry)

    def unregister(self, strategy_id: str) -> Optional[StrategyEntry]:
        """Remove a strategy from the registry. Returns the removed entry."""
        entry = self._entries.pop(strategy_id, None)
        if entry is not None:
            logger.info("strategy_unregistered strategy_id=%s", strategy_id)
        return entry

    # ------------------------------------------------------------------
    # Config loading
    # ------------------------------------------------------------------

    def load_active_from_config(
        self,
        activation_path: Path,
        factory: Optional[StrategyFactory] = None,
    ) -> list[str]:
        """Read strategy_activation.json and register active strategies.

        The JSON schema (see ``engine/config/strategy_activation.json``):

        - ``active_strategies``: list[str] — ids to instantiate (active=True).
        - ``disabled_strategies``: list[str] — hard-disabled; never instantiated.
        - ``unverified_strategies``: list[str] — registered but left inactive.

        When ``factory`` is None the registry performs a *dry-run* load:
        reads the file and logs which strategies WOULD be loaded. This lets
        Day-5 main.py call load twice (once for dry-run verification, once
        with the real factory) without duplicating parsing logic.

        Returns the ordered list of strategy_ids that were loaded (or would be
        loaded in dry-run mode).
        """
        activation = self._read_activation_json(activation_path)
        active_ids: list[str] = list(activation.get("active_strategies", []))
        disabled_ids: set[str] = set(activation.get("disabled_strategies", []))
        unverified_ids: set[str] = set(activation.get("unverified_strategies", []))

        # Drop any active_id that also appears in disabled — explicit config wins.
        effective_active: list[str] = [sid for sid in active_ids if sid not in disabled_ids]
        skipped_disabled = [sid for sid in active_ids if sid in disabled_ids]
        if skipped_disabled:
            logger.info(
                "strategy_registry.skip_disabled ids=%s (present in active+disabled)",
                skipped_disabled,
            )

        if factory is None:
            logger.info(
                "strategy_registry.dry_run_load active=%s disabled=%s unverified=%s",
                effective_active,
                sorted(disabled_ids),
                sorted(unverified_ids),
            )
            return effective_active

        # Real load: instantiate via factory, register with is_active=True.
        for sid in effective_active:
            try:
                instance = factory(sid, self)
            except Exception as exc:  # noqa: BLE001 — factory errors must not kill boot
                logger.error(
                    "strategy_registry.factory_failed strategy=%s err=%s",
                    sid, exc, exc_info=True,
                )
                continue
            entry = StrategyEntry(
                strategy_id=sid,
                instance=instance,
                is_active=True,
                allocation_pct=self._lookup_allocation_pct(sid),
                daily_loss_budget_usd=self._lookup_budget_usd(sid),
                last_health_ts=datetime.now(timezone.utc),
            )
            self.register(entry)

        # Register unverified strategies as *inactive* so operators can flip
        # them on via the dashboard without a restart.
        for sid in sorted(unverified_ids):
            if sid in disabled_ids or sid in self._entries:
                continue
            try:
                instance = factory(sid, self)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "strategy_registry.unverified_factory_failed strategy=%s err=%s",
                    sid, exc,
                )
                continue
            self.register(
                StrategyEntry(
                    strategy_id=sid,
                    instance=instance,
                    is_active=False,
                    allocation_pct=self._lookup_allocation_pct(sid),
                    daily_loss_budget_usd=self._lookup_budget_usd(sid),
                    deactivation_reason="UNVERIFIED",
                )
            )

        logger.info(
            "strategy_registry.load_active_from_config loaded=%d active=%d",
            len(self._entries),
            sum(1 for e in self._entries.values() if e.is_active),
        )
        return effective_active

    def _read_activation_json(self, activation_path: Path) -> dict[str, Any]:
        """Parse strategy_activation.json. Missing/unreadable → empty lists."""
        if not activation_path.exists():
            logger.warning(
                "strategy_registry.activation_missing path=%s", activation_path,
            )
            return {}
        try:
            return json.loads(activation_path.read_text() or "{}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "strategy_registry.activation_parse_failed path=%s err=%s",
                activation_path, exc,
            )
            return {}

    def _lookup_allocation_pct(self, strategy_id: str) -> Decimal:
        """Read per-strategy allocation_pct from engine.json capital section."""
        # Accept either an EngineConfig dataclass or raw dict.
        cap = _cfg_get(self._config, "capital", {}) or {}
        # strategy key in engine.json is usually the *family* (e.g. "spot_futures")
        # rather than the instance id ("spot_futures_v1"). Strip the trailing "_vN".
        family = _strip_version_suffix(strategy_id)
        allocs = cap.get("strategies", {}) if isinstance(cap, dict) else {}
        entry = allocs.get(family) if isinstance(allocs, dict) else None
        pct_raw = (entry or {}).get("allocation_pct", 0)
        try:
            return Decimal(str(pct_raw))
        except Exception:
            return Decimal("0")

    def _lookup_budget_usd(self, strategy_id: str) -> Decimal:
        """Derive per-strategy daily loss budget via the injected budget ledger.

        Fallback chain: explicit usd override → pct of allocated capital → 0.
        The ledger encapsulates the same logic; the registry mirrors the value
        for dashboard display.
        """
        if self._budget_ledger is None:
            return Decimal("0")
        try:
            status = self._budget_ledger.get_status()
        except Exception:  # noqa: BLE001
            return Decimal("0")
        bud = status.get(strategy_id)
        if bud is None:
            return Decimal("0")
        return Decimal(str(getattr(bud, "daily_loss_budget_usd", 0)))

    # ------------------------------------------------------------------
    # Runtime deactivation
    # ------------------------------------------------------------------

    def deactivate(self, strategy_id: str, reason: str) -> None:
        """Runtime deactivation (idempotent). Emits metric + structured log.

        Intended triggers:
          - BUDGET_EXHAUSTED via StrategyBudgetLedger halt
          - CB_TRIP via PerStrategyCB HALTED/SUSPENDED
          - NO_VALID_UNIVERSE via apply_universe_filter()
          - OPERATOR via dashboard toggle
        """
        entry = self._entries.get(strategy_id)
        if entry is None:
            logger.debug(
                "strategy_registry.deactivate_unknown strategy=%s reason=%s",
                strategy_id, reason,
            )
            return
        if not entry.is_active and entry.deactivation_reason == reason:
            return  # idempotent
        entry.is_active = False
        entry.deactivation_reason = reason
        entry.last_health_ts = datetime.now(timezone.utc)
        logger.warning(
            "strategy_deactivated strategy_id=%s reason=%s",
            strategy_id, reason,
        )
        if STRATEGY_DEACTIVATED_TOTAL is not None:
            try:
                STRATEGY_DEACTIVATED_TOTAL.labels(
                    strategy=strategy_id, reason=reason,
                ).inc()
            except Exception:  # pragma: no cover
                pass
        self._emit_active_metric(entry)

    def activate(self, strategy_id: str) -> bool:
        """Manual re-activation (operator). Returns True if state changed.

        Does NOT override universe/budget/CB state checks — the next event
        from those subsystems will re-deactivate if conditions still fail.
        """
        entry = self._entries.get(strategy_id)
        if entry is None:
            return False
        if entry.is_active:
            return False
        entry.is_active = True
        entry.deactivation_reason = None
        entry.last_health_ts = datetime.now(timezone.utc)
        logger.info("strategy_activated strategy_id=%s", strategy_id)
        self._emit_active_metric(entry)
        return True

    def record_error(self, strategy_id: str) -> None:
        """Increment the error counter. Does not auto-deactivate."""
        entry = self._entries.get(strategy_id)
        if entry is None:
            return
        entry.error_count += 1
        entry.last_health_ts = datetime.now(timezone.utc)
        if STRATEGY_ERROR_COUNT is not None:
            try:
                STRATEGY_ERROR_COUNT.labels(strategy=strategy_id).inc()
            except Exception:  # pragma: no cover
                pass

    # ------------------------------------------------------------------
    # Introspection API
    # ------------------------------------------------------------------

    def get(self, strategy_id: str) -> Optional[StrategyEntry]:
        return self._entries.get(strategy_id)

    def get_active(self) -> list[StrategyEntry]:
        """Return entries with ``is_active=True``, ordered by registration."""
        return [e for e in self._entries.values() if e.is_active]

    def all_entries(self) -> list[StrategyEntry]:
        return list(self._entries.values())

    def list_strategies(self) -> list[str]:
        return list(self._entries.keys())

    def health_report(self) -> dict[str, StrategyEntry]:
        """Snapshot of all entries — consumed by the dashboard /health route."""
        return {sid: e for sid, e in self._entries.items()}

    # ------------------------------------------------------------------
    # Universe binding
    # ------------------------------------------------------------------

    async def apply_universe_filter(self) -> list[str]:
        """Deactivate strategies whose UniverseMatrix yields zero entries.

        Returns the list of strategy_ids that were deactivated as a result
        of this pass. Idempotent — strategies already deactivated for the
        same reason stay as-is.
        """
        if self._universe_matrix is None:
            return []
        deactivated: list[str] = []
        for sid, entry in self._entries.items():
            count = self._count_universe_entries(sid)
            entry.universe_entry_count = count
            if count == 0 and entry.is_active:
                self.deactivate(sid, REASON_NO_VALID_UNIVERSE)
                deactivated.append(sid)
        if deactivated:
            logger.info(
                "strategy_registry.universe_filter deactivated=%s",
                deactivated,
            )
        return deactivated

    def _count_universe_entries(self, strategy_id: str) -> int:
        """Count validated (symbol, leg_a, leg_b) tuples for a strategy.

        Uses the matrix's internal entry table when available (O(n) scan).
        Falls back to 0 when the matrix does not expose iteration.
        """
        um = self._universe_matrix
        if um is None:
            return 0
        # Preferred: direct key iteration
        entries = getattr(um, "_entries", None)
        if isinstance(entries, dict):
            return sum(1 for key in entries.keys() if key and key[0] == strategy_id)
        # Fallback: no introspection available
        return 0

    # ------------------------------------------------------------------
    # Event subscriptions (budget + CB)
    # ------------------------------------------------------------------

    def subscribe_budget_ledger(
        self,
        ledger: Any = None,
        poll_interval_s: float = 5.0,
    ) -> Callable[[], None]:
        """Install a polling watcher that deactivates halted strategies.

        The ledger exposes ``is_strategy_halted(sid)`` as a lock-free read.
        A poll-based approach avoids adding an event-emitter dependency to
        the ledger (which already persists to TSDB/JSONL and is performance-
        critical on the hot path).

        Returns an ``unsubscribe()`` callable that stops the watcher.
        """
        target = ledger if ledger is not None else self._budget_ledger
        if target is None:
            return lambda: None

        # Day-4 surface is synchronous sweep (used by tests + periodic check).
        # Day-5 wires this into the engine's health_loop every poll_interval_s.
        def sweep() -> list[str]:
            deactivated: list[str] = []
            for sid, entry in self._entries.items():
                if not entry.is_active:
                    continue
                try:
                    halted = bool(target.is_strategy_halted(sid))
                except Exception:  # noqa: BLE001
                    halted = False
                if halted:
                    self.deactivate(sid, REASON_BUDGET_EXHAUSTED)
                    deactivated.append(sid)
            return deactivated

        # Expose the sweep for tests.
        self._budget_sweep = sweep  # type: ignore[attr-defined]

        def unsubscribe() -> None:
            self._budget_sweep = None  # type: ignore[attr-defined]

        self._budget_unsub = unsubscribe
        return unsubscribe

    def subscribe_circuit_breaker(
        self,
        cb: Any = None,
    ) -> Callable[[], None]:
        """Install a sweep that deactivates strategies in HALTED/SUSPENDED CB state.

        Symmetric to :meth:`subscribe_budget_ledger`: the PerStrategyCB module
        already exposes ``is_allowed(sid)`` which returns False in HALTED or
        SUSPENDED, so the registry treats those as CB_TRIP deactivations.
        """
        if cb is None:
            return lambda: None

        def sweep() -> list[str]:
            deactivated: list[str] = []
            for sid, entry in self._entries.items():
                if not entry.is_active:
                    continue
                try:
                    allowed = bool(cb.is_allowed(sid))
                except Exception:  # noqa: BLE001
                    allowed = True
                if not allowed:
                    self.deactivate(sid, REASON_CB_TRIP)
                    deactivated.append(sid)
            return deactivated

        self._cb_sweep = sweep  # type: ignore[attr-defined]

        def unsubscribe() -> None:
            self._cb_sweep = None  # type: ignore[attr-defined]

        self._cb_unsub = unsubscribe
        return unsubscribe

    def poll_budget_halts(self) -> list[str]:
        """Public hook for the engine's health loop to run one budget sweep."""
        sweep = getattr(self, "_budget_sweep", None)
        return sweep() if callable(sweep) else []

    def poll_cb_halts(self) -> list[str]:
        """Public hook for the engine's health loop to run one CB sweep."""
        sweep = getattr(self, "_cb_sweep", None)
        return sweep() if callable(sweep) else []

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _emit_active_metric(self, entry: StrategyEntry) -> None:
        if STRATEGY_REGISTRY_ACTIVE is None:
            return
        try:
            STRATEGY_REGISTRY_ACTIVE.labels(strategy=entry.strategy_id).set(
                1.0 if entry.is_active else 0.0
            )
        except Exception:  # pragma: no cover
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cfg_get(config: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from either a dict-like config or a dataclass with attr access."""
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _strip_version_suffix(strategy_id: str) -> str:
    """``cross_exchange_v1`` → ``cross_exchange``. Safe on unknown shapes."""
    if "_v" in strategy_id:
        prefix, _, suffix = strategy_id.rpartition("_v")
        if suffix.isdigit():
            return prefix
    return strategy_id


__all__ = [
    "REASON_BUDGET_EXHAUSTED",
    "REASON_CB_TRIP",
    "REASON_NO_VALID_UNIVERSE",
    "REASON_DISABLED_CONFIG",
    "REASON_OPERATOR",
    "StrategyEntry",
    "StrategyFactory",
    "StrategyRegistry",
]
