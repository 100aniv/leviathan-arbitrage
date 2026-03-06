"""Tests for engine/src/risk/kill_switch.py

Tests all 3 tiers, timing assertions, halt flag independence from Redis.
"""
from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import AsyncMock

import pytest

import src.risk.kill_switch as ks_module
from src.risk.kill_switch import (
    KillSwitch,
    KillSwitchEvent,
    _HALT_FLAG,
    clear_halt,
    halt_local,
    is_halted,
)


@pytest.fixture(autouse=True)
def reset_halt_flag():
    """Reset global halt flag before and after each test."""
    _HALT_FLAG.clear()
    yield
    _HALT_FLAG.clear()


# ---------------------------------------------------------------------------
# Module-level halt flag functions
# ---------------------------------------------------------------------------


class TestHaltFlag:
    def test_initially_not_halted(self):
        assert not is_halted()

    def test_halt_local_sets_flag(self):
        halt_local()
        assert is_halted()

    def test_clear_halt_clears_flag(self):
        halt_local()
        clear_halt()
        assert not is_halted()

    def test_halt_local_is_fast(self):
        """Halt flag set must be < 1ms (in-process, no external dep)."""
        t0 = time.perf_counter()
        halt_local()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert elapsed_ms < 1.0, f"halt_local() took {elapsed_ms:.3f}ms — too slow"

    def test_is_halted_works_without_redis(self):
        """Critical: is_halted() MUST work without Redis."""
        # No Redis involved — threading.Event only
        halt_local()
        assert is_halted()

    def test_thread_safe_halt(self):
        """Halt flag must be visible across threads immediately."""
        results = []

        def check_from_thread():
            results.append(is_halted())

        halt_local()
        t = threading.Thread(target=check_from_thread)
        t.start()
        t.join()
        assert results == [True]

    def test_clear_is_thread_safe(self):
        halt_local()
        results = []

        def clear_from_thread():
            clear_halt()
            results.append(is_halted())

        t = threading.Thread(target=clear_from_thread)
        t.start()
        t.join()
        assert results == [False]


# ---------------------------------------------------------------------------
# Tier 1 — Local Halt
# ---------------------------------------------------------------------------


class TestKillSwitchTier1:
    async def test_tier1_sets_halt_flag(self):
        ks = KillSwitch(redis_client=None, exchanges=[])
        event = await ks.trigger()
        assert is_halted()
        assert event.tier1_latency_ms is not None

    async def test_tier1_latency_under_10ms(self):
        """Tier 1 MUST complete in < 10ms (hard limit from Amendment 2)."""
        ks = KillSwitch(redis_client=None, exchanges=[])
        event = await ks.trigger()
        assert event.tier1_latency_ms < 10.0, (
            f"Tier 1 latency {event.tier1_latency_ms:.3f}ms exceeds 10ms hard limit"
        )

    async def test_tier1_with_redis(self):
        """Tier 1 sets Redis key when Redis is available."""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock()

        ks = KillSwitch(redis_client=mock_redis, exchanges=[])
        event = await ks.trigger()

        mock_redis.set.assert_called_once_with("leviathan:halt", "1", ex=86400)
        assert event.redis_halt_set is True

    async def test_tier1_halt_flag_independent_of_redis(self):
        """Critical: Halt flag MUST be set even if Redis fails."""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(side_effect=ConnectionError("Redis down"))

        ks = KillSwitch(redis_client=mock_redis, exchanges=[])
        event = await ks.trigger()

        # In-process halt flag is set despite Redis failure
        assert is_halted()
        assert event.redis_halt_set is False
        assert len(event.errors) > 0

    async def test_no_orders_possible_after_tier1(self):
        """After Tier 1, is_halted() returns True immediately."""
        ks = KillSwitch(redis_client=None, exchanges=[])
        assert not is_halted()
        await ks.trigger()
        assert is_halted()

    async def test_tier1_timing_recorded(self):
        ks = KillSwitch(redis_client=None, exchanges=[])
        event = await ks.trigger()
        assert event.tier1_ts is not None
        assert event.tier1_latency_ms >= 0.0


# ---------------------------------------------------------------------------
# Tier 2 — Remote Order Cancellation
# ---------------------------------------------------------------------------


class TestKillSwitchTier2:
    async def test_tier2_cancels_orders_on_one_exchange(self):
        mock_exchange = AsyncMock()
        mock_exchange.exchange_id = "binance"
        mock_exchange.cancel_all_orders = AsyncMock(return_value=["ord1", "ord2"])

        ks = KillSwitch(redis_client=None, exchanges=[mock_exchange])
        event = await ks.trigger()

        mock_exchange.cancel_all_orders.assert_called_once()
        assert "ord1" in event.cancelled_orders
        assert "ord2" in event.cancelled_orders

    async def test_tier2_parallel_across_exchanges(self):
        """All exchanges are cancelled in parallel, not sequentially."""
        call_times: list[float] = []

        async def slow_cancel(timeout_ms: int = 2000) -> list[str]:
            call_times.append(time.perf_counter())
            await asyncio.sleep(0.05)  # 50ms each
            return ["order_x"]

        exchange1 = AsyncMock()
        exchange1.exchange_id = "binance"
        exchange1.cancel_all_orders = slow_cancel

        exchange2 = AsyncMock()
        exchange2.exchange_id = "okx"
        exchange2.cancel_all_orders = slow_cancel

        ks = KillSwitch(redis_client=None, exchanges=[exchange1, exchange2])
        t0 = time.perf_counter()
        event = await ks.trigger()
        elapsed = (time.perf_counter() - t0) * 1000

        # Parallel: both run simultaneously, so total ~50ms not ~100ms
        assert elapsed < 200, f"Tier 2 took {elapsed:.0f}ms — not running in parallel"
        assert len(event.cancelled_orders) == 2

    async def test_tier2_exchange_failure_logged_not_raised(self):
        """Exchange cancel failure is logged; kill switch still completes."""
        mock_exchange = AsyncMock()
        mock_exchange.exchange_id = "binance"
        mock_exchange.cancel_all_orders = AsyncMock(side_effect=Exception("API error"))

        ks = KillSwitch(redis_client=None, exchanges=[mock_exchange])
        event = await ks.trigger()

        assert is_halted()
        assert len(event.errors) > 0

    async def test_tier2_no_exchanges_completes_immediately(self):
        ks = KillSwitch(redis_client=None, exchanges=[])
        event = await ks.trigger()
        assert event.tier2_latency_ms == 0.0


# ---------------------------------------------------------------------------
# Tier 3 — Position Closure
# ---------------------------------------------------------------------------


class TestKillSwitchTier3:
    async def test_tier3_closes_positions(self):
        mock_exchange = AsyncMock()
        mock_exchange.exchange_id = "binance"
        mock_exchange.cancel_all_orders = AsyncMock(return_value=[])
        mock_exchange.close_all_positions = AsyncMock(return_value=["pos1", "pos2"])

        ks = KillSwitch(redis_client=None, exchanges=[mock_exchange], tier3_enabled=True)
        event = await ks.trigger()

        mock_exchange.close_all_positions.assert_called_once()
        assert "pos1" in event.closed_positions
        assert "pos2" in event.closed_positions

    async def test_tier3_disabled_skips_close(self):
        mock_exchange = AsyncMock()
        mock_exchange.exchange_id = "binance"
        mock_exchange.cancel_all_orders = AsyncMock(return_value=[])
        mock_exchange.close_all_positions = AsyncMock(return_value=["pos1"])

        ks = KillSwitch(redis_client=None, exchanges=[mock_exchange], tier3_enabled=False)
        event = await ks.trigger()

        mock_exchange.close_all_positions.assert_not_called()
        assert event.closed_positions == []

    async def test_tier3_failure_logged_not_raised(self):
        mock_exchange = AsyncMock()
        mock_exchange.exchange_id = "binance"
        mock_exchange.cancel_all_orders = AsyncMock(return_value=[])
        mock_exchange.close_all_positions = AsyncMock(side_effect=Exception("Exchange down"))

        ks = KillSwitch(redis_client=None, exchanges=[mock_exchange], tier3_enabled=True)
        event = await ks.trigger()

        assert is_halted()
        assert len(event.errors) > 0


# ---------------------------------------------------------------------------
# Duplicate trigger / reset
# ---------------------------------------------------------------------------


class TestKillSwitchDuplicateTrigger:
    async def test_duplicate_trigger_returns_error_event(self):
        mock_exchange = AsyncMock()
        mock_exchange.exchange_id = "binance"
        mock_exchange.cancel_all_orders = AsyncMock(return_value=[])

        ks = KillSwitch(redis_client=None, exchanges=[mock_exchange])
        await ks.trigger()
        event2 = await ks.trigger()

        assert "Already triggered" in event2.errors

    async def test_reset_clears_halt_flag(self):
        ks = KillSwitch(redis_client=None, exchanges=[])
        await ks.trigger()
        assert is_halted()
        ks.reset()
        assert not is_halted()

    async def test_reset_allows_retriggering(self):
        ks = KillSwitch(redis_client=None, exchanges=[])
        await ks.trigger()
        ks.reset()
        event = await ks.trigger()
        assert is_halted()
        assert "Already triggered" not in event.errors


# ---------------------------------------------------------------------------
# KillSwitch.is_halted() method
# ---------------------------------------------------------------------------


class TestKillSwitchIsHalted:
    async def test_is_halted_method_delegates_to_flag(self):
        ks = KillSwitch()
        assert not ks.is_halted()
        halt_local()
        assert ks.is_halted()
