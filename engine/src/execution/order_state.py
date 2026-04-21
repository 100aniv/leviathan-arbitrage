"""Day 7 — OrderStateMachine: explicit order lifecycle + hash-chained emission.

Path-B v2 Day 7 ships an additive, opt-in 9-state order lifecycle layered over
the Day 6 `ExecutionJournal`. Every legal transition emits exactly one hash-chained
`ExecutionEvent`. Illegal transitions raise `TransitionError` (never silently
swallowed). Behaviour is gated by `EXECUTION_STATE_MACHINE_ENABLED` (default false)
which additionally requires `EXECUTION_JOURNAL_ENABLED=true` per §22.3 Flag
Interaction Matrix — enforced at construction time via `ConfigError`.

States (9)
----------
PENDING       — intent known, not yet sent
SENT          — exchange request in flight, no ACK yet
ACKED         — exchange ACK received, order live
PARTIAL       — partially filled
FILLED        — fully filled (terminal)
CANCELLED     — cancelled before full fill (terminal)
REJECTED      — exchange refused the request (terminal)
ROLLED_BACK   — rollback leg succeeded (terminal)
STRANDED      — rollback failed; position left on exchange (terminal; reuses
                StrandedPositionTracker downstream — Day 14 wiring)

Legal transitions
-----------------
See `_LEGAL_TRANSITIONS`. Terminal states map to empty sets.

Flag-off ergonomics
-------------------
When `EXECUTION_STATE_MACHINE_ENABLED=false`, `transition()` returns `None` and
writes nothing — even for illegal (from, to) pairs. Matches the Day 6 pattern
(flag-off append is a sentinel, not an exception). Day 14 executor migration
reads the flag once at startup and wires accordingly.

Day 7 does NOT
--------------
- Mutate `live.py` / `main.py` / `executor.py` / `atomic.py` (Day 14 migrates).
- Inject `StrandedPositionTracker` directly (Day 14 wiring forwards payload).
- Cache current_state in memory — `current_state()` reads the journal. This
  is a diagnostic/test helper only; Day 14 adds an in-executor cache.
"""
from __future__ import annotations

import os
from enum import Enum

from src.execution.journal import FLAG_ENV_VAR as JOURNAL_FLAG_ENV_VAR
from src.execution.journal import ExecutionEvent, ExecutionJournal

__all__ = [
    "ConfigError",
    "OrderState",
    "OrderStateMachine",
    "TransitionError",
    "FLAG_ENV_VAR",
]


FLAG_ENV_VAR: str = "EXECUTION_STATE_MACHINE_ENABLED"
"""Environment flag controlling state-machine activation (default false)."""

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _flag_enabled(name: str) -> bool:
    return os.environ.get(name, "false").strip().lower() in _TRUTHY


class OrderState(str, Enum):
    """Nine-state order lifecycle. Value is the journal-persisted string."""

    PENDING = "PENDING"
    SENT = "SENT"
    ACKED = "ACKED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    ROLLED_BACK = "ROLLED_BACK"
    STRANDED = "STRANDED"


# Declarative legal-transition map. Terminal states map to empty sets.
_LEGAL_TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    OrderState.PENDING: frozenset(
        {OrderState.SENT, OrderState.REJECTED, OrderState.CANCELLED}
    ),
    OrderState.SENT: frozenset(
        {
            OrderState.ACKED,
            OrderState.REJECTED,
            OrderState.CANCELLED,
            OrderState.STRANDED,
        }
    ),
    OrderState.ACKED: frozenset(
        {
            OrderState.ACKED,
            OrderState.PARTIAL,
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.ROLLED_BACK,
            OrderState.STRANDED,
        }
    ),
    OrderState.PARTIAL: frozenset(
        {
            OrderState.PARTIAL,
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.STRANDED,
        }
    ),
    # Terminals — no outgoing transitions.
    OrderState.FILLED: frozenset(),
    OrderState.CANCELLED: frozenset(),
    OrderState.REJECTED: frozenset(),
    OrderState.ROLLED_BACK: frozenset(),
    OrderState.STRANDED: frozenset(),
}


def is_terminal(state: OrderState) -> bool:
    """A state is terminal iff it has no outgoing legal transitions."""
    return not _LEGAL_TRANSITIONS[state]


class TransitionError(Exception):
    """Raised when a caller attempts an illegal `from_state → to_state` transition."""


class ConfigError(Exception):
    """Raised when feature-flag dependencies are not satisfied (§22.3 matrix)."""


class OrderStateMachine:
    """Explicit order lifecycle layered over `ExecutionJournal`.

    Construction validates that `EXECUTION_STATE_MACHINE_ENABLED=true` implies
    `EXECUTION_JOURNAL_ENABLED=true` (§22.3 matrix). Flag-off construction is
    always safe — `transition()` is a no-op.

    Usage::

        journal = ExecutionJournal(db_path=...)
        await journal.start()
        sm = OrderStateMachine(journal=journal)
        evt = await sm.transition(
            order_id="A",
            from_state=OrderState.PENDING,
            to_state=OrderState.SENT,
            payload={"qty": 10},
        )
        # evt is ExecutionEvent when flag on, None when flag off.
    """

    def __init__(
        self,
        journal: ExecutionJournal,
        flag_env: str = FLAG_ENV_VAR,
    ) -> None:
        self._journal = journal
        self._flag_env = flag_env
        # Dependency guard (§22.3 Flag Interaction Matrix).
        if _flag_enabled(self._flag_env) and not _flag_enabled(JOURNAL_FLAG_ENV_VAR):
            raise ConfigError(
                f"{self._flag_env}=true requires {JOURNAL_FLAG_ENV_VAR}=true "
                "(§22.3 Flag Interaction Matrix)"
            )

    @property
    def _enabled(self) -> bool:
        """Re-read flag on each call so monkeypatch-style test toggles work."""
        return _flag_enabled(self._flag_env)

    async def transition(
        self,
        order_id: str,
        from_state: OrderState,
        to_state: OrderState,
        payload: dict[str, object],
    ) -> ExecutionEvent | None:
        """Attempt a state transition. Emit a journal event iff legal + flag on.

        Flag-off behaviour
        ------------------
        Returns `None`. Does NOT raise on illegal transitions. Does NOT write
        to the journal.

        Flag-on behaviour
        -----------------
        - Legal (from, to): returns the persisted `ExecutionEvent`.
        - Illegal (from, to): raises `TransitionError` (no journal write).
        """
        if not self._enabled:
            return None
        legal_targets = _LEGAL_TRANSITIONS.get(from_state, frozenset())
        if to_state not in legal_targets:
            raise TransitionError(
                f"{from_state.value} → {to_state.value} is illegal "
                f"(order_id={order_id}); legal targets from {from_state.value}: "
                f"{sorted(s.value for s in legal_targets) or '[terminal]'}"
            )
        return await self._journal.append(
            order_id=order_id,
            state=to_state.value,
            payload=dict(payload),
        )

    async def current_state(self, order_id: str) -> OrderState | None:
        """Return the `OrderState` of the latest journal event for `order_id`.

        Diagnostic helper — O(events_for_order). Day 14 adds an in-executor
        cache for hot-path use.

        Returns
        -------
        OrderState
            The latest recorded state.
        None
            Flag off, or no journal events exist for `order_id`.
        """
        if not self._enabled:
            return None
        events = await self._journal.replay(order_id=order_id)
        if not events:
            return None
        last_state_str = events[-1].state
        try:
            return OrderState(last_state_str)
        except ValueError:
            # Unknown state in journal (pre-Day-7 event, manual insert, etc.).
            return None
