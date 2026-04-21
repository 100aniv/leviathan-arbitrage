"""Day 11 — parallel IOC-TTL cross-exchange executor (HIGH risk, opt-in).

Path-B v2 Day 11: converts the sequential cross-exchange leg pattern
(`executor.py:1050 → 1276`, 200-480 ms naked-exposure window) into a
concurrent `asyncio.gather` submission with per-leg IOC TTL and a new
both-legs-stranded rollback topology.

Five outcomes
-------------
1. Both legs fill within TTL  → ``SUCCESS``
2. Only leg 1 fills            → ``STRANDED_LEG1`` (register with
   ``StrandedPositionTracker``)
3. Only leg 2 fills            → ``STRANDED_LEG2`` (mirror path)
4. Neither fills               → ``NEITHER`` (IOC auto-cancels, no unwind
   required on-exchange)
5. Both fill but invariant violated after the fact
                              → ``_do_rollback_cross_parallel`` unwinds
                                 concurrently; ``ROLLED_BACK``

Plus two pre-gather reject paths:
    - ``DISABLED``        — feature flag off, adapter calls skipped
    - ``EDGE_EVAPORATED`` — pre-gather edge re-check failed

Flag matrix (§22.3)
-------------------
``EXECUTION_PARALLEL_LEGS_ENABLED`` (default false) requires:
- ``EXECUTION_JOURNAL_ENABLED=true``
- ``EXECUTION_STATE_MACHINE_ENABLED=true``
- ``EXECUTION_ROUTER_ENABLED=true``

Mis-configuration raises ``ConfigError`` at construction time (fail fast).

Scope
-----
This module does **not** edit ``executor.py``: the legacy sequential path
(`executor.py:1050-1276`) stays as rollback insurance for ≥2 weeks per plan
§3. Day 14 migrates the executor to route cross-exchange traffic through
this module.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass
from dataclasses import field as dc_field
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Callable, Protocol

import structlog

from src.execution.atomic import AtomicOrderExecutor
from src.execution.journal import FLAG_ENV_VAR as JOURNAL_FLAG_ENV_VAR
from src.execution.order_state import FLAG_ENV_VAR as STATE_MACHINE_FLAG_ENV_VAR
from src.execution.order_state import OrderState, OrderStateMachine, TransitionError
from src.execution.router import FLAG_ENV_VAR as ROUTER_FLAG_ENV_VAR
from src.execution.router import OrderRouter
from src.execution.stranded import StrandedPositionTracker

logger = structlog.get_logger(__name__)


FLAG_ENV_VAR: str = "EXECUTION_PARALLEL_LEGS_ENABLED"
"""Environment flag gating Day 11 parallel execution (default ``false``)."""

DEFAULT_IOC_TTL_MS: int = 5000
"""Default per-leg IOC TTL budget."""

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _flag_enabled(name: str) -> bool:
    return os.environ.get(name, "false").strip().lower() in _TRUTHY


_BUY_ALIASES = frozenset({"buy", "long", "bid"})
_SELL_ALIASES = frozenset({"sell", "short", "ask"})


def _normalize_side(side: Any) -> str:
    """Normalize any side representation to lowercase ``"buy"`` or ``"sell"``.

    Handles adapter/strategy variants: "BUY"/"Buy"/"long"/"bid" → "buy";
    "SELL"/"Sell"/"short"/"ask" → "sell". Unrecognised values raise ``ValueError``.
    Applied at LegState ingress and rollback-unwind path to keep the executor's
    side-comparison logic stable under H-4 review findings.
    """
    raw = str(side).strip().lower()
    if raw in _BUY_ALIASES:
        return "buy"
    if raw in _SELL_ALIASES:
        return "sell"
    raise ValueError(f"unrecognised side literal: {side!r}")


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class ConfigError(Exception):
    """Raised when Day 11 flag dependencies are not satisfied (§22.3)."""


# ---------------------------------------------------------------------------
# Status enum + result dataclass
# ---------------------------------------------------------------------------


class ExecutionStatusV2(StrEnum):
    SUCCESS = "success"
    STRANDED_LEG1 = "stranded_leg1"
    STRANDED_LEG2 = "stranded_leg2"
    NEITHER = "neither"
    EDGE_EVAPORATED = "edge_evaporated"
    ROLLED_BACK = "rolled_back"
    DISABLED = "disabled"


@dataclass
class LegState:
    """Per-leg execution outcome used by the parallel rollback topology."""

    leg_index: int
    exchange_id: str
    symbol: str
    side: str
    size: Decimal
    price: Decimal
    filled: bool = False
    filled_size: Decimal = Decimal("0")
    avg_price: Decimal = Decimal("0")
    elapsed_ms: float = 0.0
    error: str | None = None
    client_order_id: str | None = None


@dataclass
class ExecResultV2:
    """Outcome of ``CrossExchangeV2Executor.execute``."""

    status: ExecutionStatusV2
    legs: list[LegState] = dc_field(default_factory=list)
    trace_id: str = ""
    error: str = ""


# ---------------------------------------------------------------------------
# Protocol types
# ---------------------------------------------------------------------------


class _EdgeStillValidFn(Protocol):
    def __call__(self, trade_request: Any) -> bool: ...


class _BothLegsInvariantFn(Protocol):
    def __call__(self, leg_states: list[LegState]) -> bool: ...


class _AdapterProtocol(Protocol):
    async def place_ioc_limit(
        self, symbol: str, side: str, price: Decimal, size: Decimal
    ) -> Any: ...

    async def place_market(
        self, symbol: str, side: str, size: Decimal
    ) -> Any: ...


# ---------------------------------------------------------------------------
# Core executor
# ---------------------------------------------------------------------------


class CrossExchangeV2Executor:
    """Concurrent IOC-TTL cross-exchange executor with parallel rollback.

    Usage::

        executor = CrossExchangeV2Executor(
            router=router,
            state_machine=state_machine,        # required when flag on (C-1)
            stranded=stranded_tracker,
            atomic=AtomicOrderExecutor(timeout_ms=5000),
            ttl_ms=5000,
        )
        result = await executor.execute(trade_request, adapter_a, adapter_b)

    Construction enforces the §22.3 flag-dependency matrix. Flag-off behaviour
    is a pure sentinel: ``ExecResultV2(status=DISABLED)`` without touching
    any adapter. Flag-on with ``state_machine=None`` raises ``ConfigError``
    (C-1 review blocker — STRANDED transitions must be journaled).
    """

    def __init__(
        self,
        router: OrderRouter,
        stranded: StrandedPositionTracker,
        state_machine: OrderStateMachine | None = None,
        atomic: AtomicOrderExecutor | None = None,
        ttl_ms: int = DEFAULT_IOC_TTL_MS,
        edge_check_fn: Callable[[Any], bool] | None = None,
        both_legs_invariant_fn: Callable[[list[LegState]], bool] | None = None,
        flag_env: str = FLAG_ENV_VAR,
    ) -> None:
        self._router = router
        self._sm = state_machine
        self._stranded = stranded
        self._atomic = atomic or AtomicOrderExecutor(timeout_ms=ttl_ms)
        self._ttl_ms = ttl_ms
        self._edge_check_fn = edge_check_fn
        # Both-legs invariant check: default to True (both fills = SUCCESS).
        # Day 14 wires this to the fresh-book spread sanity check used by the
        # sequential path.
        self._both_legs_invariant_fn = both_legs_invariant_fn or (lambda _legs: True)
        self._flag_env = flag_env

        # §22.3 flag dependency matrix: enforced at construction so
        # mis-configuration fails fast.
        if _flag_enabled(self._flag_env):
            missing: list[str] = []
            for dep in (
                JOURNAL_FLAG_ENV_VAR,
                STATE_MACHINE_FLAG_ENV_VAR,
                ROUTER_FLAG_ENV_VAR,
            ):
                if not _flag_enabled(dep):
                    missing.append(dep)
            if missing:
                raise ConfigError(
                    f"{self._flag_env}=true requires {missing} all true "
                    "(§22.3 Flag Interaction Matrix)"
                )
            if self._sm is None:
                raise ConfigError(
                    f"{self._flag_env}=true requires a non-None state_machine "
                    "(§22.3 Flag Interaction Matrix): STRANDED transitions "
                    "must be journaled"
                )

    @property
    def _enabled(self) -> bool:
        """Re-read flag on each call so tests can toggle via monkeypatch."""
        return _flag_enabled(self._flag_env)

    async def execute(
        self,
        trade_request: Any,
        adapter_a: _AdapterProtocol,
        adapter_b: _AdapterProtocol,
    ) -> ExecResultV2:
        """Dispatch both legs concurrently via IOC TTL.

        ``trade_request`` is expected to carry at least ``.legs`` with two
        entries (TradeLeg-shaped). A ``.trace_id`` attribute is honoured
        when present; otherwise a fresh UUID is generated.
        """
        if not self._enabled:
            return ExecResultV2(
                status=ExecutionStatusV2.DISABLED,
                legs=[],
                error="EXECUTION_PARALLEL_LEGS_ENABLED=false",
            )

        legs_seq = getattr(trade_request, "legs", None) or []
        if len(legs_seq) != 2:
            raise ValueError(
                f"CrossExchangeV2Executor.execute requires exactly 2 legs; "
                f"got {len(legs_seq)}"
            )
        leg_a, leg_b = legs_seq[0], legs_seq[1]

        trace_id = str(
            getattr(trade_request, "trace_id", None) or uuid.uuid4()
        )

        # Pre-gather edge re-check (short TTL, ~50 ms fresh-book latency).
        if self._edge_check_fn is not None:
            try:
                still_valid = bool(self._edge_check_fn(trade_request))
            except Exception:
                logger.warning(
                    "cross_exchange_v2.edge_check_error",
                    trace_id=trace_id,
                    exc_info=True,
                )
                still_valid = False
            if not still_valid:
                return ExecResultV2(
                    status=ExecutionStatusV2.EDGE_EVAPORATED,
                    legs=[
                        LegState(
                            leg_index=0,
                            exchange_id=str(leg_a.exchange_id),
                            symbol=str(leg_a.symbol),
                            side=_normalize_side(leg_a.side),
                            size=Decimal(str(leg_a.size)),
                            price=Decimal(str(leg_a.price or 0)),
                        ),
                        LegState(
                            leg_index=1,
                            exchange_id=str(leg_b.exchange_id),
                            symbol=str(leg_b.symbol),
                            side=_normalize_side(leg_b.side),
                            size=Decimal(str(leg_b.size)),
                            price=Decimal(str(leg_b.price or 0)),
                        ),
                    ],
                    trace_id=trace_id,
                    error="pre_gather_edge_invalid",
                )

        # Concurrent submission — asyncio.gather with return_exceptions so
        # one-leg failures do not cancel the other leg.
        try:
            results = await asyncio.gather(
                self._submit_leg(leg_a, adapter_a, trace_id, 0),
                self._submit_leg(leg_b, adapter_b, trace_id, 1),
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            # Outer task cancellation: propagate after best-effort logging.
            logger.warning(
                "cross_exchange_v2.outer_cancelled",
                trace_id=trace_id,
            )
            raise

        leg_states: list[LegState] = []
        for idx, res in enumerate(results):
            if isinstance(res, BaseException):
                src_leg = legs_seq[idx]
                leg_states.append(
                    LegState(
                        leg_index=idx,
                        exchange_id=str(src_leg.exchange_id),
                        symbol=str(src_leg.symbol),
                        side=_normalize_side(src_leg.side),
                        size=Decimal(str(src_leg.size)),
                        price=Decimal(str(src_leg.price or 0)),
                        filled=False,
                        error=f"{type(res).__name__}: {res}",
                    )
                )
            else:
                leg_states.append(res)

        leg_a_state, leg_b_state = leg_states[0], leg_states[1]

        # Outcome selection.
        if leg_a_state.filled and leg_b_state.filled:
            # Both fills → honour the invariant callback (default True).
            try:
                invariant_ok = bool(self._both_legs_invariant_fn(leg_states))
            except Exception:
                logger.warning(
                    "cross_exchange_v2.invariant_check_error",
                    trace_id=trace_id,
                    exc_info=True,
                )
                invariant_ok = False
            if invariant_ok:
                return ExecResultV2(
                    status=ExecutionStatusV2.SUCCESS,
                    legs=leg_states,
                    trace_id=trace_id,
                )
            # Both filled but invariant violated → both-legs rollback path.
            rb_result = await self._do_rollback_cross_parallel(
                leg_states,
                reason="both_legs_invariant_violated",
                adapters=[adapter_a, adapter_b],
            )
            return ExecResultV2(
                status=ExecutionStatusV2.ROLLED_BACK,
                legs=rb_result,
                trace_id=trace_id,
                error="both_legs_invariant_violated",
            )

        if leg_a_state.filled and not leg_b_state.filled:
            # Only leg1 fills → STRANDED_LEG1 path.
            await self._register_stranded(leg_a_state, reason="leg2_ioc_ttl_expired")
            return ExecResultV2(
                status=ExecutionStatusV2.STRANDED_LEG1,
                legs=leg_states,
                trace_id=trace_id,
                error="leg2_ioc_ttl_expired",
            )

        if leg_b_state.filled and not leg_a_state.filled:
            # Only leg2 fills → mirror path.
            await self._register_stranded(leg_b_state, reason="leg1_ioc_ttl_expired")
            return ExecResultV2(
                status=ExecutionStatusV2.STRANDED_LEG2,
                legs=leg_states,
                trace_id=trace_id,
                error="leg1_ioc_ttl_expired",
            )

        # Neither filled → IOC TTL auto-cancels on-exchange, no rollback.
        return ExecResultV2(
            status=ExecutionStatusV2.NEITHER,
            legs=leg_states,
            trace_id=trace_id,
            error="both_legs_ioc_ttl_expired",
        )

    # ------------------------------------------------------------------ legs

    async def _submit_leg(
        self,
        leg: Any,
        adapter: _AdapterProtocol,
        trace_id: str,
        leg_index: int,
    ) -> LegState:
        """Submit one leg as an IOC order. Returns a populated LegState."""
        leg_state = LegState(
            leg_index=leg_index,
            exchange_id=str(leg.exchange_id),
            symbol=str(leg.symbol),
            side=_normalize_side(leg.side),
            size=Decimal(str(leg.size)),
            price=Decimal(str(leg.price or 0)),
            client_order_id=f"{trace_id}.{leg_index}",
        )

        # Day 7 transition: PENDING → SENT (best-effort; skip if SM missing
        # or disabled so tests without a journal still work).
        await self._safe_transition(
            leg_state.client_order_id,
            OrderState.PENDING,
            OrderState.SENT,
            {"trace_id": trace_id, "leg_index": leg_index},
        )

        try:
            filled, filled_size, avg_price, elapsed_ms = await self._atomic.try_ioc(
                adapter,
                leg_state.symbol,
                leg_state.side,
                leg_state.price,
                leg_state.size,
                ttl_ms=self._ttl_ms,
            )
        except asyncio.CancelledError:
            leg_state.error = "cancelled"
            raise
        except Exception as exc:
            leg_state.error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "cross_exchange_v2.submit_leg_error",
                trace_id=trace_id,
                leg_index=leg_index,
                exchange=leg_state.exchange_id,
                symbol=leg_state.symbol,
                exc_info=True,
            )
            await self._safe_transition(
                leg_state.client_order_id,
                OrderState.SENT,
                OrderState.REJECTED,
                {"reason": leg_state.error},
            )
            return leg_state

        leg_state.filled = bool(filled)
        leg_state.filled_size = filled_size
        leg_state.avg_price = avg_price
        leg_state.elapsed_ms = elapsed_ms

        # Terminal transition: ACKED first (fill received), then FILLED /
        # CANCELLED. State-machine is opt-in; if disabled these are no-ops.
        if filled:
            await self._safe_transition(
                leg_state.client_order_id,
                OrderState.SENT,
                OrderState.ACKED,
                {"filled_size": str(filled_size)},
            )
            await self._safe_transition(
                leg_state.client_order_id,
                OrderState.ACKED,
                OrderState.FILLED,
                {"avg_price": str(avg_price)},
            )
        else:
            # No fill within TTL → IOC cancels on-exchange.
            await self._safe_transition(
                leg_state.client_order_id,
                OrderState.SENT,
                OrderState.CANCELLED,
                {"reason": "ioc_ttl_expired"},
            )
        return leg_state

    async def _safe_transition(
        self,
        order_id: str | None,
        from_state: OrderState,
        to_state: OrderState,
        payload: dict[str, Any],
    ) -> None:
        """Fire-and-forget state-machine transition. Swallows all exceptions."""
        if self._sm is None or order_id is None:
            return
        try:
            await self._sm.transition(order_id, from_state, to_state, payload)
        except TransitionError:
            # Illegal transitions are bugs (not routine) — promote to ERROR
            # so operators/runbook see the violation (§12.3 silent-DEBUG ban).
            logger.error(
                "cross_exchange_v2.transition_illegal",
                order_id=order_id,
                from_state=str(from_state),
                to_state=str(to_state),
            )
        except Exception:
            logger.warning(
                "cross_exchange_v2.transition_error",
                order_id=order_id,
                exc_info=True,
            )

    # ---------------------------------------------------------------- stranded

    async def _register_stranded(self, leg: LegState, *, reason: str) -> None:
        """Register a single-leg stranded position + emit STRANDED transition.

        C-1 fix: a lone tracker.register() leaves no journal trail for the
        STRANDED state. Also transition the state-machine to STRANDED so the
        journal/replay path carries the terminal state.
        """
        value_usd = float(leg.filled_size * leg.avg_price) if leg.avg_price else 0.0
        should_halt = self._stranded.register(
            exchange_id=leg.exchange_id,
            symbol=leg.symbol,
            side=leg.side,
            size=float(leg.filled_size),
            value_usd=value_usd,
            reason=reason,
        )
        # C-1: journal the STRANDED transition via state-machine so the
        # hash-chained replay reflects the terminal state. The source state is
        # FILLED (single leg filled before its partner stranded it).
        await self._safe_transition(
            leg.client_order_id,
            OrderState.FILLED,
            OrderState.STRANDED,
            {
                "exchange": leg.exchange_id,
                "symbol": leg.symbol,
                "side": leg.side,
                "size": str(leg.filled_size),
                "value_usd": value_usd,
                "reason": reason,
            },
        )
        if should_halt:
            logger.critical(
                "cross_exchange_v2.stranded_halt_threshold_reached",
                exchange=leg.exchange_id,
                symbol=leg.symbol,
                reason=reason,
            )

    # ------------------------------------------------------------- rollback

    async def _do_rollback_cross_parallel(
        self,
        leg_states: list[LegState],
        reason: str,
        adapters: list[_AdapterProtocol],
    ) -> list[LegState]:
        """Unwind both legs concurrently when the both-legs invariant fails.

        Extends the sequential signature
        ``_do_rollback_cross(ex_a_id, leg1_order, leg1_result, leg2_result, ...)``
        to accept a ``list[LegState]`` so it naturally supports the parallel
        topology. For Day 11 we unwind each filled leg with a reverse
        market order via ``adapter.place_market``.
        """
        if len(leg_states) != len(adapters):
            raise ValueError("leg_states and adapters length mismatch")

        async def _unwind(idx: int) -> LegState:
            leg = leg_states[idx]
            if not leg.filled:
                return leg
            # H-4: leg.side has been normalized at ingress, but re-normalize
            # defensively so this helper stays robust if called elsewhere.
            close_side = "sell" if _normalize_side(leg.side) == "buy" else "buy"
            try:
                await adapters[idx].place_market(
                    leg.symbol, close_side, leg.filled_size
                )
                await self._safe_transition(
                    leg.client_order_id,
                    OrderState.FILLED,
                    OrderState.ROLLED_BACK,
                    {"reason": reason, "unwind_side": close_side},
                )
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"
                leg.error = err
                logger.error(
                    "cross_exchange_v2.rollback_leg_failed",
                    leg_index=idx,
                    exchange=leg.exchange_id,
                    symbol=leg.symbol,
                    err=err,
                )
                await self._register_stranded(
                    leg, reason=f"both_legs_rollback_failed:{err}"
                )
            return leg

        tasks = [_unwind(i) for i in range(len(leg_states))]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: list[LegState] = []
        for idx, res in enumerate(results):
            if isinstance(res, BaseException):
                leg = leg_states[idx]
                leg.error = f"{type(res).__name__}: {res}"
                out.append(leg)
            else:
                out.append(res)
        logger.warning(
            "cross_exchange_v2.rollback_complete",
            reason=reason,
            leg_count=len(leg_states),
        )
        return out


__all__ = [
    "ConfigError",
    "CrossExchangeV2Executor",
    "DEFAULT_IOC_TTL_MS",
    "ExecResultV2",
    "ExecutionStatusV2",
    "FLAG_ENV_VAR",
    "LegState",
]
