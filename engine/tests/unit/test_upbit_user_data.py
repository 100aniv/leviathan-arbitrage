"""Unit tests for UpbitUserDataStream (BUG-190).

Covers the event-driven fill confirmation path that replaces any REST polling.
Tests focus on wait_for_order_fill resolution, timeout, out-of-order buffering,
JWT auth header construction, subscribe frame shape, and reconnect wiring.
Real WebSocket connection is mocked — we push _dispatch_event directly to keep
tests hermetic and fast.
"""
from __future__ import annotations

import asyncio
import json

import jwt
import pytest

from src.infra.exchange.ws_trade.upbit_user_data import (
    UpbitUserDataStream,
)


# ---------------------------------------------------------------------------
# wait_for_order_fill resolution
# ---------------------------------------------------------------------------


class TestWaitForOrderFillResolves:
    @pytest.mark.asyncio
    async def test_resolves_when_matching_event_dispatched(self):
        stream = UpbitUserDataStream("access", "secret")

        async def _fire_event() -> None:
            # Small delay to let the waiter register.
            await asyncio.sleep(0.01)
            stream._dispatch_event(
                json.dumps(
                    {
                        "type": "myOrder",
                        "uuid": "order-abc",
                        "side": "bid",
                        "state": "done",
                        "volume": "0.5",
                        "price": "63000.5",
                    }
                )
            )

        fire_task = asyncio.create_task(_fire_event())
        result = await stream.wait_for_order_fill("order-abc", timeout=0.5)
        await fire_task

        assert result is not None
        assert result["volume"] == "0.5"
        assert result["price"] == "63000.5"
        assert result["state"] == "done"

    @pytest.mark.asyncio
    async def test_accepts_bytes_payload(self):
        stream = UpbitUserDataStream("access", "secret")

        async def _fire() -> None:
            await asyncio.sleep(0.005)
            raw = json.dumps(
                {
                    "type": "myOrder",
                    "uuid": "u-7",
                    "state": "done",
                    "volume": "1",
                    "price": "100",
                }
            ).encode("utf-8")
            stream._dispatch_event(raw)

        task = asyncio.create_task(_fire())
        result = await stream.wait_for_order_fill("u-7", timeout=0.5)
        await task
        assert result is not None
        assert result["uuid"] == "u-7"


# ---------------------------------------------------------------------------
# Timeout behaviour
# ---------------------------------------------------------------------------


class TestWaitForOrderFillTimeout:
    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self):
        stream = UpbitUserDataStream("access", "secret")
        result = await stream.wait_for_order_fill("missing", timeout=0.05)
        assert result is None
        # Waiter must be cleaned up so a later event is buffered, not orphaned.
        assert "missing" not in stream._fill_events

    @pytest.mark.asyncio
    async def test_non_done_state_ignored(self):
        """state='wait' or 'cancel' must not resolve the waiter."""
        stream = UpbitUserDataStream("access", "secret")

        async def _fire() -> None:
            await asyncio.sleep(0.01)
            stream._dispatch_event(
                json.dumps(
                    {
                        "type": "myOrder",
                        "uuid": "u-55",
                        "state": "wait",
                        "volume": "0",
                        "price": "0",
                    }
                )
            )

        task = asyncio.create_task(_fire())
        result = await stream.wait_for_order_fill("u-55", timeout=0.05)
        await task
        assert result is None


# ---------------------------------------------------------------------------
# Out-of-order buffering
# ---------------------------------------------------------------------------


class TestOutOfOrderBuffering:
    @pytest.mark.asyncio
    async def test_event_before_wait_is_buffered_and_returned(self):
        """Fill dispatched before caller awaits must still resolve."""
        stream = UpbitUserDataStream("access", "secret")
        # Dispatch before any waiter exists.
        stream._dispatch_event(
            json.dumps(
                {
                    "type": "myOrder",
                    "uuid": "u-42",
                    "state": "done",
                    "volume": "2.0",
                    "price": "2500",
                }
            )
        )
        assert "u-42" in stream._fill_buffer

        # Now a caller awaits — must return immediately from buffer.
        result = await stream.wait_for_order_fill("u-42", timeout=0.01)
        assert result is not None
        assert result["volume"] == "2.0"
        # Buffer should be consumed.
        assert "u-42" not in stream._fill_buffer

    @pytest.mark.asyncio
    async def test_stale_buffer_entries_are_pruned(self):
        """Buffered events older than _BUFFER_TTL_S are dropped."""
        from src.infra.exchange.ws_trade import upbit_user_data as mod

        stream = UpbitUserDataStream("access", "secret")
        # Insert stale entry manually (timestamp in the past beyond TTL).
        stale_ts = mod.time.monotonic() - (mod._BUFFER_TTL_S + 1.0)
        stream._fill_buffer["old"] = (
            stale_ts,
            {"uuid": "old", "state": "done"},
        )

        # Dispatch a new event — prune should trigger and drop "old".
        stream._dispatch_event(
            json.dumps(
                {
                    "type": "myOrder",
                    "uuid": "u-9",
                    "state": "done",
                    "volume": "1",
                    "price": "10",
                }
            )
        )
        assert "old" not in stream._fill_buffer
        assert "u-9" in stream._fill_buffer


# ---------------------------------------------------------------------------
# JWT auth
# ---------------------------------------------------------------------------


class TestJwtAuth:
    def test_build_jwt_is_hs256_and_includes_access_key_and_nonce(self):
        stream = UpbitUserDataStream("my-access", "my-secret")
        token = stream._build_jwt()
        assert isinstance(token, str)
        # Header
        header = jwt.get_unverified_header(token)
        assert header["alg"] == "HS256"
        # Payload round-trips with the shared secret
        payload = jwt.decode(token, "my-secret", algorithms=["HS256"])
        assert payload["access_key"] == "my-access"
        assert "nonce" in payload and payload["nonce"]
        # Private-channel JWT MUST NOT carry query_hash fields
        assert "query_hash" not in payload

    def test_build_subscribe_message_shape(self):
        stream = UpbitUserDataStream(
            "a", "b", codes=["KRW-BTC", "KRW-ETH"]
        )
        frame = stream._build_subscribe_message()
        assert isinstance(frame, list) and len(frame) == 2
        assert "ticket" in frame[0]
        assert frame[1] == {
            "type": "myOrder",
            "codes": ["KRW-BTC", "KRW-ETH"],
        }

    def test_build_subscribe_message_no_codes_omits_codes_field(self):
        stream = UpbitUserDataStream("a", "b")
        frame = stream._build_subscribe_message()
        assert frame[1] == {"type": "myOrder"}


# ---------------------------------------------------------------------------
# Reconnect / lifecycle wiring
# ---------------------------------------------------------------------------


class TestReconnectAndLifecycle:
    @pytest.mark.asyncio
    async def test_stop_cancels_pending_waiters(self):
        stream = UpbitUserDataStream("access", "secret")
        # Install a pending waiter without a real listen loop.
        waiter = asyncio.get_event_loop().create_future()
        stream._fill_events["orphan"] = waiter

        await stream.stop()

        assert waiter.cancelled()
        assert stream._fill_events == {}
        assert stream._fill_buffer == {}

    @pytest.mark.asyncio
    async def test_listen_loop_retries_on_connect_failure(self, monkeypatch):
        """On connect error the loop must sleep then retry until stopped."""
        from src.infra.exchange.ws_trade import upbit_user_data as mod

        attempts = {"n": 0}

        class _BoomConnect:
            def __await__(self):
                async def _raise():
                    attempts["n"] += 1
                    raise RuntimeError("boom")
                return _raise().__await__()

            async def __aenter__(self):
                attempts["n"] += 1
                raise RuntimeError("boom")

            async def __aexit__(self, *a):
                return False

        def _fake_connect(*args, **kwargs):
            return _BoomConnect()

        sleep_calls: list[float] = []

        async def _fake_sleep(delay):
            sleep_calls.append(delay)
            # Stop after a couple of retries so the test terminates.
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError

        monkeypatch.setattr(mod.websockets, "connect", _fake_connect)
        monkeypatch.setattr(mod.asyncio, "sleep", _fake_sleep)

        stream = UpbitUserDataStream("access", "secret")
        stream._running = True
        await stream._listen_loop()

        assert attempts["n"] >= 1
        # Exponential backoff: first sleep uses the initial value.
        assert sleep_calls[0] == mod._RECONNECT_BACKOFF_INITIAL_S
