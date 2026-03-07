"""Tests for TriangularStrategy — 3-leg same-exchange arbitrage."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.core.models import OrderSide, Signal
from src.strategies.base import CostCalculator
from src.strategies.triangular import TriangularConfig, TriangularStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_calculator(cost_per_leg: Decimal = Decimal("1")) -> CostCalculator:
    calc = MagicMock(spec=CostCalculator)
    calc.estimate_cost.return_value = cost_per_leg
    return calc


def make_triangle_signal(
    spread_pct: Decimal = Decimal("0.01"),
    exchange_id: str = "bitget",
    path: list[str] | None = None,
    pairs: list[str] | None = None,
    sides: list[str] | None = None,
    prices: list[str] | None = None,
    volume: Decimal = Decimal("1000"),
) -> Signal:
    """Build a Signal carrying triangular path metadata."""
    return Signal(
        strategy_id="triangular_v1",
        symbol="BTC/USDT",
        buy_exchange=exchange_id,
        sell_exchange=exchange_id,
        buy_price=Decimal("50000"),
        sell_price=Decimal("50500"),
        spread_pct=spread_pct,
        confidence=0.90,
        volume=volume,
        timestamp=datetime.utcnow(),
        metadata={
            "path": path or ["USDT", "BTC", "ETH"],
            "pairs": pairs or ["BTC/USDT", "ETH/BTC", "ETH/USDT"],
            "sides": sides or ["buy", "buy", "sell"],
            "prices": prices or ["50000", "0.06", "3050"],
            "exchange_id": exchange_id,
            "gross_profit_pct": str(spread_pct),
        },
    )


# ---------------------------------------------------------------------------
# Basic strategy lifecycle
# ---------------------------------------------------------------------------


class TestTriangularStrategyLifecycle:
    @pytest.mark.asyncio
    async def test_inactive_strategy_returns_none(self):
        strategy = TriangularStrategy("tri_v1", make_calculator())
        signal = make_triangle_signal()
        result = await strategy.on_signal(signal)
        assert result is None

    @pytest.mark.asyncio
    async def test_start_activates_strategy(self):
        strategy = TriangularStrategy("tri_v1", make_calculator())
        assert not strategy.is_active
        await strategy.start()
        assert strategy.is_active

    @pytest.mark.asyncio
    async def test_stop_deactivates_strategy(self):
        strategy = TriangularStrategy("tri_v1", make_calculator())
        await strategy.start()
        await strategy.stop()
        assert not strategy.is_active

    @pytest.mark.asyncio
    async def test_strategy_id_preserved(self):
        strategy = TriangularStrategy("tri_arb_001", make_calculator())
        assert strategy.strategy_id == "tri_arb_001"


# ---------------------------------------------------------------------------
# Filtering logic
# ---------------------------------------------------------------------------


class TestTriangularStrategyFiltering:
    @pytest.mark.asyncio
    async def test_below_min_profit_threshold_filtered(self):
        strategy = TriangularStrategy(
            "tri_v1",
            make_calculator(),
            TriangularConfig(min_profit_bps=Decimal("50")),
        )
        await strategy.start()
        # 0.1% = 10 bps < 50 bps threshold
        signal = make_triangle_signal(spread_pct=Decimal("0.001"))
        result = await strategy.on_signal(signal)
        assert result is None
        assert strategy.metrics.signals_filtered >= 1

    @pytest.mark.asyncio
    async def test_missing_metadata_returns_none(self):
        strategy = TriangularStrategy("tri_v1", make_calculator())
        await strategy.start()
        signal = Signal(
            strategy_id="tri_v1",
            symbol="BTC/USDT",
            buy_exchange="bitget",
            sell_exchange="bitget",
            buy_price=Decimal("50000"),
            sell_price=Decimal("50500"),
            spread_pct=Decimal("0.01"),
            confidence=0.9,
            volume=Decimal("1000"),
            timestamp=datetime.utcnow(),
            metadata={},  # no triangle info
        )
        result = await strategy.on_signal(signal)
        assert result is None

    @pytest.mark.asyncio
    async def test_high_cost_makes_net_profit_negative(self):
        """3 * 100 USDT cost > 1% * 100 USDT volume = 1 USDT gross."""
        strategy = TriangularStrategy(
            "tri_v1",
            make_calculator(Decimal("100")),
            TriangularConfig(min_profit_bps=Decimal("1")),
        )
        await strategy.start()
        signal = make_triangle_signal(spread_pct=Decimal("0.01"), volume=Decimal("100"))
        result = await strategy.on_signal(signal)
        assert result is None
        assert strategy.metrics.signals_filtered >= 1


# ---------------------------------------------------------------------------
# Trade request generation
# ---------------------------------------------------------------------------


class TestTriangularStrategyTradeRequest:
    @pytest.mark.asyncio
    async def test_profitable_signal_generates_trade_request(self):
        strategy = TriangularStrategy(
            "tri_v1",
            make_calculator(Decimal("0.1")),
            TriangularConfig(min_profit_bps=Decimal("10")),
        )
        await strategy.start()
        signal = make_triangle_signal(spread_pct=Decimal("0.01"), volume=Decimal("1000"))
        result = await strategy.on_signal(signal)
        assert result is not None

    @pytest.mark.asyncio
    async def test_trade_request_has_exactly_three_legs(self):
        strategy = TriangularStrategy(
            "tri_v1",
            make_calculator(Decimal("0.1")),
            TriangularConfig(min_profit_bps=Decimal("10")),
        )
        await strategy.start()
        signal = make_triangle_signal(spread_pct=Decimal("0.01"), volume=Decimal("1000"))
        result = await strategy.on_signal(signal)
        assert result is not None
        assert len(result.legs) == 3

    @pytest.mark.asyncio
    async def test_all_legs_on_same_exchange(self):
        strategy = TriangularStrategy(
            "tri_v1",
            make_calculator(Decimal("0.1")),
            TriangularConfig(min_profit_bps=Decimal("10")),
        )
        await strategy.start()
        signal = make_triangle_signal(spread_pct=Decimal("0.01"), exchange_id="bitget")
        result = await strategy.on_signal(signal)
        assert result is not None
        for leg in result.legs:
            assert leg.exchange_id == "bitget"

    @pytest.mark.asyncio
    async def test_leg_sides_match_signal_metadata(self):
        strategy = TriangularStrategy(
            "tri_v1",
            make_calculator(Decimal("0.1")),
        )
        await strategy.start()
        signal = make_triangle_signal(
            spread_pct=Decimal("0.01"),
            sides=["buy", "buy", "sell"],
        )
        result = await strategy.on_signal(signal)
        assert result is not None
        assert result.legs[0].side == OrderSide.BUY
        assert result.legs[1].side == OrderSide.BUY
        assert result.legs[2].side == OrderSide.SELL

    @pytest.mark.asyncio
    async def test_leg_symbols_match_signal_pairs(self):
        strategy = TriangularStrategy(
            "tri_v1",
            make_calculator(Decimal("0.1")),
        )
        await strategy.start()
        pairs = ["BTC/USDT", "ETH/BTC", "ETH/USDT"]
        signal = make_triangle_signal(spread_pct=Decimal("0.01"), pairs=pairs)
        result = await strategy.on_signal(signal)
        assert result is not None
        assert result.legs[0].symbol == "BTC/USDT"
        assert result.legs[1].symbol == "ETH/BTC"
        assert result.legs[2].symbol == "ETH/USDT"

    @pytest.mark.asyncio
    async def test_strategy_id_in_trade_request(self):
        strategy = TriangularStrategy(
            "tri_arb_007",
            make_calculator(Decimal("0.1")),
        )
        await strategy.start()
        signal = make_triangle_signal(spread_pct=Decimal("0.01"))
        result = await strategy.on_signal(signal)
        assert result is not None
        assert result.strategy_id == "tri_arb_007"

    @pytest.mark.asyncio
    async def test_net_profit_correct_with_3x_cost(self):
        """gross = spread_pct * volume; net = gross - 3 * cost_per_leg."""
        cost_per_leg = Decimal("0.5")
        strategy = TriangularStrategy(
            "tri_v1",
            make_calculator(cost_per_leg),
            TriangularConfig(min_profit_bps=Decimal("10")),
        )
        await strategy.start()
        # gross = 0.01 * 1000 = 10 USDT; cost = 3 * 0.5 = 1.5 USDT; net = 8.5 USDT
        signal = make_triangle_signal(spread_pct=Decimal("0.01"), volume=Decimal("1000"))
        result = await strategy.on_signal(signal)
        assert result is not None
        assert result.expected_profit_usdt == Decimal("8.5")

    @pytest.mark.asyncio
    async def test_size_capped_by_max_position_usdt(self):
        # max_position_usdt=500, first_price=50000 → max_base_size = 500/50000 = 0.01
        strategy = TriangularStrategy(
            "tri_v1",
            make_calculator(Decimal("0.01")),
            TriangularConfig(min_profit_bps=Decimal("10"), max_position_usdt=Decimal("500")),
        )
        await strategy.start()
        signal = make_triangle_signal(spread_pct=Decimal("0.01"), volume=Decimal("2000"))
        result = await strategy.on_signal(signal)
        assert result is not None
        expected_base_size = Decimal("500") / Decimal("50000")  # 0.01 BTC
        for leg in result.legs:
            assert leg.size == expected_base_size

    @pytest.mark.asyncio
    async def test_confidence_preserved_in_trade_request(self):
        strategy = TriangularStrategy("tri_v1", make_calculator(Decimal("0.1")))
        await strategy.start()
        signal = make_triangle_signal(spread_pct=Decimal("0.01"))
        signal.confidence  # verify field exists
        result = await strategy.on_signal(signal)
        assert result is not None
        assert result.confidence == signal.confidence


# ---------------------------------------------------------------------------
# Fee calculation — 3 legs
# ---------------------------------------------------------------------------


class TestTriangularStrategyFees:
    @pytest.mark.asyncio
    async def test_cost_calculator_called_three_times(self):
        calc = make_calculator(Decimal("0.01"))
        strategy = TriangularStrategy(
            "tri_v1",
            calc,
            TriangularConfig(min_profit_bps=Decimal("10")),
        )
        await strategy.start()
        signal = make_triangle_signal(spread_pct=Decimal("0.01"))
        await strategy.on_signal(signal)
        assert calc.estimate_cost.call_count == 3

    @pytest.mark.asyncio
    async def test_cost_calculator_receives_correct_exchange(self):
        calc = make_calculator(Decimal("0.01"))
        strategy = TriangularStrategy(
            "tri_v1",
            calc,
            TriangularConfig(min_profit_bps=Decimal("10")),
        )
        await strategy.start()
        signal = make_triangle_signal(spread_pct=Decimal("0.01"), exchange_id="bitget")
        await strategy.on_signal(signal)
        for call in calc.estimate_cost.call_args_list:
            assert call.kwargs["exchange_id"] == "bitget"


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


class TestTriangularStrategyMetrics:
    @pytest.mark.asyncio
    async def test_signals_received_count(self):
        strategy = TriangularStrategy(
            "tri_v1",
            make_calculator(Decimal("0.01")),
            TriangularConfig(min_profit_bps=Decimal("10")),
        )
        await strategy.start()
        signal = make_triangle_signal(spread_pct=Decimal("0.01"))
        await strategy.on_signal(signal)
        await strategy.on_signal(signal)
        assert strategy.metrics.signals_received == 2

    @pytest.mark.asyncio
    async def test_trade_requests_generated_count(self):
        strategy = TriangularStrategy(
            "tri_v1",
            make_calculator(Decimal("0.01")),
            TriangularConfig(min_profit_bps=Decimal("10")),
        )
        await strategy.start()
        signal = make_triangle_signal(spread_pct=Decimal("0.01"))
        await strategy.on_signal(signal)
        await strategy.on_signal(signal)
        assert strategy.metrics.trade_requests_generated == 2

    @pytest.mark.asyncio
    async def test_on_fill_increments_fills(self):
        from src.core.models import OrderSide, Trade

        strategy = TriangularStrategy("tri_v1", make_calculator())
        trade = Trade(
            trade_id="t1",
            exchange_id="bitget",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            price=Decimal("50000"),
            amount=Decimal("0.001"),
        )
        await strategy.on_fill(trade)
        assert strategy.metrics.fills_received == 1
