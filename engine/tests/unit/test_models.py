"""Tests for Pydantic domain models."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.core.models import (
    Balance,
    FeeRate,
    Order,
    OrderBook,
    OrderBookLevel,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    Signal,
    Ticker,
    Trade,
)


class TestOrderBook:
    def test_orderbook_creation(self):
        ob = OrderBook(
            exchange_id="binance",
            symbol="BTC/USDT",
            bids=[OrderBookLevel(price=Decimal("50000"), amount=Decimal("1.0"))],
            asks=[OrderBookLevel(price=Decimal("50001"), amount=Decimal("0.5"))],
        )
        assert ob.exchange_id == "binance"
        assert ob.symbol == "BTC/USDT"
        assert len(ob.bids) == 1
        assert len(ob.asks) == 1

    def test_best_bid_ask(self):
        ob = OrderBook(
            exchange_id="binance",
            symbol="BTC/USDT",
            bids=[
                OrderBookLevel(price=Decimal("50000"), amount=Decimal("1.0")),
                OrderBookLevel(price=Decimal("49999"), amount=Decimal("2.0")),
            ],
            asks=[
                OrderBookLevel(price=Decimal("50001"), amount=Decimal("0.5")),
                OrderBookLevel(price=Decimal("50002"), amount=Decimal("1.0")),
            ],
        )
        assert ob.best_bid == Decimal("50000")
        assert ob.best_ask == Decimal("50001")

    def test_spread(self):
        ob = OrderBook(
            exchange_id="binance",
            symbol="BTC/USDT",
            bids=[OrderBookLevel(price=Decimal("50000"), amount=Decimal("1.0"))],
            asks=[OrderBookLevel(price=Decimal("50001"), amount=Decimal("0.5"))],
        )
        assert ob.spread == Decimal("1")

    def test_mid_price(self):
        ob = OrderBook(
            exchange_id="binance",
            symbol="BTC/USDT",
            bids=[OrderBookLevel(price=Decimal("50000"), amount=Decimal("1.0"))],
            asks=[OrderBookLevel(price=Decimal("50002"), amount=Decimal("0.5"))],
        )
        assert ob.mid_price == Decimal("50001")

    def test_empty_orderbook_properties(self):
        ob = OrderBook(exchange_id="binance", symbol="BTC/USDT", bids=[], asks=[])
        assert ob.best_bid is None
        assert ob.best_ask is None
        assert ob.spread is None
        assert ob.mid_price is None

    def test_orderbook_serialization_roundtrip(self):
        ob = OrderBook(
            exchange_id="binance",
            symbol="BTC/USDT",
            bids=[OrderBookLevel(price=Decimal("50000"), amount=Decimal("1.0"))],
            asks=[OrderBookLevel(price=Decimal("50001"), amount=Decimal("0.5"))],
        )
        data = ob.model_dump()
        restored = OrderBook.model_validate(data)
        assert restored.exchange_id == ob.exchange_id
        assert restored.bids[0].price == ob.bids[0].price
        assert restored.asks[0].amount == ob.asks[0].amount


class TestOrder:
    def test_order_creation_defaults(self):
        order = Order(
            exchange_id="binance",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("50000"),
            amount=Decimal("0.001"),
        )
        assert order.status == OrderStatus.PENDING
        assert order.filled == Decimal("0")
        assert order.order_id is None

    def test_order_side_str_enum(self):
        assert OrderSide.BUY == "buy"
        assert OrderSide.SELL == "sell"

    def test_order_type_str_enum(self):
        assert OrderType.LIMIT == "limit"
        assert OrderType.MARKET == "market"

    def test_order_status_str_enum(self):
        assert OrderStatus.PENDING == "pending"
        assert OrderStatus.FILLED == "filled"
        assert OrderStatus.CANCELLED == "cancelled"
        assert OrderStatus.PARTIALLY_FILLED == "partially_filled"

    def test_market_order_no_price(self):
        order = Order(
            exchange_id="binance",
            symbol="BTC/USDT",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            amount=Decimal("0.01"),
        )
        assert order.price is None

    def test_order_serialization_roundtrip(self):
        order = Order(
            exchange_id="binance",
            symbol="ETH/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("3000"),
            amount=Decimal("1.0"),
        )
        data = order.model_dump()
        restored = Order.model_validate(data)
        assert restored.symbol == "ETH/USDT"
        assert restored.side == OrderSide.BUY
        assert restored.price == Decimal("3000")


class TestTrade:
    def test_trade_creation(self):
        trade = Trade(
            trade_id="t001",
            exchange_id="binance",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            price=Decimal("50000"),
            amount=Decimal("0.001"),
        )
        assert trade.trade_id == "t001"
        assert trade.fee == Decimal("0")
        assert trade.fee_currency is None

    def test_trade_with_fee(self):
        trade = Trade(
            trade_id="t002",
            exchange_id="binance",
            symbol="BTC/USDT",
            side=OrderSide.SELL,
            price=Decimal("50000"),
            amount=Decimal("0.001"),
            fee=Decimal("0.05"),
            fee_currency="USDT",
        )
        assert trade.fee == Decimal("0.05")
        assert trade.fee_currency == "USDT"


class TestBalance:
    def test_balance_creation(self):
        b = Balance(
            currency="USDT",
            free=Decimal("1000"),
            used=Decimal("500"),
            total=Decimal("1500"),
        )
        assert b.currency == "USDT"
        assert b.free == Decimal("1000")
        assert b.used == Decimal("500")
        assert b.total == Decimal("1500")

    def test_balance_serialization(self):
        b = Balance(
            currency="BTC",
            free=Decimal("1.5"),
            used=Decimal("0.5"),
            total=Decimal("2.0"),
        )
        data = b.model_dump()
        restored = Balance.model_validate(data)
        assert restored.currency == "BTC"
        assert restored.total == Decimal("2.0")


class TestFeeRate:
    def test_fee_rate_creation(self):
        fee = FeeRate(
            maker=Decimal("0.001"),
            taker=Decimal("0.001"),
            symbol="BTC/USDT",
            exchange_id="binance",
        )
        assert fee.maker == Decimal("0.001")
        assert fee.taker == Decimal("0.001")

    def test_fee_rate_minimal(self):
        fee = FeeRate(maker=Decimal("0.0005"), taker=Decimal("0.001"))
        assert fee.symbol is None
        assert fee.exchange_id is None


class TestPosition:
    def test_position_creation(self):
        pos = Position(
            exchange_id="binance",
            symbol="BTC/USDT",
            size=Decimal("0.5"),
            entry_price=Decimal("50000"),
        )
        assert pos.size == Decimal("0.5")
        assert pos.unrealized_pnl == Decimal("0")
        assert pos.leverage == 1

    def test_position_short(self):
        pos = Position(
            exchange_id="binance",
            symbol="BTC/USDT",
            size=Decimal("-0.5"),
            entry_price=Decimal("50000"),
            unrealized_pnl=Decimal("100"),
        )
        assert pos.size < 0


class TestSignal:
    def test_signal_creation(self):
        signal = Signal(
            strategy_id="arb-001",
            symbol="BTC/USDT",
            buy_exchange="upbit",
            sell_exchange="binance",
            buy_price=Decimal("50000"),
            sell_price=Decimal("51000"),
            spread_pct=Decimal("0.02"),
            confidence=0.85,
            volume=Decimal("0.1"),
        )
        assert signal.strategy_id == "arb-001"
        assert signal.confidence == 0.85

    def test_signal_confidence_too_high(self):
        with pytest.raises(Exception):
            Signal(
                strategy_id="arb-001",
                symbol="BTC/USDT",
                buy_exchange="upbit",
                sell_exchange="binance",
                buy_price=Decimal("50000"),
                sell_price=Decimal("51000"),
                spread_pct=Decimal("0.02"),
                confidence=1.5,  # invalid: > 1.0
                volume=Decimal("0.1"),
            )

    def test_signal_confidence_negative(self):
        with pytest.raises(Exception):
            Signal(
                strategy_id="arb-001",
                symbol="BTC/USDT",
                buy_exchange="upbit",
                sell_exchange="binance",
                buy_price=Decimal("50000"),
                sell_price=Decimal("51000"),
                spread_pct=Decimal("0.02"),
                confidence=-0.1,  # invalid: < 0.0
                volume=Decimal("0.1"),
            )


class TestTicker:
    def test_ticker_creation(self):
        ticker = Ticker(
            symbol="BTC/USDT",
            exchange_id="binance",
            bid=Decimal("50000"),
            ask=Decimal("50001"),
            last=Decimal("50000.5"),
            volume=Decimal("1234.5"),
        )
        assert ticker.symbol == "BTC/USDT"
        assert ticker.bid < ticker.ask
