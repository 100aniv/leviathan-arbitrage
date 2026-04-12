"""StrandedPositionTracker — conditional HALT for rollback failures.

Bug 27 fix: executor.py called halt_local() unconditionally on every rollback failure.
Bitget 22002 ("no position to close") is benign — the position is already gone.
Other failures accumulate; halt only when total stranded USD exceeds threshold ($30).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Error codes / messages that are safe to ignore (position already closed)
_BENIGN_CODES: frozenset[str] = frozenset({
    "22002",          # Bitget: no position to close (ghost)
    "40762",          # Bitget: position does not exist
    "no position to close",
    "position does not exist",
})

_HALT_THRESHOLD_USD: float = 30.0


_ENTRY_TTL_S: float = 3600.0  # BUG-04: entries expire after 1 hour


@dataclass
class _StrandedEntry:
    exchange_id: str
    symbol: str
    side: str
    size: float
    value_usd: float
    reason: str
    created_at: float = field(default_factory=time.monotonic)  # BUG-04: TTL support


class StrandedPositionTracker:
    """Tracks rollback failures; halts only when total exposure > threshold.

    Usage in executor:
        should_halt = self._stranded_tracker.register(
            exchange_id=..., symbol=..., side=...,
            size=..., value_usd=..., reason=error_str,
        )
        if should_halt:
            halt_local()
        # else: alert logged, trading continues
    """

    def __init__(self, halt_threshold_usd: float = _HALT_THRESHOLD_USD) -> None:
        self._threshold = halt_threshold_usd
        self._entries: list[_StrandedEntry] = []

    def register(
        self,
        exchange_id: str,
        symbol: str,
        side: str,
        size: float,
        value_usd: float,
        reason: str,
    ) -> bool:
        """Register a rollback failure. Returns True if caller should halt.

        Benign codes (22002, etc.) → log warning only, return False.
        Real failures → accumulate; return True if total > threshold.
        """
        reason_lower = str(reason).lower()
        for code in _BENIGN_CODES:
            if code in reason_lower:
                logger.warning(
                    "stranded_alert_no_halt exchange=%s symbol=%s reason=%s (benign — no halt)",
                    exchange_id, symbol, reason,
                )
                return False

        # BUG-04: prune expired entries to prevent unbounded growth and stale totals
        now = time.monotonic()
        self._entries = [e for e in self._entries if now - e.created_at < _ENTRY_TTL_S]

        # Idempotency guard: skip if same (exchange, symbol, side) was registered within 5s.
        # Prevents double-counting when executor calls register() from both explicit rollback
        # path and the finally block for the same failure event.
        _DEDUP_WINDOW_S = 5.0
        for e in reversed(self._entries):
            if (e.exchange_id == exchange_id and e.symbol == symbol and e.side == side
                    and now - e.created_at < _DEDUP_WINDOW_S):
                logger.debug(
                    "stranded_dedup_skipped exchange=%s symbol=%s side=%s",
                    exchange_id, symbol, side,
                )
                return False

        entry = _StrandedEntry(
            exchange_id=exchange_id,
            symbol=symbol,
            side=side,
            size=size,
            value_usd=value_usd,
            reason=reason,
        )
        self._entries.append(entry)
        total_usd = sum(e.value_usd for e in self._entries)

        logger.error(
            "stranded_position_registered exchange=%s symbol=%s side=%s "
            "value_usd=%.2f total_stranded_usd=%.2f reason=%s",
            exchange_id, symbol, side, value_usd, total_usd, reason,
        )

        if total_usd > self._threshold:
            logger.critical(
                "stranded_threshold_exceeded total_usd=%.2f threshold=%.2f — halting",
                total_usd, self._threshold,
            )
            return True

        logger.warning(
            "stranded_alert_no_halt total_usd=%.2f below threshold=%.2f — trading continues",
            total_usd, self._threshold,
        )
        return False

    @property
    def total_stranded_usd(self) -> float:
        # BUG-04: exclude expired entries from total
        now = time.monotonic()
        return sum(e.value_usd for e in self._entries if now - e.created_at < _ENTRY_TTL_S)

    def clear(self) -> None:
        self._entries.clear()
