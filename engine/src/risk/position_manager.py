"""LEVIATHAN Position Manager.

Tracks all open positions per (strategy_id, exchange_id, symbol).
Maintains net exposure per (exchange, base_asset) in Redis.
Provides real-time PnL calculation (unrealized + realized).

Position lifecycle: OPEN → UPDATE → CLOSE
Every state change is dual-written to PostgreSQL WAL + Redis.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Redis key templates
EXPOSURE_KEY = "leviathan:exposure:{exchange}:{base_asset}"


@dataclass
class PositionRecord:
    """In-memory position state with real-time PnL."""

    strategy_id: str
    exchange_id: str
    symbol: str
    side: str  # "LONG" or "SHORT"
    quantity: Decimal
    entry_price: Decimal
    mark_price: Decimal = field(default_factory=lambda: Decimal("0"))
    realized_pnl: Decimal = field(default_factory=lambda: Decimal("0"))
    wal_id: int = 0

    @property
    def unrealized_pnl(self) -> Decimal:
        if self.mark_price == Decimal("0"):
            return Decimal("0")
        if self.side == "LONG":
            return (self.mark_price - self.entry_price) * self.quantity
        else:  # SHORT
            return (self.entry_price - self.mark_price) * self.quantity

    @property
    def position_value(self) -> Decimal:
        price = self.mark_price if self.mark_price != Decimal("0") else self.entry_price
        return self.quantity * price


class PositionManager:
    """
    Tracks all open positions with real-time PnL.

    Dual-writes to PostgreSQL WAL + Redis on every state change (Amendment 1B).
    Net exposure per (exchange, base_asset) is maintained in Redis:
      leviathan:exposure:{exchange}:{base_asset}
    LONG positions contribute positive delta, SHORT positions contribute negative.
    """

    def __init__(self, dual_writer: Any, redis_client: Any) -> None:
        self._writer = dual_writer
        self._redis = redis_client
        # (strategy_id, exchange_id, symbol) -> PositionRecord
        self._positions: dict[tuple[str, str, str], PositionRecord] = {}

    async def open_position(
        self,
        strategy_id: str,
        exchange_id: str,
        symbol: str,
        side: str,  # "LONG" or "SHORT"
        quantity: Decimal,
        entry_price: Decimal,
    ) -> int:
        """
        Open a new position. Dual-writes to WAL then Redis.
        Returns wal_id from the WAL write.
        """
        wal_id = await self._writer.write_position(
            strategy_id=strategy_id,
            exchange_id=exchange_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            avg_price=entry_price,
            event_type="OPEN",
        )

        key = (strategy_id, exchange_id, symbol)
        self._positions[key] = PositionRecord(
            strategy_id=strategy_id,
            exchange_id=exchange_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            wal_id=wal_id,
        )

        # Update Redis net exposure
        delta = quantity if side == "LONG" else -quantity
        await self._update_redis_exposure(exchange_id, symbol, delta)

        logger.info(
            "position_opened",
            strategy=strategy_id,
            exchange=exchange_id,
            symbol=symbol,
            side=side,
            quantity=str(quantity),
            entry_price=str(entry_price),
            wal_id=wal_id,
        )
        return wal_id

    async def update_position(
        self,
        strategy_id: str,
        exchange_id: str,
        symbol: str,
        mark_price: Decimal,
    ) -> None:
        """Update mark price for unrealized PnL. Writes UPDATE event to WAL."""
        key = (strategy_id, exchange_id, symbol)
        record = self._positions.get(key)
        if record is None:
            logger.warning("update_position_not_found", key=str(key))
            return

        record.mark_price = mark_price

        await self._writer.write_position(
            strategy_id=strategy_id,
            exchange_id=exchange_id,
            symbol=symbol,
            side=record.side,
            quantity=record.quantity,
            avg_price=record.entry_price,
            event_type="UPDATE",
            metadata={
                "mark_price": str(mark_price),
                "unrealized_pnl": str(record.unrealized_pnl),
            },
        )

    async def close_position(
        self,
        strategy_id: str,
        exchange_id: str,
        symbol: str,
        close_price: Decimal,
    ) -> Decimal:
        """
        Close a position. Writes CLOSE event to WAL, removes from tracking.
        Returns realized PnL.
        """
        key = (strategy_id, exchange_id, symbol)
        record = self._positions.get(key)
        if record is None:
            logger.warning("close_position_not_found", key=str(key))
            return Decimal("0")

        record.mark_price = close_price
        realized_pnl = record.unrealized_pnl + record.realized_pnl

        await self._writer.write_position(
            strategy_id=strategy_id,
            exchange_id=exchange_id,
            symbol=symbol,
            side=record.side,
            quantity=record.quantity,
            avg_price=close_price,
            event_type="CLOSE",
            metadata={"realized_pnl": str(realized_pnl)},
        )

        # Reverse the exposure delta
        delta = -(record.quantity if record.side == "LONG" else -record.quantity)
        await self._update_redis_exposure(exchange_id, symbol, delta)

        del self._positions[key]

        logger.info(
            "position_closed",
            strategy=strategy_id,
            exchange=exchange_id,
            symbol=symbol,
            realized_pnl=str(realized_pnl),
        )
        return realized_pnl

    def get_positions(self) -> dict[tuple[str, str, str], PositionRecord]:
        """Return a snapshot of all open positions."""
        return dict(self._positions)

    async def get_net_exposure(self, exchange_id: str, base_asset: str) -> Decimal:
        """Read current net exposure for (exchange, base_asset) from Redis."""
        redis_key = EXPOSURE_KEY.format(exchange=exchange_id, base_asset=base_asset)
        val = await self._redis.get(redis_key)
        if val is None:
            return Decimal("0")
        return Decimal(val.decode() if isinstance(val, bytes) else str(val))

    async def _update_redis_exposure(
        self,
        exchange_id: str,
        symbol: str,
        delta: Decimal,
    ) -> None:
        """Atomically update net exposure in Redis by delta."""
        if "/" not in symbol:
            return  # Cannot determine base asset
        base_asset = symbol.split("/")[0]
        redis_key = EXPOSURE_KEY.format(exchange=exchange_id, base_asset=base_asset)

        current_val = await self._redis.get(redis_key)
        if current_val is None:
            current = Decimal("0")
        else:
            current = Decimal(
                current_val.decode() if isinstance(current_val, bytes) else str(current_val)
            )

        await self._redis.set(redis_key, str(current + delta))
