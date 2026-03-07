"""Unit tests for ShadowMode, PowerLawSlippage, and ShadowStats.

Covers:
- PowerLawSlippage formula correctness (k * size^gamma)
- Direction: BUY increases price, SELL decreases price
- Size scaling: larger size → proportionally more slippage
- ShadowMode lifecycle: start / stop
- ShadowMode._on_orderbook processes data and calls signal generator
- ShadowMode._execute_shadow_trade computes PnL and updates stats
- ShadowMode._compute_drawdown tracks peak and max drawdown correctly
- ShadowStats initialization and field updates
"""
from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import Order, OrderSide, OrderType, Signal, Trade
from src.execution.paper import PaperExecutor
from src.modes.shadow import PowerLawSlippage, ShadowMode, ShadowStats


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_signal(
    buy_price: Decimal = Decimal("50000"),
    sell_price: Decimal = Decimal("50100"),
    volume: Decimal = Decimal("0.1"),
) -> Signal:
    return Signal(
        strategy_id="shadow_arb_v1",
        symbol="BTC/USDT",
        buy_exchange="binance",
        sell_exchange="okx",
        buy_price=buy_price,
        sell_price=sell_price,
        spread_pct=Decimal("0.002"),
        confidence=0.9,
        volume=volume,
    )


def make_trade(
    side: OrderSide = OrderSide.BUY,
    price: Decimal = Decimal("50010"),
    amount: Decimal = Decimal("0.1"),
    fee: Decimal = Decimal("5.0"),
) -> Trade:
    return Trade(
        trade_id="test-trade-id",
        exchange_id="binance",
        symbol="BTC/USDT",
        side=side,
        price=price,
        amount=amount,
        fee=fee,
    )


def make_shadow_mode(
    signal_generator: MagicMock | None = None,
    paper_executor: PaperExecutor | None = None,
    telegram: MagicMock | None = None,
    market_recorder: MagicMock | None = None,
) -> ShadowMode:
    """Create a ShadowMode with all external dependencies mocked."""
    if signal_generator is None:
        signal_generator = MagicMock()
        signal_generator.on_orderbook_update = AsyncMock(return_value=None)

    collector_manager = MagicMock()
    collector_manager.start = AsyncMock()
    collector_manager.stop = AsyncMock()

    return ShadowMode(
        signal_generator=signal_generator,
        paper_executor=paper_executor,
        collector_manager=collector_manager,
        market_recorder=market_recorder,
        telegram=telegram,
        symbols=["BTC/USDT"],
    )


# ---------------------------------------------------------------------------
# PowerLawSlippage — formula correctness
# ---------------------------------------------------------------------------


class TestPowerLawSlippageFormula:
    def test_buy_order_increases_fill_price(self) -> None:
        """BUY orders receive adverse slippage: fill price > base price."""
        model = PowerLawSlippage(k=1.0, gamma=0.5)
        base = Decimal("50000")
        fill = model.apply(base, OrderSide.BUY, size=Decimal("1"))
        assert fill > base, f"Expected fill > {base}, got {fill}"

    def test_sell_order_decreases_fill_price(self) -> None:
        """SELL orders receive adverse slippage: fill price < base price."""
        model = PowerLawSlippage(k=1.0, gamma=0.5)
        base = Decimal("50000")
        fill = model.apply(base, OrderSide.SELL, size=Decimal("1"))
        assert fill < base, f"Expected fill < {base}, got {fill}"

    def test_larger_size_produces_more_slippage_buy(self) -> None:
        """Larger BUY size generates higher fill price (more adverse slippage)."""
        model = PowerLawSlippage(k=1.0, gamma=0.5)
        base = Decimal("50000")

        # Use fixed random seed to eliminate randomness for this comparison;
        # run many samples and verify the average is ordered correctly.
        import random

        small_fills = []
        large_fills = []
        rng = random.Random(42)
        with patch("random.uniform", side_effect=lambda lo, hi: rng.uniform(lo, hi)):
            for _ in range(100):
                small_fills.append(model.apply(base, OrderSide.BUY, size=Decimal("0.001")))
                large_fills.append(model.apply(base, OrderSide.BUY, size=Decimal("10.0")))

        avg_small = sum(small_fills) / len(small_fills)
        avg_large = sum(large_fills) / len(large_fills)
        assert avg_large > avg_small, (
            f"Expected larger size to produce more slippage on average: "
            f"large={avg_large}, small={avg_small}"
        )

    def test_larger_size_produces_more_slippage_sell(self) -> None:
        """Larger SELL size generates lower fill price (more adverse slippage)."""
        import random

        model = PowerLawSlippage(k=1.0, gamma=0.5)
        base = Decimal("50000")

        small_fills = []
        large_fills = []
        rng = random.Random(7)
        with patch("random.uniform", side_effect=lambda lo, hi: rng.uniform(lo, hi)):
            for _ in range(100):
                small_fills.append(model.apply(base, OrderSide.SELL, size=Decimal("0.001")))
                large_fills.append(model.apply(base, OrderSide.SELL, size=Decimal("10.0")))

        avg_small = sum(small_fills) / len(small_fills)
        avg_large = sum(large_fills) / len(large_fills)
        assert avg_large < avg_small, (
            f"Expected larger size to produce lower SELL fill price on average: "
            f"large={avg_large}, small={avg_small}"
        )

    def test_size_zero_point_001_vs_size_10_measurably_different(self) -> None:
        """size=0.001 vs size=10.0: average slippage must differ by at least 10x."""
        import random

        model = PowerLawSlippage(k=1.0, gamma=0.5)
        base = Decimal("50000")

        def avg_slippage_pct(size: Decimal, n: int = 200, seed: int = 0) -> float:
            rng = random.Random(seed)
            slippages = []
            with patch("random.uniform", side_effect=lambda lo, hi: rng.uniform(lo, hi)):
                for _ in range(n):
                    fill = model.apply(base, OrderSide.BUY, size=size)
                    slippages.append(float(abs(fill - base) / base))
            return sum(slippages) / len(slippages)

        slip_small = avg_slippage_pct(Decimal("0.001"))
        slip_large = avg_slippage_pct(Decimal("10.0"))

        # gamma=0.5: impact ratio = (10.0/0.001)^0.5 = sqrt(10000) = 100
        # So large should be ~100x the small slippage; require at least 10x.
        assert slip_large > slip_small * 10, (
            f"Expected large slippage >> small: large={slip_large:.6f}, small={slip_small:.6f}"
        )

    def test_k_parameter_scales_slippage(self) -> None:
        """Higher k produces proportionally more slippage."""
        import random

        base = Decimal("50000")
        size = Decimal("1")

        rng = random.Random(123)
        with patch("random.uniform", side_effect=lambda lo, hi: rng.uniform(lo, hi)):
            model_low_k = PowerLawSlippage(k=0.1, gamma=0.5)
            fill_low = model_low_k.apply(base, OrderSide.BUY, size=size)

        rng2 = random.Random(123)
        with patch("random.uniform", side_effect=lambda lo, hi: rng2.uniform(lo, hi)):
            model_high_k = PowerLawSlippage(k=5.0, gamma=0.5)
            fill_high = model_high_k.apply(base, OrderSide.BUY, size=size)

        assert fill_high > fill_low, (
            f"Higher k should produce larger fill price: k=5.0 gave {fill_high}, "
            f"k=0.1 gave {fill_low}"
        )

    def test_default_k_and_gamma_values(self) -> None:
        """Default parameters (k=1.0, gamma=0.5) produce valid output."""
        model = PowerLawSlippage()
        base = Decimal("100")
        fill = model.apply(base, OrderSide.BUY, size=Decimal("1"))
        assert fill > base

    def test_apply_returns_decimal(self) -> None:
        """apply() always returns a Decimal instance."""
        model = PowerLawSlippage(k=1.0, gamma=0.5)
        result = model.apply(Decimal("50000"), OrderSide.BUY, Decimal("1"))
        assert isinstance(result, Decimal)


# ---------------------------------------------------------------------------
# ShadowStats — initialization and field updates
# ---------------------------------------------------------------------------


class TestShadowStats:
    def test_initialization_sets_start_time(self) -> None:
        """ShadowStats initializes with a monotonic start_time."""
        before = time.monotonic()
        stats = ShadowStats(start_time=time.monotonic())
        after = time.monotonic()
        assert before <= stats.start_time <= after

    def test_initialization_zeros_all_counters(self) -> None:
        """All integer and float fields start at zero."""
        stats = ShadowStats(start_time=0.0)
        assert stats.signals_detected == 0
        assert stats.trades_executed == 0
        assert stats.trades_won == 0
        assert stats.trades_lost == 0
        assert stats.total_pnl == 0.0
        assert stats.peak_pnl == 0.0
        assert stats.max_drawdown == 0.0
        assert stats.last_daily_summary is None

    def test_fields_can_be_updated(self) -> None:
        """ShadowStats fields are mutable as expected."""
        stats = ShadowStats(start_time=0.0)
        stats.signals_detected = 5
        stats.trades_executed = 3
        stats.trades_won = 2
        stats.trades_lost = 1
        stats.total_pnl = 123.45
        assert stats.signals_detected == 5
        assert stats.trades_won == 2
        assert stats.total_pnl == 123.45


# ---------------------------------------------------------------------------
# ShadowMode._compute_drawdown
# ---------------------------------------------------------------------------


class TestComputeDrawdown:
    def test_no_drawdown_when_pnl_always_increases(self) -> None:
        """max_drawdown stays 0 when PnL only rises."""
        sm = make_shadow_mode()
        sm._stats.total_pnl = 100.0
        sm._compute_drawdown()
        assert sm._stats.peak_pnl == 100.0
        assert sm._stats.max_drawdown == 0.0

    def test_drawdown_computed_as_absolute_usd(self) -> None:
        """Drawdown = peak - current (absolute USD, not fraction)."""
        sm = make_shadow_mode()

        # Drive PnL to 100, then drop to 80
        sm._stats.total_pnl = 100.0
        sm._compute_drawdown()
        sm._stats.total_pnl = 80.0
        sm._compute_drawdown()

        expected_dd = 100.0 - 80.0  # 20.0 absolute USD
        assert abs(sm._stats.max_drawdown - expected_dd) < 1e-9

    def test_max_drawdown_tracks_worst_case(self) -> None:
        """max_drawdown records the worst drawdown seen, not the latest."""
        sm = make_shadow_mode()

        # Peak at 100, drop to 50 (50 USD DD), then recover to 90
        sm._stats.total_pnl = 100.0
        sm._compute_drawdown()
        sm._stats.total_pnl = 50.0
        sm._compute_drawdown()
        # Recover partially
        sm._stats.total_pnl = 90.0
        sm._compute_drawdown()

        assert abs(sm._stats.max_drawdown - 50.0) < 1e-9

    def test_drawdown_absolute_when_peak_is_zero_and_pnl_negative(self) -> None:
        """When peak=0 and PnL goes negative, drawdown = abs(pnl)."""
        sm = make_shadow_mode()
        sm._stats.total_pnl = -50.0
        sm._compute_drawdown()
        # peak stays 0, drawdown = abs(-50) = 50.0
        assert sm._stats.max_drawdown == 50.0

    def test_drawdown_zero_when_pnl_and_peak_both_zero(self) -> None:
        """No drawdown when PnL and peak are both zero."""
        sm = make_shadow_mode()
        sm._stats.total_pnl = 0.0
        sm._compute_drawdown()
        assert sm._stats.max_drawdown == 0.0


# ---------------------------------------------------------------------------
# ShadowMode lifecycle: start / stop
# ---------------------------------------------------------------------------


class TestShadowModeLifecycle:
    @pytest.mark.asyncio
    async def test_start_sets_running_flag(self) -> None:
        """start() sets _running to True."""
        sm = make_shadow_mode()
        await sm.start()
        assert sm._running is True
        await sm.stop()

    @pytest.mark.asyncio
    async def test_stop_clears_running_flag(self) -> None:
        """stop() sets _running to False."""
        sm = make_shadow_mode()
        await sm.start()
        await sm.stop()
        assert sm._running is False

    @pytest.mark.asyncio
    async def test_start_calls_collector_manager_start(self) -> None:
        """start() delegates to collector_manager.start()."""
        sm = make_shadow_mode()
        await sm.start()
        sm._collector_manager.start.assert_awaited_once()
        await sm.stop()

    @pytest.mark.asyncio
    async def test_stop_calls_collector_manager_stop(self) -> None:
        """stop() delegates to collector_manager.stop()."""
        sm = make_shadow_mode()
        await sm.start()
        await sm.stop()
        sm._collector_manager.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_sends_telegram_notification(self) -> None:
        """start() notifies Telegram when alerter is configured."""
        telegram = MagicMock()
        telegram.send_alert = AsyncMock()
        sm = make_shadow_mode(telegram=telegram)
        await sm.start()
        telegram.send_alert.assert_awaited_once()
        await sm.stop()

    @pytest.mark.asyncio
    async def test_double_start_is_idempotent(self) -> None:
        """Calling start() twice does not raise and does not restart collectors."""
        sm = make_shadow_mode()
        await sm.start()
        await sm.start()  # second call should be a no-op
        # Collector start called only once
        assert sm._collector_manager.start.await_count == 1
        await sm.stop()

    @pytest.mark.asyncio
    async def test_stop_before_start_does_not_raise(self) -> None:
        """stop() on an unstarted ShadowMode completes without error."""
        sm = make_shadow_mode()
        await sm.stop()  # should not raise

    @pytest.mark.asyncio
    async def test_start_resets_stats(self) -> None:
        """start() resets ShadowStats to a fresh state."""
        sm = make_shadow_mode()
        sm._stats.signals_detected = 99
        await sm.start()
        assert sm._stats.signals_detected == 0
        await sm.stop()


# ---------------------------------------------------------------------------
# ShadowMode._on_orderbook
# ---------------------------------------------------------------------------


class TestOnOrderbook:
    @pytest.mark.asyncio
    async def test_on_orderbook_calls_signal_generator(self) -> None:
        """_on_orderbook feeds the orderbook to the signal generator."""
        signal_generator = MagicMock()
        signal_generator.on_orderbook_update = AsyncMock(return_value=None)
        sm = make_shadow_mode(signal_generator=signal_generator)
        sm._running = True

        bids = [["50000", "1.0"], ["49999", "2.0"]]
        asks = [["50001", "1.0"], ["50002", "2.0"]]
        await sm._on_orderbook("binance", "BTC/USDT", bids, asks)

        signal_generator.on_orderbook_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_on_orderbook_skipped_when_not_running(self) -> None:
        """_on_orderbook returns immediately when _running is False."""
        signal_generator = MagicMock()
        signal_generator.on_orderbook_update = AsyncMock(return_value=None)
        sm = make_shadow_mode(signal_generator=signal_generator)
        sm._running = False  # not running

        bids = [["50000", "1.0"]]
        asks = [["50001", "1.0"]]
        await sm._on_orderbook("binance", "BTC/USDT", bids, asks)

        signal_generator.on_orderbook_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_on_orderbook_executes_trade_on_signal(self) -> None:
        """When signal generator emits a signal, _execute_shadow_trade is called."""
        signal = make_signal()
        signal_generator = MagicMock()
        signal_generator.on_orderbook_update = AsyncMock(return_value=signal)

        buy_trade = make_trade(side=OrderSide.BUY, price=Decimal("50010"), fee=Decimal("5.0"))
        sell_trade = make_trade(side=OrderSide.SELL, price=Decimal("50090"), fee=Decimal("5.0"))
        paper_executor = MagicMock()
        paper_executor.execute = AsyncMock(side_effect=[buy_trade, sell_trade])

        sm = make_shadow_mode(signal_generator=signal_generator, paper_executor=paper_executor)
        sm._running = True

        bids = [["50000", "1.0"]]
        asks = [["50001", "1.0"]]
        await sm._on_orderbook("binance", "BTC/USDT", bids, asks)

        assert paper_executor.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_on_orderbook_stores_book_in_registry(self) -> None:
        """_on_orderbook stores the updated OrderBook in _books registry."""
        sm = make_shadow_mode()
        sm._running = True

        bids = [["50000", "1.0"]]
        asks = [["50001", "1.0"]]
        await sm._on_orderbook("binance", "BTC/USDT", bids, asks)

        assert "BTC/USDT" in sm._books
        assert "binance" in sm._books["BTC/USDT"]

    @pytest.mark.asyncio
    async def test_on_orderbook_does_not_raise_on_signal_generator_error(self) -> None:
        """Exceptions in signal generator are swallowed, not propagated."""
        signal_generator = MagicMock()
        signal_generator.on_orderbook_update = AsyncMock(
            side_effect=RuntimeError("signal error")
        )
        sm = make_shadow_mode(signal_generator=signal_generator)
        sm._running = True

        # Should NOT raise
        await sm._on_orderbook("binance", "BTC/USDT", [["50000", "1.0"]], [["50001", "1.0"]])


# ---------------------------------------------------------------------------
# ShadowMode._execute_shadow_trade
# ---------------------------------------------------------------------------


class TestExecuteShadowTrade:
    @pytest.mark.asyncio
    async def test_execute_shadow_trade_increments_signals_detected(self) -> None:
        """Each call to _execute_shadow_trade increments signals_detected."""
        buy_trade = make_trade(side=OrderSide.BUY, price=Decimal("50010"), fee=Decimal("5"))
        sell_trade = make_trade(side=OrderSide.SELL, price=Decimal("50090"), fee=Decimal("5"))
        paper_executor = MagicMock()
        paper_executor.execute = AsyncMock(side_effect=[buy_trade, sell_trade])

        sm = make_shadow_mode(paper_executor=paper_executor)
        signal = make_signal()
        await sm._execute_shadow_trade(signal)

        assert sm._stats.signals_detected == 1

    @pytest.mark.asyncio
    async def test_execute_shadow_trade_increments_trades_executed(self) -> None:
        """Successful execution increments trades_executed."""
        buy_trade = make_trade(side=OrderSide.BUY, price=Decimal("50010"), fee=Decimal("5"))
        sell_trade = make_trade(side=OrderSide.SELL, price=Decimal("50090"), fee=Decimal("5"))
        paper_executor = MagicMock()
        paper_executor.execute = AsyncMock(side_effect=[buy_trade, sell_trade])

        sm = make_shadow_mode(paper_executor=paper_executor)
        await sm._execute_shadow_trade(make_signal())

        assert sm._stats.trades_executed == 1

    @pytest.mark.asyncio
    async def test_execute_shadow_trade_winning_trade_increments_trades_won(self) -> None:
        """A positive PnL trade increments trades_won."""
        # sell proceeds > buy cost → net win
        buy_trade = make_trade(
            side=OrderSide.BUY, price=Decimal("50000"), amount=Decimal("0.1"), fee=Decimal("5")
        )
        sell_trade = make_trade(
            side=OrderSide.SELL, price=Decimal("50200"), amount=Decimal("0.1"), fee=Decimal("5")
        )
        paper_executor = MagicMock()
        paper_executor.execute = AsyncMock(side_effect=[buy_trade, sell_trade])

        sm = make_shadow_mode(paper_executor=paper_executor)
        await sm._execute_shadow_trade(make_signal())

        assert sm._stats.trades_won == 1
        assert sm._stats.trades_lost == 0

    @pytest.mark.asyncio
    async def test_execute_shadow_trade_losing_trade_increments_trades_lost(self) -> None:
        """A negative PnL trade increments trades_lost."""
        # buy cost > sell proceeds → net loss
        buy_trade = make_trade(
            side=OrderSide.BUY, price=Decimal("50200"), amount=Decimal("0.1"), fee=Decimal("50")
        )
        sell_trade = make_trade(
            side=OrderSide.SELL, price=Decimal("50000"), amount=Decimal("0.1"), fee=Decimal("50")
        )
        paper_executor = MagicMock()
        paper_executor.execute = AsyncMock(side_effect=[buy_trade, sell_trade])

        sm = make_shadow_mode(paper_executor=paper_executor)
        await sm._execute_shadow_trade(make_signal())

        assert sm._stats.trades_lost == 1
        assert sm._stats.trades_won == 0

    @pytest.mark.asyncio
    async def test_execute_shadow_trade_updates_total_pnl(self) -> None:
        """total_pnl is updated after each trade."""
        buy_trade = make_trade(
            side=OrderSide.BUY, price=Decimal("50000"), amount=Decimal("0.1"), fee=Decimal("5")
        )
        sell_trade = make_trade(
            side=OrderSide.SELL, price=Decimal("50200"), amount=Decimal("0.1"), fee=Decimal("5")
        )
        paper_executor = MagicMock()
        paper_executor.execute = AsyncMock(side_effect=[buy_trade, sell_trade])

        sm = make_shadow_mode(paper_executor=paper_executor)
        await sm._execute_shadow_trade(make_signal())

        # sell_proceeds - sell_fee - buy_cost - buy_fee
        # = (50200 * 0.1 - 5) - (50000 * 0.1 + 5)
        # = (5020 - 5) - (5000 + 5) = 5015 - 5005 = 10.0
        assert abs(sm._stats.total_pnl - 10.0) < 1e-6

    @pytest.mark.asyncio
    async def test_execute_shadow_trade_does_not_raise_on_executor_error(self) -> None:
        """If paper_executor raises, _execute_shadow_trade catches and returns."""
        paper_executor = MagicMock()
        paper_executor.execute = AsyncMock(side_effect=RuntimeError("execution error"))

        sm = make_shadow_mode(paper_executor=paper_executor)
        # Should NOT raise
        await sm._execute_shadow_trade(make_signal())

        # trades_executed must NOT be incremented on failure
        assert sm._stats.trades_executed == 0

    @pytest.mark.asyncio
    async def test_execute_shadow_trade_records_to_market_recorder(self) -> None:
        """If market_recorder is provided, record_execution is called."""
        buy_trade = make_trade(
            side=OrderSide.BUY, price=Decimal("50010"), amount=Decimal("0.1"), fee=Decimal("5")
        )
        sell_trade = make_trade(
            side=OrderSide.SELL, price=Decimal("50090"), amount=Decimal("0.1"), fee=Decimal("5")
        )
        paper_executor = MagicMock()
        paper_executor.execute = AsyncMock(side_effect=[buy_trade, sell_trade])

        market_recorder = MagicMock()
        market_recorder.record_execution = MagicMock()

        sm = make_shadow_mode(
            paper_executor=paper_executor, market_recorder=market_recorder
        )
        await sm._execute_shadow_trade(make_signal())

        market_recorder.record_execution.assert_called_once()


# ---------------------------------------------------------------------------
# ShadowMode._on_orderbook — market recorder orderbook recording
# ---------------------------------------------------------------------------


class TestOnOrderbookMarketRecorder:
    @pytest.mark.asyncio
    async def test_on_orderbook_records_orderbook_when_recorder_provided(self) -> None:
        """_on_orderbook calls market_recorder.record_orderbook when best bid/ask exist."""
        market_recorder = MagicMock()
        market_recorder.record_orderbook = MagicMock()

        sm = make_shadow_mode(market_recorder=market_recorder)
        sm._running = True

        bids = [["50000", "1.0"], ["49999", "0.5"]]
        asks = [["50001", "1.0"], ["50002", "0.5"]]
        await sm._on_orderbook("binance", "BTC/USDT", bids, asks)

        market_recorder.record_orderbook.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_orderbook_skips_recording_when_no_recorder(self) -> None:
        """_on_orderbook does not error when market_recorder is None."""
        sm = make_shadow_mode(market_recorder=None)
        sm._running = True

        bids = [["50000", "1.0"]]
        asks = [["50001", "1.0"]]
        # Should not raise even with no recorder
        await sm._on_orderbook("binance", "BTC/USDT", bids, asks)

    @pytest.mark.asyncio
    async def test_on_orderbook_notifies_telegram_on_signal(self) -> None:
        """When a signal is emitted, Telegram is notified."""
        signal = make_signal()
        signal_generator = MagicMock()
        signal_generator.on_orderbook_update = AsyncMock(return_value=signal)

        buy_trade = make_trade(side=OrderSide.BUY, price=Decimal("50010"), fee=Decimal("5"))
        sell_trade = make_trade(side=OrderSide.SELL, price=Decimal("50090"), fee=Decimal("5"))
        paper_executor = MagicMock()
        paper_executor.execute = AsyncMock(side_effect=[buy_trade, sell_trade])

        telegram = MagicMock()
        telegram.send_signal_found = AsyncMock()

        sm = make_shadow_mode(
            signal_generator=signal_generator,
            paper_executor=paper_executor,
            telegram=telegram,
        )
        sm._running = True

        await sm._on_orderbook("binance", "BTC/USDT", [["50000", "1.0"]], [["50001", "1.0"]])

        telegram.send_signal_found.assert_awaited_once_with(signal)


# ---------------------------------------------------------------------------
# ShadowMode._send_summary
# ---------------------------------------------------------------------------


class TestSendSummary:
    @pytest.mark.asyncio
    async def test_send_summary_without_telegram_does_not_raise(self) -> None:
        """_send_summary completes without error when telegram is None."""
        sm = make_shadow_mode(telegram=None)
        sm._stats.trades_executed = 5
        sm._stats.trades_won = 3
        sm._stats.total_pnl = 15.0
        # Should not raise
        await sm._send_summary()

    @pytest.mark.asyncio
    async def test_send_summary_calls_telegram_send_daily_summary(self) -> None:
        """_send_summary calls telegram.send_daily_summary with correct fields."""
        telegram = MagicMock()
        telegram.send_daily_summary = AsyncMock()

        sm = make_shadow_mode(telegram=telegram)
        sm._stats.trades_executed = 10
        sm._stats.trades_won = 7
        sm._stats.total_pnl = 42.5
        sm._stats.max_drawdown = 0.05

        await sm._send_summary()

        telegram.send_daily_summary.assert_awaited_once()
        call_kwargs = telegram.send_daily_summary.call_args[0][0]
        assert call_kwargs["trades"] == 10
        assert abs(call_kwargs["win_rate"] - 0.7) < 1e-9
        assert call_kwargs["total_pnl"] == 42.5

    @pytest.mark.asyncio
    async def test_send_summary_win_rate_zero_when_no_trades(self) -> None:
        """_send_summary computes win_rate=0 when no trades executed."""
        telegram = MagicMock()
        telegram.send_daily_summary = AsyncMock()

        sm = make_shadow_mode(telegram=telegram)
        sm._stats.trades_executed = 0

        await sm._send_summary()

        call_kwargs = telegram.send_daily_summary.call_args[0][0]
        assert call_kwargs["win_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_send_summary_updates_last_daily_summary_timestamp(self) -> None:
        """_send_summary sets stats.last_daily_summary after successful send."""
        telegram = MagicMock()
        telegram.send_daily_summary = AsyncMock()

        sm = make_shadow_mode(telegram=telegram)
        assert sm._stats.last_daily_summary is None

        await sm._send_summary()

        assert sm._stats.last_daily_summary is not None


# ---------------------------------------------------------------------------
# ShadowMode.stop — Telegram final summary
# ---------------------------------------------------------------------------


class TestShadowModeStopSendsFinalSummary:
    @pytest.mark.asyncio
    async def test_stop_sends_final_summary_via_telegram(self) -> None:
        """stop() triggers a final Telegram summary when telegram is configured."""
        telegram = MagicMock()
        telegram.send_alert = AsyncMock()
        telegram.send_daily_summary = AsyncMock()

        sm = make_shadow_mode(telegram=telegram)
        await sm.start()
        await sm.stop()

        telegram.send_daily_summary.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_does_not_raise_when_telegram_send_fails(self) -> None:
        """stop() swallows Telegram errors in the final summary."""
        telegram = MagicMock()
        telegram.send_alert = AsyncMock()
        telegram.send_daily_summary = AsyncMock(side_effect=RuntimeError("network down"))

        sm = make_shadow_mode(telegram=telegram)
        await sm.start()
        # Must not raise
        await sm.stop()
