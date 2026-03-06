"""Event types for Redis Streams event bus."""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from src.core.models import Order, OrderBook, Signal, Trade


class EventType(StrEnum):
    ORDERBOOK_UPDATE = "orderbook_update"
    SIGNAL = "signal"
    ORDER = "order"
    TRADE = "trade"
    RISK = "risk"


class BaseEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: EventType
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    source: str | None = None


class OrderbookUpdateEvent(BaseEvent):
    event_type: EventType = EventType.ORDERBOOK_UPDATE
    orderbook: OrderBook


class SignalEvent(BaseEvent):
    event_type: EventType = EventType.SIGNAL
    signal: Signal


class OrderEvent(BaseEvent):
    event_type: EventType = EventType.ORDER
    order: Order
    action: str  # "created", "submitted", "filled", "cancelled"


class TradeEvent(BaseEvent):
    event_type: EventType = EventType.TRADE
    trade: Trade


class RiskEvent(BaseEvent):
    event_type: EventType = EventType.RISK
    risk_type: str  # "position_limit", "loss_limit", "circuit_breaker"
    details: dict[str, Any] = Field(default_factory=dict)
    severity: str = "warning"  # "warning", "critical"
