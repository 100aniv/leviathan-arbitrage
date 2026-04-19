"""Domain models for LEVIATHAN arbitrage engine."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    LIMIT = "limit"
    MARKET = "market"
    MAKER = "maker"


class OrderStatus(StrEnum):
    PENDING = "pending"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    FAILED = "failed"


class OrderBookLevel(BaseModel):
    price: Decimal
    amount: Decimal


class OrderBook(BaseModel):
    exchange_id: str
    symbol: str
    bids: list[OrderBookLevel]  # sorted descending by price
    asks: list[OrderBookLevel]  # sorted ascending by price
    timestamp: datetime = Field(default_factory=_utcnow)
    sequence: int | None = None

    @property
    def best_bid(self) -> Decimal | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Decimal | None:
        return self.asks[0].price if self.asks else None

    @property
    def spread(self) -> Decimal | None:
        if self.best_bid is not None and self.best_ask is not None:
            return self.best_ask - self.best_bid
        return None

    @property
    def mid_price(self) -> Decimal | None:
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / 2
        return None


class Order(BaseModel):
    order_id: str | None = None
    client_order_id: str | None = None
    exchange_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    price: Decimal | None = None
    amount: Decimal
    filled: Decimal = Decimal("0")
    remaining: Decimal | None = None
    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Trade(BaseModel):
    trade_id: str
    order_id: str | None = None
    exchange_id: str
    symbol: str
    side: OrderSide
    price: Decimal
    amount: Decimal
    fee: Decimal = Decimal("0")
    fee_currency: str | None = None
    # WS-A1: Exchange-reported realized PnL (includes commission + slippage).
    # None → not reported by adapter; fall through to fill-based recompute.
    # Non-None → use as authoritative PnL source in _compute_pnl_from_result.
    realized_pnl: Decimal | None = None
    timestamp: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Position(BaseModel):
    exchange_id: str
    symbol: str
    size: Decimal  # positive = long, negative = short
    entry_price: Decimal
    mark_price: Decimal | None = None
    unrealized_pnl: Decimal = Decimal("0")
    leverage: int = 1
    updated_at: datetime = Field(default_factory=_utcnow)


class Signal(BaseModel):
    strategy_id: str
    symbol: str
    buy_exchange: str
    sell_exchange: str
    buy_price: Decimal
    sell_price: Decimal
    spread_pct: Decimal
    confidence: float = Field(ge=0.0, le=1.0)
    volume: Decimal
    timestamp: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Ticker(BaseModel):
    symbol: str
    exchange_id: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    volume: Decimal
    timestamp: datetime = Field(default_factory=_utcnow)


class Balance(BaseModel):
    currency: str
    free: Decimal
    used: Decimal
    total: Decimal


class FeeRate(BaseModel):
    maker: Decimal
    taker: Decimal
    symbol: str | None = None
    exchange_id: str | None = None
