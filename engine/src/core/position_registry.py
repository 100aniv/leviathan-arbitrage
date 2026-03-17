"""PositionRegistry — Symbol-level lock to prevent cross-strategy collisions.

US-232: Each symbol can be held by at most one strategy at a time.
Higher-priority strategies can preempt lower-priority holders.
TTL-based auto-expiry prevents stale locks from blocking new entries.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

try:
    from prometheus_client import Counter
    _LOCK_ACQUIRED = Counter(
        "leviathan_position_lock_acquired_total",
        "Position locks successfully acquired",
        ["strategy"],
    )
    _LOCK_REJECTED = Counter(
        "leviathan_position_lock_rejected_total",
        "Position lock attempts rejected (lower priority)",
        ["strategy"],
    )
    _LOCK_PREEMPTED = Counter(
        "leviathan_position_lock_preempted_total",
        "Position locks preempted by higher-priority strategy",
        ["preempted_by"],
    )
    _METRICS_AVAILABLE = True
except Exception:  # pragma: no cover
    _METRICS_AVAILABLE = False


import logging

logger = logging.getLogger(__name__)

_DEFAULT_TTL_SECONDS = 60.0


class _LockEntry:
    __slots__ = ("strategy_id", "priority", "expires_at")

    def __init__(self, strategy_id: str, priority: int, ttl: float) -> None:
        self.strategy_id = strategy_id
        self.priority = priority
        self.expires_at = time.monotonic() + ttl


class PositionRegistry:
    """Symbol-level exclusive lock with priority preemption and TTL expiry.

    Usage:
        registry = PositionRegistry()
        if registry.try_lock("BTC/USDT", "cross_exchange"):
            try:
                # execute trade
                registry.refresh("BTC/USDT", "cross_exchange")
            finally:
                registry.release("BTC/USDT", "cross_exchange")
    """

    PRIORITY: dict[str, int] = {
        "cross_exchange": 10,
        "futures_futures": 8,
        "statistical_arb": 6,
        "triangular": 4,
        "funding_rate": 2,
        "spot_futures": 2,
        "cex_dex": 1,
    }

    def __init__(self, ttl_seconds: float = _DEFAULT_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._locks: dict[str, _LockEntry] = {}
        self._mu = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def try_lock(self, symbol: str, strategy_id: str, priority: int | None = None) -> bool:
        """Attempt to acquire an exclusive lock on *symbol* for *strategy_id*.

        Returns True if the lock was acquired (or preempted from a lower-priority
        holder). Returns False if a higher-or-equal priority strategy already holds
        the lock.
        """
        if priority is None:
            priority = self.PRIORITY.get(strategy_id, 0)

        now = time.monotonic()
        with self._mu:
            existing = self._locks.get(symbol)

            if existing is not None:
                # Expired lock — treat as free
                if existing.expires_at <= now:
                    existing = None

            if existing is None:
                self._locks[symbol] = _LockEntry(strategy_id, priority, self._ttl)
                if _METRICS_AVAILABLE:
                    _LOCK_ACQUIRED.labels(strategy=strategy_id).inc()
                logger.debug(
                    "position_registry.lock_acquired symbol=%s strategy=%s priority=%d",
                    symbol, strategy_id, priority,
                )
                return True

            # Same holder refreshes TTL
            if existing.strategy_id == strategy_id:
                existing.expires_at = now + self._ttl
                return True

            # Preempt lower-priority holder
            if priority > existing.priority:
                old_holder = existing.strategy_id
                self._locks[symbol] = _LockEntry(strategy_id, priority, self._ttl)
                if _METRICS_AVAILABLE:
                    _LOCK_ACQUIRED.labels(strategy=strategy_id).inc()
                    _LOCK_PREEMPTED.labels(preempted_by=strategy_id).inc()
                logger.warning(
                    "position_registry.lock_preempted symbol=%s preempted=%s new=%s priority=%d",
                    symbol,
                    old_holder,
                    strategy_id,
                    priority,
                )
                return True

            # Current holder has equal or higher priority — reject
            if _METRICS_AVAILABLE:
                _LOCK_REJECTED.labels(strategy=strategy_id).inc()
            logger.debug(
                "position_registry.lock_rejected symbol=%s holder=%s holder_pri=%d requester=%s req_pri=%d",
                symbol, existing.strategy_id, existing.priority, strategy_id, priority,
            )
            return False

    def release(self, symbol: str, strategy_id: str) -> None:
        """Release the lock on *symbol* if *strategy_id* is the current holder."""
        with self._mu:
            existing = self._locks.get(symbol)
            if existing is not None and existing.strategy_id == strategy_id:
                del self._locks[symbol]
                logger.debug(
                    "position_registry.lock_released symbol=%s strategy=%s",
                    symbol, strategy_id,
                )

    def refresh(self, symbol: str, strategy_id: str) -> None:
        """Reset the TTL for an active lock (call periodically during long trades)."""
        with self._mu:
            existing = self._locks.get(symbol)
            if existing is not None and existing.strategy_id == strategy_id:
                existing.expires_at = time.monotonic() + self._ttl

    def is_locked(self, symbol: str) -> bool:
        """Return True if *symbol* has a non-expired lock."""
        now = time.monotonic()
        with self._mu:
            existing = self._locks.get(symbol)
            return existing is not None and existing.expires_at > now

    def get_holder(self, symbol: str) -> Optional[str]:
        """Return the strategy_id holding the lock, or None if free/expired."""
        now = time.monotonic()
        with self._mu:
            existing = self._locks.get(symbol)
            if existing is not None and existing.expires_at > now:
                return existing.strategy_id
            return None
