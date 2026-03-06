"""Unit tests for engine/src/strategies/cex_dex.py.

Tests cover:
  - AMMSlippageModel: price_impact, expected_output, effective_price
  - DEXAdapter Protocol: runtime_checkable compliance
  - CexDexStrategy: signal filtering, direction logic, TradeRequest generation
  - Edge cases: zero reserves, zero gas, no-trade threshold
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.models import OrderSide, OrderType, Signal, Trade
from src.strategies.cex_dex import (
    AMMSlippageModel,
    CexDexConfig,
    CexDexStrategy,
    DEXAdapter,
)
from src.strategies.base import TradeRequest


# ---------------------------------------------------------------------------
# Mock DEX Adapter
# ---------------------------------------------------------------------------


class MockDEXAdapter:
    """In-memory DEX adapter for unit tests."""

    def __init__(
        self,
        dex_id: str = "uniswap_v3",
        pool_address: str = "0xCBCdF9626bC03E24f779434178A73a0B4bad62ED",
        pool_price: Decimal = Decimal("50000"),
        gas_cost: Decimal = Decimal("5.00"),
        reserve_in: Decimal = Decimal("100"),
        reserve_out: Decimal = Decimal("5000000"),
    ) -> None:
        self._dex_id = dex_id
        self._pool_address = pool_address
        self._pool_price = pool_price
        self._gas_cost = gas_cost
        self._reserve_in = reserve_in
        self._reserve_out = reserve_out

    @property
    def dex_id(self) -> str:
        return self._dex_id

    @property
    def pool_address(self) -> str:
        return self._pool_address

    async def get_pool_price(self, token_in: str, token_out: str) -> Decimal:
        return self._pool_price

    async def estimate_gas(self, size: Decimal) -> Decimal:
        return self._gas_cost

    async def get_pool_reserves(self) -> tuple[Decimal, Decimal]:
        return (self._reserve_in, self._reserve_out)


def make_mock_cost_calculator() -> MagicMock:
    calc = MagicMock()
    calc.estimate_cost = MagicMock(return_value=Decimal("0.50"))
    return calc


def make_signal(
    buy_price: Decimal = Decimal("49900"),
    sell_price: Decimal = Decimal("50100"),
    volume: Decimal = Decimal("0.1"),
    symbol: str = "BTC/USDT",
) -> Signal:
    return Signal(
        strategy_id="test_cex_dex",
        symbol=symbol,
        buy_exchange="binance",
        sell_exchange="uniswap_v3",
        buy_price=buy_price,
        sell_price=sell_price,
        spread_pct=(sell_price - buy_price) / buy_price,
        confidence=1.0,
        volume=volume,
    )


# ---------------------------------------------------------------------------
# AMMSlippageModel tests
# ---------------------------------------------------------------------------


class TestAMMSlippageModel:

    def test_price_impact_zero_for_tiny_swap(self):
        """Tiny swap vs huge pool → near-zero price impact."""
        model = AMMSlippageModel()
        impact = model.price_impact(
            reserve_in=Decimal("10000"),
            reserve_out=Decimal("500000000"),
            amount_in=Decimal("0.001"),
        )
        assert impact < Decimal("0.0001"), f"Expected near-zero impact, got {impact}"

    def test_price_impact_increases_with_swap_size(self):
        """Larger swaps must have larger price impact."""
        model = AMMSlippageModel()
        r_in = Decimal("1000")
        r_out = Decimal("50000000")

        impact_small = model.price_impact(r_in, r_out, Decimal("1"))
        impact_large = model.price_impact(r_in, r_out, Decimal("100"))

        assert impact_large > impact_small, (
            "Price impact must increase with swap size"
        )

    def test_price_impact_returns_zero_for_zero_amount(self):
        model = AMMSlippageModel()
        impact = model.price_impact(Decimal("1000"), Decimal("50000000"), Decimal("0"))
        assert impact == Decimal("0")

    def test_price_impact_returns_zero_for_zero_reserves(self):
        model = AMMSlippageModel()
        impact = model.price_impact(Decimal("0"), Decimal("50000000"), Decimal("1"))
        assert impact == Decimal("0")

    def test_expected_output_less_than_spot_rate(self):
        """After fee, output must be less than spot-rate equivalent."""
        model = AMMSlippageModel()
        r_in = Decimal("1000")   # 1000 BTC
        r_out = Decimal("50000000")  # 50M USDT → spot = 50,000 USDT/BTC
        amount_in = Decimal("1")

        out = model.expected_output(r_in, r_out, amount_in, fee_bps=30)
        spot_equiv = r_out / r_in * amount_in  # ideal spot output

        assert out < spot_equiv, "Output with fee must be less than spot equivalent"
        assert out > Decimal("0"), "Output must be positive"

    def test_expected_output_zero_for_zero_input(self):
        model = AMMSlippageModel()
        out = model.expected_output(Decimal("1000"), Decimal("50000000"), Decimal("0"))
        assert out == Decimal("0")

    def test_expected_output_higher_for_lower_fee(self):
        """Lower pool fee → higher output."""
        model = AMMSlippageModel()
        r_in, r_out, amount = Decimal("1000"), Decimal("50000000"), Decimal("1")
        out_high_fee = model.expected_output(r_in, r_out, amount, fee_bps=100)
        out_low_fee = model.expected_output(r_in, r_out, amount, fee_bps=5)
        assert out_low_fee > out_high_fee

    def test_effective_price_close_to_spot_for_small_swap(self):
        """Effective price for tiny swap should be close to pool spot price."""
        model = AMMSlippageModel()
        r_in = Decimal("10000")
        r_out = Decimal("500000000")
        amount_in = Decimal("0.001")

        effective = model.effective_price(r_in, r_out, amount_in, fee_bps=30)
        spot = r_out / r_in  # spot price

        diff_pct = abs(effective - spot) / spot
        assert diff_pct < Decimal("0.01"), (
            f"Effective price {effective} too far from spot {spot}: {diff_pct:.4%}"
        )

    def test_constant_product_invariant_holds(self):
        """After swap, k = reserve_in * reserve_out must be preserved (≈ k)."""
        model = AMMSlippageModel()
        r_in = Decimal("1000")
        r_out = Decimal("50000000")
        amount_in = Decimal("10")
        fee_bps = 30

        k_before = r_in * r_out
        amount_out = model.expected_output(r_in, r_out, amount_in, fee_bps)
        fee_factor = Decimal(str(10000 - fee_bps)) / Decimal("10000")
        amount_in_net = amount_in * fee_factor

        new_r_in = r_in + amount_in_net
        new_r_out = r_out - amount_out
        k_after = new_r_in * new_r_out

        # k should be approximately preserved (within 0.1% rounding)
        relative_diff = abs(k_after - k_before) / k_before
        assert relative_diff < Decimal("0.001"), (
            f"Constant product invariant violated: k_before={k_before}, k_after={k_after}"
        )


# ---------------------------------------------------------------------------
# DEXAdapter Protocol tests
# ---------------------------------------------------------------------------


class TestDEXAdapterProtocol:

    def test_mock_dex_adapter_satisfies_protocol(self):
        """MockDEXAdapter must satisfy DEXAdapter Protocol at runtime."""
        adapter = MockDEXAdapter()
        assert isinstance(adapter, DEXAdapter), (
            "MockDEXAdapter does not satisfy DEXAdapter Protocol"
        )

    def test_protocol_requires_pool_address_property(self):
        """Objects missing pool_address must not satisfy Protocol."""
        class Incomplete:
            @property
            def dex_id(self) -> str:
                return "x"

            async def get_pool_price(self, a, b):
                return Decimal("1")

            async def estimate_gas(self, size):
                return Decimal("1")

            async def get_pool_reserves(self):
                return (Decimal("1"), Decimal("1"))

        # Missing pool_address → should not satisfy Protocol
        # Note: Python Protocol runtime_checkable only checks methods/attrs existence
        obj = Incomplete()
        assert not isinstance(obj, DEXAdapter)


# ---------------------------------------------------------------------------
# CexDexStrategy tests
# ---------------------------------------------------------------------------


class TestCexDexStrategy:

    def make_strategy(
        self,
        dex_price: Decimal = Decimal("50500"),  # DEX is more expensive → buy CEX
        gas_cost: Decimal = Decimal("1.00"),
        min_edge_bps: Decimal = Decimal("10"),
    ) -> CexDexStrategy:
        dex = MockDEXAdapter(pool_price=dex_price, gas_cost=gas_cost)
        config = CexDexConfig(min_edge_bps=min_edge_bps, friction_cost_pct=Decimal("0.001"))
        return CexDexStrategy(
            strategy_id="test_cex_dex_v1",
            cost_calculator=make_mock_cost_calculator(),
            dex_adapter=dex,
            cex_exchange_id="binance",
            symbol="BTC/USDT",
            config=config,
        )

    async def test_strategy_returns_none_when_inactive(self):
        """Inactive strategy must return None without processing."""
        strategy = self.make_strategy()
        # Not started → is_active = False
        signal = make_signal()
        result = await strategy.on_signal(signal)
        assert result is None
        assert strategy.metrics.signals_filtered == 1

    async def test_strategy_generates_trade_when_spread_sufficient(self):
        """When net edge > min_edge_bps, strategy must return a TradeRequest."""
        # CEX mid = (49900 + 50100) / 2 = 50000
        # DEX price = 50500 → spread = 1.0%
        # friction = 0.1%, gas ~0.002% → net_edge ~0.9% >> 0.10% min
        strategy = self.make_strategy(dex_price=Decimal("50500"), gas_cost=Decimal("1.00"))
        await strategy.start()

        signal = make_signal()
        result = await strategy.on_signal(signal)

        assert result is not None, "Strategy should generate TradeRequest with sufficient spread"
        assert isinstance(result, TradeRequest)
        assert result.strategy_id == "test_cex_dex_v1"
        assert len(result.legs) == 2

    async def test_strategy_returns_none_when_spread_insufficient(self):
        """When net edge <= min_edge_bps, strategy must filter the signal."""
        # CEX mid = 50000, DEX price = 50001 → spread = 0.002% < 0.10% min_edge
        strategy = self.make_strategy(dex_price=Decimal("50001"), gas_cost=Decimal("0.50"))
        await strategy.start()

        signal = make_signal(
            buy_price=Decimal("49999.5"),
            sell_price=Decimal("50000.5"),
        )
        result = await strategy.on_signal(signal)

        assert result is None, "Strategy should filter signal with insufficient spread"
        assert strategy.metrics.signals_filtered >= 1

    async def test_direction_buy_cex_sell_dex(self):
        """When CEX is cheaper (cex_mid < dex_price), CEX leg must be BUY."""
        # CEX mid = 50000, DEX = 50500 → CEX cheaper
        strategy = self.make_strategy(dex_price=Decimal("50500"))
        await strategy.start()

        signal = make_signal()
        result = await strategy.on_signal(signal)

        assert result is not None
        cex_leg = next(l for l in result.legs if l.exchange_id == "binance")
        dex_leg = next(l for l in result.legs if l.exchange_id == "uniswap_v3")

        assert cex_leg.side == OrderSide.BUY, "CEX leg must be BUY when CEX is cheaper"
        assert dex_leg.side == OrderSide.SELL, "DEX leg must be SELL when CEX is cheaper"
        assert result.metadata["direction"] == "buy_cex_sell_dex"

    async def test_direction_buy_dex_sell_cex(self):
        """When DEX is cheaper (dex_price < cex_mid), DEX leg must be BUY."""
        # CEX mid = 50000, DEX = 49500 → DEX cheaper
        strategy = self.make_strategy(dex_price=Decimal("49500"))
        await strategy.start()

        signal = make_signal()
        result = await strategy.on_signal(signal)

        assert result is not None
        cex_leg = next(l for l in result.legs if l.exchange_id == "binance")
        dex_leg = next(l for l in result.legs if l.exchange_id == "uniswap_v3")

        assert cex_leg.side == OrderSide.SELL, "CEX leg must be SELL when DEX is cheaper"
        assert dex_leg.side == OrderSide.BUY, "DEX leg must be BUY when DEX is cheaper"
        assert result.metadata["direction"] == "buy_dex_sell_cex"

    async def test_dex_leg_includes_pool_metadata(self):
        """DEX leg must include pool_address, gas_cost_usd, and dex_fee_bps in metadata."""
        strategy = self.make_strategy(dex_price=Decimal("50500"))
        await strategy.start()

        result = await strategy.on_signal(make_signal())
        assert result is not None

        dex_leg = next(l for l in result.legs if l.exchange_id == "uniswap_v3")
        assert "dex_pool" in dex_leg.metadata, "DEX leg missing dex_pool"
        assert "gas_cost_usd" in dex_leg.metadata, "DEX leg missing gas_cost_usd"
        assert "dex_fee_bps" in dex_leg.metadata, "DEX leg missing dex_fee_bps"

    async def test_position_capped_at_max_size(self):
        """Size must not exceed config.max_position_size."""
        config = CexDexConfig(
            min_edge_bps=Decimal("5"),
            max_position_size=Decimal("0.05"),
            friction_cost_pct=Decimal("0.001"),
        )
        dex = MockDEXAdapter(pool_price=Decimal("50500"), gas_cost=Decimal("1.00"))
        strategy = CexDexStrategy(
            strategy_id="test_cap",
            cost_calculator=make_mock_cost_calculator(),
            dex_adapter=dex,
            cex_exchange_id="binance",
            symbol="BTC/USDT",
            config=config,
        )
        await strategy.start()

        # Signal requests 1.0 BTC but max is 0.05
        signal = make_signal(volume=Decimal("1.0"))
        result = await strategy.on_signal(signal)

        assert result is not None
        for leg in result.legs:
            assert leg.size <= Decimal("0.05"), (
                f"Leg size {leg.size} exceeds max_position_size 0.05"
            )

    async def test_metrics_incremented_correctly(self):
        """signals_received, signals_filtered, trade_requests_generated must track correctly."""
        # Strategy A: wide spread → 3 trade requests generated
        strategy_a = self.make_strategy(dex_price=Decimal("50500"))
        await strategy_a.start()
        for _ in range(3):
            await strategy_a.on_signal(make_signal())

        # Strategy B: tiny spread → 2 signals filtered (dex_price barely above cex_mid)
        strategy_b = self.make_strategy(dex_price=Decimal("50001"))
        await strategy_b.start()
        for _ in range(2):
            await strategy_b.on_signal(make_signal())

        assert strategy_a.metrics.signals_received == 3
        assert strategy_a.metrics.trade_requests_generated == 3
        assert strategy_a.metrics.signals_filtered == 0

        assert strategy_b.metrics.signals_received == 2
        assert strategy_b.metrics.trade_requests_generated == 0
        assert strategy_b.metrics.signals_filtered == 2

    async def test_dex_error_filters_signal(self):
        """If DEX price fetch fails, signal must be filtered gracefully (no exception)."""
        dex = MockDEXAdapter()
        dex.get_pool_price = AsyncMock(side_effect=ConnectionError("DEX node unavailable"))

        strategy = CexDexStrategy(
            strategy_id="test_dex_error",
            cost_calculator=make_mock_cost_calculator(),
            dex_adapter=dex,
            cex_exchange_id="binance",
            symbol="BTC/USDT",
        )
        await strategy.start()

        result = await strategy.on_signal(make_signal())
        assert result is None, "DEX error must produce None (filtered), not raise"
        assert strategy.metrics.signals_filtered == 1

    async def test_on_fill_increments_fill_count(self):
        """on_fill must increment fills_received metric."""
        strategy = self.make_strategy()
        fill = MagicMock(spec=Trade)
        fill.trade_id = "t001"

        await strategy.on_fill(fill)
        assert strategy.metrics.fills_received == 1

    async def test_expected_profit_positive_for_valid_trade(self):
        """expected_profit_usdt must be > 0 for a valid arb opportunity."""
        strategy = self.make_strategy(dex_price=Decimal("50500"))
        await strategy.start()

        result = await strategy.on_signal(make_signal())
        assert result is not None
        assert result.expected_profit_usdt > Decimal("0"), (
            "Expected profit must be positive for valid CEX-DEX arb"
        )

    async def test_compute_amm_output_wrapper(self):
        """compute_amm_output wrapper must delegate to AMMSlippageModel correctly."""
        strategy = self.make_strategy()
        out = strategy.compute_amm_output(
            reserve_in=Decimal("1000"),
            reserve_out=Decimal("50000000"),
            amount_in=Decimal("1"),
        )
        assert out > Decimal("0")
        # Cross-check with direct model call
        expected = AMMSlippageModel.expected_output(
            Decimal("1000"), Decimal("50000000"), Decimal("1"), fee_bps=30
        )
        assert out == expected

    async def test_compute_price_impact_wrapper(self):
        """compute_price_impact wrapper must return non-negative value < 1."""
        strategy = self.make_strategy()
        impact = strategy.compute_price_impact(
            reserve_in=Decimal("1000"),
            reserve_out=Decimal("50000000"),
            amount_in=Decimal("1"),
        )
        assert Decimal("0") <= impact < Decimal("1")

    async def test_start_stop_lifecycle(self):
        """start() sets is_active=True; stop() sets is_active=False."""
        strategy = self.make_strategy()
        assert not strategy.is_active

        await strategy.start()
        assert strategy.is_active

        await strategy.stop()
        assert not strategy.is_active
