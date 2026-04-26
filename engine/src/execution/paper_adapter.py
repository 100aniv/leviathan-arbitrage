"""Paper exchange adapter — synthetic orderbook generation + simulated execution.

Implements the ExchangeAdapter protocol using PaperExecutor for fills and
geometric Brownian motion for synthetic orderbook generation. Designed for
paper trading mode with configurable spread injection between exchanges.
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

from src.core.models import (
    Balance,
    FeeRate,
    Order,
    OrderBook,
    OrderBookLevel,
    Position,
    Trade,
)
from src.execution.paper import PaperExecutor

logger = logging.getLogger(__name__)

# Default GBM parameters
_DEFAULT_BASE_PRICE = Decimal("50000")  # BTC/USDT starting price
_DEFAULT_VOLATILITY = 0.0002  # Per-tick volatility (annualised equivalent ~20%)
_DEFAULT_DRIFT = 0.0  # No drift for fair simulation
_DEFAULT_TICK_INTERVAL = 0.1  # 100ms between ticks
_DEFAULT_BOOK_DEPTH = 10  # Levels per side
_DEFAULT_LEVEL_STEP_BPS = 5  # 5 basis points between levels
_DEFAULT_LEVEL_SIZE = Decimal("0.5")  # Base amount per level


class PaperExchangeAdapter:
    """
    Paper trading adapter implementing the ExchangeAdapter protocol.

    Features:
    - Synthetic orderbook generation via geometric Brownian motion (GBM)
    - Simulated order execution via PaperExecutor
    - Configurable spread injection for arbitrage opportunity simulation
    - Tracks simulated balances starting with initial_capital in USDT
    """

    def __init__(
        self,
        exchange_id: str = "paper_binance",
        initial_capital: Decimal = Decimal("70"),
        paper_executor: PaperExecutor | None = None,
        base_price: Decimal = _DEFAULT_BASE_PRICE,
        volatility: float = _DEFAULT_VOLATILITY,
        drift: float = _DEFAULT_DRIFT,
        tick_interval: float = _DEFAULT_TICK_INTERVAL,
        book_depth: int = _DEFAULT_BOOK_DEPTH,
        level_step_bps: int = _DEFAULT_LEVEL_STEP_BPS,
        spread_injection_rate: float = 0.0,
        spread_injection_bps: int = 30,
        fee_maker: Decimal = Decimal("0.001"),
        fee_taker: Decimal = Decimal("0.001"),
    ) -> None:
        self.exchange_id = exchange_id
        self._executor = paper_executor or PaperExecutor()
        self._initial_capital = initial_capital

        # GBM parameters
        self._base_price = base_price
        self._current_price = float(base_price)
        self._volatility = volatility
        self._drift = drift
        self._tick_interval = tick_interval

        # Orderbook generation
        self._book_depth = book_depth
        self._level_step_bps = level_step_bps

        # Spread injection for cross-exchange arbitrage simulation
        self._spread_injection_rate = spread_injection_rate
        self._spread_injection_bps = spread_injection_bps

        # Fee rates
        self._fee_maker = fee_maker
        self._fee_taker = fee_taker

        # Universe-matrix interface attributes.
        # _market_type distinguishes spot vs futures venues for shape routing
        # (futures_futures / spot_futures strategies depend on this).
        self._market_type = "futures" if exchange_id.endswith("_futures") else "spot"

        # Simulated balances: currency -> Balance
        self._balances: dict[str, Balance] = {
            "USDT": Balance(
                currency="USDT",
                free=initial_capital,
                used=Decimal("0"),
                total=initial_capital,
            ),
        }

        # Simulated positions
        self._positions: list[Position] = []

        # Open orders (for cancel tracking)
        self._open_orders: dict[str, Order] = {}

        # Subscription tasks
        self._subscription_tasks: list[asyncio.Task[None]] = []

    # -------------------------------------------------------------------
    # ExchangeAdapter protocol: connection lifecycle
    # -------------------------------------------------------------------

    async def connect(self) -> None:
        """No-op for paper adapter."""
        logger.info("Paper adapter '%s' connected", self.exchange_id)

    # -------------------------------------------------------------------
    # Universe-matrix interface: spec from ExchangeAdapter Protocol
    # -------------------------------------------------------------------

    def supports_symbol(self, symbol: str) -> bool:
        """Paper venues accept any symbol — synthetic book generated on demand."""
        return True

    async def get_min_notional(self, symbol: str) -> Decimal:
        """Minimal floor for paper trading — real venues typically $5-20."""
        return Decimal("5")

    async def disconnect(self) -> None:
        """Cancel all subscription tasks."""
        for task in self._subscription_tasks:
            task.cancel()
        self._subscription_tasks.clear()
        logger.info("Paper adapter '%s' disconnected", self.exchange_id)

    # -------------------------------------------------------------------
    # ExchangeAdapter protocol: market data
    # -------------------------------------------------------------------

    async def subscribe_orderbook(
        self, symbol: str, callback: Callable[[OrderBook], None]
    ) -> None:
        """
        Start async loop generating synthetic orderbooks and calling callback.

        Uses geometric Brownian motion to evolve the mid price, then builds
        a synthetic orderbook around it. Optionally injects spread offsets
        at spread_injection_rate to simulate arbitrage opportunities.
        """

        async def _orderbook_loop() -> None:
            while True:
                try:
                    # Evolve price via GBM: dS = S * (mu*dt + sigma*dW)
                    dt = self._tick_interval
                    dw = random.gauss(0, math.sqrt(dt))
                    self._current_price *= math.exp(
                        (self._drift - 0.5 * self._volatility**2) * dt
                        + self._volatility * dw
                    )

                    mid = Decimal(str(round(self._current_price, 2)))

                    # Optionally inject spread offset to create arb opportunities
                    offset_bps = Decimal("0")
                    if (
                        self._spread_injection_rate > 0
                        and random.random() < self._spread_injection_rate
                    ):
                        # Shift the mid price by injection_bps in random direction
                        direction = random.choice([1, -1])
                        offset_bps = (
                            Decimal(str(self._spread_injection_bps))
                            * Decimal("0.0001")
                            * direction
                        )
                        mid = mid * (Decimal("1") + offset_bps)

                    orderbook = self._build_orderbook(symbol, mid)
                    callback(orderbook)

                    await asyncio.sleep(self._tick_interval)
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.exception(
                        "Orderbook generation error on %s", self.exchange_id
                    )
                    await asyncio.sleep(self._tick_interval)

        task = asyncio.create_task(_orderbook_loop())
        self._subscription_tasks.append(task)

    async def subscribe_ticker(self, symbol: str, callback: Callable) -> None:
        """Ticker subscription — delegates to orderbook with mid-price extraction."""

        async def _ticker_loop() -> None:
            while True:
                try:
                    mid = Decimal(str(round(self._current_price, 2)))
                    callback(
                        {
                            "symbol": symbol,
                            "exchange_id": self.exchange_id,
                            "bid": mid * Decimal("0.9999"),
                            "ask": mid * Decimal("1.0001"),
                            "last": mid,
                            "volume": Decimal("100"),
                        }
                    )
                    await asyncio.sleep(self._tick_interval)
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.exception(
                        "Ticker generation error on %s", self.exchange_id
                    )
                    await asyncio.sleep(self._tick_interval)

        task = asyncio.create_task(_ticker_loop())
        self._subscription_tasks.append(task)

    async def get_orderbook_snapshot(
        self, symbol: str, depth: int = 20
    ) -> OrderBook:
        """Return a single synthetic orderbook snapshot."""
        mid = Decimal(str(round(self._current_price, 2)))
        return self._build_orderbook(symbol, mid, depth=depth)

    # -------------------------------------------------------------------
    # ExchangeAdapter protocol: order execution
    # -------------------------------------------------------------------

    async def place_order(self, order: Order) -> Trade:
        """
        Execute order via PaperExecutor.

        Sets the order price to current_price if not already set (market order).
        Updates simulated balances after fill.
        """
        # Fill in current price for market orders
        effective_order = order
        if order.price is None or order.price <= 0:
            effective_order = order.model_copy(
                update={"price": Decimal(str(round(self._current_price, 2)))}
            )

        trade = await self._executor.execute(effective_order)
        self._update_balances(trade)
        return trade

    async def cancel_order(self, order_id: str, symbol: str | None = None) -> bool:
        """Cancel an open order (simulated).

        Codex BLOCKING #1 (2026-04-26): symbol kwarg 추가 (Native parity).
        Paper는 symbol을 무시하지만 시그니처는 ExchangeAdapterPort에 정합.
        """
        if order_id in self._open_orders:
            del self._open_orders[order_id]
            return True
        return False

    async def cancel_all_orders(self, symbol: str | None = None) -> int:
        """Cancel all open orders. Returns count cancelled."""
        if symbol is None:
            count = len(self._open_orders)
            self._open_orders.clear()
            return count

        to_remove = [
            oid
            for oid, o in self._open_orders.items()
            if o.symbol == symbol
        ]
        for oid in to_remove:
            del self._open_orders[oid]
        return len(to_remove)

    # -------------------------------------------------------------------
    # ExchangeAdapter protocol: account data
    # -------------------------------------------------------------------

    async def get_balances(self) -> dict[str, Balance]:
        """Return current simulated balances."""
        return dict(self._balances)

    async def get_positions(self) -> list[Position]:
        """Return current simulated positions."""
        return list(self._positions)

    async def get_fee_rate(self, symbol: str) -> FeeRate:
        """Return configured fee rate."""
        return FeeRate(
            maker=self._fee_maker,
            taker=self._fee_taker,
            symbol=symbol,
            exchange_id=self.exchange_id,
        )

    async def get_lot_step(self, symbol: str) -> "Decimal":
        """Paper adapter: return a fine-grained step so lot_size_sync is a no-op."""
        from decimal import Decimal
        return Decimal("0.001")

    @property
    def health_score(self) -> float:
        """Paper adapter is always healthy."""
        return 1.0

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    def _build_orderbook(
        self,
        symbol: str,
        mid_price: Decimal,
        depth: int | None = None,
    ) -> OrderBook:
        """Build a synthetic orderbook around the given mid price."""
        effective_depth = depth if depth is not None else self._book_depth
        step = mid_price * Decimal(str(self._level_step_bps)) * Decimal("0.0001")

        # Ensure minimum step of 0.01
        if step < Decimal("0.01"):
            step = Decimal("0.01")

        bids: list[OrderBookLevel] = []
        asks: list[OrderBookLevel] = []

        half_spread = step / 2

        for i in range(effective_depth):
            offset = half_spread + step * i
            # Randomize amount slightly for realism
            amount_jitter = Decimal(str(round(random.uniform(0.8, 1.2), 2)))
            level_amount = _DEFAULT_LEVEL_SIZE * amount_jitter

            bid_price = mid_price - offset
            ask_price = mid_price + offset

            if bid_price > 0:
                bids.append(OrderBookLevel(price=bid_price, amount=level_amount))
            asks.append(OrderBookLevel(price=ask_price, amount=level_amount))

        return OrderBook(
            exchange_id=self.exchange_id,
            symbol=symbol,
            bids=bids,  # Already sorted descending (highest first)
            asks=asks,  # Already sorted ascending (lowest first)
            timestamp=datetime.now(timezone.utc),
            sequence=None,
        )

    def _update_balances(self, trade: Trade) -> None:
        """Update simulated balances after a trade fill."""
        cost = trade.price * trade.amount
        fee = trade.fee

        # Extract base currency from symbol (e.g., "BTC/USDT" -> "BTC")
        parts = trade.symbol.split("/")
        base_currency = parts[0] if len(parts) == 2 else trade.symbol
        quote_currency = parts[1] if len(parts) == 2 else "USDT"

        if trade.side.value == "buy":
            # Deduct quote currency, add base currency
            self._adjust_balance(quote_currency, -(cost + fee))
            self._adjust_balance(base_currency, trade.amount)
        else:
            # Add quote currency, deduct base currency
            self._adjust_balance(quote_currency, cost - fee)
            self._adjust_balance(base_currency, -trade.amount)

    def _adjust_balance(self, currency: str, delta: Decimal) -> None:
        """Adjust the free and total balance for a currency."""
        if currency in self._balances:
            bal = self._balances[currency]
            new_free = bal.free + delta
            new_total = bal.total + delta
            self._balances[currency] = Balance(
                currency=currency,
                free=new_free,
                used=bal.used,
                total=new_total,
            )
        elif delta > 0:
            self._balances[currency] = Balance(
                currency=currency,
                free=delta,
                used=Decimal("0"),
                total=delta,
            )
