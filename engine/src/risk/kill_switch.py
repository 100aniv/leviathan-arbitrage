"""LEVIATHAN Kill Switch — 3-Tier Emergency Stop.

Amendment 1D: In-process halt flag (threading.Event) — Redis-independent.
Amendment 2A: 3-tier kill switch (local halt → cancel orders → close positions).

TIER 1 (< 1ms):    threading.Event.set() + Redis SET
TIER 2 (< 500ms):  asyncio.gather cancel all pending orders
TIER 3 (< 2000ms): asyncio.gather close all positions at market

CRITICAL: After Tier 1 completes, NO new orders can be submitted regardless
of Tier 2/3 status. The engine is effectively halted.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# In-process halt flag — Amendment 1D
#
# This is the safety-critical path. Works without Redis, PostgreSQL, or ANY
# external dependency. Setting this flag is < 0.01ms.
# Every order submission path MUST check is_halted() before proceeding.
# ---------------------------------------------------------------------------
_HALT_FLAG = threading.Event()  # set() = halted, clear() = normal


def halt_local() -> None:
    """Set local halt flag. < 0.01ms. No external dependency."""
    _HALT_FLAG.set()


def is_halted() -> bool:
    """
    Check halt state. Every order submission path MUST call this.
    Uses threading.Event — works WITHOUT Redis.
    """
    return _HALT_FLAG.is_set()


def clear_halt() -> None:
    """
    Clear halt flag. Only callable after full reconciliation passes.
    Both in-process flag AND Redis HALT key must agree before resuming.
    """
    _HALT_FLAG.clear()


# ---------------------------------------------------------------------------
# Protocols for exchange adapters (dependency inversion)
# ---------------------------------------------------------------------------


class ExchangeAdapter(Protocol):
    """Minimal interface required by kill switch."""

    @property
    def exchange_id(self) -> str: ...

    async def cancel_all_orders(self, timeout_ms: int = 2000) -> list[str]:
        """Cancel all pending orders. Returns list of cancelled order IDs."""
        ...

    async def close_all_positions(self, timeout_ms: int = 3000) -> list[str]:
        """Close all open positions at market. Returns list of closed position IDs."""
        ...


# ---------------------------------------------------------------------------
# Kill Switch Event — timing breakdown
# ---------------------------------------------------------------------------


@dataclass
class KillSwitchEvent:
    trigger_ts: float  # time.perf_counter() at trigger
    tier1_ts: float | None = None
    tier2_ts: float | None = None
    tier3_ts: float | None = None
    tier1_latency_ms: float | None = None
    tier2_latency_ms: float | None = None
    tier3_latency_ms: float | None = None
    cancelled_orders: list[str] = field(default_factory=list)
    closed_positions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    redis_halt_set: bool = False


# ---------------------------------------------------------------------------
# Kill Switch
# ---------------------------------------------------------------------------


class KillSwitch:
    """
    3-Tier Emergency Kill Switch (Amendment 2A).

    After Tier 1 completes (< 10ms hard limit), NO new orders can be submitted
    regardless of Tier 2/3 status. The engine is effectively halted even if
    exchange cancellations are still in-flight.
    """

    HALT_REDIS_KEY = "leviathan:halt"
    HALT_REDIS_TTL = 86400  # 24h

    def __init__(
        self,
        redis_client: Any | None = None,
        exchanges: list[Any] | None = None,
        tier3_enabled: bool = True,
    ) -> None:
        self._redis = redis_client
        self._exchanges: list[Any] = exchanges or []
        self._tier3_enabled = tier3_enabled
        self._lock = asyncio.Lock()
        self._triggered = False

    def is_halted(self) -> bool:
        """Check halt state. Delegates to module-level is_halted()."""
        return is_halted()

    async def trigger(self) -> KillSwitchEvent:
        """
        Execute full 3-tier kill switch sequence.
        Returns timing breakdown via KillSwitchEvent.
        """
        async with self._lock:
            if self._triggered:
                logger.warning("kill_switch_already_triggered")
                event = KillSwitchEvent(trigger_ts=time.perf_counter())
                event.errors.append("Already triggered")
                return event
            self._triggered = True

        event = KillSwitchEvent(trigger_ts=time.perf_counter())

        # TIER 1 — Local Halt (target < 1ms, hard limit < 10ms)
        await self._tier1_local_halt(event)

        # TIER 2 — Remote Order Cancellation (target < 500ms)
        await self._tier2_cancel_orders(event)

        # TIER 3 — Position Closure (target < 2000ms, configurable)
        if self._tier3_enabled:
            await self._tier3_close_positions(event)

        logger.critical(
            "kill_switch_complete",
            tier1_ms=event.tier1_latency_ms,
            tier2_ms=event.tier2_latency_ms,
            tier3_ms=event.tier3_latency_ms,
            cancelled_orders=len(event.cancelled_orders),
            closed_positions=len(event.closed_positions),
            errors=len(event.errors),
        )

        return event

    async def _tier1_local_halt(self, event: KillSwitchEvent) -> None:
        """
        TIER 1: Set in-process halt flag + Redis HALT key.
        Target: < 1ms (halt_local() is < 0.01ms).
        """
        t_start = time.perf_counter()

        # Step 1: Set in-process halt flag — < 0.01ms, NO external dependency
        halt_local()

        # Step 2: Set Redis HALT key — ~0.5ms (non-fatal if fails)
        if self._redis is not None:
            try:
                await self._redis.set(self.HALT_REDIS_KEY, "1", ex=self.HALT_REDIS_TTL)
                event.redis_halt_set = True
            except Exception as exc:
                # Redis failure is non-fatal — in-process flag is already set
                msg = f"Redis HALT key failed (in-process flag still set): {exc}"
                logger.error(msg)
                event.errors.append(msg)

        event.tier1_ts = time.perf_counter()
        event.tier1_latency_ms = (event.tier1_ts - t_start) * 1000

        logger.critical(
            "kill_switch_tier1_complete",
            latency_ms=event.tier1_latency_ms,
            redis_halt_set=event.redis_halt_set,
        )

    async def _tier2_cancel_orders(self, event: KillSwitchEvent) -> None:
        """
        TIER 2: Cancel all pending orders on all exchanges (parallel).
        Target: < 500ms. Per-exchange timeout: 2000ms.
        """
        if not self._exchanges:
            event.tier2_ts = time.perf_counter()
            event.tier2_latency_ms = 0.0
            return

        t_start = time.perf_counter()

        async def cancel_exchange(adapter: Any) -> list[str]:
            try:
                cancelled = await asyncio.wait_for(
                    adapter.cancel_all_orders(timeout_ms=2000),
                    timeout=2.0,
                )
                logger.info(
                    "tier2_cancel_complete",
                    exchange=adapter.exchange_id,
                    count=len(cancelled),
                )
                return cancelled
            except TimeoutError:
                msg = f"Tier2 cancel timeout on {adapter.exchange_id}"
                logger.error(msg)
                event.errors.append(msg)
                return []
            except Exception as exc:
                msg = f"Tier2 cancel error on {adapter.exchange_id}: {exc}"
                logger.error(msg)
                event.errors.append(msg)
                # Retry once
                try:
                    return await asyncio.wait_for(
                        adapter.cancel_all_orders(timeout_ms=2000),
                        timeout=2.0,
                    )
                except Exception as retry_exc:
                    msg2 = f"Tier2 retry failed on {adapter.exchange_id}: {retry_exc}"
                    logger.error(msg2)
                    event.errors.append(msg2)
                    return []

        results = await asyncio.gather(
            *[cancel_exchange(ex) for ex in self._exchanges],
            return_exceptions=False,
        )

        for order_ids in results:
            event.cancelled_orders.extend(order_ids)

        event.tier2_ts = time.perf_counter()
        event.tier2_latency_ms = (event.tier2_ts - t_start) * 1000

        logger.critical(
            "kill_switch_tier2_complete",
            latency_ms=event.tier2_latency_ms,
            cancelled=len(event.cancelled_orders),
        )

    async def _tier3_close_positions(self, event: KillSwitchEvent) -> None:
        """
        TIER 3: Close all open positions at market (parallel).
        Target: < 2000ms. Per-exchange timeout: 3000ms.
        """
        if not self._exchanges:
            event.tier3_ts = time.perf_counter()
            event.tier3_latency_ms = 0.0
            return

        t_start = time.perf_counter()

        async def close_exchange(adapter: Any) -> list[str]:
            try:
                closed = await asyncio.wait_for(
                    adapter.close_all_positions(timeout_ms=3000),
                    timeout=3.0,
                )
                logger.info(
                    "tier3_close_complete",
                    exchange=adapter.exchange_id,
                    count=len(closed),
                )
                return closed
            except TimeoutError:
                msg = f"Tier3 close timeout on {adapter.exchange_id}"
                logger.critical(msg)
                event.errors.append(msg)
                return []
            except Exception as exc:
                msg = f"Tier3 close error on {adapter.exchange_id}: {exc}"
                logger.critical(msg)
                event.errors.append(msg)
                return []

        results = await asyncio.gather(
            *[close_exchange(ex) for ex in self._exchanges],
            return_exceptions=False,
        )

        for pos_ids in results:
            event.closed_positions.extend(pos_ids)

        event.tier3_ts = time.perf_counter()
        event.tier3_latency_ms = (event.tier3_ts - t_start) * 1000

        logger.critical(
            "kill_switch_tier3_complete",
            latency_ms=event.tier3_latency_ms,
            closed=len(event.closed_positions),
        )

    def reset(self) -> None:
        """
        Reset kill switch state. Only after full reconciliation.
        Clears both in-process flag and triggered state.
        """
        clear_halt()
        self._triggered = False
