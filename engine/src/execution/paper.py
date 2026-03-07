"""Paper trading executor — simulates fills with slippage model."""
from __future__ import annotations

import random
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

from src.core.models import Order, OrderSide, Trade


class OrderRejectedError(Exception):
    """Raised when a simulated order is rejected."""


class SlippageModel:
    """
    Slippage model for paper trading fill simulation.

    Applies adverse slippage: buys fill higher, sells fill lower.
    base_slippage_pct: base slippage percentage (e.g. 0.001 = 0.1%)
    volatility_factor: multiplier on random component
    """

    def __init__(
        self,
        base_slippage_pct: Decimal = Decimal("0.001"),
        volatility_factor: Decimal = Decimal("1.0"),
    ) -> None:
        self.base_slippage_pct = base_slippage_pct
        self.volatility_factor = volatility_factor

    def apply(
        self, base_price: Decimal, side: OrderSide, size: Decimal = Decimal("1")
    ) -> Decimal:
        """
        Return fill price with adverse slippage applied.

        Buy → price increases. Sell → price decreases.
        Includes a small random component for realism.

        Args:
            base_price: Reference price before slippage.
            side: BUY or SELL.
            size: Order size (used by subclasses like PowerLawSlippage).
        """
        random_component = Decimal(str(random.uniform(0.0, 0.5))) * self.volatility_factor
        total_slippage = self.base_slippage_pct * (Decimal("1") + random_component)

        if side == OrderSide.BUY:
            return base_price * (Decimal("1") + total_slippage)
        else:
            return base_price * (Decimal("1") - total_slippage)


@dataclass
class SimulatedTrade:
    """Record of a simulated paper trade."""

    trade: Trade
    order: Order
    slippage_pct: Decimal
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class PaperExecutor:
    """
    Paper trading executor.

    Simulates exchange fills with configurable:
    - slippage model
    - partial fill rate (0.0 = no partial fills, 1.0 = always partial)
    - rejection rate (0.0 = never, 1.0 = always)
    - fee rate
    """

    def __init__(
        self,
        slippage_model: SlippageModel | None = None,
        fee_rate: Decimal = Decimal("0.001"),
        partial_fill_rate: Decimal = Decimal("0.0"),
        rejection_rate: Decimal = Decimal("0.0"),
        on_trade: Callable[[SimulatedTrade], None] | None = None,
    ) -> None:
        self.slippage_model = slippage_model or SlippageModel()
        self.fee_rate = fee_rate
        self.partial_fill_rate = partial_fill_rate
        self.rejection_rate = rejection_rate
        self.on_trade = on_trade
        self._history: deque[SimulatedTrade] = deque(maxlen=10_000)

    @property
    def trade_history(self) -> list[Trade]:
        return list(r.trade for r in self._history)

    async def execute(self, order: Order) -> Trade:
        """
        Simulate order execution.

        Raises OrderRejectedError if rejection_rate triggers.
        Returns Trade with simulated fill price, amount, fee.
        """
        # Rejection scenario
        if float(self.rejection_rate) > 0.0:
            if random.random() < float(self.rejection_rate):
                raise OrderRejectedError(
                    f"Simulated rejection for order on {order.exchange_id}"
                )

        # Determine fill amount (partial fill scenario)
        fill_amount = order.amount
        if float(self.partial_fill_rate) > 0.0 and random.random() < float(self.partial_fill_rate):
            # Partial fill: 50%-99% of requested amount
            partial_pct = Decimal(str(random.uniform(0.5, 0.99)))
            fill_amount = order.amount * partial_pct

        # Apply slippage to price (pass size for power-law models)
        base_price = order.price or Decimal("0")
        fill_price = self.slippage_model.apply(base_price, order.side, fill_amount) if base_price > 0 else Decimal("0")

        # Compute slippage percentage for recording
        if base_price > 0:
            slippage_pct = abs(fill_price - base_price) / base_price
        else:
            slippage_pct = Decimal("0")

        # Compute fee
        fee = fill_price * fill_amount * self.fee_rate

        trade = Trade(
            trade_id=str(uuid.uuid4()),
            order_id=order.order_id,
            exchange_id=order.exchange_id,
            symbol=order.symbol,
            side=order.side,
            price=fill_price,
            amount=fill_amount,
            fee=fee,
            fee_currency="USDT",
        )

        record = SimulatedTrade(
            trade=trade,
            order=order,
            slippage_pct=slippage_pct,
        )
        self._history.append(record)

        if self.on_trade:
            self.on_trade(record)

        return trade

    def total_pnl(self) -> Decimal:
        """
        Sum of PnL across all simulated trades.

        PnL per trade = sell proceeds - buy cost - fees.
        Positive = profit, negative = loss.
        """
        total = Decimal("0")
        for record in self._history:
            t = record.trade
            if t.side == OrderSide.SELL:
                total += t.price * t.amount - t.fee
            else:
                total -= t.price * t.amount + t.fee
        return total

    def reset(self) -> None:
        """Clear all recorded trade history."""
        self._history.clear()
