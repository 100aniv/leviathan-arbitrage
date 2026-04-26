"""Flash Guard — rapid price movement detection and trading pause.

Monitors price changes across all exchanges. If any symbol experiences
a price move exceeding the threshold within the lookback window,
trading is paused until prices stabilize.

Phase 0 SIT-3 requirement: 5min window, 3% threshold.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from decimal import Decimal

logger = logging.getLogger(__name__)

_DEFAULT_THRESHOLD_PCT = 3.0  # 3% price move triggers guard
_DEFAULT_WINDOW_S = 300  # 5 minutes
_DEFAULT_COOLDOWN_S = 60  # 1 minute cooldown after trigger
_DEFAULT_MIN_EXCHANGES_FOR_TRIGGER = 2  # 2026-04-26: cross-exchange confirmation required (avoid single-exchange stale-data false-trigger)
_CROSS_EXCHANGE_WINDOW_S = 60  # ms-precision overlap window for confirming flash


@dataclass
class FlashEvent:
    symbol: str
    exchange: str
    price_change_pct: float
    timestamp: float
    old_price: float
    new_price: float


class FlashGuard:
    """Detects rapid price movements and pauses trading.

    Thread-safe via single-threaded asyncio event loop assumption.
    Integrates with RiskGuardian as an additional pre-trade check.
    """

    def __init__(
        self,
        threshold_pct: float = _DEFAULT_THRESHOLD_PCT,
        window_seconds: int = _DEFAULT_WINDOW_S,
        cooldown_seconds: int = _DEFAULT_COOLDOWN_S,
        min_exchanges_for_trigger: int = _DEFAULT_MIN_EXCHANGES_FOR_TRIGGER,
    ) -> None:
        self._threshold_pct = threshold_pct
        self._window_s = window_seconds
        self._cooldown_s = cooldown_seconds
        # 2026-04-26: cross-exchange confirmation gate. Single-exchange flash
        # (e.g. Upbit BTC -8% while Binance/OKX/Bitget unchanged) is treated as
        # local stale-data and only logged — does NOT halt the engine.
        self._min_exchanges_for_trigger = max(1, min_exchanges_for_trigger)

        # (symbol, exchange) -> deque of (timestamp, price)
        self._price_history: dict[tuple[str, str], deque] = defaultdict(
            lambda: deque(maxlen=600)
        )
        # symbol -> dict[exchange -> last_flash_timestamp] for cross-exchange confirmation
        self._pending_flashes: dict[str, dict[str, float]] = defaultdict(dict)

        self._triggered = False
        self._trigger_time: float = 0.0
        self._last_event: FlashEvent | None = None
        self._events: deque[FlashEvent] = deque(maxlen=100)
        self._suppressed_count: int = 0  # local-only flash count (not triggered)

    @property
    def is_triggered(self) -> bool:
        """Check if flash guard is currently active (trading paused)."""
        if not self._triggered:
            return False
        # Auto-release after cooldown
        if time.monotonic() - self._trigger_time > self._cooldown_s:
            self._triggered = False
            logger.info("flash_guard.released cooldown=%ds", self._cooldown_s)
            return False
        return True

    @property
    def last_event(self) -> FlashEvent | None:
        return self._last_event

    def record_price(
        self, symbol: str, exchange: str, price: Decimal | float,
    ) -> bool:
        """Record a new price observation. Returns True if flash detected.

        Called from the orderbook update hot path — must be fast.
        """
        now = time.monotonic()
        price_f = float(price)
        key = (symbol, exchange)
        history = self._price_history[key]

        # Prune old entries outside window
        while history and (now - history[0][0]) > self._window_s:
            history.popleft()

        # Check for flash crash/spike against oldest price in window
        if history:
            oldest_price = history[0][1]
            if oldest_price > 0:
                change_pct = abs(price_f - oldest_price) / oldest_price * 100
                if change_pct >= self._threshold_pct:
                    # 2026-04-26: cross-exchange confirmation gate
                    pending = self._pending_flashes[symbol]
                    # Prune stale entries outside cross-exchange window
                    pending = {ex: ts for ex, ts in pending.items() if (now - ts) <= _CROSS_EXCHANGE_WINDOW_S}
                    pending[exchange] = now
                    self._pending_flashes[symbol] = pending
                    confirming_exchanges = len(pending)

                    event = FlashEvent(
                        symbol=symbol,
                        exchange=exchange,
                        price_change_pct=change_pct,
                        timestamp=now,
                        old_price=oldest_price,
                        new_price=price_f,
                    )

                    if confirming_exchanges >= self._min_exchanges_for_trigger:
                        # Confirmed flash — halt
                        self._triggered = True
                        self._trigger_time = now
                        self._last_event = event
                        self._events.append(event)
                        logger.warning(
                            "flash_guard.triggered symbol=%s exchange=%s change=%.2f%% old=%.6f new=%.6f window=%ds confirming=%d",
                            symbol, exchange, change_pct, oldest_price, price_f, self._window_s, confirming_exchanges,
                        )
                        history.append((now, price_f))
                        return True
                    else:
                        # Single-exchange flash — likely local stale data, log only
                        self._suppressed_count += 1
                        logger.info(
                            "flash_guard.local_only symbol=%s exchange=%s change=%.2f%% suppressed (need %d confirming exchanges)",
                            symbol, exchange, change_pct, self._min_exchanges_for_trigger,
                        )
                        history.append((now, price_f))
                        return False

        history.append((now, price_f))
        return False

    def check_allowed(self) -> tuple[bool, str]:
        """Pre-trade check. Returns (allowed, reason).

        Integrates with RiskGuardian check pipeline.
        """
        if self.is_triggered:
            evt = self._last_event
            reason = (
                f"Flash Guard active: {evt.symbol} {evt.exchange} "
                f"{evt.price_change_pct:.1f}% in {self._window_s}s"
                if evt else "Flash Guard active"
            )
            return False, reason
        return True, ""

    def reset(self) -> None:
        """Manually reset the flash guard (e.g., after /resume command)."""
        self._triggered = False
        self._trigger_time = 0.0
        logger.info("flash_guard.manual_reset triggered=False")
