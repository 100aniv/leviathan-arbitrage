"""Integration test: 7 strategies with real CostCalculator — no crash on estimate_cost().

Verifies GAP-10 resolution: friction CostCalculator satisfies base.CostCalculator Protocol.
"""
import asyncio

import pytest
from decimal import Decimal

from src.core.models import OrderSide, Signal
from src.friction.cost_calculator import CostCalculator as FrictionCostCalculator
from src.friction.fee_model import FeeModel
from src.friction.slippage_model import CEXOrderbookSlippage
from src.strategies.base import CostCalculator as CostCalculatorProtocol
from src.strategies.cross_exchange import CrossExchangeStrategy
from src.strategies.spot_futures import SpotFuturesStrategy
from src.strategies.futures_futures import FuturesFuturesStrategy
from src.strategies.triangular import TriangularStrategy
from src.strategies.funding_rate import FundingRateStrategy
from src.strategies.statistical_arb import StatisticalArbStrategy
from src.strategies.latency_arb import LatencyArbStrategy
from src.core.latency_tracker import LatencyTracker


@pytest.fixture
def real_cost_calculator():
    """Build a real CostCalculator from FeeModel + CEXOrderbookSlippage."""
    fee_model = FeeModel()
    slippage = CEXOrderbookSlippage(k=Decimal("1.0"), cold_start=False)
    return FrictionCostCalculator(fee_model=fee_model, slippage_model=slippage)


class TestProtocolConformance:
    """Verify friction CostCalculator satisfies base.CostCalculator Protocol."""

    def test_isinstance_check(self, real_cost_calculator):
        assert isinstance(real_cost_calculator, CostCalculatorProtocol)

    def test_estimate_cost_method_exists(self, real_cost_calculator):
        assert hasattr(real_cost_calculator, "estimate_cost")
        assert callable(real_cost_calculator.estimate_cost)

    def test_estimate_cost_returns_decimal(self, real_cost_calculator):
        result = real_cost_calculator.estimate_cost(
            exchange_id="binance",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            size=Decimal("0.01"),
            price=Decimal("50000"),
        )
        assert isinstance(result, Decimal)
        assert result > 0

    def test_estimate_cost_binance(self, real_cost_calculator):
        # 0.10% taker * (50000 * 0.01) = 0.10% * 500 = 0.50
        result = real_cost_calculator.estimate_cost(
            "binance", "BTC/USDT", OrderSide.BUY, Decimal("0.01"), Decimal("50000")
        )
        assert result == Decimal("0.5")

    def test_estimate_cost_paper_prefix_stripped(self, real_cost_calculator):
        result = real_cost_calculator.estimate_cost(
            "paper_binance", "BTC/USDT", OrderSide.BUY, Decimal("0.01"), Decimal("50000")
        )
        assert result == Decimal("0.5")

    def test_estimate_cost_unknown_exchange_no_crash(self, real_cost_calculator):
        result = real_cost_calculator.estimate_cost(
            "totally_unknown", "BTC/USDT", OrderSide.BUY, Decimal("0.01"), Decimal("50000")
        )
        # Should use 0.25% fallback: 0.0025 * 500 = 1.25
        assert result == Decimal("1.25")


class TestStrategyInstantiationWithRealCostCalculator:
    """All 7 strategies instantiate with real CostCalculator without error."""

    def test_cross_exchange(self, real_cost_calculator):
        s = CrossExchangeStrategy("cross_exchange", real_cost_calculator)
        assert s.strategy_id == "cross_exchange"

    def test_spot_futures(self, real_cost_calculator):
        s = SpotFuturesStrategy("spot_futures", real_cost_calculator)
        assert s.strategy_id == "spot_futures"

    def test_futures_futures(self, real_cost_calculator):
        s = FuturesFuturesStrategy("futures_futures", real_cost_calculator)
        assert s.strategy_id == "futures_futures"

    def test_triangular(self, real_cost_calculator):
        s = TriangularStrategy("triangular", real_cost_calculator)
        assert s.strategy_id == "triangular"

    def test_funding_rate(self, real_cost_calculator):
        s = FundingRateStrategy("funding_rate", real_cost_calculator)
        assert s.strategy_id == "funding_rate"

    def test_statistical_arb(self, real_cost_calculator):
        s = StatisticalArbStrategy("statistical_arb", real_cost_calculator)
        assert s.strategy_id == "statistical_arb"

    def test_latency_arb(self, real_cost_calculator):
        tracker = LatencyTracker()
        s = LatencyArbStrategy("latency_arb", real_cost_calculator, tracker)
        assert s.strategy_id == "latency_arb"


class TestStrategyOnSignalWithRealCostCalculator:
    """All 7 strategies handle on_signal with real CostCalculator — no crash."""

    def _make_signal(self, strategy_id: str, **metadata_kw) -> Signal:
        meta = {
            "strategy_id": strategy_id,
            "buy_exchange": "binance",
            "sell_exchange": "okx",
        }
        meta.update(metadata_kw)
        return Signal(
            symbol="BTC/USDT",
            buy_exchange="binance",
            sell_exchange="okx",
            buy_price=Decimal("50000"),
            sell_price=Decimal("50050"),
            spread_pct=Decimal("0.001"),
            confidence=0.9,
            volume=Decimal("0.01"),
            strategy_id=strategy_id,
            metadata=meta,
        )

    @pytest.mark.asyncio
    async def test_cross_exchange_on_signal(self, real_cost_calculator):
        s = CrossExchangeStrategy("cross_exchange", real_cost_calculator)
        await s.start()
        sig = self._make_signal("cross_exchange_spot")
        result = await s.on_signal(sig)
        # May return None or TradeRequest — either is fine, no crash is the test
        assert result is None or result.strategy_id == "cross_exchange"

    @pytest.mark.asyncio
    async def test_spot_futures_on_signal(self, real_cost_calculator):
        s = SpotFuturesStrategy("spot_futures", real_cost_calculator)
        await s.start()
        sig = self._make_signal(
            "spot_futures_basis",
            basis_bps=20.0,
            spot_symbol="BTC/USDT",
            futures_symbol="BTC/USDT:USDT",
            funding_rate=0.0001,
        )
        result = await s.on_signal(sig)
        assert result is None or result.strategy_id == "spot_futures"

    @pytest.mark.asyncio
    async def test_futures_futures_on_signal(self, real_cost_calculator):
        s = FuturesFuturesStrategy("futures_futures", real_cost_calculator)
        await s.start()
        sig = self._make_signal("futures_futures_cross")
        result = await s.on_signal(sig)
        assert result is None or result.strategy_id == "futures_futures"

    @pytest.mark.asyncio
    async def test_triangular_on_signal(self, real_cost_calculator):
        s = TriangularStrategy("triangular", real_cost_calculator)
        await s.start()
        sig = self._make_signal(
            "triangular",
            path=["USDT", "BTC", "ETH"],
            pairs=["BTC/USDT", "ETH/BTC", "ETH/USDT"],
            sides=["buy", "buy", "sell"],
            prices=[50000.0, 0.05, 2500.0],
        )
        sig.buy_exchange = "binance"
        sig.sell_exchange = "binance"
        result = await s.on_signal(sig)
        assert result is None or result.strategy_id == "triangular"

    @pytest.mark.asyncio
    async def test_funding_rate_on_signal(self, real_cost_calculator):
        s = FundingRateStrategy("funding_rate", real_cost_calculator)
        await s.start()
        sig = self._make_signal(
            "funding_rate_arb",
            funding_rate_sell=0.001,
            funding_rate_buy=0.0001,
        )
        result = await s.on_signal(sig)
        assert result is None or result.strategy_id == "funding_rate"

    @pytest.mark.asyncio
    async def test_statistical_arb_on_signal(self, real_cost_calculator):
        s = StatisticalArbStrategy("statistical_arb", real_cost_calculator)
        await s.start()
        sig = self._make_signal("statistical_arb")
        result = await s.on_signal(sig)
        assert result is None or result.strategy_id == "statistical_arb"

    @pytest.mark.asyncio
    async def test_latency_arb_on_signal(self, real_cost_calculator):
        tracker = LatencyTracker()
        s = LatencyArbStrategy("latency_arb", real_cost_calculator, tracker)
        await s.start()
        sig = self._make_signal("latency_arb")
        result = await s.on_signal(sig)
        assert result is None or result.strategy_id == "latency_arb"


class TestFuturesExchangeFees:
    """Verify all futures exchanges work with estimate_cost."""

    @pytest.mark.parametrize("exchange,expected_rate", [
        ("binance_futures", Decimal("0.0005")),
        ("bybit_futures", Decimal("0.00055")),
        ("okx_futures", Decimal("0.0005")),
        ("bitget_futures", Decimal("0.0006")),
    ])
    def test_futures_exchange_estimate_cost(self, real_cost_calculator, exchange, expected_rate):
        notional = Decimal("10000")
        result = real_cost_calculator.estimate_cost(
            exchange, "BTC/USDT", OrderSide.BUY, Decimal("0.2"), Decimal("50000")
        )
        expected = expected_rate * notional
        assert result == expected
