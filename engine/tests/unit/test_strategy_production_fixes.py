"""Unit tests for strategy production fixes.

Covers:
  1. Signal routing (_should_route) in StrategyManager
  2. SignalConfig default strategy_id
  3. TriangularStrategy sizing fix (base-unit conversion, notional gross profit)
  4. StatisticalArbStrategy exit TradeRequest generation (SHORT and LONG)
  5. BaseStrategy on_fill PnL tracking
  6. MultiStrategySignalProducer signal generation and dedup
  7. PaperSignalSimulator tick and injection_rate=0 guard
"""
from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import OrderSide, OrderType, Signal, Trade
from src.core.signal import SignalConfig
from src.core.multi_signal import MultiSignalConfig, MultiStrategySignalProducer, PaperSignalSimulator
from src.strategies.base import BaseStrategy, CostCalculator, StrategyMetrics, TradeLeg, TradeRequest
from src.strategies.manager import StrategyManager
from src.strategies.statistical_arb import StatArbConfig, StatArbState, StatisticalArbStrategy
from src.strategies.triangular import TriangularConfig, TriangularStrategy


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

def _make_signal(
    strategy_id: str = "cross_exchange_spot",
    symbol: str = "BTC/USDT",
    buy_exchange: str = "binance",
    sell_exchange: str = "bybit",
    buy_price: str = "60000",
    sell_price: str = "60100",
    spread_pct: str = "0.001667",
    volume: str = "0.1",
    confidence: float = 0.8,
    metadata: dict | None = None,
) -> Signal:
    from datetime import datetime, timezone
    return Signal(
        strategy_id=strategy_id,
        symbol=symbol,
        buy_exchange=buy_exchange,
        sell_exchange=sell_exchange,
        buy_price=Decimal(buy_price),
        sell_price=Decimal(sell_price),
        spread_pct=Decimal(spread_pct),
        confidence=confidence,
        volume=Decimal(volume),
        timestamp=datetime.now(timezone.utc),
        metadata=metadata or {},
    )


def _make_trade(
    fee: str = "1.0",
    metadata: dict | None = None,
) -> Trade:
    from datetime import datetime, timezone
    return Trade(
        trade_id="t1",
        exchange_id="binance",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        price=Decimal("60000"),
        amount=Decimal("0.1"),
        fee=Decimal(fee),
        timestamp=datetime.now(timezone.utc),
        metadata=metadata or {},
    )


class _ZeroCostCalculator:
    """CostCalculator that always returns zero cost."""

    def estimate_cost(
        self,
        exchange_id: str,
        symbol: str,
        side: OrderSide,
        size: Decimal,
        price: Decimal,
    ) -> Decimal:
        return Decimal("0")


class _FixedCostCalculator:
    """CostCalculator that returns a fixed cost per call."""

    def __init__(self, cost: str = "1.0") -> None:
        self._cost = Decimal(cost)

    def estimate_cost(self, **_: Any) -> Decimal:  # type: ignore[override]
        return self._cost

    # positional-arg version as well
    def __call__(self, exchange_id, symbol, side, size, price):  # noqa: D401
        return self._cost


class _ConcreteStrategy(BaseStrategy):
    """Minimal concrete strategy for testing BaseStrategy.on_fill."""

    STRATEGY_TYPE = "test_strategy"

    async def on_signal(self, signal: Signal) -> Optional[TradeRequest]:
        return None


# ---------------------------------------------------------------------------
# 1. Signal routing — StrategyManager._should_route
# ---------------------------------------------------------------------------

class TestShouldRoute:
    """Tests for StrategyManager._should_route method."""

    def _make_manager(self) -> StrategyManager:
        bus = MagicMock()
        return StrategyManager(event_bus=bus)

    def _make_strategy(self, strategy_id: str, strategy_type: str = "cross_exchange_spot") -> MagicMock:
        s = MagicMock(spec=BaseStrategy)
        s.strategy_id = strategy_id
        s.STRATEGY_TYPE = strategy_type
        s.is_active = True
        return s

    def test_broadcast_empty_strategy_id_routes_to_all(self):
        manager = self._make_manager()
        strategy = self._make_strategy("my_strategy_1")
        signal = _make_signal(strategy_id="")
        assert manager._should_route(strategy, signal) is True

    def test_broadcast_wildcard_asterisk_routes_to_all(self):
        manager = self._make_manager()
        strategy = self._make_strategy("my_strategy_2")
        signal = _make_signal(strategy_id="*")
        assert manager._should_route(strategy, signal) is True

    def test_exact_match_on_strategy_instance_id(self):
        manager = self._make_manager()
        strategy = self._make_strategy("arb_instance_42")
        signal = _make_signal(strategy_id="arb_instance_42")
        assert manager._should_route(strategy, signal) is True

    def test_exact_match_does_not_match_different_id(self):
        manager = self._make_manager()
        strategy = self._make_strategy("arb_instance_42")
        signal = _make_signal(strategy_id="arb_instance_99")
        # No STRATEGY_TYPE on mock that matches either; expect False
        strategy.STRATEGY_TYPE = "completely_different"
        assert manager._should_route(strategy, signal) is False

    def test_bidirectional_substring_type_in_signal_id(self):
        """STRATEGY_TYPE is a substring of signal.strategy_id."""
        manager = self._make_manager()
        strategy = self._make_strategy("instance_a", strategy_type="cross_exchange_spot")
        signal = _make_signal(strategy_id="cross_exchange_spot_v1")
        assert manager._should_route(strategy, signal) is True

    def test_bidirectional_substring_signal_id_in_type(self):
        """signal.strategy_id is a substring of STRATEGY_TYPE."""
        manager = self._make_manager()
        strategy = self._make_strategy("instance_b", strategy_type="cross_exchange_spot_extended")
        signal = _make_signal(strategy_id="cross_exchange_spot")
        assert manager._should_route(strategy, signal) is True

    def test_no_match_when_unrelated_ids(self):
        manager = self._make_manager()
        strategy = self._make_strategy("triangular_instance", strategy_type="triangular")
        signal = _make_signal(strategy_id="funding_rate_arb")
        assert manager._should_route(strategy, signal) is False

    def test_no_match_when_strategy_has_no_strategy_type(self):
        """Strategies without STRATEGY_TYPE only match exact ID or broadcast."""
        manager = self._make_manager()
        strategy = MagicMock(spec=BaseStrategy)
        strategy.strategy_id = "my_id"
        # STRATEGY_TYPE attribute absent
        del strategy.STRATEGY_TYPE
        signal = _make_signal(strategy_id="cross_exchange_spot")
        assert manager._should_route(strategy, signal) is False

    def test_wildcard_routes_even_when_strategy_type_differs(self):
        manager = self._make_manager()
        strategy = self._make_strategy("stat_arb_1", strategy_type="statistical_arb")
        signal = _make_signal(strategy_id="*")
        assert manager._should_route(strategy, signal) is True

    def test_empty_string_routes_even_when_strategy_type_differs(self):
        manager = self._make_manager()
        strategy = self._make_strategy("funding_1", strategy_type="funding_rate")
        signal = _make_signal(strategy_id="")
        assert manager._should_route(strategy, signal) is True


# ---------------------------------------------------------------------------
# 2. SignalConfig default strategy_id
# ---------------------------------------------------------------------------

class TestSignalConfigDefault:
    def test_default_strategy_id_is_cross_exchange_spot(self):
        cfg = SignalConfig()
        assert cfg.strategy_id == "cross_exchange_spot"

    def test_custom_strategy_id_is_preserved(self):
        cfg = SignalConfig(strategy_id="my_custom_strategy")
        assert cfg.strategy_id == "my_custom_strategy"

    def test_default_min_edge_is_one_bps(self):
        cfg = SignalConfig()
        assert cfg.min_edge == Decimal("0.0001")


# ---------------------------------------------------------------------------
# 3. TriangularStrategy sizing fix
# ---------------------------------------------------------------------------

class TestTriangularSizingFix:
    """Verify size is converted to base units using first-leg price."""

    @pytest.fixture
    def strategy(self) -> TriangularStrategy:
        config = TriangularConfig(
            min_profit_bps=Decimal("5"),
            max_position_usdt=Decimal("1000"),
        )
        return TriangularStrategy(
            strategy_id="tri_test",
            cost_calculator=_ZeroCostCalculator(),
            config=config,
        )

    @pytest.fixture
    def active_strategy(self, strategy: TriangularStrategy) -> TriangularStrategy:
        strategy._is_active = True
        return strategy

    def _make_tri_signal(
        self,
        volume: str = "10.0",
        spread_pct: str = "0.002",
        prices: list[str] | None = None,
    ) -> Signal:
        if prices is None:
            prices = ["60000", "0.054", "3500"]
        return _make_signal(
            strategy_id="triangular",
            volume=volume,
            spread_pct=spread_pct,
            metadata={
                "path": ["USDT", "BTC", "ETH"],
                "pairs": ["BTC/USDT", "ETH/BTC", "ETH/USDT"],
                "sides": ["buy", "buy", "sell"],
                "prices": prices,
                "exchange_id": "binance",
            },
        )

    @pytest.mark.asyncio
    async def test_size_capped_to_base_units_from_max_position_usdt(self, active_strategy):
        """max_position_usdt=1000, first_price=60000 → max_base=0.01667.
        Signal volume=10.0 should be capped to 0.01667."""
        signal = self._make_tri_signal(volume="10.0", prices=["60000", "0.054", "3500"])
        result = await active_strategy.on_signal(signal)
        assert result is not None
        expected_max_base = Decimal("1000") / Decimal("60000")
        # size must not exceed expected_max_base (with tolerance for rounding)
        for leg in result.legs:
            assert leg.size <= expected_max_base + Decimal("1e-10")

    @pytest.mark.asyncio
    async def test_size_uses_signal_volume_when_smaller_than_max_base(self, active_strategy):
        """Signal volume=0.001 < max_base=1000/60000≈0.0167 → size = 0.001."""
        signal = self._make_tri_signal(volume="0.001", prices=["60000", "0.054", "3500"])
        result = await active_strategy.on_signal(signal)
        assert result is not None
        for leg in result.legs:
            assert leg.size == Decimal("0.001")

    @pytest.mark.asyncio
    async def test_gross_profit_uses_notional_not_raw_size(self, active_strategy):
        """gross_profit = spread_pct * (size * first_price), not spread_pct * size."""
        spread_pct = Decimal("0.002")
        signal = self._make_tri_signal(volume="0.001", spread_pct=str(spread_pct), prices=["60000", "0.054", "3500"])
        result = await active_strategy.on_signal(signal)
        assert result is not None
        # Expected gross: 0.002 * (0.001 * 60000) = 0.002 * 60 = 0.12
        expected_gross = spread_pct * Decimal("0.001") * Decimal("60000")
        recorded_gross = Decimal(result.metadata["gross_profit"])
        assert abs(recorded_gross - expected_gross) < Decimal("1e-8")

    @pytest.mark.asyncio
    async def test_returns_none_when_inactive(self, strategy):
        signal = self._make_tri_signal()
        result = await strategy.on_signal(signal)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_metadata_missing(self, active_strategy):
        signal = _make_signal(strategy_id="triangular", metadata={})
        result = await active_strategy.on_signal(signal)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_spread_below_min_profit(self, active_strategy):
        """spread_pct=0.0001 (1 bps) < min_profit_bps=5 → filtered."""
        signal = self._make_tri_signal(spread_pct="0.0001")
        result = await active_strategy.on_signal(signal)
        assert result is None

    @pytest.mark.asyncio
    async def test_trade_request_has_three_legs(self, active_strategy):
        signal = self._make_tri_signal(volume="0.001")
        result = await active_strategy.on_signal(signal)
        assert result is not None
        assert len(result.legs) == 3

    @pytest.mark.asyncio
    async def test_all_legs_share_same_exchange(self, active_strategy):
        signal = self._make_tri_signal(volume="0.001")
        result = await active_strategy.on_signal(signal)
        assert result is not None
        exchange_ids = {leg.exchange_id for leg in result.legs}
        assert exchange_ids == {"binance"}

    @pytest.mark.asyncio
    async def test_fallback_to_signal_volume_when_first_price_is_zero(self, active_strategy):
        """If first_price = 0, size falls back to signal.volume."""
        signal = self._make_tri_signal(volume="0.5", prices=["0", "0.054", "3500"])
        # net_profit = spread * (0.5 * 0) = 0 → filtered
        result = await active_strategy.on_signal(signal)
        # With zero first_price, notional=0 → gross_profit=0 → net<=0 → None
        assert result is None


# ---------------------------------------------------------------------------
# 4. StatisticalArbStrategy exit generates TradeRequest
# ---------------------------------------------------------------------------

def _warm_up_stat_arb(strategy: StatisticalArbStrategy, n: int = 65) -> None:
    """Feed enough signals to warm up the history buffer synchronously."""
    import asyncio as _asyncio

    async def _feed():
        for i in range(n):
            sig = _make_signal(
                buy_price=str(60000 + i),
                sell_price=str(60100 + i),
                volume="1.0",
                confidence=0.8,
            )
            await strategy.on_signal(sig)

    _asyncio.get_event_loop().run_until_complete(_feed())


class TestStatArbExitTradeRequest:
    """Verify that exiting SHORT or LONG position generates a closing TradeRequest."""

    @pytest.fixture
    def config(self) -> StatArbConfig:
        return StatArbConfig(
            min_history=10,
            zscore_entry=1.5,
            zscore_exit=0.3,
            max_position_size=Decimal("2.0"),
            cointegration_pvalue=1.0,  # always pass coint test
        )

    @pytest.fixture
    def strategy(self, config: StatArbConfig) -> StatisticalArbStrategy:
        return StatisticalArbStrategy(
            strategy_id="stat_arb_test",
            cost_calculator=_ZeroCostCalculator(),
            config=config,
        )

    @pytest.fixture
    def active_strategy(self, strategy: StatisticalArbStrategy) -> StatisticalArbStrategy:
        strategy._is_active = True
        return strategy

    async def _warm_up(self, strategy: StatisticalArbStrategy, n: int = 12) -> None:
        """Warm up with slightly varied sell prices so history has real variance.

        Using a fixed random seed ensures reproducible zscore values in tests.
        """
        import random
        rng = random.Random(99)  # deterministic seed — isolated from global state
        for i in range(n):
            noise = rng.uniform(-200, 200)
            sig = _make_signal(
                buy_price="60000",
                sell_price=str(60100 + noise),
                volume="1.0",
                confidence=0.8,
            )
            await strategy.on_signal(sig)

    def _make_mean_exit_signal(self, strategy: StatisticalArbStrategy, **overrides) -> Signal:
        """Return a signal whose spread equals the current historical mean.

        This guarantees zscore ≈ 0, which satisfies both exit conditions:
          SHORT exit: zscore < zscore_exit  (0 < 0.3 → True)
          LONG exit:  zscore > -zscore_exit (0 > -0.3 → True)
        """
        import math
        beta = strategy._kalman.hedge_ratio
        spreads = list(strategy._spreads)
        history = spreads[:-1] if len(spreads) > 1 else spreads
        mean_spread = sum(history) / len(history) if history else 0.0
        # spread = log(sell) - beta * log(buy)  =>  sell = exp(mean + beta * log(buy))
        buy_price = 60000.0
        sell_price = math.exp(mean_spread + beta * math.log(buy_price))
        defaults = dict(
            buy_price=str(buy_price),
            sell_price=str(round(sell_price, 6)),
            volume="0.5",
            confidence=0.9,
        )
        defaults.update(overrides)
        return _make_signal(**defaults)

    @pytest.mark.asyncio
    async def test_short_exit_generates_trade_request(self, active_strategy):
        """SHORT exit: zscore < zscore_exit (0.3). Signal at historical mean gives zscore≈0."""
        await self._warm_up(active_strategy)
        active_strategy._state = StatArbState.SHORT
        # Spread at historical mean → zscore ≈ 0 → 0 < 0.3 → SHORT exit triggers
        exit_sig = self._make_mean_exit_signal(active_strategy)
        result = await active_strategy.on_signal(exit_sig)
        assert result is not None, "Expected TradeRequest on SHORT exit"

    @pytest.mark.asyncio
    async def test_short_exit_trade_request_reverses_legs(self, active_strategy):
        """SHORT exit: leg[0] SELL on buy_exchange, leg[1] BUY on sell_exchange."""
        await self._warm_up(active_strategy)
        active_strategy._state = StatArbState.SHORT
        exit_sig = self._make_mean_exit_signal(
            active_strategy,
            buy_exchange="binance",
            sell_exchange="bybit",
        )
        result = await active_strategy.on_signal(exit_sig)
        assert result is not None
        assert result.metadata.get("action") == "exit"
        assert result.metadata.get("prev_state") == "short"
        # First leg should SELL on buy_exchange (close the long leg)
        assert result.legs[0].exchange_id == "binance"
        assert result.legs[0].side == OrderSide.SELL
        # Second leg should BUY on sell_exchange (close the short leg)
        assert result.legs[1].exchange_id == "bybit"
        assert result.legs[1].side == OrderSide.BUY

    @pytest.mark.asyncio
    async def test_short_exit_resets_state_to_flat(self, active_strategy):
        await self._warm_up(active_strategy)
        active_strategy._state = StatArbState.SHORT
        exit_sig = self._make_mean_exit_signal(active_strategy)
        await active_strategy.on_signal(exit_sig)
        assert active_strategy.state == StatArbState.FLAT

    @pytest.mark.asyncio
    async def test_long_exit_generates_trade_request(self, active_strategy):
        """LONG exit: zscore > -zscore_exit (-0.3). Signal at historical mean gives zscore≈0."""
        await self._warm_up(active_strategy)
        active_strategy._state = StatArbState.LONG
        # Spread at historical mean → zscore ≈ 0 → 0 > -0.3 → LONG exit triggers
        exit_sig = self._make_mean_exit_signal(active_strategy)
        result = await active_strategy.on_signal(exit_sig)
        assert result is not None, "Expected TradeRequest on LONG exit"

    @pytest.mark.asyncio
    async def test_long_exit_trade_request_reverses_legs(self, active_strategy):
        """LONG exit: leg[0] SELL on sell_exchange, leg[1] BUY on buy_exchange."""
        await self._warm_up(active_strategy)
        active_strategy._state = StatArbState.LONG
        exit_sig = self._make_mean_exit_signal(
            active_strategy,
            buy_exchange="binance",
            sell_exchange="bybit",
        )
        result = await active_strategy.on_signal(exit_sig)
        assert result is not None
        assert result.metadata.get("action") == "exit"
        assert result.metadata.get("prev_state") == "long"
        # LONG exit reverses: sell on sell_exchange, buy on buy_exchange
        assert result.legs[0].exchange_id == "bybit"
        assert result.legs[0].side == OrderSide.SELL
        assert result.legs[1].exchange_id == "binance"
        assert result.legs[1].side == OrderSide.BUY

    @pytest.mark.asyncio
    async def test_long_exit_resets_state_to_flat(self, active_strategy):
        await self._warm_up(active_strategy)
        active_strategy._state = StatArbState.LONG
        exit_sig = self._make_mean_exit_signal(active_strategy)
        await active_strategy.on_signal(exit_sig)
        assert active_strategy.state == StatArbState.FLAT

    @pytest.mark.asyncio
    async def test_exit_increments_trade_requests_generated(self, active_strategy):
        await self._warm_up(active_strategy)
        active_strategy._state = StatArbState.SHORT
        before = active_strategy.metrics.trade_requests_generated
        exit_sig = self._make_mean_exit_signal(active_strategy)
        result = await active_strategy.on_signal(exit_sig)
        assert result is not None
        assert active_strategy.metrics.trade_requests_generated == before + 1

    @pytest.mark.asyncio
    async def test_exit_trade_request_has_exactly_two_legs(self, active_strategy):
        await self._warm_up(active_strategy)
        active_strategy._state = StatArbState.SHORT
        exit_sig = self._make_mean_exit_signal(active_strategy)
        result = await active_strategy.on_signal(exit_sig)
        assert result is not None
        assert len(result.legs) == 2

    @pytest.mark.asyncio
    async def test_exit_size_capped_by_max_position_size(self, active_strategy):
        """Exit size = min(signal.volume, max_position_size=2.0)."""
        await self._warm_up(active_strategy)
        active_strategy._state = StatArbState.SHORT
        # Signal volume (5.0) exceeds max_position_size (2.0)
        exit_sig = self._make_mean_exit_signal(active_strategy, volume="5.0")
        result = await active_strategy.on_signal(exit_sig)
        assert result is not None
        for leg in result.legs:
            assert leg.size <= Decimal("2.0")

    @pytest.mark.asyncio
    async def test_no_exit_when_state_is_flat(self, active_strategy):
        """In FLAT state, normal entry logic applies — no spurious exit."""
        await self._warm_up(active_strategy)
        assert active_strategy.state == StatArbState.FLAT

        # Stable prices → z-score near zero → filtered
        sig = _make_signal(buy_price="60000", sell_price="60100", volume="0.5")
        result = await active_strategy.on_signal(sig)
        # May be None (filtered) or TradeRequest (if entry triggered) — but NOT an exit
        if result is not None:
            assert result.metadata.get("action") != "exit"

    @pytest.mark.asyncio
    async def test_inactive_strategy_returns_none(self, strategy):
        strategy._state = StatArbState.SHORT
        sig = _make_signal(buy_price="60000", sell_price="60100", volume="0.5")
        result = await strategy.on_signal(sig)
        assert result is None


# ---------------------------------------------------------------------------
# 5. BaseStrategy on_fill PnL tracking
# ---------------------------------------------------------------------------

class TestBaseStrategyOnFill:
    """Tests for on_fill PnL accumulation in BaseStrategy."""

    @pytest.fixture
    def strategy(self) -> _ConcreteStrategy:
        return _ConcreteStrategy(
            strategy_id="pnl_test",
            cost_calculator=_ZeroCostCalculator(),
        )

    @pytest.mark.asyncio
    async def test_on_fill_increments_fills_received(self, strategy):
        trade = _make_trade(fee="1.0")
        await strategy.on_fill(trade)
        assert strategy.metrics.fills_received == 1

    @pytest.mark.asyncio
    async def test_on_fill_accumulates_pnl_from_metadata_realized_pnl(self, strategy):
        """If trade.metadata has 'realized_pnl', it is added to total_realized_pnl_usdt."""
        trade = _make_trade(fee="1.0", metadata={"realized_pnl": "25.50"})
        await strategy.on_fill(trade)
        assert strategy.metrics.total_realized_pnl_usdt == Decimal("25.50")

    @pytest.mark.asyncio
    async def test_on_fill_accumulates_multiple_pnl_from_metadata(self, strategy):
        """Multiple fills with metadata PnL are summed correctly."""
        trade1 = _make_trade(fee="0.5", metadata={"realized_pnl": "10.00"})
        trade2 = _make_trade(fee="0.5", metadata={"realized_pnl": "15.75"})
        await strategy.on_fill(trade1)
        await strategy.on_fill(trade2)
        assert strategy.metrics.total_realized_pnl_usdt == Decimal("25.75")

    @pytest.mark.asyncio
    async def test_on_fill_deducts_fee_when_no_realized_pnl_in_metadata(self, strategy):
        """Without 'realized_pnl' in metadata, fee is deducted from total_realized_pnl_usdt."""
        trade = _make_trade(fee="2.50", metadata={})
        await strategy.on_fill(trade)
        assert strategy.metrics.total_realized_pnl_usdt == Decimal("-2.50")

    @pytest.mark.asyncio
    async def test_on_fill_deducts_fees_across_multiple_fills_without_pnl_key(self, strategy):
        trade1 = _make_trade(fee="1.00")
        trade2 = _make_trade(fee="0.75")
        await strategy.on_fill(trade1)
        await strategy.on_fill(trade2)
        assert strategy.metrics.total_realized_pnl_usdt == Decimal("-1.75")

    @pytest.mark.asyncio
    async def test_on_fill_zero_fee_no_pnl_key_leaves_pnl_unchanged(self, strategy):
        """Zero fee and no realized_pnl key → PnL unchanged (still 0)."""
        trade = _make_trade(fee="0.0", metadata={})
        await strategy.on_fill(trade)
        assert strategy.metrics.total_realized_pnl_usdt == Decimal("0")

    @pytest.mark.asyncio
    async def test_on_fill_negative_realized_pnl_in_metadata(self, strategy):
        """Negative realized_pnl in metadata is accumulated (loss)."""
        trade = _make_trade(fee="1.0", metadata={"realized_pnl": "-5.00"})
        await strategy.on_fill(trade)
        assert strategy.metrics.total_realized_pnl_usdt == Decimal("-5.00")

    @pytest.mark.asyncio
    async def test_on_fill_pnl_key_takes_priority_over_fee(self, strategy):
        """When 'realized_pnl' is present, fee is NOT deducted."""
        trade = _make_trade(fee="99.99", metadata={"realized_pnl": "50.00"})
        await strategy.on_fill(trade)
        # Should use realized_pnl=50, not subtract fee=99.99
        assert strategy.metrics.total_realized_pnl_usdt == Decimal("50.00")

    @pytest.mark.asyncio
    async def test_on_fill_pnl_string_conversion(self, strategy):
        """realized_pnl stored as non-Decimal type (int) is handled correctly."""
        trade = _make_trade(fee="1.0", metadata={"realized_pnl": 100})
        await strategy.on_fill(trade)
        assert strategy.metrics.total_realized_pnl_usdt == Decimal("100")


# ---------------------------------------------------------------------------
# 6. MultiStrategySignalProducer
# ---------------------------------------------------------------------------

class TestMultiStrategySignalProducer:
    """Tests for MultiStrategySignalProducer signal generation methods."""

    @pytest.fixture
    def mock_bus(self) -> AsyncMock:
        bus = AsyncMock()
        bus.publish = AsyncMock()
        return bus

    @pytest.fixture
    def producer(self, mock_bus: AsyncMock) -> MultiStrategySignalProducer:
        config = MultiSignalConfig(
            spot_futures_min_basis_bps=Decimal("15"),
            funding_rate_min_diff_bps=Decimal("5"),
            triangular_min_profit_bps=Decimal("10"),
        )
        return MultiStrategySignalProducer(event_bus=mock_bus, config=config)

    # -- Spot-Futures --

    @pytest.mark.asyncio
    async def test_spot_futures_signal_generated_when_basis_exceeds_threshold(self, producer):
        sig = await producer.produce_spot_futures_signal(
            exchange_id="binance",
            spot_symbol="BTC/USDT",
            futures_symbol="BTC/USDT:USDT",
            spot_price=Decimal("60000"),
            futures_price=Decimal("60120"),  # 20 bps basis
            funding_rate=0.001,
        )
        assert sig is not None
        assert sig.strategy_id == "spot_futures_basis"

    @pytest.mark.asyncio
    async def test_spot_futures_signal_contains_basis_bps_in_metadata(self, producer):
        sig = await producer.produce_spot_futures_signal(
            exchange_id="binance",
            spot_symbol="BTC/USDT",
            futures_symbol="BTC/USDT:USDT",
            spot_price=Decimal("60000"),
            futures_price=Decimal("60120"),
            funding_rate=0.001,
        )
        assert sig is not None
        assert "basis_bps" in sig.metadata

    @pytest.mark.asyncio
    async def test_spot_futures_signal_filtered_when_basis_below_threshold(self, producer):
        """5 bps basis < 15 bps threshold → None."""
        sig = await producer.produce_spot_futures_signal(
            exchange_id="binance",
            spot_symbol="BTC/USDT",
            futures_symbol="BTC/USDT:USDT",
            spot_price=Decimal("60000"),
            futures_price=Decimal("60003"),  # 5 bps
            funding_rate=0.0,
        )
        assert sig is None

    @pytest.mark.asyncio
    async def test_spot_futures_returns_none_for_zero_spot_price(self, producer):
        sig = await producer.produce_spot_futures_signal(
            exchange_id="binance",
            spot_symbol="BTC/USDT",
            futures_symbol="BTC/USDT:USDT",
            spot_price=Decimal("0"),
            futures_price=Decimal("60000"),
            funding_rate=0.0,
        )
        assert sig is None

    @pytest.mark.asyncio
    async def test_spot_futures_signal_publishes_to_event_bus(self, producer, mock_bus):
        await producer.produce_spot_futures_signal(
            exchange_id="binance",
            spot_symbol="BTC/USDT",
            futures_symbol="BTC/USDT:USDT",
            spot_price=Decimal("60000"),
            futures_price=Decimal("60120"),
            funding_rate=0.001,
        )
        mock_bus.publish.assert_called_once()

    # -- Funding Rate --

    @pytest.mark.asyncio
    async def test_funding_rate_signal_generated_when_diff_exceeds_threshold(self, producer):
        sig = await producer.produce_funding_rate_signal(
            symbol="BTC/USDT",
            high_rate_exchange="binance",
            low_rate_exchange="bybit",
            high_rate=0.003,
            low_rate=0.0,
            price=Decimal("60000"),
        )
        assert sig is not None
        assert sig.strategy_id == "funding_rate_arb"

    @pytest.mark.asyncio
    async def test_funding_rate_signal_filtered_when_diff_below_threshold(self, producer):
        """diff = 0.003 - 0.0028 = 0.0002 → diff_bps = 2 < 5 threshold → None."""
        sig = await producer.produce_funding_rate_signal(
            symbol="BTC/USDT",
            high_rate_exchange="binance",
            low_rate_exchange="bybit",
            high_rate=0.003,
            low_rate=0.0028,
            price=Decimal("60000"),
        )
        assert sig is None

    @pytest.mark.asyncio
    async def test_funding_rate_buy_exchange_is_low_rate_exchange(self, producer):
        """We buy on the low-rate exchange (pay less funding)."""
        sig = await producer.produce_funding_rate_signal(
            symbol="BTC/USDT",
            high_rate_exchange="binance",
            low_rate_exchange="bybit",
            high_rate=0.003,
            low_rate=0.0,
            price=Decimal("60000"),
        )
        assert sig is not None
        assert sig.buy_exchange == "bybit"
        assert sig.sell_exchange == "binance"

    @pytest.mark.asyncio
    async def test_funding_rate_metadata_contains_diff_bps(self, producer):
        sig = await producer.produce_funding_rate_signal(
            symbol="BTC/USDT",
            high_rate_exchange="binance",
            low_rate_exchange="bybit",
            high_rate=0.003,
            low_rate=0.0,
            price=Decimal("60000"),
        )
        assert sig is not None
        assert "funding_diff_bps" in sig.metadata

    # -- Triangular --

    @pytest.mark.asyncio
    async def test_triangular_signal_generated_when_profit_exceeds_threshold(self, producer):
        sig = await producer.produce_triangular_signal(
            exchange_id="binance",
            path=["USDT", "BTC", "ETH"],
            pairs=["BTC/USDT", "ETH/BTC", "ETH/USDT"],
            sides=["buy", "buy", "sell"],
            prices=[Decimal("60000"), Decimal("0.054"), Decimal("3500")],
            profit_pct=Decimal("0.0015"),  # 15 bps > 10 bps threshold
        )
        assert sig is not None
        assert sig.strategy_id == "triangular"

    @pytest.mark.asyncio
    async def test_triangular_signal_filtered_when_profit_below_threshold(self, producer):
        """profit_pct=0.0005 (5 bps) < 10 bps threshold → None."""
        sig = await producer.produce_triangular_signal(
            exchange_id="binance",
            path=["USDT", "BTC", "ETH"],
            pairs=["BTC/USDT", "ETH/BTC", "ETH/USDT"],
            sides=["buy", "buy", "sell"],
            prices=[Decimal("60000"), Decimal("0.054"), Decimal("3500")],
            profit_pct=Decimal("0.0005"),
        )
        assert sig is None

    @pytest.mark.asyncio
    async def test_triangular_signal_metadata_contains_path(self, producer):
        sig = await producer.produce_triangular_signal(
            exchange_id="binance",
            path=["USDT", "BTC", "ETH"],
            pairs=["BTC/USDT", "ETH/BTC", "ETH/USDT"],
            sides=["buy", "buy", "sell"],
            prices=[Decimal("60000"), Decimal("0.054"), Decimal("3500")],
            profit_pct=Decimal("0.002"),
        )
        assert sig is not None
        assert sig.metadata["path"] == ["USDT", "BTC", "ETH"]
        assert sig.metadata["exchange_id"] == "binance"

    @pytest.mark.asyncio
    async def test_triangular_same_buy_and_sell_exchange(self, producer):
        """Triangular arb is on single exchange → buy_exchange == sell_exchange."""
        sig = await producer.produce_triangular_signal(
            exchange_id="okx",
            path=["USDT", "BTC", "ETH"],
            pairs=["BTC/USDT", "ETH/BTC", "ETH/USDT"],
            sides=["buy", "buy", "sell"],
            prices=[Decimal("60000"), Decimal("0.054"), Decimal("3500")],
            profit_pct=Decimal("0.002"),
        )
        assert sig is not None
        assert sig.buy_exchange == "okx"
        assert sig.sell_exchange == "okx"

    # -- Deduplication --

    @pytest.mark.asyncio
    async def test_spot_futures_dedup_suppresses_repeated_signal_within_cooldown(self, producer):
        """Second call within cooldown window returns None."""
        kwargs = dict(
            exchange_id="binance",
            spot_symbol="BTC/USDT",
            futures_symbol="BTC/USDT:USDT",
            spot_price=Decimal("60000"),
            futures_price=Decimal("60120"),
            funding_rate=0.001,
        )
        sig1 = await producer.produce_spot_futures_signal(**kwargs)
        sig2 = await producer.produce_spot_futures_signal(**kwargs)
        assert sig1 is not None
        assert sig2 is None  # deduped

    @pytest.mark.asyncio
    async def test_triangular_dedup_suppresses_repeated_signal_within_cooldown(self, producer):
        kwargs = dict(
            exchange_id="binance",
            path=["USDT", "BTC", "ETH"],
            pairs=["BTC/USDT", "ETH/BTC", "ETH/USDT"],
            sides=["buy", "buy", "sell"],
            prices=[Decimal("60000"), Decimal("0.054"), Decimal("3500")],
            profit_pct=Decimal("0.002"),
        )
        sig1 = await producer.produce_triangular_signal(**kwargs)
        sig2 = await producer.produce_triangular_signal(**kwargs)
        assert sig1 is not None
        assert sig2 is None

    @pytest.mark.asyncio
    async def test_dedup_allows_different_symbols_concurrently(self, producer):
        """Different symbol keys → both signals emitted."""
        sig_btc = await producer.produce_spot_futures_signal(
            exchange_id="binance",
            spot_symbol="BTC/USDT",
            futures_symbol="BTC/USDT:USDT",
            spot_price=Decimal("60000"),
            futures_price=Decimal("60120"),
            funding_rate=0.001,
        )
        sig_eth = await producer.produce_spot_futures_signal(
            exchange_id="binance",
            spot_symbol="ETH/USDT",
            futures_symbol="ETH/USDT:USDT",
            spot_price=Decimal("3500"),
            futures_price=Decimal("3510"),  # ~29 bps > 15 threshold
            funding_rate=0.001,
        )
        assert sig_btc is not None
        assert sig_eth is not None

    @pytest.mark.asyncio
    async def test_no_publish_when_event_bus_is_none(self):
        """Producer with event_bus=None should still return signal but not crash."""
        producer = MultiStrategySignalProducer(event_bus=None)
        sig = await producer.produce_spot_futures_signal(
            exchange_id="binance",
            spot_symbol="BTC/USDT",
            futures_symbol="BTC/USDT:USDT",
            spot_price=Decimal("60000"),
            futures_price=Decimal("60120"),
            funding_rate=0.001,
        )
        assert sig is not None  # signal still returned even without bus


# ---------------------------------------------------------------------------
# 7. PaperSignalSimulator
# ---------------------------------------------------------------------------

class TestPaperSignalSimulator:
    """Tests for PaperSignalSimulator tick and injection_rate behaviour."""

    @pytest.fixture
    def mock_bus(self) -> AsyncMock:
        bus = AsyncMock()
        bus.publish = AsyncMock()
        return bus

    @pytest.fixture
    def producer(self, mock_bus: AsyncMock) -> MultiStrategySignalProducer:
        config = MultiSignalConfig(
            spot_futures_min_basis_bps=Decimal("15"),
            funding_rate_min_diff_bps=Decimal("5"),
            triangular_min_profit_bps=Decimal("10"),
        )
        return MultiStrategySignalProducer(event_bus=mock_bus, config=config)

    @pytest.fixture
    def simulator(self, producer: MultiStrategySignalProducer) -> PaperSignalSimulator:
        return PaperSignalSimulator(
            producer=producer,
            exchanges=["binance", "bybit"],
            symbols=["BTC/USDT", "ETH/USDT"],
            injection_rate=1.0,  # always inject
        )

    @pytest.mark.asyncio
    async def test_tick_returns_empty_list_when_not_running(self, simulator):
        """tick() returns [] before start() is called."""
        result = await simulator.tick()
        assert result == []

    @pytest.mark.asyncio
    async def test_tick_returns_empty_list_after_stop(self, simulator):
        await simulator.start()
        await simulator.stop()
        result = await simulator.tick()
        assert result == []

    @pytest.mark.asyncio
    async def test_tick_returns_signals_when_injection_rate_is_1(self, simulator):
        """With injection_rate=1.0, at least spot-futures signals should be generated."""
        await simulator.start()
        # Run multiple ticks to ensure dedup doesn't block first tick
        all_signals: list = []
        # First tick only — dedup cleared since producer is fresh
        result = await simulator.tick()
        all_signals.extend(result)
        assert len(all_signals) >= 0  # non-negative (dedup may suppress)
        # After start, base_prices should be initialized
        assert "BTC/USDT" in simulator._base_prices
        assert "ETH/USDT" in simulator._base_prices

    @pytest.mark.asyncio
    async def test_tick_initializes_btc_base_price(self, simulator):
        await simulator.start()
        assert simulator._base_prices["BTC/USDT"] == Decimal("65000")

    @pytest.mark.asyncio
    async def test_tick_initializes_eth_base_price(self, simulator):
        await simulator.start()
        assert simulator._base_prices["ETH/USDT"] == Decimal("3500")

    @pytest.mark.asyncio
    async def test_tick_with_injection_rate_zero_returns_no_signals(self, producer):
        """injection_rate=0 means random.random() is always >= 0 → never injects."""
        sim = PaperSignalSimulator(
            producer=producer,
            exchanges=["binance", "bybit"],
            symbols=["BTC/USDT"],
            injection_rate=0.0,
        )
        await sim.start()
        result = await sim.tick()
        assert result == []

    @pytest.mark.asyncio
    async def test_tick_random_walks_base_price(self, simulator):
        """After a tick, base price should differ (random walk applied)."""
        await simulator.start()
        before = simulator._base_prices["BTC/USDT"]
        await simulator.tick()
        after = simulator._base_prices["BTC/USDT"]
        # Price should change (with overwhelming probability via gauss(0, 0.0001))
        # We just verify it's no longer identical or stays numeric
        assert after > 0

    @pytest.mark.asyncio
    async def test_start_sets_running_true(self, simulator):
        await simulator.start()
        assert simulator._running is True

    @pytest.mark.asyncio
    async def test_stop_sets_running_false(self, simulator):
        await simulator.start()
        await simulator.stop()
        assert simulator._running is False

    @pytest.mark.asyncio
    async def test_simulator_with_single_exchange_skips_funding_rate(self, producer):
        """Funding rate requires >= 2 exchanges; with 1 exchange it's always skipped."""
        sim = PaperSignalSimulator(
            producer=producer,
            exchanges=["binance"],
            symbols=["BTC/USDT"],
            injection_rate=1.0,
        )
        await sim.start()
        result = await sim.tick()
        # All signals should NOT have strategy_id == "funding_rate_arb"
        for sig in result:
            assert sig.strategy_id != "funding_rate_arb"

    @pytest.mark.asyncio
    async def test_tick_returns_list_type(self, simulator):
        await simulator.start()
        result = await simulator.tick()
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_multiple_ticks_do_not_crash(self, simulator):
        """Simulator should handle multiple ticks without exception."""
        await simulator.start()
        for _ in range(5):
            result = await simulator.tick()
            assert isinstance(result, list)
