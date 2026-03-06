"""Database infrastructure package."""

from src.infra.db.connection import DatabasePool, get_async_engine, get_async_sessionmaker
from src.infra.db.dual_write import DualWriter, EngineHaltError, TradeRejectedError, compute_checksum
from src.infra.db.schema import (
    Base,
    CapitalAllocationLock,
    EventType,
    LockStatus,
    Order,
    PositionSide,
    PositionWAL,
    StrategyConfig,
    Trade,
)

__all__ = [
    "Base",
    "CapitalAllocationLock",
    "DatabasePool",
    "DualWriter",
    "EngineHaltError",
    "EventType",
    "LockStatus",
    "Order",
    "PositionSide",
    "PositionWAL",
    "StrategyConfig",
    "Trade",
    "TradeRejectedError",
    "compute_checksum",
    "get_async_engine",
    "get_async_sessionmaker",
]
