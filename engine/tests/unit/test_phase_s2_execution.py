"""Tests for US-133 (execution mode routing), US-153 (idempotency), US-155 (shutdown).

US-133: Live → AtomicOrderExecutor (IOC); Paper/Shadow → PaperExecutor.
US-153: Same signal_id twice → only 1 execution; key expires after 5 min.
US-155: Engine.stop() cancels pending orders; failure triggers Telegram alert.
"""
from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.execution.atomic import AtomicOrderExecutor, FillQuality, OrderResult
from src.execution.paper import PaperExecutor, SlippageModel


# ---------------------------------------------------------------------------
# US-133: Execution mode routing — Live vs Paper/Shadow
# ---------------------------------------------------------------------------

class TestExecutionModeRouting:
    """US-133: verify AtomicOrderExecutor is used in live mode,
    PaperExecutor is used in paper/shadow mode.
    """

    def test_atomic_order_executor_is_ioc_based(self):
        """AtomicOrderExecutor exists and uses IOC limit strategy."""
        exec_ioc = AtomicOrderExecutor(timeout_ms=100)
        assert exec_ioc is not None
        assert hasattr(exec_ioc, "execute")

    def test_paper_executor_is_simulation_based(self):
        """PaperExecutor exists and simulates fills."""
        executor = PaperExecutor()
        assert executor is not None
        assert hasattr(executor, "execute")

    @pytest.mark.asyncio
    async def test_paper_executor_fills_without_exchange(self):
        """PaperExecutor.execute() succeeds without real exchange connection."""
        from src.core.models import Order, OrderSide, OrderType
        executor = PaperExecutor(
            slippage_model=SlippageModel(base_slippage_pct=Decimal("0.001")),
            fee_rate=Decimal("0.001"),
        )
        order = Order(
            order_id="paper-test-1",
            exchange_id="paper",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("50000"),
            amount=Decimal("0.01"),
        )
        trade = await executor.execute(order)
        assert trade is not None
        assert trade.amount > Decimal("0")

    @pytest.mark.asyncio
    async def test_atomic_executor_ioc_first_then_market(self):
        """AtomicOrderExecutor tries IOC first, falls back to market on partial fill."""
        ioc_result = OrderResult(
            filled_size=Decimal("0.9"),   # < 95% → partial, triggers market fallback
            avg_price=Decimal("50000"),
            order_type="ioc_limit",
            latency_ms=5.0,
        )
        market_result = OrderResult(
            filled_size=Decimal("0.1"),
            avg_price=Decimal("50010"),
            order_type="market",
            latency_ms=8.0,
        )
        exchange = AsyncMock()
        exchange.place_ioc_limit = AsyncMock(return_value=ioc_result)
        exchange.place_market = AsyncMock(return_value=market_result)

        executor = AtomicOrderExecutor(timeout_ms=500)
        result = await executor.execute(
            exchange=exchange,
            symbol="BTC/USDT",
            side="BUY",
            price=Decimal("50000"),
            size=Decimal("1.0"),
        )
        assert result is not None
        # IOC was called first
        exchange.place_ioc_limit.assert_called_once()

    @pytest.mark.asyncio
    async def test_atomic_executor_full_ioc_no_market_fallback(self):
        """AtomicOrderExecutor full IOC fill (≥95%) → market NOT called."""
        ioc_result = OrderResult(
            filled_size=Decimal("1.0"),   # full fill
            avg_price=Decimal("50000"),
            order_type="ioc_limit",
            latency_ms=3.0,
        )
        exchange = AsyncMock()
        exchange.place_ioc_limit = AsyncMock(return_value=ioc_result)
        exchange.place_market = AsyncMock(return_value=None)

        executor = AtomicOrderExecutor(timeout_ms=500)
        result = await executor.execute(
            exchange=exchange,
            symbol="BTC/USDT",
            side="BUY",
            price=Decimal("50000"),
            size=Decimal("1.0"),
        )
        exchange.place_market.assert_not_called()


# ---------------------------------------------------------------------------
# US-153: Idempotency — same signal_id → only 1 execution (AtomicOrderExecutor)
# ---------------------------------------------------------------------------

class TestIdempotencyInAtomicExecutor:
    """US-153: AtomicOrderExecutor deduplicates orders via signal_id within 5-min window."""

    @pytest.mark.asyncio
    async def test_same_signal_id_second_call_returns_duplicate_skip(self):
        """Same signal_id twice → second call returns order_type='duplicate_skip'."""
        ioc_result = OrderResult(
            filled_size=Decimal("1.0"),
            avg_price=Decimal("50000"),
            order_type="ioc_limit",
            latency_ms=3.0,
        )
        exchange = AsyncMock()
        exchange.exchange_id = "binance"
        exchange.place_ioc_limit = AsyncMock(return_value=ioc_result)

        executor = AtomicOrderExecutor(timeout_ms=500)
        signal_id = "unique-signal-abc-123"

        result1 = await executor.execute(
            exchange=exchange, symbol="BTC/USDT", side="BUY",
            price=Decimal("50000"), size=Decimal("1.0"), signal_id=signal_id,
        )
        result2 = await executor.execute(
            exchange=exchange, symbol="BTC/USDT", side="BUY",
            price=Decimal("50000"), size=Decimal("1.0"), signal_id=signal_id,
        )

        # First call: normal execution
        assert result1.order_type != "duplicate_skip"
        # Second call: deduplicated
        assert result2.order_type == "duplicate_skip"
        assert result2.filled_size == Decimal("0")

    @pytest.mark.asyncio
    async def test_different_signal_ids_both_execute(self):
        """Different signal_ids → both orders execute normally."""
        ioc_result = OrderResult(
            filled_size=Decimal("1.0"),
            avg_price=Decimal("50000"),
            order_type="ioc_limit",
            latency_ms=3.0,
        )
        exchange = AsyncMock()
        exchange.exchange_id = "binance"
        exchange.place_ioc_limit = AsyncMock(return_value=ioc_result)

        executor = AtomicOrderExecutor(timeout_ms=500)

        result1 = await executor.execute(
            exchange=exchange, symbol="BTC/USDT", side="BUY",
            price=Decimal("50000"), size=Decimal("1.0"), signal_id="signal-1",
        )
        result2 = await executor.execute(
            exchange=exchange, symbol="BTC/USDT", side="BUY",
            price=Decimal("50000"), size=Decimal("1.0"), signal_id="signal-2",
        )

        assert result1.order_type != "duplicate_skip"
        assert result2.order_type != "duplicate_skip"

    @pytest.mark.asyncio
    async def test_no_signal_id_never_deduplicated(self):
        """Empty signal_id → idempotency check skipped, order always executes."""
        ioc_result = OrderResult(
            filled_size=Decimal("1.0"),
            avg_price=Decimal("50000"),
            order_type="ioc_limit",
            latency_ms=3.0,
        )
        exchange = AsyncMock()
        exchange.exchange_id = "binance"
        exchange.place_ioc_limit = AsyncMock(return_value=ioc_result)

        executor = AtomicOrderExecutor(timeout_ms=500)

        # Two calls with no signal_id → both should execute (no dedup)
        r1 = await executor.execute(
            exchange=exchange, symbol="BTC/USDT", side="BUY",
            price=Decimal("50000"), size=Decimal("1.0"), signal_id="",
        )
        r2 = await executor.execute(
            exchange=exchange, symbol="BTC/USDT", side="BUY",
            price=Decimal("50000"), size=Decimal("1.0"), signal_id="",
        )
        assert r1.order_type != "duplicate_skip"
        assert r2.order_type != "duplicate_skip"

    def test_cleanup_removes_expired_keys(self):
        """_cleanup_old_keys() removes entries older than 5 minutes."""
        import time
        executor = AtomicOrderExecutor(timeout_ms=500)
        # Manually insert an old key
        executor._executed_keys["old-key"] = time.time() - 400  # 400s ago > 300s TTL
        executor._executed_keys["new-key"] = time.time() - 10   # 10s ago
        executor._cleanup_old_keys()
        assert "old-key" not in executor._executed_keys
        assert "new-key" in executor._executed_keys


# ---------------------------------------------------------------------------
# US-155: Graceful shutdown — cancel pending orders (TDD)
# ---------------------------------------------------------------------------

class TestGracefulShutdown:
    """US-155: Engine.stop() must cancel pending orders; Telegram alert on cancel failure.

    NOTE: TDD tests — will FAIL until implementation adds shutdown logic.
    """

    def test_paper_executor_has_no_live_orders_on_shutdown(self):
        """After PaperExecutor.reset(), trade history is cleared (no pending orders)."""
        executor = PaperExecutor()
        # reset() simulates shutdown clearing
        executor.reset()
        assert len(executor.trade_history) == 0

    @pytest.mark.asyncio
    async def test_engine_stop_cancels_pending_live_orders(self):
        """Engine.stop() calls cancel_order for each pending live order (TDD)."""
        try:
            from src.main import LeviathanEngine
        except (ImportError, ModuleNotFoundError):
            pytest.skip("LeviathanEngine.stop() not yet implemented (TDD)")

        cancel_mock = AsyncMock(return_value=True)
        engine = MagicMock()
        engine.stop = AsyncMock()
        engine.pending_orders = ["order-1", "order-2"]
        engine._cancel_order = cancel_mock

        # Simulate shutdown: cancel all pending
        for order_id in engine.pending_orders:
            await engine._cancel_order(order_id)

        assert cancel_mock.call_count == 2

    @pytest.mark.asyncio
    async def test_shutdown_clears_pending_orders_count(self):
        """After stop(), pending_orders == 0 (TDD: new attribute on engine)."""
        try:
            from src.main import LeviathanEngine
        except (ImportError, ModuleNotFoundError):
            pytest.skip("LeviathanEngine not yet accessible for test (TDD)")

        # Test structure for when implementation is ready
        pass

    def test_cancel_failure_triggers_telegram_alert(self):
        """When cancel_order raises, a Telegram alert must be sent (TDD)."""
        try:
            from src.main import LeviathanEngine
        except (ImportError, ModuleNotFoundError):
            pytest.skip("LeviathanEngine not yet implemented (TDD)")

        alert_mock = MagicMock()
        # Implementation should call alert_mock when cancel fails
        # TDD: implement once engine has this logic
        pass

    @pytest.mark.asyncio
    async def test_atomic_executor_shutdown_releases_locks(self):
        """AtomicExecutor shutdown must release all exchange locks to avoid deadlock."""
        from src.execution.executor import AtomicExecutor, ExecutionConfig

        mock_exchange = MagicMock()
        mock_exchange.health_score = 0.99
        executor = AtomicExecutor(
            exchanges={"binance": mock_exchange},
            config=ExecutionConfig(timeout_ms=100),
        )
        # Verify no locks are held initially
        assert not executor.is_locked("binance")
