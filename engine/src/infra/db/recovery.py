"""
Redis recovery protocol (Amendment 1B).

Recovery sequence on Redis restart:
  1. Engine detects Redis unavailability → set in-process HALT flag
  2. On Redis reconnection:
     a. Read latest position_wal entries per (strategy_id, exchange_id, symbol) from PG
     b. Write reconstructed position state to Redis
     c. Reconcile with exchange APIs
     d. If exchange state matches reconstructed state → clear HALT flag, resume
     e. If mismatch → keep HALT, emit CRITICAL alert, require manual review
"""

import logging
from decimal import Decimal

from src.risk.kill_switch import halt_local, clear_halt

logger = logging.getLogger(__name__)

_LATEST_WAL_SQL = """
    SELECT DISTINCT ON (strategy_id, exchange_id, symbol)
        wal_id, strategy_id, exchange_id, symbol, side, quantity, avg_price, event_type
    FROM position_wal
    ORDER BY strategy_id, exchange_id, symbol, ts DESC
"""


class RecoveryManager:
    """
    Manages Redis recovery after unavailability.

    Usage:
        manager = RecoveryManager(db_pool=pool, redis_client=redis, exchange_clients=clients)
        manager.on_redis_unavailable()    # call on connection error
        await manager.recover()           # call on reconnection
    """

    def __init__(self, db_pool=None, redis_client=None, exchange_clients=None) -> None:
        self._db = db_pool
        self._redis = redis_client
        self._exchange_clients = exchange_clients or {}

    def on_redis_unavailable(self) -> None:
        """Detect Redis unavailability and set HALT flag."""
        logger.critical("Redis unavailable — setting HALT flag. No new trades will be submitted.")
        self._halt()

    def _halt(self) -> None:
        halt_local()

    def _clear_halt(self) -> None:
        clear_halt()

    async def recover(self) -> bool:
        """
        Full recovery sequence. Returns True if recovery succeeded and trading resumed.
        """
        logger.info("Starting Redis recovery sequence")

        # Step a: Read latest WAL entries from PostgreSQL
        wal_entries = await self._get_latest_wal_entries()
        logger.info("Read %d WAL entries from PostgreSQL", len(wal_entries))

        # Step b: Reconstruct Redis state from WAL
        await self._write_wal_to_redis(wal_entries)
        logger.info("Reconstructed Redis state from WAL")

        # Step c+d: Reconcile with exchange APIs
        reconciled = await self._reconcile_with_exchange(wal_entries)

        if reconciled:
            logger.info("Reconciliation successful — clearing HALT flag, resuming trading")
            self._clear_halt()
            return True
        else:
            logger.critical(
                "Reconciliation FAILED — exchange state does not match WAL. "
                "HALT flag remains set. Manual review required."
            )
            return False

    async def _get_latest_wal_entries(self) -> list[dict]:
        """Read the latest WAL entry per (strategy_id, exchange_id, symbol) from PostgreSQL."""
        async with self._db.pool.acquire() as conn:
            rows = await conn.fetch(_LATEST_WAL_SQL)
            return [dict(row) for row in rows]

    async def _write_wal_to_redis(self, wal_entries: list[dict]) -> bool:
        """Write reconstructed position state to Redis."""
        pipeline = self._redis.pipeline()
        for entry in wal_entries:
            # Skip CLOSE entries — no open position to reconstruct
            if entry.get("event_type") == "CLOSE":
                continue
            key = (
                f"leviathan:position:{entry['strategy_id']}"
                f":{entry['exchange_id']}:{entry['symbol']}"
            )
            pipeline.hset(key, mapping={
                "side": entry["side"],
                "quantity": str(entry["quantity"]),
                "avg_price": str(entry["avg_price"]),
                "event_type": entry["event_type"],
                "wal_id": entry["wal_id"],
            })
        await pipeline.execute()
        return True

    async def _reconcile_with_exchange(
        self, wal_entries: list[dict] | None = None
    ) -> bool:
        """
        Compare reconstructed state with live exchange APIs.
        Returns True if all positions match, False if any mismatch.
        """
        if not wal_entries:
            return True

        for entry in wal_entries:
            # BUG-97: skip CLOSE entries — they indicate position already closed,
            # no need to reconcile against live exchange state.
            if entry.get("event_type") == "CLOSE":
                continue

            exchange_id = entry["exchange_id"]
            symbol = entry["symbol"]

            client = self._exchange_clients.get(exchange_id)
            if client is None:
                logger.warning(
                    "No exchange client for %s — skipping reconciliation for %s",
                    exchange_id, symbol,
                )
                continue

            # BUG-97: native adapters use get_positions() (list), ccxt uses fetch_position(symbol) (dict).
            # Duck-type: prefer native get_positions() when available, fallback to ccxt.
            try:
                if hasattr(client, "get_positions"):
                    positions = await client.get_positions()
                    _p = next((p for p in positions if getattr(p, "symbol", None) == symbol), None)
                    # BUG-97.2: native Position uses `size` (signed), legacy dict used `quantity`
                    _raw = getattr(_p, "size", None) if _p else None
                    if _raw is None and _p is not None:
                        _raw = getattr(_p, "quantity", 0)
                    # Signed size (-24 SHORT, +24 LONG) — compare absolute to WAL quantity
                    exchange_qty = abs(Decimal(str(_raw or 0)))
                else:
                    exchange_position = await client.fetch_position(symbol)
                    exchange_qty = Decimal(str(exchange_position.get("quantity", 0)))
            except Exception as exc:
                logger.error(
                    "Failed to fetch position from %s for %s: %s",
                    exchange_id, symbol, exc,
                )
                return False

            wal_qty = Decimal(str(entry["quantity"]))

            # Allow 0.01% tolerance for rounding differences
            tolerance = wal_qty * Decimal("0.0001")
            if abs(wal_qty - exchange_qty) > tolerance:
                logger.critical(
                    "Position MISMATCH on %s %s: WAL=%s, exchange=%s",
                    exchange_id, symbol, wal_qty, exchange_qty,
                )
                return False

        return True
