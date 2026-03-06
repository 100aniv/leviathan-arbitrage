"""Tests for CostCalculator — complete friction cost formula (Amendment 3D)."""
import pytest
from decimal import Decimal

from src.core.order_book import OrderBook
from src.friction.cost_calculator import CostCalculator, FrictionCost, TradeOutcome
from src.friction.fee_model import FeeModel
from src.friction.slippage_model import CEXOrderbookSlippage


@pytest.fixture
def buy_book():
    book = OrderBook(symbol="BTC/USDT", exchange="binance")
    book.apply_snapshot(
        bids=[("50000.00", "10.0")],
        asks=[("50001.00", "10.0")],
    )
    return book


@pytest.fixture
def sell_book():
    book = OrderBook(symbol="BTC/USDT", exchange="okx")
    book.apply_snapshot(
        bids=[("50050.00", "10.0")],
        asks=[("50051.00", "10.0")],
    )
    return book


@pytest.fixture
def calculator():
    fee_model = FeeModel()
    slippage_model = CEXOrderbookSlippage(k=Decimal("1.0"), cold_start=False)
    return CostCalculator(
        fee_model=fee_model,
        slippage_model=slippage_model,
        network_cost=Decimal("0.50"),
        funding_cost=Decimal("0.10"),
        opportunity_cost=Decimal("0.05"),
    )


class TestFrictionCostFormula:
    def test_returns_friction_cost_instance(self, calculator, buy_book, sell_book):
        result = calculator.calculate(
            buy_exchange="binance",
            sell_exchange="okx",
            buy_book=buy_book,
            sell_book=sell_book,
            size=Decimal("1.0"),
            buy_price=Decimal("50001.00"),
            sell_price=Decimal("50050.00"),
            adv=Decimal("10000"),
            sigma=Decimal("0.001"),
        )
        assert isinstance(result, FrictionCost)

    def test_gross_spread_calculation(self, calculator, buy_book, sell_book):
        result = calculator.calculate(
            buy_exchange="binance",
            sell_exchange="okx",
            buy_book=buy_book,
            sell_book=sell_book,
            size=Decimal("1.0"),
            buy_price=Decimal("50001.00"),
            sell_price=Decimal("50050.00"),
        )
        expected_gross = (Decimal("50050") - Decimal("50001")) * Decimal("1.0")
        assert result.gross_spread == expected_gross

    def test_net_profit_equals_gross_minus_total_cost(self, calculator, buy_book, sell_book):
        result = calculator.calculate(
            buy_exchange="binance",
            sell_exchange="okx",
            buy_book=buy_book,
            sell_book=sell_book,
            size=Decimal("1.0"),
            buy_price=Decimal("50001.00"),
            sell_price=Decimal("50050.00"),
        )
        assert result.net_profit == result.gross_spread - result.total_cost

    def test_total_cost_includes_all_components(self, calculator, buy_book, sell_book):
        result = calculator.calculate(
            buy_exchange="binance",
            sell_exchange="okx",
            buy_book=buy_book,
            sell_book=sell_book,
            size=Decimal("1.0"),
            buy_price=Decimal("50001.00"),
            sell_price=Decimal("50050.00"),
        )
        manual_total = (
            result.fee_buy
            + result.fee_sell
            + result.slippage_buy
            + result.slippage_sell
            + result.network_cost
            + result.funding_cost
            + result.opportunity_cost
            + result.rollback_cost_expected
        )
        assert result.total_cost == manual_total

    def test_net_profit_positive_for_wide_spread(self, calculator, buy_book, sell_book):
        result = calculator.calculate(
            buy_exchange="binance",
            sell_exchange="okx",
            buy_book=buy_book,
            sell_book=sell_book,
            size=Decimal("1.0"),
            buy_price=Decimal("50001.00"),
            sell_price=Decimal("50500.00"),  # very wide spread
            adv=Decimal("10000"),
            sigma=Decimal("0.001"),
        )
        assert result.net_profit > 0

    def test_fee_buy_is_taker_on_buy_exchange(self, calculator, buy_book, sell_book):
        result = calculator.calculate(
            buy_exchange="binance",
            sell_exchange="okx",
            buy_book=buy_book,
            sell_book=sell_book,
            size=Decimal("1.0"),
            buy_price=Decimal("50001.00"),
            sell_price=Decimal("50050.00"),
        )
        fee_model = FeeModel()
        expected_fee_buy = fee_model.taker_fee("binance", Decimal("50001.00"))
        assert result.fee_buy == expected_fee_buy

    def test_network_cost_propagated(self, buy_book, sell_book):
        calc = CostCalculator(
            fee_model=FeeModel(),
            slippage_model=CEXOrderbookSlippage(cold_start=False),
            network_cost=Decimal("1.23"),
        )
        result = calc.calculate(
            buy_exchange="binance",
            sell_exchange="okx",
            buy_book=buy_book,
            sell_book=sell_book,
            size=Decimal("1.0"),
            buy_price=Decimal("50001.00"),
            sell_price=Decimal("50050.00"),
        )
        assert result.network_cost == Decimal("1.23")


class TestRollbackCost:
    def test_cold_start_rollback_probability(self):
        calc = CostCalculator(FeeModel(), CEXOrderbookSlippage())
        assert calc.rollback_probability() == Decimal("0.05")

    def test_rollback_probability_from_history(self):
        calc = CostCalculator(FeeModel(), CEXOrderbookSlippage())
        calc.record_trade(TradeOutcome(rolled_back=True, rollback_cost=Decimal("10")))
        calc.record_trade(TradeOutcome(rolled_back=False))
        calc.record_trade(TradeOutcome(rolled_back=False))
        calc.record_trade(TradeOutcome(rolled_back=False))
        # 1/4 = 25%
        assert calc.rollback_probability() == Decimal("0.25")

    def test_expected_rollback_cost_cold_start(self):
        calc = CostCalculator(FeeModel(), CEXOrderbookSlippage())
        # Cold start: P=5%, avg_cost=100 → E=5
        expected = calc.expected_rollback_cost(Decimal("100"))
        assert expected == Decimal("5")

    def test_expected_rollback_cost_from_history(self):
        calc = CostCalculator(FeeModel(), CEXOrderbookSlippage())
        # 1 rollback out of 4 trades → P=0.25, avg_cost=20 → E=5
        for rolled_back in [True, False, False, False]:
            calc.record_trade(TradeOutcome(rolled_back=rolled_back, rollback_cost=Decimal("20")))
        expected = calc.expected_rollback_cost(Decimal("20"))
        assert expected == Decimal("5")

    def test_rollback_window_is_30(self):
        calc = CostCalculator(FeeModel(), CEXOrderbookSlippage())
        # Add 35 trades — first 5 are not rolled back (will be evicted by window)
        for _ in range(5):
            calc.record_trade(TradeOutcome(rolled_back=False))
        for _ in range(30):
            calc.record_trade(TradeOutcome(rolled_back=True, rollback_cost=Decimal("10")))
        # Window of 30, all rolled back → P=1.0
        assert calc.rollback_probability() == Decimal("1")

    def test_zero_rollback_probability_when_no_failures(self):
        calc = CostCalculator(FeeModel(), CEXOrderbookSlippage())
        for _ in range(10):
            calc.record_trade(TradeOutcome(rolled_back=False))
        assert calc.rollback_probability() == Decimal("0")
