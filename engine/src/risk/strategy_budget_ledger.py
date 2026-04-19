"""Phoenix Path-B Day-3: per-strategy daily loss budget ledger.

This module is the *authoritative* per-strategy budget tracker. It answers
one question: "Has strategy X exhausted its allocated daily loss budget?"

Design principles (derived from the cross-exchange cascading-halt incident):

- Each strategy has an **independent** daily loss budget (default 2% of
  allocated capital, configurable per-strategy in ``engine.json``).
- Budget consumption is sourced from **exchange-reported income events**
  (``REALIZED_PNL``, ``COMMISSION``, ``FUNDING_FEE``), never the engine's
  own PnL calculation — the engine has been wrong before.
- When one strategy breaches its budget it is auto-halted; other strategies
  keep trading. There is **no cascading halt**.
- Daily reset at UTC 00:00 restores full budget and clears halt flags.
- State is persisted write-through to TimescaleDB (plus JSONL fallback)
  so restarts do not lose the running daily balance.

Integration:

- ``PnLReconciler`` (Path-B Day-1 module) forwards every persisted income
  event to :meth:`on_exchange_income_event`.
- ``PreTradeValidator._check_budget_remaining`` calls
  :meth:`check_remaining` to emit ``ReasonCode.BUDGET_EXHAUSTED``.
- ``StrategyManager._should_route`` consults :meth:`is_strategy_halted`
  before delivering a signal, skipping halted strategies entirely.
- The daily Telegram report (Day-3 downstream) consumes
  :meth:`get_daily_report`.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_BUDGET_PCT = Decimal("2.0")  # 2% of allocated capital per day
UNCATEGORIZED_ID = "uncategorized"
FALLBACK_DIR_DEFAULT = "logs/strategy_budgets"

# Income types that consume budget (funding fees, commissions, realised PnL).
# Transfers and rebates are NOT attributed to any strategy's budget.
_INCOME_TYPES_COUNTED = frozenset({
    "REALIZED_PNL",
    "COMMISSION",
    "FUNDING_FEE",
    "INSURANCE_CLEAR",
})

# Prometheus metrics (optional — skip if prometheus_client unavailable).
try:  # pragma: no cover - metric registration is a side-effect
    from prometheus_client import Counter, Gauge

    STRATEGY_BUDGET_REMAINING = Gauge(
        "leviathan_strategy_budget_remaining_usd",
        "Per-strategy remaining daily loss budget (USD)",
        ["strategy"],
    )
    STRATEGY_BUDGET_BALANCE = Gauge(
        "leviathan_strategy_budget_balance_usd",
        "Per-strategy cumulative daily PnL balance (USD, negative = loss)",
        ["strategy"],
    )
    STRATEGY_BUDGET_HALTED = Gauge(
        "leviathan_strategy_budget_halted",
        "1 if strategy has breached daily loss budget, else 0",
        ["strategy"],
    )
    STRATEGY_BUDGET_HALT_EVENTS = Counter(
        "leviathan_strategy_budget_halt_events_total",
        "Per-strategy budget-exhaustion halt events",
        ["strategy"],
    )
except Exception:  # pragma: no cover - metrics optional
    STRATEGY_BUDGET_REMAINING = None  # type: ignore[assignment]
    STRATEGY_BUDGET_BALANCE = None  # type: ignore[assignment]
    STRATEGY_BUDGET_HALTED = None  # type: ignore[assignment]
    STRATEGY_BUDGET_HALT_EVENTS = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class StrategyBudget:
    """Per-strategy daily loss budget record.

    ``daily_pnl_balance_usd`` is running: negative means the strategy is
    currently consuming its budget. Budget is exhausted when
    ``-balance >= daily_loss_budget_usd`` (with small epsilon tolerance).
    """

    strategy_id: str
    daily_loss_budget_usd: Decimal
    daily_pnl_balance_usd: Decimal
    reset_ts_utc: datetime
    is_halted: bool = False
    allocated_capital_usd: Decimal = Decimal("0")
    halt_events_today: int = 0

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "daily_loss_budget_usd": str(self.daily_loss_budget_usd),
            "daily_pnl_balance_usd": str(self.daily_pnl_balance_usd),
            "reset_ts_utc": self.reset_ts_utc.isoformat(),
            "is_halted": self.is_halted,
            "allocated_capital_usd": str(self.allocated_capital_usd),
            "halt_events_today": self.halt_events_today,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StrategyBudget:
        ts = data["reset_ts_utc"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return cls(
            strategy_id=str(data["strategy_id"]),
            daily_loss_budget_usd=Decimal(str(data["daily_loss_budget_usd"])),
            daily_pnl_balance_usd=Decimal(str(data["daily_pnl_balance_usd"])),
            reset_ts_utc=ts,
            is_halted=bool(data.get("is_halted", False)),
            allocated_capital_usd=Decimal(str(data.get("allocated_capital_usd", "0"))),
            halt_events_today=int(data.get("halt_events_today", 0)),
        )

    def remaining_usd(self) -> Decimal:
        """Budget remaining today (``>= 0``). Zero means exhausted."""
        used = -self.daily_pnl_balance_usd if self.daily_pnl_balance_usd < 0 else Decimal("0")
        return max(self.daily_loss_budget_usd - used, Decimal("0"))


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

TradeLookup = Callable[[dict[str, Any]], Awaitable[Optional[str]]]
"""Injected resolver that maps an income event to its originating
``strategy_id``. Returns ``None`` when no matching engine trade is found.

Contract: must not raise. Non-deterministic lookups should log + return None.
"""


class StrategyBudgetLedger:
    """Single source of truth for per-strategy daily loss budgets.

    Thread-safety: all mutating operations are serialised by ``_lock``.
    Read-only introspection (:meth:`get_status`, :meth:`is_strategy_halted`)
    is lock-free on the currently-live snapshot dict — callers tolerate
    a stale read by at most one in-flight write.
    """

    def __init__(
        self,
        strategy_ids: list[str],
        allocated_capital_usd: dict[str, Decimal],
        budget_overrides_usd: Optional[dict[str, Decimal]] = None,
        default_budget_pct: Decimal = DEFAULT_BUDGET_PCT,
        db_pool: Any = None,
        trade_lookup: Optional[TradeLookup] = None,
        fallback_dir: Optional[Path] = None,
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._strategy_ids = list(dict.fromkeys(strategy_ids + [UNCATEGORIZED_ID]))
        self._allocated_capital_usd = {
            sid: Decimal(str(allocated_capital_usd.get(sid, 0))) for sid in self._strategy_ids
        }
        self._budget_overrides_usd = budget_overrides_usd or {}
        self._default_budget_pct = Decimal(str(default_budget_pct))
        self._db_pool = db_pool
        self._trade_lookup = trade_lookup
        self._fallback_dir = Path(fallback_dir) if fallback_dir else Path(FALLBACK_DIR_DEFAULT)
        self._now_fn = now_fn

        self._lock = asyncio.Lock()
        self._budgets: dict[str, StrategyBudget] = {}
        self._seen_tran_ids: set[str] = set()  # dedup guard inside a reset window
        self._started = False

    # ------------------------------------------------------------------
    # Budget sizing helpers
    # ------------------------------------------------------------------

    def _budget_for(self, strategy_id: str) -> Decimal:
        """Compute today's budget for ``strategy_id``.

        Priority: explicit USD override → pct of allocated capital → 0.
        """
        override = self._budget_overrides_usd.get(strategy_id)
        if override is not None:
            return Decimal(str(override))
        allocated = self._allocated_capital_usd.get(strategy_id, Decimal("0"))
        return (allocated * self._default_budget_pct / Decimal("100")).quantize(Decimal("0.0001"))

    def _fresh_budget(self, strategy_id: str, now: datetime) -> StrategyBudget:
        return StrategyBudget(
            strategy_id=strategy_id,
            daily_loss_budget_usd=self._budget_for(strategy_id),
            daily_pnl_balance_usd=Decimal("0"),
            reset_ts_utc=_floor_utc_day(now),
            is_halted=False,
            allocated_capital_usd=self._allocated_capital_usd.get(strategy_id, Decimal("0")),
            halt_events_today=0,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Initialise budget state from persistence.

        Prefers TimescaleDB for today's row set. Falls back to the JSON file
        under ``logs/strategy_budgets/YYYYMMDD.json``. Any missing strategy
        is initialised fresh.
        """
        if self._started:
            return
        now = self._now_fn()
        today = _floor_utc_day(now).date()
        loaded: dict[str, StrategyBudget] = {}

        if self._db_pool is not None:
            try:
                async with self._db_pool.acquire() as conn:
                    rows = await conn.fetch(
                        "SELECT strategy_id, daily_loss_budget_usd, daily_pnl_balance_usd, "
                        "reset_ts_utc, is_halted FROM strategy_budgets "
                        "WHERE reset_date = $1",
                        today,
                    )
                    for row in rows:
                        sid = str(row["strategy_id"])
                        loaded[sid] = StrategyBudget(
                            strategy_id=sid,
                            daily_loss_budget_usd=Decimal(str(row["daily_loss_budget_usd"])),
                            daily_pnl_balance_usd=Decimal(str(row["daily_pnl_balance_usd"])),
                            reset_ts_utc=row["reset_ts_utc"],
                            is_halted=bool(row["is_halted"]),
                            allocated_capital_usd=self._allocated_capital_usd.get(
                                sid, Decimal("0")
                            ),
                        )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "strategy_budget_ledger.db_load_failed err=%s — falling back to JSON",
                    exc,
                )
                loaded = {}

        if not loaded:
            loaded = self._load_from_json(today)

        # Fill in any missing strategy with fresh budget.
        for sid in self._strategy_ids:
            if sid in loaded:
                self._budgets[sid] = loaded[sid]
            else:
                self._budgets[sid] = self._fresh_budget(sid, now)

        self._emit_all_metrics()
        self._started = True
        logger.info(
            "strategy_budget_ledger.started strategies=%d reset_date=%s",
            len(self._budgets),
            today.isoformat(),
        )

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, StrategyBudget]:
        """Return a snapshot of all per-strategy budgets (read-only)."""
        return {sid: _copy_budget(b) for sid, b in self._budgets.items()}

    def is_strategy_halted(self, strategy_id: str) -> bool:
        """Fast lock-free halt check consumed by ``StrategyManager``."""
        bud = self._budgets.get(strategy_id)
        return bool(bud and bud.is_halted)

    async def check_remaining(
        self,
        strategy_id: str,
        worst_case_loss_usd: Decimal = Decimal("0"),
    ) -> bool:
        """Return ``True`` when the strategy may still trade.

        Applies ``worst_case_loss_usd`` as a prospective debit; the budget is
        considered exhausted when the resulting balance would exceed the
        configured cap. Unknown strategies default to "uncategorized".
        """
        sid = strategy_id if strategy_id in self._budgets else UNCATEGORIZED_ID
        bud = self._budgets.get(sid)
        if bud is None:
            return False
        if bud.is_halted:
            return False
        projected = bud.daily_pnl_balance_usd - abs(Decimal(str(worst_case_loss_usd)))
        used = -projected if projected < 0 else Decimal("0")
        return used < bud.daily_loss_budget_usd

    # ------------------------------------------------------------------
    # Income event ingestion
    # ------------------------------------------------------------------

    async def on_exchange_income_event(self, event: dict[str, Any]) -> None:
        """Attribute an exchange income event to a strategy's daily balance.

        Event shape matches :mod:`src.infra.exchange.exchange_income_fetcher`
        (``exchange``, ``income_type``, ``symbol``, ``amount_usdt``, ``tran_id``,
        ``ts_ms``) plus optional ``strategy_id``/``trace_id``.

        Attribution order:

        1. ``event["strategy_id"]`` when the caller already resolved it.
        2. ``self._trade_lookup(event)`` when injected.
        3. ``UNCATEGORIZED_ID`` bucket, with WARNING log.

        Events whose ``income_type`` is not in :data:`_INCOME_TYPES_COUNTED`
        are ignored. Duplicate ``tran_id`` values within the current reset
        window are skipped.
        """
        itype = str(event.get("income_type", "")).upper()
        if itype and itype not in _INCOME_TYPES_COUNTED:
            return

        try:
            amount = Decimal(str(event.get("amount_usdt") or 0))
        except Exception:  # noqa: BLE001
            logger.warning("strategy_budget_ledger.bad_amount event=%s", event)
            return
        if amount == 0:
            return

        tran_id = str(event.get("tran_id") or "")
        if tran_id and tran_id in self._seen_tran_ids:
            return

        strategy_id = await self._resolve_strategy_id(event)
        if strategy_id is None:
            strategy_id = UNCATEGORIZED_ID
            logger.warning(
                "strategy_budget_ledger.uncategorized_income exchange=%s "
                "symbol=%s income_type=%s amount_usdt=%s tran_id=%s",
                event.get("exchange", "?"),
                event.get("symbol", "?"),
                itype,
                amount,
                tran_id,
            )

        await self.update_pnl(strategy_id, amount, tran_id=tran_id)

    async def _resolve_strategy_id(self, event: dict[str, Any]) -> Optional[str]:
        explicit = event.get("strategy_id")
        if explicit:
            return str(explicit)
        if self._trade_lookup is None:
            return None
        try:
            return await self._trade_lookup(event)
        except Exception as exc:  # noqa: BLE001
            logger.warning("strategy_budget_ledger.trade_lookup_failed err=%s", exc)
            return None

    # ------------------------------------------------------------------
    # Atomic PnL mutation
    # ------------------------------------------------------------------

    async def update_pnl(
        self,
        strategy_id: str,
        pnl_delta_usd: Decimal,
        *,
        tran_id: str = "",
    ) -> StrategyBudget:
        """Add ``pnl_delta_usd`` to the strategy's running balance.

        Atomic under ``self._lock``. Triggers halt when the new balance would
        cross the budget threshold. Write-through persists state to TSDB +
        JSONL fallback. Returns an immutable copy of the updated record.
        """
        delta = Decimal(str(pnl_delta_usd))
        async with self._lock:
            await self._maybe_roll_day(self._now_fn())
            sid = strategy_id if strategy_id in self._budgets else UNCATEGORIZED_ID
            bud = self._budgets[sid]
            bud.daily_pnl_balance_usd += delta

            if tran_id:
                self._seen_tran_ids.add(tran_id)

            # Evaluate halt condition on the updated balance.
            used = -bud.daily_pnl_balance_usd if bud.daily_pnl_balance_usd < 0 else Decimal("0")
            if used >= bud.daily_loss_budget_usd and not bud.is_halted:
                bud.is_halted = True
                bud.halt_events_today += 1
                logger.error(
                    "strategy_budget_ledger.halted strategy=%s balance=%s budget=%s",
                    sid,
                    bud.daily_pnl_balance_usd,
                    bud.daily_loss_budget_usd,
                )
                if STRATEGY_BUDGET_HALT_EVENTS is not None:
                    try:
                        STRATEGY_BUDGET_HALT_EVENTS.labels(strategy=sid).inc()
                    except Exception:  # pragma: no cover
                        pass

            await self._persist(bud)
            self._emit_metrics(bud)
            return _copy_budget(bud)

    # ------------------------------------------------------------------
    # Daily reset
    # ------------------------------------------------------------------

    async def reset_daily(self, now_utc: Optional[datetime] = None) -> None:
        """Restore full budget and clear halt flags.

        Called by the engine's UTC 00:00 scheduler. Idempotent — calling more
        than once within the same UTC day leaves the fresh budgets in place.
        """
        now = now_utc or self._now_fn()
        async with self._lock:
            self._seen_tran_ids.clear()
            for sid in self._strategy_ids:
                self._budgets[sid] = self._fresh_budget(sid, now)
                await self._persist(self._budgets[sid])
            self._emit_all_metrics()
            logger.info(
                "strategy_budget_ledger.reset_daily reset_ts=%s strategies=%d",
                _floor_utc_day(now).isoformat(),
                len(self._budgets),
            )

    async def _maybe_roll_day(self, now: datetime) -> None:
        """Auto-reset when a write crosses UTC midnight."""
        today_start = _floor_utc_day(now)
        any_bud = next(iter(self._budgets.values()), None)
        if any_bud is None:
            return
        if any_bud.reset_ts_utc < today_start:
            # Roll without re-entering the lock (we already hold it).
            self._seen_tran_ids.clear()
            for sid in self._strategy_ids:
                self._budgets[sid] = self._fresh_budget(sid, now)
                await self._persist(self._budgets[sid])
            self._emit_all_metrics()
            logger.info(
                "strategy_budget_ledger.auto_rolled reset_ts=%s",
                today_start.isoformat(),
            )

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    async def get_daily_report(self) -> dict[str, Any]:
        """Snapshot consumed by the daily Telegram reconciliation report."""
        snapshot = self.get_status()
        total_budget = sum(
            (b.daily_loss_budget_usd for b in snapshot.values()), start=Decimal("0")
        )
        total_balance = sum(
            (b.daily_pnl_balance_usd for b in snapshot.values()), start=Decimal("0")
        )
        halted = sorted(sid for sid, b in snapshot.items() if b.is_halted)
        return {
            "reset_ts_utc": _floor_utc_day(self._now_fn()).isoformat(),
            "total_budget_usd": str(total_budget),
            "total_balance_usd": str(total_balance),
            "halted_strategies": halted,
            "per_strategy": {sid: b.to_dict() for sid, b in snapshot.items()},
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def _persist(self, budget: StrategyBudget) -> None:
        """Write-through: TSDB first, JSONL fallback on any failure."""
        reset_date = budget.reset_ts_utc.date()
        wrote_to_db = False
        if self._db_pool is not None:
            try:
                async with self._db_pool.acquire() as conn:
                    await conn.execute(
                        "INSERT INTO strategy_budgets "
                        "(reset_date, strategy_id, daily_loss_budget_usd, "
                        "daily_pnl_balance_usd, reset_ts_utc, is_halted, "
                        "last_update_ts) VALUES ($1,$2,$3,$4,$5,$6, NOW()) "
                        "ON CONFLICT (reset_date, strategy_id) DO UPDATE SET "
                        "daily_loss_budget_usd = EXCLUDED.daily_loss_budget_usd, "
                        "daily_pnl_balance_usd = EXCLUDED.daily_pnl_balance_usd, "
                        "is_halted = EXCLUDED.is_halted, "
                        "last_update_ts = NOW()",
                        reset_date,
                        budget.strategy_id,
                        budget.daily_loss_budget_usd,
                        budget.daily_pnl_balance_usd,
                        budget.reset_ts_utc,
                        budget.is_halted,
                    )
                wrote_to_db = True
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "strategy_budget_ledger.db_persist_failed strategy=%s err=%s",
                    budget.strategy_id,
                    exc,
                )

        if not wrote_to_db:
            self._write_json_fallback(budget)

    def _write_json_fallback(self, budget: StrategyBudget) -> None:
        try:
            path = self._fallback_path_for(budget.reset_ts_utc)
            path.parent.mkdir(parents=True, exist_ok=True)
            # Merge into existing snapshot so partial writes do not clobber
            # peer strategies written in the same reset window.
            existing: dict[str, Any] = {}
            if path.exists():
                try:
                    existing = json.loads(path.read_text() or "{}")
                except Exception:  # noqa: BLE001
                    existing = {}
            existing[budget.strategy_id] = budget.to_dict()
            path.write_text(json.dumps(existing, indent=2, sort_keys=True))
        except OSError as exc:
            logger.warning(
                "strategy_budget_ledger.json_persist_failed strategy=%s err=%s",
                budget.strategy_id,
                exc,
            )

    def _load_from_json(self, reset_date: date) -> dict[str, StrategyBudget]:
        path = self._fallback_path_for(datetime.combine(reset_date, time.min, tzinfo=timezone.utc))
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text() or "{}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "strategy_budget_ledger.json_load_failed path=%s err=%s",
                path, exc,
            )
            return {}
        out: dict[str, StrategyBudget] = {}
        for sid, data in raw.items():
            try:
                out[sid] = StrategyBudget.from_dict(data)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "strategy_budget_ledger.json_row_bad strategy=%s err=%s",
                    sid, exc,
                )
        return out

    def _fallback_path_for(self, dt: datetime) -> Path:
        return self._fallback_dir / f"{dt.strftime('%Y%m%d')}.json"

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _emit_metrics(self, bud: StrategyBudget) -> None:
        if STRATEGY_BUDGET_BALANCE is not None:
            try:
                STRATEGY_BUDGET_BALANCE.labels(strategy=bud.strategy_id).set(
                    float(bud.daily_pnl_balance_usd)
                )
                STRATEGY_BUDGET_REMAINING.labels(strategy=bud.strategy_id).set(
                    float(bud.remaining_usd())
                )
                STRATEGY_BUDGET_HALTED.labels(strategy=bud.strategy_id).set(
                    1.0 if bud.is_halted else 0.0
                )
            except Exception:  # pragma: no cover
                pass

    def _emit_all_metrics(self) -> None:
        for bud in self._budgets.values():
            self._emit_metrics(bud)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _floor_utc_day(dt: datetime) -> datetime:
    """Return ``dt`` truncated to 00:00:00 UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return datetime.combine(dt.astimezone(timezone.utc).date(), time.min, tzinfo=timezone.utc)


def _copy_budget(bud: StrategyBudget) -> StrategyBudget:
    return StrategyBudget(
        strategy_id=bud.strategy_id,
        daily_loss_budget_usd=bud.daily_loss_budget_usd,
        daily_pnl_balance_usd=bud.daily_pnl_balance_usd,
        reset_ts_utc=bud.reset_ts_utc,
        is_halted=bud.is_halted,
        allocated_capital_usd=bud.allocated_capital_usd,
        halt_events_today=bud.halt_events_today,
    )


__all__ = [
    "DEFAULT_BUDGET_PCT",
    "StrategyBudget",
    "StrategyBudgetLedger",
    "TradeLookup",
    "UNCATEGORIZED_ID",
]
