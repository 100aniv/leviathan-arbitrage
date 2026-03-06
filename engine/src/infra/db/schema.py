"""
SQLAlchemy ORM models for LEVIATHAN.

All monetary and quantity fields use Numeric (mapped to Decimal in Python).
Never use float for financial values.
"""

import enum
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    JSON,
    Numeric,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class EventType(str, enum.Enum):
    OPEN = "OPEN"
    UPDATE = "UPDATE"
    CLOSE = "CLOSE"
    LOCK = "LOCK"
    UNLOCK = "UNLOCK"


class PositionSide(str, enum.Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class LockStatus(str, enum.Enum):
    ACQUIRED = "ACQUIRED"
    RELEASED = "RELEASED"


class PositionWAL(Base):
    """
    Position Write-Ahead Log (Amendment 1A).

    Every position-critical state change is appended here BEFORE Redis write.
    This is the durability guarantee; Redis is the fast-read path.
    """

    __tablename__ = "position_wal"

    wal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)  # EventType value
    strategy_id: Mapped[str] = mapped_column(Text, nullable=False)
    exchange_id: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)  # PositionSide value
    quantity: Mapped[float] = mapped_column(Numeric(precision=28, scale=10), nullable=False)
    avg_price: Mapped[float] = mapped_column(Numeric(precision=28, scale=10), nullable=False)
    wal_metadata: Mapped[dict | None] = mapped_column(JSON, name="metadata", nullable=True)
    checksum: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("idx_wal_strategy_ts", "strategy_id", sa.text("ts DESC")),
        Index("idx_wal_exchange_symbol", "exchange_id", "symbol"),
    )


class CapitalAllocationLock(Base):
    """Capital allocation lock table."""

    __tablename__ = "capital_allocation_lock"

    lock_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    strategy_id: Mapped[str] = mapped_column(Text, nullable=False)
    exchange_id: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(precision=28, scale=10), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)  # LockStatus value
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Trade(Base):
    """Executed trade record."""

    __tablename__ = "trades"

    trade_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    strategy_id: Mapped[str] = mapped_column(Text, nullable=False)
    exchange_id: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(precision=28, scale=10), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(precision=28, scale=10), nullable=False)
    fee: Mapped[float] = mapped_column(Numeric(precision=28, scale=10), nullable=False)
    order_id: Mapped[str] = mapped_column(Text, nullable=False)


class Order(Base):
    """Order record."""

    __tablename__ = "orders"

    order_id: Mapped[str] = mapped_column(Text, primary_key=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    strategy_id: Mapped[str] = mapped_column(Text, nullable=False)
    exchange_id: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(precision=28, scale=10), nullable=False)
    price: Mapped[float | None] = mapped_column(Numeric(precision=28, scale=10), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    filled_qty: Mapped[float] = mapped_column(
        Numeric(precision=28, scale=10), nullable=False, server_default="0"
    )


class StrategyConfig(Base):
    """Strategy configuration."""

    __tablename__ = "strategy_config"

    strategy_id: Mapped[str] = mapped_column(Text, primary_key=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
