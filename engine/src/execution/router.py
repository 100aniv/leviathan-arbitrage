"""Day 8 — OrderRouter: thin adapter boundary with idempotency + optional journal hook.

Path-B v2 Day 8 introduces a stable `submit(order, adapter, trace_id, leg_index)`
boundary in front of `adapter.place_order()`. Key responsibilities:

* Formats a stable `client_order_id = f"{trace_id}.{leg_index}"` (plan §3.4).
* Deduplicates retries within a 10-minute TTL window. Duplicate submits return
  the originally cached `RouteResult` without a second adapter call.
* When the optional Day 7 `OrderStateMachine` is supplied AND the feature flag
  is active, emits a `PENDING → SENT` state transition before the adapter
  call. The journal event sequence number (if any) is recorded on the returned
  `RouteResult`.

Additive and opt-in — controlled by the `EXECUTION_ROUTER_ENABLED` environment
flag (default false). Flag OFF = zero behaviour change: direct `adapter.place_order`
call, no dedup, no journal interaction.

`live.py`, `main.py`, `executor.py`, `atomic.py` are untouched in Day 8. Day 14
migrates the legacy executor onto this substrate.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any, Protocol

import structlog

logger = structlog.get_logger(__name__)


FLAG_ENV_VAR: str = "EXECUTION_ROUTER_ENABLED"
"""Environment flag controlling router activation (default false)."""

IDEMPOTENCY_TTL_S: float = 600.0
"""Dedup cache TTL in seconds (10 minutes per plan §3.4)."""

_TRUTHY = {"1", "true", "yes", "on"}


class _AdapterProtocol(Protocol):
    """Minimum adapter surface: an awaitable place_order(order) returning a response
    with an ``order_id`` attribute."""

    async def place_order(self, order: Any) -> Any: ...


class _StateMachineProtocol(Protocol):
    """Minimum OrderStateMachine surface used by the router (optional dependency)."""

    async def transition(
        self,
        order_id: str,
        from_state: Any,
        to_state: Any,
        payload: dict[str, Any],
    ) -> Any: ...


@dataclass(frozen=True)
class RouteResult:
    """Outcome of an `OrderRouter.submit` call.

    Attributes:
        order_id: Exchange-assigned order id (from adapter response).
        client_order_id: Router-assigned client id, ``f"{trace_id}.{leg_index}"``.
        adapter_response: Raw adapter response object (preserved for caller).
        state: Post-submit state string (``"SENT"`` when router active, else bypass-state).
        journal_event_seq: Journal event seq number if a state transition was emitted,
            else ``None``.
    """

    order_id: str
    client_order_id: str
    adapter_response: Any
    state: str
    journal_event_seq: int | None = None


class OrderRouter:
    """Thin adapter boundary with 10-min dedup cache and optional SENT journal emission.

    Usage::

        router = OrderRouter(state_machine=state_machine_or_none)
        result = await router.submit(order, adapter, trace_id="abc", leg_index=0)
        # result.order_id, result.client_order_id, result.journal_event_seq

    Construction is always safe (no DB, no I/O). Behaviour is fully controlled by the
    ``EXECUTION_ROUTER_ENABLED`` environment variable at call-time.
    """

    def __init__(
        self,
        state_machine: _StateMachineProtocol | None = None,
        flag_env: str = FLAG_ENV_VAR,
        ttl_s: float = IDEMPOTENCY_TTL_S,
    ) -> None:
        self._state_machine = state_machine
        self._flag_env = flag_env
        self._ttl_s = ttl_s
        # Dedup cache: client_order_id -> (insert_time_monotonic, RouteResult).
        self._dedup: dict[str, tuple[float, RouteResult]] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ flags

    def _flag_active(self) -> bool:
        """Read EXECUTION_ROUTER_ENABLED at call time (dynamic; tests monkeypatch)."""
        return os.environ.get(self._flag_env, "false").strip().lower() in _TRUTHY

    # ------------------------------------------------------------------ core

    async def submit(
        self,
        order: Any,
        adapter: _AdapterProtocol,
        trace_id: str,
        leg_index: int,
    ) -> RouteResult:
        """Submit ``order`` via ``adapter`` with idempotency + optional journal hook.

        Args:
            order: Adapter-native order object forwarded to ``adapter.place_order``.
            adapter: Object exposing ``async place_order(order) -> response``.
            trace_id: Caller-supplied trace correlation id.
            leg_index: Leg index within the trace (0-based).

        Returns:
            RouteResult with stable ``client_order_id`` and exchange ``order_id``.
        """
        client_order_id = f"{trace_id}.{leg_index}"

        # Flag OFF: pure bypass — no dedup, no journal, direct adapter call.
        if not self._flag_active():
            response = await adapter.place_order(order)
            return RouteResult(
                order_id=str(getattr(response, "order_id", "")),
                client_order_id=client_order_id,
                adapter_response=response,
                state="SENT",
                journal_event_seq=None,
            )

        # Flag ON — check dedup cache under lock.
        async with self._lock:
            self._evict_expired_locked()
            cached = self._dedup.get(client_order_id)
            if cached is not None:
                _, cached_result = cached
                logger.debug(
                    "order_router.dedup_hit",
                    client_order_id=client_order_id,
                )
                return cached_result

        # Emit PENDING → SENT transition via state machine (if supplied).
        journal_event_seq: int | None = None
        if self._state_machine is not None:
            try:
                evt = await self._state_machine.transition(
                    client_order_id,
                    "PENDING",
                    "SENT",
                    {"trace_id": trace_id, "leg_index": leg_index},
                )
            except Exception:  # pragma: no cover - defensive, never mask adapter
                logger.warning(
                    "order_router.state_machine_transition_failed",
                    client_order_id=client_order_id,
                    exc_info=True,
                )
                evt = None
            if evt is not None:
                seq_val = getattr(evt, "seq", None)
                if isinstance(seq_val, int):
                    journal_event_seq = seq_val

        # Adapter call happens outside the dedup lock so distinct client_order_ids
        # do not serialise. If the adapter raises, no dedup entry is recorded —
        # retry must re-attempt.
        response = await adapter.place_order(order)

        result = RouteResult(
            order_id=str(getattr(response, "order_id", "")),
            client_order_id=client_order_id,
            adapter_response=response,
            state="SENT",
            journal_event_seq=journal_event_seq,
        )

        # Record dedup entry under the same lock used for reads.
        async with self._lock:
            self._dedup[client_order_id] = (time.monotonic(), result)

        return result

    # -------------------------------------------------------------- internals

    def _evict_expired_locked(self) -> None:
        """Drop dedup entries older than TTL. Caller must hold ``self._lock``."""
        if not self._dedup:
            return
        now = time.monotonic()
        ttl = self._ttl_s
        expired = [
            coid
            for coid, (inserted_at, _) in self._dedup.items()
            if now - inserted_at >= ttl
        ]
        for coid in expired:
            self._dedup.pop(coid, None)
        if expired:
            logger.debug("order_router.dedup_expired", count=len(expired))


__all__ = [
    "FLAG_ENV_VAR",
    "IDEMPOTENCY_TTL_S",
    "OrderRouter",
    "RouteResult",
]
