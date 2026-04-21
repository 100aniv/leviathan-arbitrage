"""Tests for ExchangeAdapter Protocol."""
from __future__ import annotations

from decimal import Decimal
from typing import Callable

import pytest

from src.core.models import (
    Balance,
    FeeRate,
    Order,
    OrderBook,
    OrderBookLevel,
    OrderSide,
    OrderType,
    Position,
    Trade,
)
from src.infra.exchange.base import ExchangeAdapter


class ConcreteAdapter:
    """Minimal concrete implementation of ExchangeAdapter Protocol for testing."""

    exchange_id = "test_exchange"

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def subscribe_orderbook(self, symbol: str, callback: Callable[[OrderBook], None]) -> None:
        pass

    async def subscribe_ticker(self, symbol: str, callback: Callable) -> None:
        pass

    async def get_orderbook_snapshot(self, symbol: str, depth: int = 20) -> OrderBook:
        return OrderBook(
            exchange_id=self.exchange_id,
            symbol=symbol,
            bids=[OrderBookLevel(price=Decimal("50000"), amount=Decimal("1.0"))],
            asks=[OrderBookLevel(price=Decimal("50001"), amount=Decimal("0.5"))],
        )

    async def place_order(self, order: Order) -> Trade:
        return Trade(
            trade_id="test-001",
            exchange_id=self.exchange_id,
            symbol=order.symbol,
            side=order.side,
            price=order.price or Decimal("0"),
            amount=order.amount,
        )

    async def cancel_order(self, order_id: str) -> bool:
        return True

    async def cancel_all_orders(self, symbol: str | None = None) -> int:
        return 0

    async def get_balances(self) -> dict[str, Balance]:
        return {
            "USDT": Balance(
                currency="USDT",
                free=Decimal("10000"),
                used=Decimal("0"),
                total=Decimal("10000"),
            )
        }

    async def get_positions(self) -> list[Position]:
        return []

    async def get_fee_rate(self, symbol: str) -> FeeRate:
        return FeeRate(
            maker=Decimal("0.001"),
            taker=Decimal("0.001"),
            symbol=symbol,
        )

    async def get_min_notional(self, symbol: str) -> Decimal:
        return Decimal("5")

    def supports_symbol(self, symbol: str) -> bool:
        return True

    @property
    def health_score(self) -> float:
        return 1.0


class TestExchangeAdapterProtocol:
    def test_concrete_adapter_satisfies_protocol(self):
        adapter = ConcreteAdapter()
        assert isinstance(adapter, ExchangeAdapter)

    def test_exchange_id_attribute(self):
        adapter = ConcreteAdapter()
        assert adapter.exchange_id == "test_exchange"
        assert isinstance(adapter.exchange_id, str)

    @pytest.mark.asyncio
    async def test_get_orderbook_snapshot(self):
        adapter = ConcreteAdapter()
        ob = await adapter.get_orderbook_snapshot("BTC/USDT")
        assert ob.symbol == "BTC/USDT"
        assert ob.exchange_id == "test_exchange"
        assert ob.best_bid == Decimal("50000")
        assert ob.best_ask == Decimal("50001")

    @pytest.mark.asyncio
    async def test_get_orderbook_snapshot_depth(self):
        adapter = ConcreteAdapter()
        ob = await adapter.get_orderbook_snapshot("ETH/USDT", depth=5)
        assert ob.symbol == "ETH/USDT"

    @pytest.mark.asyncio
    async def test_place_order(self):
        adapter = ConcreteAdapter()
        order = Order(
            exchange_id="test_exchange",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("50000"),
            amount=Decimal("0.001"),
        )
        trade = await adapter.place_order(order)
        assert trade.trade_id == "test-001"
        assert trade.symbol == "BTC/USDT"
        assert trade.side == OrderSide.BUY

    @pytest.mark.asyncio
    async def test_cancel_order_returns_bool(self):
        adapter = ConcreteAdapter()
        result = await adapter.cancel_order("order-123")
        assert isinstance(result, bool)
        assert result is True

    @pytest.mark.asyncio
    async def test_cancel_all_orders_returns_int(self):
        adapter = ConcreteAdapter()
        result = await adapter.cancel_all_orders("BTC/USDT")
        assert isinstance(result, int)

    @pytest.mark.asyncio
    async def test_get_balances_returns_dict(self):
        adapter = ConcreteAdapter()
        balances = await adapter.get_balances()
        assert isinstance(balances, dict)
        assert "USDT" in balances
        assert balances["USDT"].total == Decimal("10000")

    @pytest.mark.asyncio
    async def test_get_positions_returns_list(self):
        adapter = ConcreteAdapter()
        positions = await adapter.get_positions()
        assert isinstance(positions, list)

    @pytest.mark.asyncio
    async def test_get_fee_rate(self):
        adapter = ConcreteAdapter()
        fee = await adapter.get_fee_rate("BTC/USDT")
        assert isinstance(fee, FeeRate)
        assert fee.maker == Decimal("0.001")
        assert fee.taker == Decimal("0.001")

    def test_health_score_is_float_in_range(self):
        adapter = ConcreteAdapter()
        score = adapter.health_score
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_protocol_requires_exchange_id(self):
        """Objects without exchange_id do not satisfy protocol."""

        class BadAdapter:
            async def connect(self) -> None:
                pass

        bad = BadAdapter()
        assert not isinstance(bad, ExchangeAdapter)
