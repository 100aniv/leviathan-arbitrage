"""Tests for engine/src/risk/circuit_breaker.py

Tests all state transitions: CLOSED → OPEN → HALF_OPEN → CLOSED
"""
from __future__ import annotations

import asyncio

import pytest

from src.risk.circuit_breaker import CircuitBreaker, CircuitBreakerState


class TestCircuitBreakerInitialState:
    def test_starts_closed(self):
        cb = CircuitBreaker()
        assert cb.state == CircuitBreakerState.CLOSED

    def test_allows_trading_when_closed(self):
        cb = CircuitBreaker()
        assert cb.allows_trading()

    def test_not_open_initially(self):
        cb = CircuitBreaker()
        assert not cb.is_open()

    def test_stats_initialized_to_zero(self):
        cb = CircuitBreaker()
        assert cb.stats.consecutive_losses == 0
        assert cb.stats.total_losses == 0
        assert cb.stats.api_errors == 0


class TestCircuitBreakerDrawdownTrigger:
    async def test_mdd_above_threshold_opens_breaker(self):
        cb = CircuitBreaker(mdd_threshold=0.02)
        await cb.update_drawdown(0.025)  # 2.5% > 2% threshold
        assert cb.state == CircuitBreakerState.OPEN

    async def test_mdd_below_threshold_stays_closed(self):
        cb = CircuitBreaker(mdd_threshold=0.02)
        await cb.update_drawdown(0.015)  # 1.5% < 2%
        assert cb.state == CircuitBreakerState.CLOSED

    async def test_mdd_exactly_at_threshold_stays_closed(self):
        cb = CircuitBreaker(mdd_threshold=0.02)
        await cb.update_drawdown(0.02)  # exactly at — not exceeded
        assert cb.state == CircuitBreakerState.CLOSED

    async def test_mdd_via_record_loss(self):
        cb = CircuitBreaker(mdd_threshold=0.02)
        await cb.record_loss(drawdown_pct=0.03)
        assert cb.state == CircuitBreakerState.OPEN


class TestCircuitBreakerConsecutiveLosses:
    async def test_consecutive_losses_trigger_at_limit(self):
        cb = CircuitBreaker(consecutive_loss_limit=3)
        await cb.record_loss()
        await cb.record_loss()
        assert cb.state == CircuitBreakerState.CLOSED  # 2, not triggered yet
        await cb.record_loss()
        assert cb.state == CircuitBreakerState.OPEN    # 3, triggered

    async def test_win_resets_consecutive_loss_count(self):
        cb = CircuitBreaker(consecutive_loss_limit=3)
        await cb.record_loss()
        await cb.record_loss()
        await cb.record_win()          # reset
        await cb.record_loss()         # only 1 since last win
        assert cb.state == CircuitBreakerState.CLOSED

    async def test_consecutive_losses_counted_correctly(self):
        cb = CircuitBreaker(consecutive_loss_limit=5)
        for _ in range(4):
            await cb.record_loss()
        assert cb.stats.consecutive_losses == 4
        assert cb.state == CircuitBreakerState.CLOSED
        await cb.record_loss()
        assert cb.state == CircuitBreakerState.OPEN


class TestCircuitBreakerAPIErrorRate:
    async def test_high_api_error_rate_triggers(self):
        cb = CircuitBreaker(api_error_rate_threshold=0.20)
        for _ in range(7):
            await cb.record_api_success()
        for _ in range(3):
            await cb.record_api_error()  # 3/10 = 30% > 20%
        assert cb.state == CircuitBreakerState.OPEN

    async def test_low_api_error_rate_stays_closed(self):
        cb = CircuitBreaker(api_error_rate_threshold=0.20)
        for _ in range(9):
            await cb.record_api_success()
        await cb.record_api_error()  # 1/10 = 10% < 20%
        assert cb.state == CircuitBreakerState.CLOSED

    async def test_zero_requests_no_trigger(self):
        cb = CircuitBreaker(api_error_rate_threshold=0.20)
        # No api_success recorded yet, just errors
        # With 0 requests, error rate computation skipped
        assert cb.state == CircuitBreakerState.CLOSED


class TestCircuitBreakerOpenState:
    async def test_open_disallows_trading(self):
        cb = CircuitBreaker(consecutive_loss_limit=1)
        await cb.record_loss()
        assert cb.state == CircuitBreakerState.OPEN
        assert not cb.allows_trading()
        assert cb.is_open()

    async def test_manual_trigger_opens(self):
        cb = CircuitBreaker()
        await cb.trigger_manual("test_reason")
        assert cb.state == CircuitBreakerState.OPEN
        assert cb.stats.last_trigger_reason == "test_reason"

    async def test_manual_trigger_on_already_open_is_noop(self):
        cb = CircuitBreaker()
        await cb.trigger_manual("first")
        await cb.trigger_manual("second")
        # Stays OPEN, no error raised
        assert cb.state == CircuitBreakerState.OPEN
        assert cb.stats.last_trigger_reason == "first"


class TestCircuitBreakerCooldownToHalfOpen:
    async def test_cooldown_transitions_to_half_open(self):
        cb = CircuitBreaker(
            consecutive_loss_limit=1,
            cooldown_seconds=0.05,  # 50ms for test speed
        )
        await cb.record_loss()
        assert cb.state == CircuitBreakerState.OPEN

        await asyncio.sleep(0.12)  # wait for cooldown
        assert cb.state == CircuitBreakerState.HALF_OPEN
        assert cb.allows_trading()

    async def test_half_open_stats_reset(self):
        cb = CircuitBreaker(
            consecutive_loss_limit=1,
            cooldown_seconds=0.05,
        )
        await cb.record_loss()
        await asyncio.sleep(0.12)
        assert cb.stats.half_open_test_count == 0
        assert cb.stats.half_open_successes == 0

    async def test_loss_in_half_open_reopens(self):
        cb = CircuitBreaker(
            consecutive_loss_limit=1,
            cooldown_seconds=0.05,
        )
        await cb.record_loss()
        await asyncio.sleep(0.12)
        assert cb.state == CircuitBreakerState.HALF_OPEN

        await cb.record_loss()
        assert cb.state == CircuitBreakerState.OPEN


class TestCircuitBreakerHalfOpenRecovery:
    async def test_recovery_to_closed_after_wins(self):
        cb = CircuitBreaker(
            consecutive_loss_limit=1,
            cooldown_seconds=0.05,
            half_open_test_count=2,
        )
        await cb.record_loss()
        await asyncio.sleep(0.12)
        assert cb.state == CircuitBreakerState.HALF_OPEN

        await cb.record_win()
        assert cb.state == CircuitBreakerState.HALF_OPEN  # 1 of 2

        await cb.record_win()
        assert cb.state == CircuitBreakerState.CLOSED     # 2 of 2 → CLOSED

    async def test_closed_resets_consecutive_losses(self):
        cb = CircuitBreaker(
            consecutive_loss_limit=1,
            cooldown_seconds=0.05,
            half_open_test_count=1,
        )
        await cb.record_loss()
        await asyncio.sleep(0.12)
        await cb.record_win()
        assert cb.state == CircuitBreakerState.CLOSED
        assert cb.stats.consecutive_losses == 0


class TestCircuitBreakerStateChangeCallback:
    async def test_open_callback_called(self):
        changes: list[tuple[CircuitBreakerState, str]] = []
        cb = CircuitBreaker(
            consecutive_loss_limit=1,
            on_state_change=lambda s, r: changes.append((s, r)),
        )
        await cb.record_loss()
        assert len(changes) == 1
        assert changes[0][0] == CircuitBreakerState.OPEN

    async def test_half_open_callback_called(self):
        changes: list[CircuitBreakerState] = []
        cb = CircuitBreaker(
            consecutive_loss_limit=1,
            cooldown_seconds=0.05,
            on_state_change=lambda s, r: changes.append(s),
        )
        await cb.record_loss()
        await asyncio.sleep(0.12)
        assert CircuitBreakerState.HALF_OPEN in changes

    async def test_closed_callback_called_on_recovery(self):
        changes: list[CircuitBreakerState] = []
        cb = CircuitBreaker(
            consecutive_loss_limit=1,
            cooldown_seconds=0.05,
            half_open_test_count=1,
            on_state_change=lambda s, r: changes.append(s),
        )
        await cb.record_loss()
        await asyncio.sleep(0.12)
        await cb.record_win()
        assert CircuitBreakerState.CLOSED in changes
