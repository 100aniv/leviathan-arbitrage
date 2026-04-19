"""Unit tests for BinanceUserDataStream (BUG-189).

Covers the event-driven fill confirmation path that replaces REST polling.
Tests focus on wait_for_order_fill resolution, timeout, out-of-order
buffering, and listen_key keepalive cadence. Real WebSocket connection is
mocked via an injected listen loop (we push _dispatch_event directly or run
a fake async iterator) to keep tests hermetic and fast.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from src.infra.exchange.ws_trade.binance_user_data import (
    BinanceUserDataStream,
)


# ---------------------------------------------------------------------------
# wait_for_order_fill resolution
# ---------------------------------------------------------------------------


class TestWaitForOrderFillResolves:
    @pytest.mark.asyncio
    async def test_resolves_when_matching_event_dispatched(self):
        signed = AsyncMock()
        stream = BinanceUserDataStream("k", signed)

        async def _fire_event() -> None:
            # Small delay to let the waiter register.
            await asyncio.sleep(0.01)
            stream._dispatch_event(
                json.dumps(
                    {
                        "e": "ORDER_TRADE_UPDATE",
                        "o": {
                            "i": 12345,
                            "X": "FILLED",
                            "z": "0.5",
                            "ap": "63000.5",
                        },
                    }
                )
            )

        fire_task = asyncio.create_task(_fire_event())
        result = await stream.wait_for_order_fill("12345", timeout=0.5)
        await fire_task

        assert result is not None
        assert result["z"] == "0.5"
        assert result["ap"] == "63000.5"
        assert result["X"] == "FILLED"

    @pytest.mark.asyncio
    async def test_accepts_bytes_payload(self):
        signed = AsyncMock()
        stream = BinanceUserDataStream("k", signed)

        async def _fire() -> None:
            await asyncio.sleep(0.005)
            raw = json.dumps(
                {
                    "e": "ORDER_TRADE_UPDATE",
                    "o": {"i": 7, "X": "FILLED", "z": "1", "ap": "100"},
                }
            ).encode("utf-8")
            stream._dispatch_event(raw)

        task = asyncio.create_task(_fire())
        result = await stream.wait_for_order_fill("7", timeout=0.5)
        await task
        assert result is not None
        assert result["i"] == 7


# ---------------------------------------------------------------------------
# Timeout behaviour
# ---------------------------------------------------------------------------


class TestWaitForOrderFillTimeout:
    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self):
        stream = BinanceUserDataStream("k", AsyncMock())
        result = await stream.wait_for_order_fill("999", timeout=0.05)
        assert result is None
        # Waiter must be cleaned up so a later event is buffered, not orphaned.
        assert "999" not in stream._fill_events

    @pytest.mark.asyncio
    async def test_non_fill_status_ignored(self):
        """NEW / PARTIALLY_FILLED status must not resolve the waiter."""
        stream = BinanceUserDataStream("k", AsyncMock())

        async def _fire() -> None:
            await asyncio.sleep(0.01)
            stream._dispatch_event(
                json.dumps(
                    {
                        "e": "ORDER_TRADE_UPDATE",
                        "o": {"i": 55, "X": "NEW", "z": "0", "ap": "0"},
                    }
                )
            )

        task = asyncio.create_task(_fire())
        result = await stream.wait_for_order_fill("55", timeout=0.05)
        await task
        assert result is None


# ---------------------------------------------------------------------------
# Out-of-order buffering
# ---------------------------------------------------------------------------


class TestOutOfOrderBuffering:
    @pytest.mark.asyncio
    async def test_event_before_wait_is_buffered_and_returned(self):
        """Fill dispatched before caller awaits must still resolve."""
        stream = BinanceUserDataStream("k", AsyncMock())
        # Dispatch before any waiter exists.
        stream._dispatch_event(
            json.dumps(
                {
                    "e": "ORDER_TRADE_UPDATE",
                    "o": {
                        "i": 42,
                        "X": "FILLED",
                        "z": "2.0",
                        "ap": "2500",
                    },
                }
            )
        )
        assert "42" in stream._fill_buffer

        # Now a caller awaits — must return immediately from buffer.
        result = await stream.wait_for_order_fill("42", timeout=0.01)
        assert result is not None
        assert result["z"] == "2.0"
        # Buffer should be consumed.
        assert "42" not in stream._fill_buffer

    @pytest.mark.asyncio
    async def test_stale_buffer_entries_are_pruned(self, monkeypatch):
        """Buffered events older than _BUFFER_TTL_S are dropped."""
        from src.infra.exchange.ws_trade import binance_user_data as mod

        stream = BinanceUserDataStream("k", AsyncMock())
        # Insert stale entry manually (timestamp in the past beyond TTL).
        stale_ts = mod.time.monotonic() - (mod._BUFFER_TTL_S + 1.0)
        stream._fill_buffer["old"] = (stale_ts, {"i": "old", "X": "FILLED"})

        # Dispatch a new event — prune should trigger and drop "old".
        stream._dispatch_event(
            json.dumps(
                {
                    "e": "ORDER_TRADE_UPDATE",
                    "o": {"i": 9, "X": "FILLED", "z": "1", "ap": "10"},
                }
            )
        )
        assert "old" not in stream._fill_buffer
        assert "9" in stream._fill_buffer


# ---------------------------------------------------------------------------
# Listen-key lifecycle + keepalive
# ---------------------------------------------------------------------------


class TestListenKeyLifecycle:
    @pytest.mark.asyncio
    async def test_create_listen_key_posts_and_returns_key(self):
        calls: list[tuple] = []

        async def signed(method, path, params=None, data=None):
            calls.append((method, path))
            return {"listenKey": "abc123xyz"}

        stream = BinanceUserDataStream("k", signed)
        key = await stream._create_listen_key()
        assert key == "abc123xyz"
        assert calls == [("POST", "/fapi/v1/listenKey")]

    @pytest.mark.asyncio
    async def test_create_listen_key_raises_when_missing(self):
        async def signed(method, path, params=None, data=None):
            return {}

        stream = BinanceUserDataStream("k", signed)
        with pytest.raises(RuntimeError, match="listenKey missing"):
            await stream._create_listen_key()

    @pytest.mark.asyncio
    async def test_keepalive_once_puts_listen_key(self):
        calls: list[tuple] = []

        async def signed(method, path, params=None, data=None):
            calls.append((method, path))
            return {}

        stream = BinanceUserDataStream("k", signed)
        await stream._keepalive_once()
        assert calls == [("PUT", "/fapi/v1/listenKey")]

    @pytest.mark.asyncio
    async def test_keepalive_loop_invokes_put_on_interval(self, monkeypatch):
        """Fast-forward asyncio.sleep to verify keepalive cadence."""
        from src.infra.exchange.ws_trade import binance_user_data as mod

        sleep_calls: list[float] = []
        put_count = {"n": 0}

        async def fake_sleep(delay):
            sleep_calls.append(delay)
            # Stop after a few iterations to bound the loop.
            if len(sleep_calls) >= 3:
                # Emulate cancellation of the task from the outside.
                raise asyncio.CancelledError

        async def signed(method, path, params=None, data=None):
            if method == "PUT":
                put_count["n"] += 1
            return {}

        monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)

        stream = BinanceUserDataStream("k", signed)
        stream._running = True
        # Run keepalive loop directly; it returns when CancelledError propagates.
        await stream._keepalive_loop()

        # Each sleep call should be the configured interval (30 minutes).
        assert sleep_calls, "keepalive loop must sleep at least once"
        assert all(d == mod._KEEPALIVE_INTERVAL_S for d in sleep_calls)
        # put is called after each successful sleep before the cancel on iter 3.
        assert put_count["n"] == 2

    @pytest.mark.asyncio
    async def test_stop_deletes_listen_key_and_clears_waiters(self):
        calls: list[tuple] = []

        async def signed(method, path, params=None, data=None):
            calls.append((method, path))
            if method == "POST":
                return {"listenKey": "key_to_delete"}
            return {}

        stream = BinanceUserDataStream("k", signed)
        # Simulate start-without-listen-loop: set key + a pending waiter.
        stream._listen_key = "key_to_delete"
        stream._running = True
        waiter = asyncio.get_event_loop().create_future()
        stream._fill_events["orphan"] = waiter

        await stream.stop()

        assert ("DELETE", "/fapi/v1/listenKey") in calls
        assert stream._listen_key is None
        assert waiter.cancelled()
        assert stream._fill_events == {}
