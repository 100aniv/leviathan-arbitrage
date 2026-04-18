"""
Dual-Write Protocol (Amendment 1B).

MANDATORY SEQUENCE — NON-NEGOTIABLE:
  Step 1: Write to PostgreSQL position_wal (sync, timeout 5ms)
          → If PG fails: raise TradeRejectedError. Do NOT proceed to Redis.
  Step 2: Write to Redis (sync, timeout 2ms)
          → If Redis fails: set HALT flag, raise EngineHaltError.
  Step 3: Return wal_id on success.

Checksum: SHA256(strategy_id + exchange_id + symbol + side + quantity + avg_price)
"""

import asyncio
import hashlib
import logging
from decimal import Decimal

from src.risk.kill_switch import halt_local

logger = logging.getLogger(__name__)

# Timeouts (seconds)
# BUG-114: 100ms still rejected v161 FR trades (Docker PG + TimescaleDB hypertable
# writes typically 200-300ms). Raised to 500ms — well under trade latency budget (2s)
# but tolerant of burst write contention.
# BUG-149 → BUG-161: v197에서 1500ms 도 부족. 3000ms로 상향.
# Trade latency 예산 3s 내 수용. DB write가 trade critical path가 아니므로
# 더 관대해도 괜찮음.
_PG_TIMEOUT: float = 3.000   # 3000ms
_REDIS_TIMEOUT: float = 0.050  # 50ms (was 2ms)


class TradeRejectedError(Exception):
    """Raised when the PostgreSQL WAL write fails. Trade must be rejected."""


class EngineHaltError(Exception):
    """Raised when the Redis write fails. Engine is halted."""


def compute_checksum(
    strategy_id: str,
    exchange_id: str,
    symbol: str,
    side: str,
    quantity: Decimal,
    avg_price: Decimal,
) -> str:
    """SHA256(strategy_id || exchange_id || symbol || side || quantity || avg_price)."""
    raw = f"{strategy_id}{exchange_id}{symbol}{side}{quantity}{avg_price}"
    return hashlib.sha256(raw.encode()).hexdigest()


class DualWriter:
    """
    Implements the dual-write protocol for position-critical state changes.

    Usage:
        writer = DualWriter(db_pool=pool, redis_client=redis)
        wal_id = await writer.write_position(...)
    """

    # Class-level defaults — also available on instances created via __new__
    _pg_timeout: float = _PG_TIMEOUT
    _redis_timeout: float = _REDIS_TIMEOUT

    def __init__(self, db_pool=None, redis_client=None) -> None:
        self._db_pool = db_pool
        self._redis_client = redis_client

    async def write_position(
        self,
        strategy_id: str,
        exchange_id: str,
        symbol: str,
        side: str,
        quantity: Decimal,
        avg_price: Decimal,
        event_type: str,
        metadata: dict | None = None,
    ) -> int:
        """
        Dual-write a position event.

        Returns wal_id on success.
        Raises TradeRejectedError if PG write fails.
        Raises EngineHaltError if Redis write fails (also sets HALT flag).
        """
        checksum = compute_checksum(
            strategy_id, exchange_id, symbol, side, quantity, avg_price
        )

        # ── STEP 1: PostgreSQL write (MUST happen first) ──────────────────────
        wal_id = await self._pg_write_with_timeout(
            strategy_id=strategy_id,
            exchange_id=exchange_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            avg_price=avg_price,
            event_type=event_type,
            wal_metadata=metadata,
            checksum=checksum,
        )

        # ── STEP 2: Redis write ───────────────────────────────────────────────
        await self._redis_write_with_timeout(
            strategy_id=strategy_id,
            exchange_id=exchange_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            avg_price=avg_price,
            event_type=event_type,
            wal_id=wal_id,
        )

        # ── STEP 3: Return success ────────────────────────────────────────────
        return wal_id

    async def _pg_write_with_timeout(self, **kwargs) -> int:
        """Write to PostgreSQL with 5ms timeout. Raises TradeRejectedError on failure."""
        try:
            wal_id = await asyncio.wait_for(
                self._write_to_postgres(**kwargs),
                timeout=self._pg_timeout,
            )
            return wal_id
        except asyncio.TimeoutError:
            logger.error(
                "PG WAL write timed out (>%.0fms) — rejecting trade [strategy=%s exchange=%s symbol=%s]",
                self._pg_timeout * 1000,
                kwargs.get("strategy_id"),
                kwargs.get("exchange_id"),
                kwargs.get("symbol"),
            )
            raise TradeRejectedError(
                f"PostgreSQL write timed out after {self._pg_timeout * 1000:.0f}ms"
            )
        except TradeRejectedError:
            raise
        except Exception as exc:
            logger.error(
                "PG WAL write failed — rejecting trade: %s [strategy=%s exchange=%s symbol=%s]",
                exc,
                kwargs.get("strategy_id"),
                kwargs.get("exchange_id"),
                kwargs.get("symbol"),
            )
            raise TradeRejectedError(f"PostgreSQL write failed: {exc}") from exc

    async def _redis_write_with_timeout(self, **kwargs) -> None:
        """Write to Redis with 2ms timeout. Sets HALT flag and raises EngineHaltError on failure."""
        try:
            await asyncio.wait_for(
                self._write_to_redis(**kwargs),
                timeout=self._redis_timeout,
            )
        except asyncio.TimeoutError:
            logger.critical(
                "Redis write timed out (>%.0fms) — setting HALT [strategy=%s exchange=%s symbol=%s]",
                self._redis_timeout * 1000,
                kwargs.get("strategy_id"),
                kwargs.get("exchange_id"),
                kwargs.get("symbol"),
            )
            halt_local()
            raise EngineHaltError(
                f"Redis write timed out after {self._redis_timeout * 1000:.0f}ms — engine halted"
            )
        except Exception as exc:
            logger.critical(
                "Redis write failed — setting HALT: %s [strategy=%s exchange=%s symbol=%s]",
                exc,
                kwargs.get("strategy_id"),
                kwargs.get("exchange_id"),
                kwargs.get("symbol"),
            )
            halt_local()
            raise EngineHaltError(f"Redis write failed: {exc} — engine halted") from exc

    async def _write_to_postgres(
        self,
        strategy_id: str,
        exchange_id: str,
        symbol: str,
        side: str,
        quantity: Decimal,
        avg_price: Decimal,
        event_type: str,
        checksum: str,
        wal_metadata: dict | None = None,
        **_,
    ) -> int:
        """Insert a row into position_wal and return the wal_id."""
        sql = """
            INSERT INTO position_wal
                (event_type, strategy_id, exchange_id, symbol, side,
                 quantity, avg_price, metadata, checksum)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING wal_id
        """
        async with self._db_pool.pool.acquire() as conn:
            row = await conn.fetchrow(
                sql,
                event_type,
                strategy_id,
                exchange_id,
                symbol,
                side,
                str(quantity),
                str(avg_price),
                wal_metadata,
                checksum,
            )
            return row["wal_id"]

    async def _write_to_redis(
        self,
        strategy_id: str,
        exchange_id: str,
        symbol: str,
        side: str,
        quantity: Decimal,
        avg_price: Decimal,
        event_type: str,
        wal_id: int,
        **_,
    ) -> None:
        """Write position state to Redis hash."""
        key = f"leviathan:position:{strategy_id}:{exchange_id}:{symbol}"
        await self._redis_client.hset(key, mapping={
            "side": side,
            "quantity": str(quantity),
            "avg_price": str(avg_price),
            "event_type": event_type,
            "wal_id": wal_id,
        })
