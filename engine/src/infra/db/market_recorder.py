from __future__ import annotations

import asyncio
import logging
import os
from decimal import Decimal
from datetime import datetime, timezone
from typing import Any

import asyncpg
import structlog

log: structlog.BoundLogger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Row type aliases (plain tuples handed to executemany)
# ---------------------------------------------------------------------------
_OrderbookRow = tuple[
    datetime, str, str, str, str, Decimal, Decimal, Decimal, Decimal
]
_ExecutionRow = tuple[
    datetime, str, str | None, str, str, str,
    Decimal, Decimal, Decimal,
    Decimal | None, Decimal | None, Decimal | None, Decimal | None,
    str, str,
]

# SQL templates -----------------------------------------------------------
_INSERT_ORDERBOOK = """
    INSERT INTO orderbook_snapshots
        (ts, exchange, symbol, bids_json, asks_json,
         best_bid, best_ask, spread_bps, mid_price)
    VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7, $8, $9)
    ON CONFLICT DO NOTHING
"""

_INSERT_EXECUTION = """
    INSERT INTO execution_log
        (ts, strategy_id, signal_id,
         buy_exchange, sell_exchange, symbol,
         buy_price, sell_price, size,
         gross_spread_bps, fee_total, slippage_total, net_pnl,
         status, metadata)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9,
            $10, $11, $12, $13, $14, $15::jsonb)
    ON CONFLICT DO NOTHING
"""


class MarketRecorder:
    """Async batch writer for market data to TimescaleDB.

    Buffers rows and flushes every FLUSH_INTERVAL_MS or when the buffer hits
    MAX_BUFFER_SIZE.  Non-blocking: flush errors are logged but never crash
    the engine.

    Usage::

        recorder = MarketRecorder(pool)
        await recorder.start()
        ...
        recorder.record_orderbook(...)
        ...
        await recorder.stop()
    """

    FLUSH_INTERVAL_MS: int = int(os.getenv("MARKET_FLUSH_INTERVAL_MS", "100"))
    MAX_BUFFER_SIZE: int = int(os.getenv("MARKET_BUFFER_SIZE", "1000"))

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._orderbook_queue: asyncio.Queue[_OrderbookRow] = asyncio.Queue(maxsize=10000)
        self._execution_queue: asyncio.Queue[_ExecutionRow] = asyncio.Queue(maxsize=5000)
        self._flush_task: asyncio.Task[None] | None = None
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background flush loop."""
        if self._running:
            log.warning("market_recorder.already_running")
            return
        self._running = True
        self._flush_task = asyncio.create_task(
            self._flush_loop(), name="market_recorder_flush"
        )
        log.info(
            "market_recorder.started",
            flush_interval_ms=self.FLUSH_INTERVAL_MS,
            max_buffer_size=self.MAX_BUFFER_SIZE,
        )

    async def stop(self) -> None:
        """Flush remaining buffered rows, then stop the background task."""
        self._running = False
        if self._flush_task is not None:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None

        # Drain any remaining rows before shutting down.
        await self._flush()
        log.info("market_recorder.stopped")

    # ------------------------------------------------------------------
    # Batch insert (public API for manual flush / compliance)
    # ------------------------------------------------------------------

    async def batch_insert_pending(self) -> int:
        """Flush all pending buffered rows via batch insert to TimescaleDB.

        Returns approximate count of rows flushed. Normally the background
        flush loop handles this automatically; call explicitly only for
        graceful shutdown or testing.
        """
        ob_count = self._orderbook_queue.qsize()
        ex_count = self._execution_queue.qsize()
        await self._flush()
        return ob_count + ex_count

    # ------------------------------------------------------------------
    # Public record methods (synchronous / non-blocking)
    # ------------------------------------------------------------------

    def record_orderbook(
        self,
        exchange: str,
        symbol: str,
        bids: list[list[Any]],
        asks: list[list[Any]],
        best_bid: Decimal,
        best_ask: Decimal,
    ) -> None:
        """Buffer an orderbook snapshot for async batch insert.

        Computes spread_bps and mid_price inline.  Never raises.
        """
        try:
            import json

            ts = datetime.now(tz=timezone.utc)
            mid_price = (best_bid + best_ask) / Decimal("2")
            spread_bps = (
                (best_ask - best_bid) / mid_price * Decimal("10000")
                if mid_price > 0
                else Decimal("0")
            )
            row: _OrderbookRow = (
                ts,
                exchange,
                symbol,
                json.dumps(bids),
                json.dumps(asks),
                best_bid,
                best_ask,
                spread_bps,
                mid_price,
            )
            self._orderbook_queue.put_nowait(row)
            self._maybe_trigger_flush()
        except Exception:
            log.exception(
                "market_recorder.record_orderbook.error",
                exchange=exchange,
                symbol=symbol,
            )

    def record_execution(
        self,
        strategy_id: str,
        buy_exchange: str,
        sell_exchange: str,
        symbol: str,
        buy_price: Decimal,
        sell_price: Decimal,
        size: Decimal,
        *,
        signal_id: str | None = None,
        gross_spread_bps: Decimal | None = None,
        fee_total: Decimal | None = None,
        slippage_total: Decimal | None = None,
        net_pnl: Decimal | None = None,
        status: str = "pending",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Buffer a trade execution record for async batch insert.

        Never raises.
        """
        try:
            import json

            ts = datetime.now(tz=timezone.utc)
            metadata_str = json.dumps(metadata or {})
            row: _ExecutionRow = (
                ts,
                strategy_id,
                signal_id,
                buy_exchange,
                sell_exchange,
                symbol,
                buy_price,
                sell_price,
                size,
                gross_spread_bps,
                fee_total,
                slippage_total,
                net_pnl,
                status,
                metadata_str,
            )
            self._execution_queue.put_nowait(row)
            self._maybe_trigger_flush()
        except Exception:
            log.exception(
                "market_recorder.record_execution.error",
                strategy_id=strategy_id,
                symbol=symbol,
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _maybe_trigger_flush(self) -> None:
        """Schedule an immediate flush if either buffer is at capacity."""
        ob_size = self._orderbook_queue.qsize()
        ex_size = self._execution_queue.qsize()
        if ob_size >= self.MAX_BUFFER_SIZE or ex_size >= self.MAX_BUFFER_SIZE:
            # Fire-and-forget flush coroutine on the running event loop.
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._flush(), name="market_recorder_overflow_flush")
            except RuntimeError:
                # No running loop — nothing to do (e.g. called from sync context).
                pass

    async def _flush_loop(self) -> None:
        """Periodically drain the write buffers at FLUSH_INTERVAL_MS cadence."""
        interval = self.FLUSH_INTERVAL_MS / 1_000.0
        while self._running:
            try:
                await asyncio.sleep(interval)
                await self._flush()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Never crash the engine — just log and keep looping.
                log.exception("market_recorder.flush_loop.error")

    # Seconds to wait for a pool connection before giving up on this flush cycle.
    # Without a timeout, a dead pool causes acquire() to hang forever, which
    # stacks up unbounded overflow-flush tasks and eventually crashes the process.
    ACQUIRE_TIMEOUT_S: float = float(os.getenv("MARKET_FLUSH_ACQUIRE_TIMEOUT_S", "5.0"))

    async def _flush(self) -> None:
        """Drain both queues into TimescaleDB using executemany.

        Any database error is caught and logged; buffered rows are discarded
        on failure rather than re-queued, to prevent unbounded memory growth.
        """
        ob_rows = _drain_queue(self._orderbook_queue)
        ex_rows = _drain_queue(self._execution_queue)

        if not ob_rows and not ex_rows:
            return

        try:
            async with self._pool.acquire(timeout=self.ACQUIRE_TIMEOUT_S) as conn:
                async with conn.transaction():
                    if ob_rows:
                        await conn.executemany(_INSERT_ORDERBOOK, ob_rows)
                        log.debug(
                            "market_recorder.flushed_orderbook",
                            count=len(ob_rows),
                        )
                    if ex_rows:
                        await conn.executemany(_INSERT_EXECUTION, ex_rows)
                        log.debug(
                            "market_recorder.flushed_executions",
                            count=len(ex_rows),
                        )
        except asyncio.TimeoutError:
            log.warning(
                "market_recorder.flush.acquire_timeout",
                ob_rows_dropped=len(ob_rows),
                ex_rows_dropped=len(ex_rows),
                timeout_s=self.ACQUIRE_TIMEOUT_S,
            )
        except asyncpg.PostgresError as exc:
            log.error(
                "market_recorder.flush.postgres_error",
                error=str(exc),
                ob_rows_dropped=len(ob_rows),
                ex_rows_dropped=len(ex_rows),
            )
        except Exception:
            log.exception(
                "market_recorder.flush.unexpected_error",
                ob_rows_dropped=len(ob_rows),
                ex_rows_dropped=len(ex_rows),
            )


# ---------------------------------------------------------------------------
# Module-level utility
# ---------------------------------------------------------------------------

def _drain_queue(q: asyncio.Queue[Any]) -> list[Any]:
    """Non-blocking drain of all currently available items from *q*."""
    rows: list[Any] = []
    while True:
        try:
            rows.append(q.get_nowait())
        except asyncio.QueueEmpty:
            break
    return rows
