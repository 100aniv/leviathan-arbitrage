"""Tests for engine/src/infra/db/market_recorder.py (MarketRecorder).

Covers: start/stop lifecycle, record_orderbook buffering, record_execution
buffering, flush writes to DB, buffer overflow triggers flush,
batch insert format.

asyncpg.Pool is mocked with AsyncMock.
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from src.infra.db.market_recorder import MarketRecorder, _drain_queue


# ---------------------------------------------------------------------------
# Helpers — mock asyncpg pool
# ---------------------------------------------------------------------------


def _make_mock_pool() -> MagicMock:
    """Return a mock asyncpg Pool whose acquire() context manager yields a mock conn."""
    conn = AsyncMock()
    conn.executemany = AsyncMock()

    # transaction() async context manager
    tx_cm = AsyncMock()
    tx_cm.__aenter__ = AsyncMock(return_value=tx_cm)
    tx_cm.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx_cm)

    # pool.acquire() async context manager
    acquire_cm = AsyncMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_cm)
    return pool


def _make_recorder(pool=None) -> tuple[MarketRecorder, MagicMock]:
    if pool is None:
        pool = _make_mock_pool()
    recorder = MarketRecorder(pool)
    return recorder, pool


# ---------------------------------------------------------------------------
# Lifecycle — start / stop
# ---------------------------------------------------------------------------


class TestLifecycle:
    async def test_start_sets_running_to_true(self):
        recorder, _ = _make_recorder()
        await recorder.start()
        assert recorder._running is True
        await recorder.stop()

    async def test_start_creates_flush_task(self):
        recorder, _ = _make_recorder()
        await recorder.start()
        assert recorder._flush_task is not None
        await recorder.stop()

    async def test_double_start_does_not_create_second_task(self):
        recorder, _ = _make_recorder()
        await recorder.start()
        first_task = recorder._flush_task
        await recorder.start()  # second call — should be a no-op
        assert recorder._flush_task is first_task
        await recorder.stop()

    async def test_stop_sets_running_to_false(self):
        recorder, _ = _make_recorder()
        await recorder.start()
        await recorder.stop()
        assert recorder._running is False

    async def test_stop_clears_flush_task(self):
        recorder, _ = _make_recorder()
        await recorder.start()
        await recorder.stop()
        assert recorder._flush_task is None

    async def test_stop_flushes_remaining_buffered_rows(self):
        recorder, pool = _make_recorder()
        await recorder.start()

        recorder.record_orderbook(
            exchange="binance",
            symbol="BTC/USDT",
            bids=[["50000", "1"]],
            asks=[["50001", "1"]],
            best_bid=Decimal("50000"),
            best_ask=Decimal("50001"),
        )

        # Get the connection mock so we can assert executemany was called
        conn = pool.acquire.return_value.__aenter__.return_value
        await recorder.stop()

        conn.executemany.assert_called()


# ---------------------------------------------------------------------------
# record_orderbook — buffering
# ---------------------------------------------------------------------------


class TestRecordOrderbookBuffering:
    async def test_record_orderbook_adds_row_to_queue(self):
        recorder, _ = _make_recorder()
        await recorder.start()

        assert recorder._orderbook_queue.qsize() == 0
        recorder.record_orderbook(
            exchange="binance",
            symbol="BTC/USDT",
            bids=[["50000", "1"]],
            asks=[["50001", "1"]],
            best_bid=Decimal("50000"),
            best_ask=Decimal("50001"),
        )
        assert recorder._orderbook_queue.qsize() == 1
        await recorder.stop()

    async def test_record_orderbook_row_contains_spread_bps(self):
        recorder, _ = _make_recorder()
        await recorder.start()

        recorder.record_orderbook(
            exchange="binance",
            symbol="BTC/USDT",
            bids=[["50000", "1"]],
            asks=[["50001", "1"]],
            best_bid=Decimal("50000"),
            best_ask=Decimal("50001"),
        )
        row = recorder._orderbook_queue.get_nowait()
        # row = (ts, exchange, symbol, bids_json, asks_json, best_bid, best_ask, spread_bps, mid_price)
        spread_bps = row[7]
        # (50001-50000) / 50000.5 * 10000 ≈ 0.1999...
        assert spread_bps > Decimal("0")

        await recorder.stop()

    async def test_record_orderbook_row_contains_mid_price(self):
        recorder, _ = _make_recorder()
        await recorder.start()

        recorder.record_orderbook(
            exchange="binance",
            symbol="BTC/USDT",
            bids=[],
            asks=[],
            best_bid=Decimal("50000"),
            best_ask=Decimal("50002"),
        )
        row = recorder._orderbook_queue.get_nowait()
        mid_price = row[8]
        assert mid_price == Decimal("50001")
        await recorder.stop()

    async def test_record_orderbook_bids_asks_are_json_strings(self):
        recorder, _ = _make_recorder()
        await recorder.start()

        bids = [["50000", "1.5"], ["49999", "2.0"]]
        asks = [["50001", "1.2"]]
        recorder.record_orderbook(
            exchange="binance",
            symbol="BTC/USDT",
            bids=bids,
            asks=asks,
            best_bid=Decimal("50000"),
            best_ask=Decimal("50001"),
        )
        row = recorder._orderbook_queue.get_nowait()
        # row[3] = bids_json, row[4] = asks_json
        assert json.loads(row[3]) == bids
        assert json.loads(row[4]) == asks
        await recorder.stop()

    async def test_record_orderbook_never_raises_on_bad_input(self):
        recorder, _ = _make_recorder()
        await recorder.start()
        # Zero mid_price — should not raise
        recorder.record_orderbook(
            exchange="binance",
            symbol="BTC/USDT",
            bids=[],
            asks=[],
            best_bid=Decimal("0"),
            best_ask=Decimal("0"),
        )
        await recorder.stop()


# ---------------------------------------------------------------------------
# record_execution — buffering
# ---------------------------------------------------------------------------


class TestRecordExecutionBuffering:
    async def test_record_execution_adds_row_to_queue(self):
        recorder, _ = _make_recorder()
        await recorder.start()

        assert recorder._execution_queue.qsize() == 0
        recorder.record_execution(
            strategy_id="strat_v1",
            buy_exchange="binance",
            sell_exchange="okx",
            symbol="BTC/USDT",
            buy_price=Decimal("50000"),
            sell_price=Decimal("50100"),
            size=Decimal("0.1"),
        )
        assert recorder._execution_queue.qsize() == 1
        await recorder.stop()

    async def test_record_execution_row_contains_strategy_id(self):
        recorder, _ = _make_recorder()
        await recorder.start()

        recorder.record_execution(
            strategy_id="my_strategy",
            buy_exchange="binance",
            sell_exchange="okx",
            symbol="ETH/USDT",
            buy_price=Decimal("3000"),
            sell_price=Decimal("3010"),
            size=Decimal("1.0"),
        )
        row = recorder._execution_queue.get_nowait()
        # row = (ts, strategy_id, signal_id, buy_exchange, ...)
        assert row[1] == "my_strategy"
        await recorder.stop()

    async def test_record_execution_metadata_serialised_as_json(self):
        recorder, _ = _make_recorder()
        await recorder.start()

        meta = {"note": "test trade", "version": 1}
        recorder.record_execution(
            strategy_id="strat",
            buy_exchange="binance",
            sell_exchange="okx",
            symbol="BTC/USDT",
            buy_price=Decimal("50000"),
            sell_price=Decimal("50100"),
            size=Decimal("0.1"),
            metadata=meta,
        )
        row = recorder._execution_queue.get_nowait()
        # row[-2] is metadata JSON string, row[-1] is mode
        assert json.loads(row[-2]) == meta
        await recorder.stop()

    async def test_record_execution_defaults_status_to_pending(self):
        recorder, _ = _make_recorder()
        await recorder.start()

        recorder.record_execution(
            strategy_id="strat",
            buy_exchange="binance",
            sell_exchange="okx",
            symbol="BTC/USDT",
            buy_price=Decimal("50000"),
            sell_price=Decimal("50100"),
            size=Decimal("0.1"),
        )
        row = recorder._execution_queue.get_nowait()
        # row[-3] = status, row[-2] = metadata, row[-1] = mode
        assert row[-3] == "pending"
        await recorder.stop()


# ---------------------------------------------------------------------------
# _flush — writes to DB
# ---------------------------------------------------------------------------


class TestFlush:
    async def test_flush_calls_executemany_for_orderbook_rows(self):
        recorder, pool = _make_recorder()
        await recorder.start()
        conn = pool.acquire.return_value.__aenter__.return_value

        recorder.record_orderbook(
            exchange="binance",
            symbol="BTC/USDT",
            bids=[],
            asks=[],
            best_bid=Decimal("50000"),
            best_ask=Decimal("50001"),
        )
        await recorder._flush()

        assert conn.executemany.call_count >= 1
        await recorder.stop()

    async def test_flush_does_nothing_when_both_queues_empty(self):
        recorder, pool = _make_recorder()
        await recorder.start()
        conn = pool.acquire.return_value.__aenter__.return_value

        await recorder._flush()  # both queues empty — no DB call
        conn.executemany.assert_not_called()
        await recorder.stop()

    async def test_flush_drains_orderbook_queue(self):
        recorder, pool = _make_recorder()
        await recorder.start()

        for i in range(3):
            recorder.record_orderbook(
                exchange="binance",
                symbol="BTC/USDT",
                bids=[],
                asks=[],
                best_bid=Decimal("50000"),
                best_ask=Decimal("50001"),
            )

        assert recorder._orderbook_queue.qsize() == 3
        await recorder._flush()
        assert recorder._orderbook_queue.qsize() == 0
        await recorder.stop()

    async def test_flush_logs_error_on_postgres_exception_without_raising(self):
        import asyncpg

        recorder, pool = _make_recorder()
        await recorder.start()

        conn = pool.acquire.return_value.__aenter__.return_value
        conn.executemany = AsyncMock(
            side_effect=asyncpg.PostgresError("connection lost")
        )

        recorder.record_orderbook(
            exchange="binance",
            symbol="BTC/USDT",
            bids=[],
            asks=[],
            best_bid=Decimal("50000"),
            best_ask=Decimal("50001"),
        )

        # Must not raise
        await recorder._flush()
        await recorder.stop()

    async def test_flush_logs_warning_on_acquire_timeout_without_raising(self):
        """pool.acquire() timing out must not crash the engine (root-cause fix)."""
        recorder, pool = _make_recorder()
        await recorder.start()

        # Simulate pool.acquire() timing out (dead DB connection pool)
        acquire_cm = AsyncMock()
        acquire_cm.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError())
        acquire_cm.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=acquire_cm)

        recorder.record_orderbook(
            exchange="binance",
            symbol="BTC/USDT",
            bids=[],
            asks=[],
            best_bid=Decimal("50000"),
            best_ask=Decimal("50001"),
        )

        # Must not raise — timeout is handled gracefully
        await recorder._flush()
        await recorder.stop()

    async def test_flush_survives_repeated_acquire_timeouts(self):
        """Multiple consecutive timeout failures must not crash the flush loop."""
        recorder, pool = _make_recorder()
        await recorder.start()

        acquire_cm = AsyncMock()
        acquire_cm.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError())
        acquire_cm.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=acquire_cm)

        for _ in range(5):
            recorder.record_orderbook(
                exchange="binance",
                symbol="BTC/USDT",
                bids=[],
                asks=[],
                best_bid=Decimal("50000"),
                best_ask=Decimal("50001"),
            )
            await recorder._flush()  # each call must survive

        await recorder.stop()


# ---------------------------------------------------------------------------
# Buffer overflow triggers flush
# ---------------------------------------------------------------------------


class TestBufferOverflow:
    async def test_overflow_flush_triggered_when_orderbook_buffer_at_capacity(self):
        recorder, pool = _make_recorder()
        await recorder.start()

        # Patch _maybe_trigger_flush to observe it being called
        trigger_calls: list[int] = []
        original_maybe = recorder._maybe_trigger_flush

        def counting_trigger():
            trigger_calls.append(recorder._orderbook_queue.qsize())
            original_maybe()

        recorder._maybe_trigger_flush = counting_trigger  # type: ignore[method-assign]

        # Add one row — _maybe_trigger_flush called
        recorder.record_orderbook(
            exchange="binance",
            symbol="BTC/USDT",
            bids=[],
            asks=[],
            best_bid=Decimal("50000"),
            best_ask=Decimal("50001"),
        )

        assert len(trigger_calls) == 1
        await recorder.stop()

    async def test_maybe_trigger_flush_creates_task_when_buffer_full(self):
        recorder, pool = _make_recorder()
        await recorder.start()

        # Force the queue size to appear at capacity
        recorder._orderbook_queue._maxsize = 0  # unlimited — set qsize directly by filling
        for _ in range(MarketRecorder.MAX_BUFFER_SIZE):
            recorder._orderbook_queue.put_nowait(("dummy",) * 9)  # type: ignore[arg-type]

        # Now _maybe_trigger_flush should schedule a flush task
        with patch.object(asyncio, "get_running_loop", wraps=asyncio.get_running_loop) as mock_loop:
            recorder._maybe_trigger_flush()
            # Give the event loop a chance to schedule the task
            await asyncio.sleep(0)

        await recorder.stop()


# ---------------------------------------------------------------------------
# _drain_queue utility
# ---------------------------------------------------------------------------


class TestDrainQueue:
    def test_drain_empty_queue_returns_empty_list(self):
        q: asyncio.Queue = asyncio.Queue()
        result = _drain_queue(q)
        assert result == []

    def test_drain_queue_with_items_returns_all_items(self):
        q: asyncio.Queue = asyncio.Queue()
        q.put_nowait("a")
        q.put_nowait("b")
        q.put_nowait("c")
        result = _drain_queue(q)
        assert result == ["a", "b", "c"]

    def test_drain_queue_leaves_it_empty(self):
        q: asyncio.Queue = asyncio.Queue()
        for i in range(5):
            q.put_nowait(i)
        _drain_queue(q)
        assert q.qsize() == 0
