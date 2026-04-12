"""MarginTracker — in-flight margin reservation to prevent Binance -2019/400 errors.

Bug 29 fix: futures_futures checks margin_available from signal snapshot.
Concurrent signals all see the same snapshot, reserve the same margin,
and all pass the check — but together they exceed available margin.
This tracker atomically reserves in-flight margin per exchange.

BUG-19 fix: reservations are TTL-based (60s auto-expiry) so accumulated
in-flight amounts do not block trades indefinitely when release() is not called.
"""
from __future__ import annotations

import asyncio
import logging
import time
from decimal import Decimal

logger = logging.getLogger(__name__)

_BUFFER_PCT: Decimal = Decimal("0.15")  # 15% safety buffer on top of required
_RESERVATION_TTL_S: float = 60.0  # auto-expire after 60s (covers typical order execution)


class MarginTracker:
    """Async-safe in-flight margin reservation per exchange.

    Reservations are TTL-based: entries older than 60s are auto-pruned on
    each check_and_reserve() call, preventing indefinite accumulation when
    release() is not explicitly called (BUG-19 fix).

    Usage:
        ok = await tracker.check_and_reserve(exchange_id, required_usd, available_usd)
        if not ok:
            return None  # margin_tracker_blocked
        # release() is optional — TTL handles cleanup if not called
        await tracker.release(exchange_id, required_usd)
    """

    def __init__(self, ttl_s: float = _RESERVATION_TTL_S) -> None:
        # list of (exchange_id, effective_usd, expires_at)
        self._entries: list[tuple[str, Decimal, float]] = []
        self._lock = asyncio.Lock()
        self._ttl_s = ttl_s

    def _prune_expired(self) -> None:
        """Remove expired reservations. Must be called inside _lock."""
        now = time.monotonic()
        before = len(self._entries)
        self._entries = [(ex, amt, exp) for ex, amt, exp in self._entries if exp > now]
        pruned = before - len(self._entries)
        if pruned > 0:
            logger.debug("margin_tracker.pruned count=%d", pruned)

    def _total_reserved(self, exchange_id: str) -> Decimal:
        """Sum of valid (non-expired) reservations for exchange. Must call inside _lock."""
        now = time.monotonic()
        return sum(
            (amt for ex, amt, exp in self._entries if ex == exchange_id and exp > now),
            Decimal("0"),
        )

    async def check_and_reserve(
        self,
        exchange_id: str,
        required_usd: Decimal,
        available_usd: Decimal,
    ) -> bool:
        """Check if margin is available accounting for in-flight, then reserve.

        Returns True if approved (margin reserved). False if blocked.
        Applies 15% buffer: effective_required = required_usd * 1.15.
        Prunes expired entries before checking.
        """
        if required_usd == Decimal("0"):
            return True  # zero-amount: no reservation needed, avoid polluting _entries
        effective = required_usd * (Decimal("1") + _BUFFER_PCT)
        async with self._lock:
            self._prune_expired()
            in_flight = self._total_reserved(exchange_id)
            net_available = available_usd - in_flight
            if net_available < effective:
                logger.warning(
                    "margin_tracker_blocked exchange=%s required=%.2f effective=%.2f "
                    "available=%.2f in_flight=%.2f net_available=%.2f",
                    exchange_id,
                    float(required_usd),
                    float(effective),
                    float(available_usd),
                    float(in_flight),
                    float(net_available),
                )
                return False
            expires_at = time.monotonic() + self._ttl_s
            self._entries.append((exchange_id, effective, expires_at))
            logger.debug(
                "margin_tracker.reserved exchange=%s effective=%.2f remaining=%.2f ttl=%.0fs",
                exchange_id,
                float(effective),
                float(net_available - effective),
                self._ttl_s,
            )
            return True

    async def release(self, exchange_id: str, required_usd: Decimal) -> None:
        """Release previously reserved margin after fill or failure.

        Removes the oldest matching reservation for this exchange.
        If TTL already expired the entry, this is a no-op.
        """
        effective = required_usd * (Decimal("1") + _BUFFER_PCT)
        async with self._lock:
            self._prune_expired()
            # Remove the first (oldest) matching entry for this exchange
            for i, (ex, amt, exp) in enumerate(self._entries):
                if ex == exchange_id and abs(amt - effective) / max(effective, Decimal("0.01")) < Decimal("0.001"):
                    self._entries.pop(i)
                    logger.debug(
                        "margin_tracker.released exchange=%s released=%.2f remaining=%.2f",
                        exchange_id,
                        float(effective),
                        float(self._total_reserved(exchange_id)),
                    )
                    return
            logger.debug(
                "margin_tracker.release_noop exchange=%s effective=%.2f (already expired or not found)",
                exchange_id,
                float(effective),
            )

    async def reset(self, exchange_id: str | None = None) -> None:
        """Reset reserved amounts (call after halt clear or exchange reconnect)."""
        async with self._lock:
            if exchange_id:
                self._entries = [
                    (ex, amt, exp) for ex, amt, exp in self._entries if ex != exchange_id
                ]
            else:
                self._entries.clear()

    async def get_reserved(self, exchange_id: str) -> Decimal:
        """Return total in-flight reservation for exchange (lock-safe read)."""
        async with self._lock:
            return self._total_reserved(exchange_id)
