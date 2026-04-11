"""DeduplicationGate — asyncio.Lock per collision key, prevents race-condition duplicate orders.

Bug 26 fix: _on_orderbook() fires _signal_generator + _real_signal_producer concurrently.
Both paths pass the old dict-based collision check before either writes the key
(await boundary between check and write), producing 2-4 duplicate orders per signal.
This gate makes check-and-register atomic via per-key asyncio.Lock.
"""
from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

_DEFAULT_WINDOW_S: float = 10.0


class DeduplicationGate:
    """Atomic collision gate: per-key asyncio.Lock, TTL-based expiry."""

    def __init__(self, window_s: float = _DEFAULT_WINDOW_S) -> None:
        self._window_s = window_s
        self._locks: dict[str, asyncio.Lock] = {}
        self._timestamps: dict[str, float] = {}
        self._meta_lock = asyncio.Lock()

    async def _get_lock(self, key: str) -> asyncio.Lock:
        async with self._meta_lock:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]

    async def check_and_register(self, key: str) -> bool:
        """Return True if key is fresh (caller should proceed). False = duplicate (block).

        Atomic: lock acquired before timestamp check, released after write.
        Prevents two concurrent coroutines from both passing the same key.
        """
        lock = await self._get_lock(key)
        async with lock:
            now = time.monotonic()
            last = self._timestamps.get(key)
            if last is not None and (now - last) < self._window_s:
                logger.debug("dedup_blocked key=%s elapsed=%.2fs", key, now - last)
                return False
            self._timestamps[key] = now
            return True

    async def cleanup_stale(self) -> None:
        """Remove entries older than 2× window to prevent unbounded growth."""
        now = time.monotonic()
        async with self._meta_lock:
            stale = [k for k, t in self._timestamps.items() if now - t > self._window_s * 2]
            for k in stale:
                del self._timestamps[k]
                self._locks.pop(k, None)
        if stale:
            logger.debug("dedup_gate.cleanup_stale count=%d", len(stale))
