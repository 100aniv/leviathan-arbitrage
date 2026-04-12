"""Total friction cost aggregator (Amendment 3D).

Net_Profit = Gross_Spread
           - Fee_Buy - Fee_Sell
           - Slippage_Buy - Slippage_Sell
           - Network_Cost - Funding_Cost - Opportunity_Cost
           - E[Rollback_Cost]

E[Rollback_Cost] = P(rollback) * Avg_Rollback_Cost
P(rollback) from rolling 30-trade window; cold-start default = 5%.
"""
from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass
from decimal import Decimal

from src.core.models import OrderSide
from src.core.order_book import OrderBook
from src.friction.fee_model import FeeModel
from src.friction.slippage_model import CEXOrderbookSlippage


@dataclass
class TradeOutcome:
    rolled_back: bool
    rollback_cost: Decimal = Decimal("0")


@dataclass
class FrictionCost:
    fee_buy: Decimal
    fee_sell: Decimal
    slippage_buy: Decimal
    slippage_sell: Decimal
    network_cost: Decimal
    funding_cost: Decimal
    opportunity_cost: Decimal
    rollback_cost_expected: Decimal
    gross_spread: Decimal

    @property
    def total_cost(self) -> Decimal:
        return (
            self.fee_buy
            + self.fee_sell
            + self.slippage_buy
            + self.slippage_sell
            + self.network_cost
            + self.funding_cost
            + self.opportunity_cost
            + self.rollback_cost_expected
        )

    @property
    def net_profit(self) -> Decimal:
        return self.gross_spread - self.total_cost


class CostCalculator:
    """
    Complete friction cost calculator for cross-exchange arbitrage.

    Net_Profit = Gross_Spread - Fee_Buy - Fee_Sell - Slippage_Buy - Slippage_Sell
                 - Network_Cost - Funding_Cost - Opportunity_Cost - E[Rollback_Cost]
    """

    ROLLBACK_WINDOW = 30

    def __init__(
        self,
        fee_model: FeeModel,
        slippage_model: CEXOrderbookSlippage | None = None,
        network_cost: Decimal = Decimal("0"),
        funding_cost: Decimal = Decimal("0"),
        opportunity_cost: Decimal = Decimal("0"),
        transfer_coin: str = "XRP",
    ) -> None:
        self._fee_model = fee_model
        self._slippage_model = slippage_model or CEXOrderbookSlippage()
        self._network_cost = network_cost
        self._funding_cost = funding_cost
        self._opportunity_cost = opportunity_cost
        self._transfer_coin = transfer_coin
        self._trade_history: deque[TradeOutcome] = deque(maxlen=self.ROLLBACK_WINDOW)

    def record_trade(self, outcome: TradeOutcome) -> None:
        """Record trade outcome to update rolling rollback probability."""
        self._trade_history.append(outcome)

    @staticmethod
    def _is_rollback_disabled() -> bool:
        return os.getenv("DISABLE_ROLLBACK_COST", "").lower() in ("true", "1", "yes")

    def rollback_probability(self) -> Decimal:
        """P(rollback) from rolling 30-trade window. Returns 5% cold-start if no history."""
        if not self._trade_history:
            return Decimal("0") if self._is_rollback_disabled() else Decimal("0.05")
        rolled_back = sum(1 for t in self._trade_history if t.rolled_back)
        return Decimal(rolled_back) / Decimal(len(self._trade_history))

    def avg_rollback_cost_from_history(self) -> Decimal:
        """Compute average rollback cost from recorded rolled-back trades."""
        costs = [t.rollback_cost for t in self._trade_history if t.rolled_back]
        if not costs:
            return Decimal("0")
        return sum(costs) / Decimal(len(costs))

    def expected_rollback_cost(self, avg_rollback_cost: Decimal) -> Decimal:
        """E[Rollback_Cost] = P(rollback) * Avg_Rollback_Cost."""
        return self.rollback_probability() * avg_rollback_cost

    def estimate_cost(
        self,
        exchange_id: str,
        symbol: str,
        side: OrderSide,
        size: Decimal,
        price: Decimal,
        dest_exchange_id: str | None = None,
    ) -> Decimal:
        """Estimate cost in USDT for one trade leg (Protocol bridge).

        Satisfies strategies/base.py CostCalculator Protocol.
        Returns taker_fee + network_cost for the given notional (price * size).
        Note: slippage is excluded here — it is applied upstream by SignalGenerator
        (CEXOrderbookSlippage pre-filter) and cannot be computed without an orderbook.

        Deprecated: prefer calculate() for full two-leg friction with slippage.
        US-247: network_cost=0 when both legs are on the same exchange (no transfer needed).
        """
        ex = exchange_id.removeprefix("paper_").removeprefix("sandbox_")
        notional = price * size
        fee = self._fee_model.taker_fee(ex, notional)
        # US-247: Skip network transfer cost for intra-exchange trades (e.g. triangular)
        dest_ex = dest_exchange_id.removeprefix("paper_").removeprefix("sandbox_") if dest_exchange_id else None
        network_cost = Decimal("0") if (dest_ex is not None and dest_ex == ex) else self._network_cost
        # US-247: Add expected rollback cost (avg $5 per rollback event)
        rollback_cost = self.expected_rollback_cost(Decimal("5"))
        return fee + network_cost + rollback_cost

    def estimate_futures_cost(
        self,
        buy_exchange: str,
        sell_exchange: str,
        buy_notional: Decimal,
        sell_notional: Decimal,
        entry_only: bool = False,
    ) -> Decimal:
        """Futures cost: entry (+ exit) taker fees + rollback (no network transfer).

        A convergence arbitrage is a 4-leg round trip:
          - Entry:  buy on buy_exchange  +  sell on sell_exchange
          - Exit:   sell on buy_exchange +  buy on sell_exchange

        entry_only=True: signal-generation gate — only entry fees considered.
          Exit fees are incurred at close time, not at signal time.
        entry_only=False (default): full round-trip cost for post-trade accounting.

        BUG-CRITICAL note: prior version omitted exit fees (50% underestimate).
        entry_only flag preserves that correction for post-trade use while allowing
        signal-time filtering to use realistic entry-only thresholds.
        """
        buy_ex = buy_exchange.removeprefix("paper_").removeprefix("sandbox_")
        sell_ex = sell_exchange.removeprefix("paper_").removeprefix("sandbox_")
        # Entry fees
        fee_buy_entry = self._fee_model.taker_fee(buy_ex, buy_notional)
        fee_sell_entry = self._fee_model.taker_fee(sell_ex, sell_notional)
        # Rollback: cost to emergency-close one leg ≈ buy-side taker fee on avg notional
        avg_notional = (buy_notional + sell_notional) / 2
        avg_rollback_usd = self._fee_model.taker_fee(buy_ex, avg_notional)
        rollback_cost = self.expected_rollback_cost(avg_rollback_usd)
        if entry_only:
            return fee_buy_entry + fee_sell_entry + rollback_cost
        # Exit fees (convergence: reverse each leg at similar notional)
        fee_buy_exit = self._fee_model.taker_fee(buy_ex, buy_notional)
        fee_sell_exit = self._fee_model.taker_fee(sell_ex, sell_notional)
        return fee_buy_entry + fee_sell_entry + fee_buy_exit + fee_sell_exit + rollback_cost

    def calculate(
        self,
        buy_exchange: str,
        sell_exchange: str,
        buy_book: OrderBook,
        sell_book: OrderBook,
        size: Decimal,
        buy_price: Decimal,
        sell_price: Decimal,
        adv: Decimal = Decimal("1000"),
        sigma: Decimal = Decimal("0.001"),
        avg_rollback_cost: Decimal | None = None,
        transfer_coin: str | None = None,
    ) -> FrictionCost:
        """
        Compute complete friction cost for one arbitrage trade leg.

        Args:
            buy_exchange:      Exchange where we buy (taker).
            sell_exchange:     Exchange where we sell (taker).
            buy_book:          Orderbook for buy exchange (for slippage).
            sell_book:         Orderbook for sell exchange (for slippage).
            size:              Trade size in base asset.
            buy_price:         Expected buy execution price.
            sell_price:        Expected sell execution price.
            adv:               Average daily volume (base asset units).
            sigma:             Price volatility fraction.
            avg_rollback_cost: Average cost per rollback event (USD).
        """
        if avg_rollback_cost is None:
            avg_rollback_cost = self.avg_rollback_cost_from_history()

        buy_notional = buy_price * size
        sell_notional = sell_price * size
        gross_spread = sell_notional - buy_notional

        # Normalize exchange IDs — strip paper_/sandbox_ prefixes for fee lookup
        buy_ex = buy_exchange.removeprefix("paper_").removeprefix("sandbox_")
        sell_ex = sell_exchange.removeprefix("paper_").removeprefix("sandbox_")

        fee_buy = self._fee_model.taker_fee(buy_ex, buy_notional)
        fee_sell = self._fee_model.taker_fee(sell_ex, sell_notional)

        slip_buy = self._slippage_model.predict(buy_book, size, adv, sigma)
        slip_sell = self._slippage_model.predict(sell_book, size, adv, sigma)

        rollback_cost_exp = self.expected_rollback_cost(avg_rollback_cost)

        # Network cost: use static value if explicitly set (non-zero),
        # otherwise compute dynamically from withdrawal fee lookup
        # Use per-call transfer_coin if provided, else fall back to constructor default
        coin = transfer_coin or self._transfer_coin
        network_cost = self._network_cost
        if network_cost == Decimal("0") and hasattr(self._fee_model, "network_cost"):
            network_cost = self._fee_model.network_cost(
                buy_ex, sell_ex, coin
            )

        return FrictionCost(
            fee_buy=fee_buy,
            fee_sell=fee_sell,
            slippage_buy=slip_buy.expected,
            slippage_sell=slip_sell.expected,
            network_cost=network_cost,
            funding_cost=self._funding_cost,
            opportunity_cost=self._opportunity_cost,
            rollback_cost_expected=rollback_cost_exp,
            gross_spread=gross_spread,
        )
