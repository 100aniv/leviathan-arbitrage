"""Shadow Mode End-to-End Simulation Test.

Runs a full shadow mode simulation with mocked dependencies:
  - MockSignalGenerator produces 200 signals over synthetic timestamps
  - PaperExecutor with PowerLawSlippage (k=1.0, gamma=0.5)
  - MockCollectorManager provides orderbook data
  - Verifies PnL tracking, trade count, drawdown, win rate
  - Runs LiveGate evaluation on shadow results (pass and fail scenarios)
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import Order, OrderSide, OrderType, Signal
from src.execution.paper import PaperExecutor
from src.modes.shadow import PowerLawSlippage, ShadowMode, ShadowStats
from src.analysis.walk_forward import WalkForwardResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_signal(
    rng: random.Random,
    index: int,
    base_price: float = 50_000.0,
) -> Signal:
    """Create a synthetic Signal with random spread and size."""
    spread_bps = rng.uniform(5, 30)
    volume = Decimal(str(round(rng.uniform(0.01, 1.0), 4)))
    buy_price = Decimal(str(round(base_price, 2)))
    sell_price = buy_price * (1 + Decimal(str(spread_bps / 10_000)))
    spread_pct = (sell_price - buy_price) / buy_price

    return Signal(
        strategy_id="shadow_arb_v1",
        symbol="BTC/USDT",
        buy_exchange="binance",
        sell_exchange="upbit",
        buy_price=buy_price,
        sell_price=sell_price,
        spread_pct=spread_pct,
        confidence=round(rng.uniform(0.6, 1.0), 3),
        volume=volume,
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


class MockSignalGenerator:
    """Returns one signal per call until the signal list is exhausted."""

    def __init__(self, signals: list[Signal]) -> None:
        self._signals = list(signals)
        self._idx = 0

    async def on_orderbook_update(self, book: Any, books: Any) -> Signal | None:
        if self._idx < len(self._signals):
            sig = self._signals[self._idx]
            self._idx += 1
            return sig
        return None


class MockCollectorManager:
    """Minimal collector manager that calls the provided callback once per start."""

    def __init__(self, on_orderbook: Any = None) -> None:
        self._on_orderbook = on_orderbook
        self._started = False

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def rng() -> random.Random:
    return random.Random(42)


@pytest.fixture()
def signals_200(rng: random.Random) -> list[Signal]:
    return [_make_signal(rng, i) for i in range(200)]


@pytest.fixture()
def paper_executor() -> PaperExecutor:
    return PaperExecutor(
        slippage_model=PowerLawSlippage(k=1.0, gamma=0.5),
        fee_rate=Decimal("0.001"),
    )


# ---------------------------------------------------------------------------
# Task 1a-c: Full shadow loop simulation
# ---------------------------------------------------------------------------


class TestShadowModeSimulation:
    """Full shadow mode simulation with 200 synthetic signals."""

    def _build_shadow_mode(
        self,
        signals: list[Signal],
        paper_executor: PaperExecutor,
    ) -> ShadowMode:
        collector = MockCollectorManager()
        generator = MockSignalGenerator(signals)

        # Build a fake orderbook class whose instances have best_bid/best_ask.
        # Must set __name__ because ShadowMode.__init__ logs it via structlog.
        class FakeOrderBook:
            __name__ = "FakeOrderBook"

            def __init__(self, symbol: str, exchange: str) -> None:
                pass

            def apply_snapshot(self, bids: Any, asks: Any) -> None:
                pass

            def best_bid(self) -> Decimal:
                return Decimal("50000")

            def best_ask(self) -> Decimal:
                return Decimal("50010")

        with patch("src.modes.shadow.get_orderbook_class", return_value=FakeOrderBook):
            shadow = ShadowMode(
                signal_generator=generator,
                paper_executor=paper_executor,
                collector_manager=collector,
                market_recorder=None,
                telegram=None,
            )

        # Bypass rate limiter: integration tests verify signal processing, not rate limiting.
        # Rate limiting behaviour is covered by tests/test_shadow_rate_limit.py.
        shadow._rate_limiter = MagicMock()
        shadow._rate_limiter.try_acquire.return_value = True

        return shadow

    @pytest.mark.asyncio
    async def test_shadow_processes_all_signals(
        self, signals_200: list[Signal], paper_executor: PaperExecutor
    ) -> None:
        """Processing 200 signals results in trade_count == 200."""
        shadow = self._build_shadow_mode(signals_200, paper_executor)
        # Manually set _running so _execute_shadow_trade fires
        shadow._running = True

        # Patch metrics calls to avoid registry errors in tests
        with patch("src.modes.shadow.TRADES_TOTAL"), \
             patch("src.modes.shadow.PNL_TOTAL"), \
             patch("src.modes.shadow.DRAWDOWN_CURRENT"), \
             patch("src.modes.shadow.SIGNALS_TOTAL"), \
             patch("src.modes.shadow.SIGNAL_COUNT"), \
             patch("src.modes.shadow.SIGNAL_PROCESSING_TIME"), \
             patch("src.modes.shadow.COLLECTOR_MESSAGES"), \
             patch("src.modes.shadow.EXCHANGE_HEALTH_SCORE"), \
             patch("src.modes.shadow.SPREAD_BPS"):

            for sig in signals_200:
                await shadow._execute_shadow_trade(sig)

        assert shadow._stats.trades_executed == 200

    @pytest.mark.asyncio
    async def test_pnl_is_tracked(
        self, signals_200: list[Signal], paper_executor: PaperExecutor
    ) -> None:
        """Total PnL is non-zero after processing 200 signals."""
        shadow = self._build_shadow_mode(signals_200, paper_executor)
        shadow._running = True

        with patch("src.modes.shadow.TRADES_TOTAL"), \
             patch("src.modes.shadow.PNL_TOTAL"), \
             patch("src.modes.shadow.DRAWDOWN_CURRENT"), \
             patch("src.modes.shadow.SIGNALS_TOTAL"), \
             patch("src.modes.shadow.SIGNAL_COUNT"), \
             patch("src.modes.shadow.SIGNAL_PROCESSING_TIME"), \
             patch("src.modes.shadow.COLLECTOR_MESSAGES"), \
             patch("src.modes.shadow.EXCHANGE_HEALTH_SCORE"), \
             patch("src.modes.shadow.SPREAD_BPS"):

            for sig in signals_200:
                await shadow._execute_shadow_trade(sig)

        assert shadow._stats.total_pnl != 0.0, "Expected non-zero total PnL"

    @pytest.mark.asyncio
    async def test_drawdown_computed(
        self, signals_200: list[Signal], paper_executor: PaperExecutor
    ) -> None:
        """Drawdown is correctly computed (>= 0.0)."""
        shadow = self._build_shadow_mode(signals_200, paper_executor)
        shadow._running = True

        with patch("src.modes.shadow.TRADES_TOTAL"), \
             patch("src.modes.shadow.PNL_TOTAL"), \
             patch("src.modes.shadow.DRAWDOWN_CURRENT"), \
             patch("src.modes.shadow.SIGNALS_TOTAL"), \
             patch("src.modes.shadow.SIGNAL_COUNT"), \
             patch("src.modes.shadow.SIGNAL_PROCESSING_TIME"), \
             patch("src.modes.shadow.COLLECTOR_MESSAGES"), \
             patch("src.modes.shadow.EXCHANGE_HEALTH_SCORE"), \
             patch("src.modes.shadow.SPREAD_BPS"):

            for sig in signals_200:
                await shadow._execute_shadow_trade(sig)

        assert shadow._stats.max_drawdown >= 0.0

    @pytest.mark.asyncio
    async def test_win_rate_in_valid_range(
        self, signals_200: list[Signal], paper_executor: PaperExecutor
    ) -> None:
        """Win rate is between 0 and 1."""
        shadow = self._build_shadow_mode(signals_200, paper_executor)
        shadow._running = True

        with patch("src.modes.shadow.TRADES_TOTAL"), \
             patch("src.modes.shadow.PNL_TOTAL"), \
             patch("src.modes.shadow.DRAWDOWN_CURRENT"), \
             patch("src.modes.shadow.SIGNALS_TOTAL"), \
             patch("src.modes.shadow.SIGNAL_COUNT"), \
             patch("src.modes.shadow.SIGNAL_PROCESSING_TIME"), \
             patch("src.modes.shadow.COLLECTOR_MESSAGES"), \
             patch("src.modes.shadow.EXCHANGE_HEALTH_SCORE"), \
             patch("src.modes.shadow.SPREAD_BPS"):

            for sig in signals_200:
                await shadow._execute_shadow_trade(sig)

        stats = shadow._stats
        total = stats.trades_executed
        assert total > 0
        win_rate = stats.trades_won / total
        assert 0.0 <= win_rate <= 1.0

    @pytest.mark.asyncio
    async def test_stats_accessible(
        self, signals_200: list[Signal], paper_executor: PaperExecutor
    ) -> None:
        """Stats dataclass is accessible and has expected fields."""
        shadow = self._build_shadow_mode(signals_200, paper_executor)
        shadow._running = True

        with patch("src.modes.shadow.TRADES_TOTAL"), \
             patch("src.modes.shadow.PNL_TOTAL"), \
             patch("src.modes.shadow.DRAWDOWN_CURRENT"), \
             patch("src.modes.shadow.SIGNALS_TOTAL"), \
             patch("src.modes.shadow.SIGNAL_COUNT"), \
             patch("src.modes.shadow.SIGNAL_PROCESSING_TIME"), \
             patch("src.modes.shadow.COLLECTOR_MESSAGES"), \
             patch("src.modes.shadow.EXCHANGE_HEALTH_SCORE"), \
             patch("src.modes.shadow.SPREAD_BPS"):

            for sig in signals_200[:10]:
                await shadow._execute_shadow_trade(sig)

        stats = shadow._stats
        assert hasattr(stats, "total_pnl")
        assert hasattr(stats, "trades_executed")
        assert hasattr(stats, "max_drawdown")
        assert hasattr(stats, "trades_won")
        assert hasattr(stats, "trades_lost")
        assert hasattr(stats, "signals_detected")

    @pytest.mark.asyncio
    async def test_signals_detected_equals_trades_executed(
        self, signals_200: list[Signal], paper_executor: PaperExecutor
    ) -> None:
        """signals_detected increments before execute; trades_executed after."""
        shadow = self._build_shadow_mode(signals_200, paper_executor)
        shadow._running = True

        with patch("src.modes.shadow.TRADES_TOTAL"), \
             patch("src.modes.shadow.PNL_TOTAL"), \
             patch("src.modes.shadow.DRAWDOWN_CURRENT"), \
             patch("src.modes.shadow.SIGNALS_TOTAL"), \
             patch("src.modes.shadow.SIGNAL_COUNT"), \
             patch("src.modes.shadow.SIGNAL_PROCESSING_TIME"), \
             patch("src.modes.shadow.COLLECTOR_MESSAGES"), \
             patch("src.modes.shadow.EXCHANGE_HEALTH_SCORE"), \
             patch("src.modes.shadow.SPREAD_BPS"):

            for sig in signals_200:
                await shadow._execute_shadow_trade(sig)

        # Both should be 200 when no errors occur
        assert shadow._stats.signals_detected == shadow._stats.trades_executed == 200


# ---------------------------------------------------------------------------
# Task 1d: LiveGate evaluation on shadow results
# ---------------------------------------------------------------------------


class TestLiveGateEvaluation:
    """Verify LiveGate pass/fail logic using mocked WalkForwardAnalyzer."""

    def _make_wf_result(
        self,
        sharpe: float,
        mdd: float,
        signals_per_day: float = 150.0,
    ) -> WalkForwardResult:
        result = WalkForwardResult()
        result.overall_sharpe = sharpe
        result.overall_mdd = mdd
        result.avg_signals_per_day = signals_per_day
        result.overall_win_rate = 0.6
        result.overall_trades = int(signals_per_day * 7)
        result.overall_pnl = sharpe * 100.0
        result.live_eligible = (
            sharpe >= 2.5 and mdd < 0.05 and signals_per_day >= 100
        )
        return result

    @pytest.mark.asyncio
    async def test_gate_passes_with_good_stats(self) -> None:
        """LiveGate eligible=True when Sharpe>2.5, MDD<5%, signals>100."""
        from src.modes.live_gate import LiveGate

        mock_pool = MagicMock()
        gate = LiveGate(pool=mock_pool, telegram=None, kill_switch=None)

        good_result = self._make_wf_result(sharpe=3.0, mdd=0.02, signals_per_day=150)

        with patch.object(gate._analyzer, "analyze", AsyncMock(return_value=good_result)), \
             patch.object(gate, "_check_kill_switch", return_value=False), \
             patch.object(gate, "_get_circuit_breaker_state", return_value=0.0), \
             patch.object(gate, "_check_exchange_health", return_value=(True, "All healthy")):

            result = await gate.evaluate(strategy_id="shadow_arb_v1")

        assert result.eligible is True, f"Expected eligible but got block_reasons={result.block_reasons}"
        assert len(result.block_reasons) == 0

    @pytest.mark.asyncio
    async def test_gate_fails_with_bad_sharpe(self) -> None:
        """LiveGate eligible=False when Sharpe<1.0."""
        from src.modes.live_gate import LiveGate

        mock_pool = MagicMock()
        gate = LiveGate(pool=mock_pool, telegram=None, kill_switch=None)

        bad_result = self._make_wf_result(sharpe=0.8, mdd=0.03, signals_per_day=150)

        with patch.object(gate._analyzer, "analyze", AsyncMock(return_value=bad_result)), \
             patch.object(gate, "_check_kill_switch", return_value=False), \
             patch.object(gate, "_get_circuit_breaker_state", return_value=0.0), \
             patch.object(gate, "_check_exchange_health", return_value=(True, "All healthy")):

            result = await gate.evaluate(strategy_id="shadow_arb_v1")

        assert result.eligible is False
        assert any("Sharpe" in r for r in result.block_reasons)

    @pytest.mark.asyncio
    async def test_gate_fails_with_high_mdd(self) -> None:
        """LiveGate eligible=False when MDD>10%."""
        from src.modes.live_gate import LiveGate

        mock_pool = MagicMock()
        gate = LiveGate(pool=mock_pool, telegram=None, kill_switch=None)

        bad_result = self._make_wf_result(sharpe=3.0, mdd=0.12, signals_per_day=150)

        with patch.object(gate._analyzer, "analyze", AsyncMock(return_value=bad_result)), \
             patch.object(gate, "_check_kill_switch", return_value=False), \
             patch.object(gate, "_get_circuit_breaker_state", return_value=0.0), \
             patch.object(gate, "_check_exchange_health", return_value=(True, "All healthy")):

            result = await gate.evaluate(strategy_id="shadow_arb_v1")

        assert result.eligible is False
        assert any("MDD" in r for r in result.block_reasons)

    @pytest.mark.asyncio
    async def test_gate_fails_with_both_bad_metrics(self) -> None:
        """LiveGate reports multiple block reasons when both Sharpe and MDD fail."""
        from src.modes.live_gate import LiveGate

        mock_pool = MagicMock()
        gate = LiveGate(pool=mock_pool, telegram=None, kill_switch=None)

        bad_result = self._make_wf_result(sharpe=0.5, mdd=0.15, signals_per_day=150)

        with patch.object(gate._analyzer, "analyze", AsyncMock(return_value=bad_result)), \
             patch.object(gate, "_check_kill_switch", return_value=False), \
             patch.object(gate, "_get_circuit_breaker_state", return_value=0.0), \
             patch.object(gate, "_check_exchange_health", return_value=(True, "All healthy")):

            result = await gate.evaluate(strategy_id="shadow_arb_v1")

        assert result.eligible is False
        assert len(result.block_reasons) >= 2

    @pytest.mark.asyncio
    async def test_gate_result_stored_as_latest(self) -> None:
        """latest_result is updated after evaluate()."""
        from src.modes.live_gate import LiveGate

        mock_pool = MagicMock()
        gate = LiveGate(pool=mock_pool, telegram=None, kill_switch=None)

        assert gate.latest_result is None
        assert gate.is_live_eligible() is False

        good_result = self._make_wf_result(sharpe=3.0, mdd=0.02, signals_per_day=150)

        with patch.object(gate._analyzer, "analyze", AsyncMock(return_value=good_result)), \
             patch.object(gate, "_check_kill_switch", return_value=False), \
             patch.object(gate, "_get_circuit_breaker_state", return_value=0.0), \
             patch.object(gate, "_check_exchange_health", return_value=(True, "All healthy")):

            await gate.evaluate(strategy_id="shadow_arb_v1")

        assert gate.latest_result is not None
        assert gate.is_live_eligible() is True


# ---------------------------------------------------------------------------
# PowerLawSlippage unit tests
# ---------------------------------------------------------------------------


class TestPowerLawSlippage:
    """Unit tests for the PowerLawSlippage model."""

    def test_buy_increases_price(self) -> None:
        model = PowerLawSlippage(k=1.0, gamma=0.5)
        base = Decimal("50000")
        fill = model.apply(base, OrderSide.BUY, Decimal("1.0"))
        assert fill > base, f"Expected fill > base for BUY, got {fill} vs {base}"

    def test_sell_decreases_price(self) -> None:
        model = PowerLawSlippage(k=1.0, gamma=0.5)
        base = Decimal("50000")
        fill = model.apply(base, OrderSide.SELL, Decimal("1.0"))
        assert fill < base, f"Expected fill < base for SELL, got {fill} vs {base}"

    def test_larger_size_more_slippage(self) -> None:
        """Larger order size leads to proportionally more slippage (power law)."""
        random.seed(0)
        model = PowerLawSlippage(k=1.0, gamma=0.5)
        base = Decimal("50000")

        # Take multiple samples to get stable means
        slippage_small: list[float] = []
        slippage_large: list[float] = []
        for _ in range(50):
            fill_small = model.apply(base, OrderSide.BUY, Decimal("0.01"))
            fill_large = model.apply(base, OrderSide.BUY, Decimal("10.0"))
            slippage_small.append(float(fill_small - base))
            slippage_large.append(float(fill_large - base))

        avg_small = sum(slippage_small) / len(slippage_small)
        avg_large = sum(slippage_large) / len(slippage_large)
        assert avg_large > avg_small, (
            f"Expected larger slippage for larger order: large={avg_large:.4f} small={avg_small:.4f}"
        )
