"""DeduplicationGate — single meta-lock, prevents race-condition duplicate orders.

Bug 26 fix: _on_orderbook() fires _signal_generator + _real_signal_producer concurrently.
Both paths pass the old dict-based collision check before either writes the key
(await boundary between check and write), producing 2-4 duplicate orders per signal.

Design: single _meta_lock held for entire check+register (no per-key locks).
Safe because the critical section has no await points — the dict check and write are
synchronous, so asyncio's single-threaded scheduler cannot preempt between them.
Per-key locks removed: they created a TOCTOU window when cleanup deleted a per-key lock
while a coroutine held a reference to the old object, allowing a brief period of two
distinct lock objects for the same key after a cleanup cycle.
"""
from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

_DEFAULT_WINDOW_S: float = 10.0


class DeduplicationGate:
    """Atomic collision gate: single meta-lock, TTL-based expiry."""

    def __init__(self, window_s: float = _DEFAULT_WINDOW_S) -> None:
        self._window_s = window_s
        self._timestamps: dict[str, float] = {}
        self._meta_lock = asyncio.Lock()

    async def check_and_register(self, key: str) -> bool:
        """Return True if key is fresh (caller should proceed). False = duplicate (block).

        Atomic: meta_lock held for entire check+register.
        No per-key locks — the critical section has no await points, so there is no
        opportunity for another coroutine to interleave between check and write.
        """
        async with self._meta_lock:
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
        if stale:
            logger.debug("dedup_gate.cleanup_stale count=%d", len(stale))
