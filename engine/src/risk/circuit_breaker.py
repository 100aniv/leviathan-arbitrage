"""LEVIATHAN Circuit Breaker — State Machine.

States: CLOSED → OPEN → HALF_OPEN → CLOSED

Triggers:
  - MDD > threshold
  - Consecutive losses > N
  - API error rate > threshold

Configurable cooldown timer. Half-open: limited trading for testing.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

import structlog

logger = structlog.get_logger(__name__)


class CircuitBreakerState(str, Enum):
    CLOSED = "CLOSED"        # Normal trading
    OPEN = "OPEN"            # All trading halted
    HALF_OPEN = "HALF_OPEN"  # Limited testing after cooldown


@dataclass
class CircuitBreakerStats:
    consecutive_losses: int = 0
    total_losses: int = 0
    total_wins: int = 0
    api_errors: int = 0
    api_requests: int = 0
    current_drawdown_pct: float = 0.0
    last_trigger_reason: str = ""
    state_changed_at: float = field(default_factory=time.monotonic)
    half_open_test_count: int = 0
    half_open_successes: int = 0


class CircuitBreaker:
    """
    Circuit breaker with CLOSED → OPEN → HALF_OPEN → CLOSED state machine.

    Thread-safe state transitions using asyncio.Lock.
    """

    def __init__(
        self,
        mdd_threshold: float = 0.02,
        consecutive_loss_limit: int = 5,
        api_error_rate_threshold: float = 0.20,
        cooldown_seconds: float = 300.0,
        half_open_test_count: int = 3,
        on_state_change: Callable[[CircuitBreakerState, str], None] | None = None,
    ) -> None:
        self._mdd_threshold = mdd_threshold
        self._consecutive_loss_limit = consecutive_loss_limit
        self._api_error_rate_threshold = api_error_rate_threshold
        self._cooldown_seconds = cooldown_seconds
        self._half_open_test_count = half_open_test_count
        self._on_state_change = on_state_change

        self._state = CircuitBreakerState.CLOSED
        self._stats = CircuitBreakerStats()
        self._lock = asyncio.Lock()
        self._cooldown_task: asyncio.Task | None = None

    @property
    def state(self) -> CircuitBreakerState:
        return self._state

    @property
    def stats(self) -> CircuitBreakerStats:
        return self._stats

    def is_open(self) -> bool:
        return self._state == CircuitBreakerState.OPEN

    def allows_trading(self) -> bool:
        """Returns True if trading is allowed (CLOSED or HALF_OPEN)."""
        return self._state in (CircuitBreakerState.CLOSED, CircuitBreakerState.HALF_OPEN)

    async def record_loss(self, drawdown_pct: float | None = None) -> None:
        """Record a losing trade. May trigger OPEN state."""
        async with self._lock:
            self._stats.consecutive_losses += 1
            self._stats.total_losses += 1

            if drawdown_pct is not None:
                self._stats.current_drawdown_pct = drawdown_pct

            if self._state == CircuitBreakerState.CLOSED:
                await self._check_triggers_locked("loss")
            elif self._state == CircuitBreakerState.HALF_OPEN:
                # Any loss in HALF_OPEN → back to OPEN
                await self._transition_to_open_locked("loss_in_half_open")

    async def record_win(self) -> None:
        """Record a winning trade. May transition HALF_OPEN → CLOSED."""
        async with self._lock:
            self._stats.consecutive_losses = 0
            self._stats.total_wins += 1

            if self._state == CircuitBreakerState.HALF_OPEN:
                self._stats.half_open_successes += 1
                self._stats.half_open_test_count += 1

                if self._stats.half_open_successes >= self._half_open_test_count:
                    await self._transition_to_closed_locked()

    async def record_api_error(self) -> None:
        """Record an API error. High error rate may trigger OPEN."""
        async with self._lock:
            self._stats.api_errors += 1
            self._stats.api_requests += 1

            if self._state == CircuitBreakerState.CLOSED:
                await self._check_triggers_locked("api_error")

    async def record_api_success(self) -> None:
        """Record a successful API call."""
        async with self._lock:
            self._stats.api_requests += 1

    async def update_drawdown(self, drawdown_pct: float) -> None:
        """Update current drawdown. Triggers OPEN if threshold exceeded."""
        async with self._lock:
            self._stats.current_drawdown_pct = drawdown_pct
            if self._state == CircuitBreakerState.CLOSED:
                await self._check_triggers_locked("drawdown_update")

    async def trigger_manual(self, reason: str = "manual") -> None:
        """Manually trigger circuit breaker."""
        async with self._lock:
            if self._state != CircuitBreakerState.OPEN:
                await self._transition_to_open_locked(reason)

    async def _check_triggers_locked(self, context: str) -> None:
        """Check all trigger conditions. Must be called with _lock held."""
        reason: str | None = None

        if self._stats.current_drawdown_pct > self._mdd_threshold:
            reason = (
                f"mdd_exceeded:{self._stats.current_drawdown_pct:.4f}"
                f">{self._mdd_threshold}"
            )
        elif self._stats.consecutive_losses >= self._consecutive_loss_limit:
            reason = (
                f"consecutive_losses:{self._stats.consecutive_losses}"
                f">={self._consecutive_loss_limit}"
            )
        elif self._stats.api_requests > 0:
            error_rate = self._stats.api_errors / self._stats.api_requests
            if error_rate > self._api_error_rate_threshold:
                reason = (
                    f"api_error_rate:{error_rate:.3f}"
                    f">{self._api_error_rate_threshold}"
                )

        if reason:
            await self._transition_to_open_locked(reason)

    async def _transition_to_open_locked(self, reason: str) -> None:
        """Transition to OPEN state. Must be called with _lock held."""
        self._state = CircuitBreakerState.OPEN
        self._stats.last_trigger_reason = reason
        self._stats.state_changed_at = time.monotonic()

        logger.warning(
            "circuit_breaker_open",
            reason=reason,
            consecutive_losses=self._stats.consecutive_losses,
            drawdown_pct=self._stats.current_drawdown_pct,
        )

        if self._on_state_change:
            self._on_state_change(CircuitBreakerState.OPEN, reason)

        # Schedule cooldown transition to HALF_OPEN
        if self._cooldown_task and not self._cooldown_task.done():
            self._cooldown_task.cancel()
        self._cooldown_task = asyncio.create_task(self._cooldown_to_half_open())

    async def _cooldown_to_half_open(self) -> None:
        """Wait for cooldown period, then transition to HALF_OPEN."""
        try:
            await asyncio.sleep(self._cooldown_seconds)
        except asyncio.CancelledError:
            return

        async with self._lock:
            if self._state == CircuitBreakerState.OPEN:
                self._state = CircuitBreakerState.HALF_OPEN
                self._stats.state_changed_at = time.monotonic()
                self._stats.half_open_test_count = 0
                self._stats.half_open_successes = 0

                logger.info(
                    "circuit_breaker_half_open",
                    cooldown_s=self._cooldown_seconds,
                )

                if self._on_state_change:
                    self._on_state_change(
                        CircuitBreakerState.HALF_OPEN, "cooldown_expired"
                    )

    async def _transition_to_closed_locked(self) -> None:
        """Transition to CLOSED state. Must be called with _lock held."""
        self._state = CircuitBreakerState.CLOSED
        self._stats.state_changed_at = time.monotonic()
        self._stats.consecutive_losses = 0
        self._stats.api_errors = 0
        self._stats.api_requests = 0

        logger.info(
            "circuit_breaker_closed",
            half_open_successes=self._stats.half_open_successes,
        )

        if self._on_state_change:
            self._on_state_change(CircuitBreakerState.CLOSED, "recovery")
