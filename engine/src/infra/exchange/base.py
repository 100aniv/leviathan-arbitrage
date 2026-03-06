"""ExchangeAdapter Protocol — defines the interface all exchange adapters must implement."""
from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable

from src.core.models import Balance, FeeRate, Order, OrderBook, Position, Trade


@runtime_checkable
class ExchangeAdapter(Protocol):
    """
    Protocol defining the interface for all exchange adapters.

    All adapters must implement this interface. Use @runtime_checkable so that
    isinstance() checks work for dependency injection and testing.
    """

    exchange_id: str

    async def connect(self) -> None:
        """Initialize connection to the exchange (load markets, authenticate)."""
        ...

    async def disconnect(self) -> None:
        """Close all connections and clean up resources."""
        ...

    async def subscribe_orderbook(
        self, symbol: str, callback: Callable[[OrderBook], None]
    ) -> None:
        """Subscribe to live orderbook updates via WebSocket."""
        ...

    async def subscribe_ticker(self, symbol: str, callback: Callable) -> None:
        """Subscribe to live ticker updates via WebSocket."""
        ...

    async def get_orderbook_snapshot(self, symbol: str, depth: int = 20) -> OrderBook:
        """Fetch a current orderbook snapshot via REST API."""
        ...

    async def place_order(self, order: Order) -> Trade:
        """Submit an order to the exchange and return the resulting trade."""
        ...

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a specific order. Returns True if successfully cancelled."""
        ...

    async def cancel_all_orders(self, symbol: str | None = None) -> int:
        """Cancel all open orders (optionally filtered by symbol). Returns count cancelled."""
        ...

    async def get_balances(self) -> dict[str, Balance]:
        """Fetch all non-zero account balances keyed by currency."""
        ...

    async def get_positions(self) -> list[Position]:
        """Fetch open positions (for futures/margin accounts)."""
        ...

    async def get_fee_rate(self, symbol: str) -> FeeRate:
        """Fetch the trading fee rate for a given symbol."""
        ...

    @property
    def health_score(self) -> float:
        """Health score 0.0 (dead) to 1.0 (perfect)."""
        ...
