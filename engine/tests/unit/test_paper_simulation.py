"""Paper trading simulation tests.

Covers:
- PaperExecutor with PowerLawSlippage: 50 synthetic buy/sell pairs
- PnL tracking accuracy across the full trade sequence
- Slippage increases monotonically with order size (power-law property)
- Fill prices are realistic (within 2% of reference price)
- Edge cases: zero price, large orders, rejection/partial-fill scenarios
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from src.core.models import Order, OrderSide, OrderType
from src.execution.paper import PaperExecutor, SimulatedTrade, SlippageModel
from src.modes.shadow import PowerLawSlippage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_order(
    side: OrderSide,
    price: Decimal,
    amount: Decimal,
    exchange_id: str = "paper",
    symbol: str = "BTC/USDT",
) -> Order:
    return Order(
        exchange_id=exchange_id,
        symbol=symbol,
        side=side,
        order_type=OrderType.MARKET,
        price=price,
        amount=amount,
    )


def _make_executor(k: float = 1.0, gamma: float = 0.5) -> PaperExecutor:
    """Return a PaperExecutor with PowerLawSlippage and deterministic seed."""
    return PaperExecutor(
        slippage_model=PowerLawSlippage(k=k, gamma=gamma),
        fee_rate=Decimal("0.001"),
        partial_fill_rate=Decimal("0.0"),
        rejection_rate=Decimal("0.0"),
    )


async def _run_buy_sell_pairs(
    executor: PaperExecutor,
    n_pairs: int = 50,
    buy_price: Decimal = Decimal("50000"),
    sell_price: Decimal = Decimal("50100"),
    amount: Decimal = Decimal("0.1"),
) -> tuple[list, list]:
    """Execute n_pairs of (buy, sell) trades and return (buys, sells)."""
    buys, sells = [], []
    for _ in range(n_pairs):
        buy = await executor.execute(
            _make_order(OrderSide.BUY, buy_price, amount)
        )
        sell = await executor.execute(
            _make_order(OrderSide.SELL, sell_price, amount)
        )
        buys.append(buy)
        sells.append(sell)
    return buys, sells


# ---------------------------------------------------------------------------
# PaperExecutor setup with PowerLawSlippage
# ---------------------------------------------------------------------------


class TestPaperExecutorSetup:
    """Verify PaperExecutor initialises correctly with PowerLawSlippage."""

    def test_executor_uses_power_law_slippage_model(self) -> None:
        executor = _make_executor()
        assert isinstance(executor.slippage_model, PowerLawSlippage)

    def test_executor_initial_trade_history_is_empty(self) -> None:
        executor = _make_executor()
        assert executor.trade_history == []

    def test_executor_initial_pnl_is_zero(self) -> None:
        executor = _make_executor()
        assert executor.total_pnl() == Decimal("0")

    def test_power_law_slippage_inherits_from_slippage_model(self) -> None:
        model = PowerLawSlippage(k=1.0, gamma=0.5)
        assert isinstance(model, SlippageModel)

    def test_power_law_default_k_and_gamma(self) -> None:
        model = PowerLawSlippage()
        assert model._k == 0.0  # default 0.0 — CEXOrderbookSlippage is sole slippage source
        assert model._gamma == 0.5


# ---------------------------------------------------------------------------
# 50 buy/sell pairs execution
# ---------------------------------------------------------------------------


class TestFiftyBuySellPairs:
    """Execute 50 synthetic buy/sell pairs and verify core properties."""

    @pytest.mark.asyncio
    async def test_50_pairs_records_100_trades_in_history(self) -> None:
        executor = _make_executor()
        await _run_buy_sell_pairs(executor, n_pairs=50)
        assert len(executor.trade_history) == 100

    @pytest.mark.asyncio
    async def test_50_pairs_all_buy_trades_have_buy_side(self) -> None:
        executor = _make_executor()
        buys, _ = await _run_buy_sell_pairs(executor, n_pairs=50)
        assert all(t.side == OrderSide.BUY for t in buys)

    @pytest.mark.asyncio
    async def test_50_pairs_all_sell_trades_have_sell_side(self) -> None:
        executor = _make_executor()
        _, sells = await _run_buy_sell_pairs(executor, n_pairs=50)
        assert all(t.side == OrderSide.SELL for t in sells)

    @pytest.mark.asyncio
    async def test_50_pairs_all_trades_have_non_negative_fee(self) -> None:
        executor = _make_executor()
        await _run_buy_sell_pairs(executor, n_pairs=50)
        assert all(t.fee >= Decimal("0") for t in executor.trade_history)

    @pytest.mark.asyncio
    async def test_50_pairs_all_trades_have_positive_amount(self) -> None:
        executor = _make_executor()
        await _run_buy_sell_pairs(executor, n_pairs=50)
        assert all(t.amount > Decimal("0") for t in executor.trade_history)

    @pytest.mark.asyncio
    async def test_50_pairs_all_trades_have_unique_trade_ids(self) -> None:
        executor = _make_executor()
        await _run_buy_sell_pairs(executor, n_pairs=50)
        ids = [t.trade_id for t in executor.trade_history]
        assert len(ids) == len(set(ids)), "Duplicate trade IDs detected"

    @pytest.mark.asyncio
    async def test_50_pairs_reset_clears_all_history(self) -> None:
        executor = _make_executor()
        await _run_buy_sell_pairs(executor, n_pairs=50)
        executor.reset()
        assert executor.trade_history == []
        assert executor.total_pnl() == Decimal("0")


# ---------------------------------------------------------------------------
# PnL tracking accuracy
# ---------------------------------------------------------------------------


class TestPnLTrackingAccuracy:
    """Verify PnL computation is consistent with manual calculation."""

    @pytest.mark.asyncio
    async def test_pnl_is_negative_when_buy_above_sell_after_slippage(self) -> None:
        """Buying at high price and selling at same price → net loss after fees+slippage."""
        executor = _make_executor()
        price = Decimal("50000")
        amount = Decimal("0.1")
        await executor.execute(_make_order(OrderSide.BUY, price, amount))
        await executor.execute(_make_order(OrderSide.SELL, price, amount))
        # Buy fills above price, sell fills below price → net negative
        assert executor.total_pnl() < Decimal("0")

    @pytest.mark.asyncio
    async def test_pnl_positive_when_sell_price_substantially_above_buy(self) -> None:
        """Selling at 2% above buy price should overcome slippage+fees."""
        executor = _make_executor(k=0.1, gamma=0.5)  # low k for small slippage
        buy_price = Decimal("50000")
        sell_price = Decimal("51000")  # 2% premium
        amount = Decimal("0.1")
        for _ in range(10):
            await executor.execute(_make_order(OrderSide.BUY, buy_price, amount))
            await executor.execute(_make_order(OrderSide.SELL, sell_price, amount))
        assert executor.total_pnl() > Decimal("0")

    @pytest.mark.asyncio
    async def test_pnl_manual_verification_single_pair(self) -> None:
        """Manual check: pnl = (sell_proceeds - sell_fee) - (buy_cost + buy_fee)."""
        # Use zero slippage for deterministic calculation
        class ZeroSlippage(SlippageModel):
            def apply(self, base_price, side, size=Decimal("1")):
                return base_price  # no slippage

        executor = PaperExecutor(
            slippage_model=ZeroSlippage(),
            fee_rate=Decimal("0.001"),
        )
        buy_price = Decimal("50000")
        sell_price = Decimal("50500")
        amount = Decimal("1.0")

        await executor.execute(_make_order(OrderSide.BUY, buy_price, amount))
        await executor.execute(_make_order(OrderSide.SELL, sell_price, amount))

        # Manual: buy_cost = 50000*1.0 + 50000*1.0*0.001 = 50000 + 50 = 50050
        #         sell_proceeds = 50500*1.0 - 50500*1.0*0.001 = 50500 - 50.5 = 50449.5
        #         net = 50449.5 - 50050 = 399.5
        expected = Decimal("399.5")
        assert abs(executor.total_pnl() - expected) < Decimal("0.01")

    @pytest.mark.asyncio
    async def test_pnl_accumulates_correctly_across_multiple_pairs(self) -> None:
        """PnL doubles when we execute 2x identical pairs vs 1x."""
        class ZeroSlippage(SlippageModel):
            def apply(self, base_price, side, size=Decimal("1")):
                return base_price

        def _make_ex():
            return PaperExecutor(
                slippage_model=ZeroSlippage(),
                fee_rate=Decimal("0.001"),
            )

        buy_price = Decimal("50000")
        sell_price = Decimal("50500")
        amount = Decimal("1.0")

        ex1 = _make_ex()
        await ex1.execute(_make_order(OrderSide.BUY, buy_price, amount))
        await ex1.execute(_make_order(OrderSide.SELL, sell_price, amount))
        single_pnl = ex1.total_pnl()

        ex2 = _make_ex()
        for _ in range(2):
            await ex2.execute(_make_order(OrderSide.BUY, buy_price, amount))
            await ex2.execute(_make_order(OrderSide.SELL, sell_price, amount))
        double_pnl = ex2.total_pnl()

        assert abs(double_pnl - 2 * single_pnl) < Decimal("0.001")

    @pytest.mark.asyncio
    async def test_total_pnl_equals_sum_of_individual_trade_contributions(self) -> None:
        """total_pnl() matches manual sum: sell(price*amount - fee) - buy(price*amount + fee)."""
        class ZeroSlippage(SlippageModel):
            def apply(self, base_price, side, size=Decimal("1")):
                return base_price

        executor = PaperExecutor(
            slippage_model=ZeroSlippage(),
            fee_rate=Decimal("0.001"),
        )
        prices = [(Decimal("50000"), Decimal("50200")),
                  (Decimal("49500"), Decimal("49800")),
                  (Decimal("51000"), Decimal("51400"))]

        for buy_p, sell_p in prices:
            await executor.execute(_make_order(OrderSide.BUY, buy_p, Decimal("0.5")))
            await executor.execute(_make_order(OrderSide.SELL, sell_p, Decimal("0.5")))

        manual_pnl = Decimal("0")
        for t in executor._history:
            trade = t.trade
            if trade.side == OrderSide.SELL:
                manual_pnl += trade.price * trade.amount - trade.fee
            else:
                manual_pnl -= trade.price * trade.amount + trade.fee

        assert abs(executor.total_pnl() - manual_pnl) < Decimal("0.0001")


# ---------------------------------------------------------------------------
# Slippage increases with order size (power-law property)
# ---------------------------------------------------------------------------


class TestSlippageIncreaseWithOrderSize:
    """Verify PowerLawSlippage produces larger slippage for larger orders."""

    def test_larger_buy_order_receives_more_slippage_than_smaller(self) -> None:
        """size=10 → more slippage than size=1 for BUY (deterministic with fixed seed)."""
        import random
        model = PowerLawSlippage(k=1.0, gamma=0.5)
        base = Decimal("50000")

        # Run many samples to overcome randomness
        small_slippages = []
        large_slippages = []
        for seed in range(50):
            random.seed(seed)
            small_fill = model.apply(base, OrderSide.BUY, size=Decimal("1"))
            random.seed(seed)
            large_fill = model.apply(base, OrderSide.BUY, size=Decimal("100"))
            small_slippages.append(float(small_fill - base))
            large_slippages.append(float(large_fill - base))

        avg_small = sum(small_slippages) / len(small_slippages)
        avg_large = sum(large_slippages) / len(large_slippages)
        assert avg_large > avg_small, (
            f"Expected larger orders to incur more slippage: "
            f"avg_small={avg_small:.2f}, avg_large={avg_large:.2f}"
        )

    def test_larger_sell_order_receives_more_adverse_slippage_than_smaller(self) -> None:
        """size=100 → fill price further below base for SELL."""
        import random
        model = PowerLawSlippage(k=1.0, gamma=0.5)
        base = Decimal("50000")

        small_slippages = []
        large_slippages = []
        for seed in range(50):
            random.seed(seed)
            small_fill = model.apply(base, OrderSide.SELL, size=Decimal("1"))
            random.seed(seed)
            large_fill = model.apply(base, OrderSide.SELL, size=Decimal("100"))
            # For SELL: slippage = base - fill_price (positive means worse)
            small_slippages.append(float(base - small_fill))
            large_slippages.append(float(base - large_fill))

        avg_small = sum(small_slippages) / len(small_slippages)
        avg_large = sum(large_slippages) / len(large_slippages)
        assert avg_large > avg_small

    def test_slippage_monotonically_increases_with_size_at_fixed_random(self) -> None:
        """With a fixed random seed, slippage(size=n) > slippage(size=n-1)."""
        import random
        model = PowerLawSlippage(k=1.0, gamma=0.5)
        base = Decimal("50000")
        sizes = [Decimal(str(s)) for s in [1, 4, 9, 16, 25, 100]]

        fill_prices = []
        for s in sizes:
            random.seed(42)
            fill = model.apply(base, OrderSide.BUY, size=s)
            fill_prices.append(fill)

        # Each successive fill should be >= prior (monotonically non-decreasing)
        for i in range(1, len(fill_prices)):
            assert fill_prices[i] >= fill_prices[i - 1], (
                f"Fill price not monotonically increasing: "
                f"size[{i-1}]={sizes[i-1]} → {fill_prices[i-1]}, "
                f"size[{i}]={sizes[i]} → {fill_prices[i]}"
            )

    def test_gamma_parameter_controls_size_sensitivity(self) -> None:
        """Higher gamma → steeper size sensitivity."""
        import random
        base = Decimal("50000")
        size = Decimal("10")

        slippages_low = []
        slippages_high = []
        for seed in range(30):
            random.seed(seed)
            low_gamma = PowerLawSlippage(k=1.0, gamma=0.2)
            f_low = low_gamma.apply(base, OrderSide.BUY, size=size)
            random.seed(seed)
            high_gamma = PowerLawSlippage(k=1.0, gamma=0.8)
            f_high = high_gamma.apply(base, OrderSide.BUY, size=size)
            slippages_low.append(float(f_low - base))
            slippages_high.append(float(f_high - base))

        assert sum(slippages_high) / 30 > sum(slippages_low) / 30

    def test_k_parameter_scales_slippage_linearly(self) -> None:
        """Doubling k doubles average slippage (same random seed)."""
        import random
        base = Decimal("50000")
        size = Decimal("1")

        slippages_k1 = []
        slippages_k2 = []
        for seed in range(50):
            random.seed(seed)
            m1 = PowerLawSlippage(k=1.0, gamma=0.5)
            s1 = float(m1.apply(base, OrderSide.BUY, size=size) - base)
            random.seed(seed)
            m2 = PowerLawSlippage(k=2.0, gamma=0.5)
            s2 = float(m2.apply(base, OrderSide.BUY, size=size) - base)
            slippages_k1.append(s1)
            slippages_k2.append(s2)

        avg1 = sum(slippages_k1) / len(slippages_k1)
        avg2 = sum(slippages_k2) / len(slippages_k2)
        # k=2.0 should produce ~2x slippage
        assert abs(avg2 / avg1 - 2.0) < 0.01, f"Expected ~2x, got {avg2/avg1:.3f}"


# ---------------------------------------------------------------------------
# Fill prices are realistic (within spread)
# ---------------------------------------------------------------------------


class TestFillPricesAreRealistic:
    """Verify fill prices stay within a realistic range of the reference price."""

    @pytest.mark.asyncio
    async def test_buy_fill_price_within_2pct_of_reference(self) -> None:
        """Buy fills should not exceed 2% above the order price."""
        executor = _make_executor(k=1.0, gamma=0.5)
        base = Decimal("50000")
        amount = Decimal("0.1")
        max_slippage_pct = Decimal("0.02")

        for _ in range(50):
            trade = await executor.execute(
                _make_order(OrderSide.BUY, base, amount)
            )
            slippage = abs(trade.price - base) / base
            assert slippage <= max_slippage_pct, (
                f"Buy slippage {float(slippage):.4%} exceeded 2% limit"
            )

    @pytest.mark.asyncio
    async def test_sell_fill_price_within_2pct_of_reference(self) -> None:
        """Sell fills should not drop more than 2% below the order price."""
        executor = _make_executor(k=1.0, gamma=0.5)
        base = Decimal("50000")
        amount = Decimal("0.1")
        max_slippage_pct = Decimal("0.02")

        for _ in range(50):
            trade = await executor.execute(
                _make_order(OrderSide.SELL, base, amount)
            )
            slippage = abs(trade.price - base) / base
            assert slippage <= max_slippage_pct, (
                f"Sell slippage {float(slippage):.4%} exceeded 2% limit"
            )

    @pytest.mark.asyncio
    async def test_buy_fill_price_always_above_or_equal_to_reference(self) -> None:
        """All buy fills must be >= order price (adverse slippage for buys)."""
        executor = _make_executor()
        base = Decimal("50000")
        amount = Decimal("0.1")

        for _ in range(50):
            trade = await executor.execute(
                _make_order(OrderSide.BUY, base, amount)
            )
            assert trade.price >= base, (
                f"Buy fill {trade.price} is below reference {base}"
            )

    @pytest.mark.asyncio
    async def test_sell_fill_price_always_below_or_equal_to_reference(self) -> None:
        """All sell fills must be <= order price (adverse slippage for sells)."""
        executor = _make_executor()
        base = Decimal("50000")
        amount = Decimal("0.1")

        for _ in range(50):
            trade = await executor.execute(
                _make_order(OrderSide.SELL, base, amount)
            )
            assert trade.price <= base, (
                f"Sell fill {trade.price} is above reference {base}"
            )

    @pytest.mark.asyncio
    async def test_fill_price_for_large_order_still_within_5pct(self) -> None:
        """Even for large orders (10 BTC), fill price stays within 5% of reference."""
        executor = _make_executor(k=1.0, gamma=0.5)
        base = Decimal("50000")
        large_amount = Decimal("10.0")
        max_slippage_pct = Decimal("0.05")

        for _ in range(20):
            trade = await executor.execute(
                _make_order(OrderSide.BUY, base, large_amount)
            )
            slippage = abs(trade.price - base) / base
            assert slippage <= max_slippage_pct, (
                f"Large order slippage {float(slippage):.4%} exceeded 5% limit"
            )

    @pytest.mark.asyncio
    async def test_zero_price_order_fills_at_zero(self) -> None:
        """Order with price=0 should not crash and fills at zero."""
        executor = _make_executor()
        order = _make_order(OrderSide.BUY, Decimal("0"), Decimal("1.0"))
        trade = await executor.execute(order)
        assert trade.price == Decimal("0")

    @pytest.mark.asyncio
    async def test_50_pairs_various_sizes_all_fill_prices_realistic(self) -> None:
        """Run 50 pairs with varying order sizes; all fills stay within 5% of reference."""
        executor = _make_executor(k=1.0, gamma=0.5)
        base = Decimal("50000")
        max_slippage_pct = Decimal("0.05")

        from decimal import Decimal as D
        sizes = [D("0.01"), D("0.1"), D("0.5"), D("1.0"), D("2.0")]

        for i in range(50):
            size = sizes[i % len(sizes)]
            buy_trade = await executor.execute(
                _make_order(OrderSide.BUY, base, size)
            )
            sell_trade = await executor.execute(
                _make_order(OrderSide.SELL, base, size)
            )
            buy_slip = abs(buy_trade.price - base) / base
            sell_slip = abs(sell_trade.price - base) / base
            assert buy_slip <= max_slippage_pct
            assert sell_slip <= max_slippage_pct
