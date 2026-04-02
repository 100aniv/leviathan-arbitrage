"""Phase H: LiveMode unit tests.

Tests the LiveMode orchestrator with mock executor, verifying:
1. Initialization and lifecycle (start/stop)
2. Direct in-process signal routing (no Redis)
3. DI executor integration (Paper/Atomic)
4. LiveGate enforcement with Shadow fallback
5. Stats tracking and observability
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import Order, OrderSide, OrderType, Signal
from src.modes.live import LiveGateFailed, LiveMode, LiveModeStats, PerStrategyStats
from src.strategies.base import TradeLeg, TradeRequest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_signal_generator():
    sg = AsyncMock()
    sg.on_orderbook_update = AsyncMock(return_value=None)
    return sg


@pytest.fixture
def mock_executor():
    from src.execution.executor import ExecutionStatus
    executor = AsyncMock()
    executor.execute_same_exchange = AsyncMock(return_value=MagicMock(
        status=ExecutionStatus.SUCCESS, realized_pnl=0.01, legs=[],
    ))
    executor.execute_cross_exchange = AsyncMock(return_value=MagicMock(
        status=ExecutionStatus.SUCCESS, realized_pnl=0.02, legs=[],
    ))
    executor.execute_multi_leg = AsyncMock(return_value=MagicMock(
        status=ExecutionStatus.SUCCESS, realized_pnl=0.005, legs=[],
    ))
    return executor


@pytest.fixture
def mock_strategy_manager():
    sm = MagicMock()
    sm.list_strategies.return_value = ["cross_exchange_v1", "triangular_v1"]
    sm.get_strategy.return_value = MagicMock(
        is_active=False, shadow_mode=True, strategy_id="cross_exchange_v1"
    )
    sm.start_strategy = AsyncMock()
    sm.route_signal = AsyncMock(return_value=[])
    return sm


@pytest.fixture
def make_live_mode(mock_signal_generator, mock_executor, mock_strategy_manager):
    """Factory to create LiveMode with optional overrides."""

    def _make(**overrides):
        defaults = dict(
            signal_generator=mock_signal_generator,
            executor=mock_executor,
            strategy_manager=mock_strategy_manager,
            symbols=["BTC/USDT"],
            exchanges=["binance", "upbit"],
            execution_mode="live",  # live: mock executor is used directly; paper: auto-wires PaperExecutor
        )
        defaults.update(overrides)
        return LiveMode(**defaults)

    return _make


def make_signal(
    symbol: str = "BTC/USDT",
    buy_exchange: str = "binance",
    sell_exchange: str = "upbit",
    strategy_id: str = "cross_exchange_v1",
) -> Signal:
    return Signal(
        symbol=symbol,
        buy_exchange=buy_exchange,
        sell_exchange=sell_exchange,
        buy_price=Decimal("50000"),
        sell_price=Decimal("50100"),
        spread_pct=Decimal("0.002"),
        confidence=0.85,
        volume=Decimal("0.01"),
        strategy_id=strategy_id,
    )


def make_trade_request(
    strategy_id: str = "cross_exchange_v1",
    symbol: str = "BTC/USDT",
) -> TradeRequest:
    return TradeRequest(
        strategy_id=strategy_id,
        legs=[
            TradeLeg(
                exchange_id="binance",
                symbol=symbol,
                side=OrderSide.BUY,
                size=Decimal("0.01"),
                price=Decimal("50000"),
                order_type=OrderType.LIMIT,
            ),
            TradeLeg(
                exchange_id="upbit",
                symbol=symbol,
                side=OrderSide.SELL,
                size=Decimal("0.01"),
                price=Decimal("50100"),
                order_type=OrderType.LIMIT,
            ),
        ],
        expected_profit_usdt=Decimal("0.50"),
    )


# ---------------------------------------------------------------------------
# Test: Initialization
# ---------------------------------------------------------------------------


class TestLiveModeInit:
    def test_init_paper_mode(self, make_live_mode):
        lm = make_live_mode(execution_mode="paper")
        assert lm.execution_mode == "paper"
        assert not lm.running

    def test_init_live_mode(self, make_live_mode):
        lm = make_live_mode(execution_mode="live")
        assert lm.execution_mode == "live"

    def test_stats_initialized(self, make_live_mode):
        lm = make_live_mode()
        assert isinstance(lm.stats, LiveModeStats)
        assert lm.stats.trades_executed == 0
        assert lm.stats.total_pnl == 0.0


# ---------------------------------------------------------------------------
# Test: Lifecycle (start/stop)
# ---------------------------------------------------------------------------


class TestLiveModeLifecycle:
    @pytest.fixture(autouse=True)
    def mock_approval_gate(self):
        """US-364: mock Telegram approval gate — prevents real Telegram calls in tests."""
        with patch(
            "src.infra.approval_gate.request_live_approval",
            new_callable=AsyncMock,
            return_value=True,
        ):
            yield

    @pytest.mark.asyncio
    async def test_start_activates_strategies(self, make_live_mode, mock_strategy_manager):
        lm = make_live_mode()
        with patch("src.collectors.manager.CollectorManager") as MockCM:
            mock_cm_instance = AsyncMock()
            mock_cm_instance.start = AsyncMock()
            mock_cm_instance.stop = AsyncMock()
            MockCM.return_value = mock_cm_instance
            await lm.start()

        # Strategies should be started
        assert mock_strategy_manager.start_strategy.call_count == 2
        assert lm.running

        await lm.stop()
        assert not lm.running

    @pytest.mark.asyncio
    async def test_start_without_live_gate_proceeds(self, make_live_mode):
        """Without LiveGate, should proceed (소액 테스트 모드)."""
        lm = make_live_mode(live_gate=None)
        with patch("src.collectors.manager.CollectorManager") as MockCM:
            mock_cm_instance = AsyncMock()
            mock_cm_instance.start = AsyncMock()
            mock_cm_instance.stop = AsyncMock()
            MockCM.return_value = mock_cm_instance
            await lm.start()
        assert lm.running
        await lm.stop()

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self, make_live_mode):
        lm = make_live_mode()
        # stop() without start() should not raise
        await lm.stop()
        assert not lm.running


# ---------------------------------------------------------------------------
# Test: LiveGate enforcement
# ---------------------------------------------------------------------------


class TestLiveGate:
    @pytest.fixture(autouse=True)
    def mock_approval_gate(self):
        """US-364: mock Telegram approval gate — prevents real Telegram calls in tests."""
        with patch(
            "src.infra.approval_gate.request_live_approval",
            new_callable=AsyncMock,
            return_value=True,
        ):
            yield

    @pytest.mark.asyncio
    async def test_live_gate_pass(self, make_live_mode):
        from src.modes.live_gate import LiveGate

        gate = MagicMock(spec=LiveGate)
        gate.enforce_or_fallback = AsyncMock(return_value=True)

        lm = make_live_mode(live_gate=gate)
        with patch("src.collectors.manager.CollectorManager") as MockCM:
            mock_cm = AsyncMock()
            mock_cm.start = AsyncMock()
            mock_cm.stop = AsyncMock()
            MockCM.return_value = mock_cm
            await lm.start()
        assert lm.running
        await lm.stop()

    @pytest.mark.asyncio
    async def test_live_gate_fail_raises(self, make_live_mode):
        """LiveGate failure should raise LiveGateFailed, NOT silently return."""
        from src.modes.live_gate import LiveGate

        gate = MagicMock(spec=LiveGate)
        gate.enforce_or_fallback = AsyncMock(return_value=False)

        lm = make_live_mode(live_gate=gate)
        with pytest.raises(LiveGateFailed):
            await lm.start()

        assert not lm.running  # Should NOT be running after gate failure


# ---------------------------------------------------------------------------
# Test: Signal routing (direct in-process)
# ---------------------------------------------------------------------------


class TestSignalRouting:
    @pytest.mark.asyncio
    async def test_route_signal_calls_strategy_manager(
        self, make_live_mode, mock_strategy_manager
    ):
        lm = make_live_mode()
        lm._running = True

        signal = make_signal()
        await lm._route_signal_to_strategies(signal)

        mock_strategy_manager.route_signal.assert_awaited_once_with(signal)

    @pytest.mark.asyncio
    async def test_route_signal_executes_trade_requests(
        self, make_live_mode, mock_strategy_manager, mock_executor
    ):
        tr = make_trade_request()
        mock_strategy_manager.route_signal = AsyncMock(return_value=[tr])

        lm = make_live_mode()
        lm._running = True

        signal = make_signal()
        await lm._route_signal_to_strategies(signal)

        # Executor should have been called
        assert mock_executor.execute_cross_exchange.await_count == 1

    @pytest.mark.asyncio
    async def test_route_signal_fallback_on_error(
        self, make_live_mode, mock_strategy_manager
    ):
        """On routing error, should fallback to direct signal execution."""
        mock_strategy_manager.route_signal = AsyncMock(side_effect=RuntimeError("boom"))

        lm = make_live_mode()
        lm._running = True

        signal = make_signal()
        # Should not raise — fallback handles it
        await lm._route_signal_to_strategies(signal)


# ---------------------------------------------------------------------------
# Test: Trade execution
# ---------------------------------------------------------------------------


class TestTradeExecution:
    @pytest.mark.asyncio
    async def test_execute_trade_request_same_exchange(
        self, make_live_mode, mock_executor
    ):
        tr = TradeRequest(
            strategy_id="triangular_v1",
            legs=[
                TradeLeg(exchange_id="binance", symbol="BTC/USDT",
                         side=OrderSide.BUY, size=Decimal("0.01"),
                         price=Decimal("50000"), order_type=OrderType.LIMIT),
                TradeLeg(exchange_id="binance", symbol="ETH/USDT",
                         side=OrderSide.SELL, size=Decimal("0.1"),
                         price=Decimal("3000"), order_type=OrderType.LIMIT),
            ],
        )
        lm = make_live_mode()
        lm._running = True
        await lm._execute_trade_request(tr)

        mock_executor.execute_same_exchange.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_trade_request_cross_exchange(
        self, make_live_mode, mock_executor
    ):
        tr = make_trade_request()
        lm = make_live_mode()
        lm._running = True
        await lm._execute_trade_request(tr)

        mock_executor.execute_cross_exchange.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_updates_stats(self, make_live_mode, mock_executor):
        from src.execution.executor import ExecutionStatus
        mock_executor.execute_cross_exchange = AsyncMock(
            return_value=MagicMock(status=ExecutionStatus.SUCCESS, realized_pnl=0.05, legs=[])
        )

        tr = make_trade_request()
        lm = make_live_mode()
        lm._running = True
        await lm._execute_trade_request(tr)

        assert lm.stats.trades_executed == 1
        assert lm.stats.total_pnl == pytest.approx(0.05, abs=0.001)
        assert lm.stats.trades_won == 1

    @pytest.mark.asyncio
    async def test_kill_switch_blocks_execution(self, make_live_mode, mock_executor):
        ks = MagicMock()
        ks.is_halted.return_value = True

        tr = make_trade_request()
        lm = make_live_mode(kill_switch=ks)
        lm._running = True
        await lm._execute_trade_request(tr)

        # Executor should NOT have been called
        mock_executor.execute_cross_exchange.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_risk_guardian_blocks_execution(self, make_live_mode, mock_executor):
        rg = MagicMock()
        rg.check_trade_request.return_value = False

        tr = make_trade_request()
        lm = make_live_mode(risk_guardian=rg)
        lm._running = True
        await lm._execute_trade_request(tr)

        mock_executor.execute_cross_exchange.assert_not_awaited()
        assert lm.stats.trades_risk_blocked == 1

    @pytest.mark.asyncio
    async def test_collision_detection(self, make_live_mode, mock_executor):
        tr = make_trade_request()
        lm = make_live_mode()
        lm._running = True

        # First execution should pass
        await lm._execute_trade_request(tr)
        assert mock_executor.execute_cross_exchange.await_count == 1

        # Second immediate execution with same key should be blocked (collision)
        await lm._execute_trade_request(tr)
        assert mock_executor.execute_cross_exchange.await_count == 1  # Still 1


# ---------------------------------------------------------------------------
# Test: Stats tracking
# ---------------------------------------------------------------------------


class TestStats:
    def test_pnl_update_win(self, make_live_mode):
        lm = make_live_mode()
        lm._update_pnl_stats(Decimal("1.5"), "test_strategy")
        assert lm.stats.total_pnl == pytest.approx(1.5)
        assert lm.stats.trades_won == 1
        assert lm.stats.peak_pnl == pytest.approx(1.5)
        assert lm.stats.max_drawdown == 0.0

    def test_pnl_update_loss(self, make_live_mode):
        lm = make_live_mode()
        lm._update_pnl_stats(Decimal("1.0"), "test_strategy")
        lm._update_pnl_stats(Decimal("-0.5"), "test_strategy")
        assert lm.stats.total_pnl == pytest.approx(0.5)
        assert lm.stats.peak_pnl == pytest.approx(1.0)
        assert lm.stats.max_drawdown == pytest.approx(0.5)

    def test_per_strategy_stats(self, make_live_mode):
        lm = make_live_mode()
        lm._update_pnl_stats(Decimal("1.0"), "strat_a")
        lm._update_pnl_stats(Decimal("-0.5"), "strat_b")
        assert "strat_a" in lm.stats.by_strategy
        assert "strat_b" in lm.stats.by_strategy
        assert lm.stats.by_strategy["strat_a"].wins == 1
        assert lm.stats.by_strategy["strat_b"].losses == 1


# ---------------------------------------------------------------------------
# Test: Executor routing
# ---------------------------------------------------------------------------


class TestExecutorRouting:
    @pytest.mark.asyncio
    async def test_multi_leg_same_exchange(self, make_live_mode, mock_executor):
        """3+ legs same exchange → execute_multi_leg."""
        tr = TradeRequest(
            strategy_id="triangular_v1",
            legs=[
                TradeLeg(exchange_id="binance", symbol="BTC/USDT",
                         side=OrderSide.BUY, size=Decimal("0.01"),
                         price=Decimal("50000"), order_type=OrderType.LIMIT),
                TradeLeg(exchange_id="binance", symbol="ETH/BTC",
                         side=OrderSide.BUY, size=Decimal("0.1"),
                         price=Decimal("0.06"), order_type=OrderType.LIMIT),
                TradeLeg(exchange_id="binance", symbol="ETH/USDT",
                         side=OrderSide.SELL, size=Decimal("0.1"),
                         price=Decimal("3000"), order_type=OrderType.LIMIT),
            ],
        )
        lm = make_live_mode()
        lm._running = True
        await lm._execute_trade_request(tr)

        mock_executor.execute_multi_leg.assert_awaited_once()

    def test_collision_key_deterministic(self, make_live_mode):
        lm = make_live_mode()
        tr = make_trade_request()
        key1 = lm._build_collision_key(tr)
        key2 = lm._build_collision_key(tr)
        assert key1 == key2
        assert "BTC/USDT" in key1
        assert "binance" in key1
