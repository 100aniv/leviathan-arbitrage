"""Unit tests for Bug 26-29 gate classes.

Bug 26: DeduplicationGate — asyncio.Lock per collision key
Bug 27: StrandedPositionTracker — conditional HALT threshold
Bug 29: MarginTracker — in-flight margin reservation
"""
import asyncio
from decimal import Decimal

import pytest

from src.execution.dedup import DeduplicationGate
from src.execution.stranded import StrandedPositionTracker
from src.execution.margin_tracker import MarginTracker


# ── DeduplicationGate ────────────────────────────────────────────────────────

class TestDeduplicationGate:
    @pytest.mark.asyncio
    async def test_first_call_passes(self):
        gate = DeduplicationGate(window_s=10.0)
        result = await gate.check_and_register("ETH/USDT:binance-bitget")
        assert result is True

    @pytest.mark.asyncio
    async def test_duplicate_within_window_blocked(self):
        gate = DeduplicationGate(window_s=10.0)
        key = "BTC/USDT:binance-bitget"
        assert await gate.check_and_register(key) is True
        assert await gate.check_and_register(key) is False  # duplicate

    @pytest.mark.asyncio
    async def test_different_keys_not_blocked(self):
        gate = DeduplicationGate(window_s=10.0)
        assert await gate.check_and_register("ETH/USDT:a-b") is True
        assert await gate.check_and_register("BTC/USDT:a-b") is True  # different key

    @pytest.mark.asyncio
    async def test_concurrent_same_key_only_one_passes(self):
        """Bug 26 핵심: 동시 coroutine에서 같은 key → 1개만 통과."""
        gate = DeduplicationGate(window_s=10.0)
        key = "SOL/USDT:binance-bitget"

        results = await asyncio.gather(
            gate.check_and_register(key),
            gate.check_and_register(key),
            gate.check_and_register(key),
        )
        assert results.count(True) == 1
        assert results.count(False) == 2

    @pytest.mark.asyncio
    async def test_cleanup_stale_removes_old_entries(self):
        gate = DeduplicationGate(window_s=0.01)  # 10ms window
        key = "XRP/USDT:a-b"
        await gate.check_and_register(key)
        await asyncio.sleep(0.03)  # > 2× window
        await gate.cleanup_stale()
        # after cleanup, key should be fresh
        assert await gate.check_and_register(key) is True


# ── StrandedPositionTracker ──────────────────────────────────────────────────

class TestStrandedPositionTracker:
    def test_benign_22002_no_halt(self):
        tracker = StrandedPositionTracker(halt_threshold_usd=30.0)
        should_halt = tracker.register(
            exchange_id="bitget_futures",
            symbol="BARD/USDT",
            side="buy",
            size=256,
            value_usd=5.0,
            reason="22002: no position to close",
        )
        assert should_halt is False
        assert tracker.total_stranded_usd == 0.0

    def test_benign_40762_no_halt(self):
        tracker = StrandedPositionTracker(halt_threshold_usd=30.0)
        should_halt = tracker.register(
            exchange_id="bitget_futures",
            symbol="ALT/USDT",
            side="sell",
            size=100,
            value_usd=10.0,
            reason="40762: position does not exist",
        )
        assert should_halt is False

    def test_real_failure_below_threshold_no_halt(self):
        tracker = StrandedPositionTracker(halt_threshold_usd=30.0)
        should_halt = tracker.register(
            exchange_id="binance_futures",
            symbol="ETH/USDT",
            side="buy",
            size=0.01,
            value_usd=20.0,
            reason="timeout",
        )
        assert should_halt is False
        assert tracker.total_stranded_usd == 20.0

    def test_real_failure_exceeds_threshold_halts(self):
        tracker = StrandedPositionTracker(halt_threshold_usd=30.0)
        tracker.register("binance_futures", "ETH/USDT", "buy", 0.01, 20.0, "timeout")
        should_halt = tracker.register(
            exchange_id="binance_futures",
            symbol="BTC/USDT",
            side="sell",
            size=0.001,
            value_usd=15.0,
            reason="network error",
        )
        assert should_halt is True  # 20+15=35 > 30

    def test_clear_resets_total(self):
        tracker = StrandedPositionTracker(halt_threshold_usd=30.0)
        tracker.register("binance_futures", "ETH/USDT", "buy", 0.01, 20.0, "timeout")
        assert tracker.total_stranded_usd == 20.0
        tracker.clear()
        assert tracker.total_stranded_usd == 0.0


# ── MarginTracker ────────────────────────────────────────────────────────────

class TestMarginTracker:
    @pytest.mark.asyncio
    async def test_sufficient_margin_approved(self):
        tracker = MarginTracker()
        ok = await tracker.check_and_reserve(
            exchange_id="binance_futures",
            required_usd=Decimal("10"),
            available_usd=Decimal("100"),
        )
        assert ok is True

    @pytest.mark.asyncio
    async def test_insufficient_margin_blocked(self):
        tracker = MarginTracker()
        ok = await tracker.check_and_reserve(
            exchange_id="binance_futures",
            required_usd=Decimal("100"),
            available_usd=Decimal("50"),
        )
        assert ok is False

    @pytest.mark.asyncio
    async def test_inflight_reduces_available(self):
        """Bug 29 핵심: 동시 예약 시 in-flight 누적으로 두 번째 차단."""
        tracker = MarginTracker()
        # First reservation: 10 * 1.15 = 11.5 reserved
        ok1 = await tracker.check_and_reserve("binance_futures", Decimal("10"), Decimal("20"))
        assert ok1 is True
        # Second: net_available = 20 - 11.5 = 8.5, required_effective = 10*1.15=11.5 → blocked
        ok2 = await tracker.check_and_reserve("binance_futures", Decimal("10"), Decimal("20"))
        assert ok2 is False

    @pytest.mark.asyncio
    async def test_release_restores_available(self):
        tracker = MarginTracker()
        await tracker.check_and_reserve("binance_futures", Decimal("10"), Decimal("20"))
        await tracker.release("binance_futures", Decimal("10"))
        reserved = await tracker.get_reserved("binance_futures")
        assert reserved == Decimal("0")

    @pytest.mark.asyncio
    async def test_different_exchanges_independent(self):
        tracker = MarginTracker()
        ok1 = await tracker.check_and_reserve("binance_futures", Decimal("10"), Decimal("20"))
        ok2 = await tracker.check_and_reserve("bitget_futures", Decimal("10"), Decimal("20"))
        assert ok1 is True
        assert ok2 is True

    @pytest.mark.asyncio
    async def test_reset_clears_exchange(self):
        tracker = MarginTracker()
        await tracker.check_and_reserve("binance_futures", Decimal("10"), Decimal("50"))
        await tracker.reset("binance_futures")
        assert await tracker.get_reserved("binance_futures") == Decimal("0")
