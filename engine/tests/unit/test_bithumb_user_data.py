"""Unit tests for BithumbUserDataStream (BUG-191).

Mirrors test_binance_user_data.py / test_coinone_user_data.py structure.
Covers the event-driven fill confirmation path via the Bithumb v2 private
WebSocket myOrder channel. WebSocket connection is mocked via direct
_dispatch_event injection to keep tests hermetic and fast.

Bithumb v2 private WS spec (confirmed via CCXT PR #27138, 2025-10):
  endpoint : wss://ws-api.bithumb.com/websocket/v1/private
  auth     : Authorization: Bearer <HS256-JWT>
             payload = {access_key, nonce (UUID), timestamp (ms)}
  subscribe: [{ticket: <uuid>}, {type: "myOrder", codes: [...]}]
  event    : {type: "myOrder", uuid: "<order-uuid>", state: "done"|"trade",
              executed_volume: "0.01", price: "63000000", ...}
  fill states: {"done", "trade"}   wait/cancel are non-fill
"""
from __future__ import annotations

import asyncio
import json

import pytest

from src.infra.exchange.ws_trade.bithumb_user_data import (
    BithumbUserDataStream,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fill_event(order_uuid: str, state: str = "done",
                     executed_volume: str = "0.01",
                     price: str = "63000000") -> str:
    return json.dumps({
        "type": "myOrder",
        "uuid": order_uuid,
        "state": state,
        "executed_volume": executed_volume,
        "price": price,
    })


# ---------------------------------------------------------------------------
# wait_for_order_fill resolution
# ---------------------------------------------------------------------------


class TestWaitForOrderFillResolves:
    @pytest.mark.asyncio
    async def test_resolves_when_matching_event_dispatched(self):
        stream = BithumbUserDataStream("access-key", "secret-key")

        async def _fire() -> None:
            await asyncio.sleep(0.01)
            stream._dispatch_event(_make_fill_event("uuid-1"))

        task = asyncio.create_task(_fire())
        result = await stream.wait_for_order_fill("uuid-1", timeout=0.5)
        await task

        assert result is not None
        assert result["uuid"] == "uuid-1"
        assert result["state"] == "done"
        assert result["executed_volume"] == "0.01"
        assert result["price"] == "63000000"

    @pytest.mark.asyncio
    async def test_accepts_bytes_payload(self):
        stream = BithumbUserDataStream("access-key", "secret-key")

        async def _fire() -> None:
            await asyncio.sleep(0.005)
            raw = _make_fill_event("uuid-2", state="done").encode("utf-8")
            stream._dispatch_event(raw)

        task = asyncio.create_task(_fire())
        result = await stream.wait_for_order_fill("uuid-2", timeout=0.5)
        await task

        assert result is not None
        assert result["uuid"] == "uuid-2"
        assert result["state"] == "done"

    @pytest.mark.asyncio
    async def test_trade_state_ignored_only_done_resolves(self):
        """BUG-213: ``state='trade'`` (per-execution partial) MUST NOT resolve
        the waiter. Only the terminal ``state='done'`` carries the final
        cumulative fill. Resolving on the first partial causes subsequent
        executions to become ghost inventory.
        """
        stream = BithumbUserDataStream("access-key", "secret-key")

        async def _fire_partial_then_done() -> None:
            # Partial fill first — must be ignored.
            await asyncio.sleep(0.01)
            stream._dispatch_event(
                _make_fill_event("uuid-seq", state="trade",
                                 executed_volume="0.4", price="63000000")
            )
            # Terminal fill with the full cumulative qty — must resolve.
            await asyncio.sleep(0.01)
            stream._dispatch_event(
                _make_fill_event("uuid-seq", state="done",
                                 executed_volume="1.0", price="63000000")
            )

        task = asyncio.create_task(_fire_partial_then_done())
        result = await stream.wait_for_order_fill("uuid-seq", timeout=0.5)
        await task

        assert result is not None
        # Must see the terminal event's cumulative qty, NOT the first partial.
        assert result["state"] == "done"
        assert result["executed_volume"] == "1.0"


# ---------------------------------------------------------------------------
# Timeout behaviour
# ---------------------------------------------------------------------------


class TestWaitForOrderFillTimeout:
    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self):
        stream = BithumbUserDataStream("access-key", "secret-key")
        result = await stream.wait_for_order_fill("missing-uuid", timeout=0.05)
        assert result is None
        # Waiter must be cleaned up.
        assert "missing-uuid" not in stream._fill_events

    @pytest.mark.asyncio
    async def test_non_fill_state_ignored(self):
        """state='wait' or 'cancel' must not resolve the waiter."""
        stream = BithumbUserDataStream("access-key", "secret-key")

        async def _fire() -> None:
            await asyncio.sleep(0.01)
            stream._dispatch_event(json.dumps({
                "type": "myOrder",
                "uuid": "uuid-wait",
                "state": "wait",
            }))

        task = asyncio.create_task(_fire())
        result = await stream.wait_for_order_fill("uuid-wait", timeout=0.05)
        await task
        assert result is None

    @pytest.mark.asyncio
    async def test_non_myorder_type_ignored(self):
        """Events with type != 'myOrder' must not resolve myOrder waiters."""
        stream = BithumbUserDataStream("access-key", "secret-key")

        async def _fire() -> None:
            await asyncio.sleep(0.01)
            stream._dispatch_event(json.dumps({
                "type": "myAsset",
                "uuid": "uuid-asset",
                "state": "done",
            }))

        task = asyncio.create_task(_fire())
        result = await stream.wait_for_order_fill("uuid-asset", timeout=0.05)
        await task
        assert result is None


# ---------------------------------------------------------------------------
# Out-of-order buffering
# ---------------------------------------------------------------------------


class TestOutOfOrderBuffering:
    @pytest.mark.asyncio
    async def test_event_before_wait_is_buffered_and_returned(self):
        """Fill dispatched before caller awaits must still resolve."""
        stream = BithumbUserDataStream("access-key", "secret-key")
        stream._dispatch_event(_make_fill_event("uuid-buf", executed_volume="2.0"))
        assert "uuid-buf" in stream._fill_buffer

        result = await stream.wait_for_order_fill("uuid-buf", timeout=0.01)
        assert result is not None
        assert result["executed_volume"] == "2.0"
        assert "uuid-buf" not in stream._fill_buffer

    @pytest.mark.asyncio
    async def test_stale_buffer_entries_are_pruned(self):
        """Buffered events older than _BUFFER_TTL_S are dropped on next dispatch."""
        from src.infra.exchange.ws_trade import bithumb_user_data as mod

        stream = BithumbUserDataStream("access-key", "secret-key")
        stale_ts = mod.time.monotonic() - (mod._BUFFER_TTL_S + 1.0)
        stream._fill_buffer["old-uuid"] = (stale_ts, {"uuid": "old-uuid", "state": "done"})

        # Dispatching a new event triggers prune.
        stream._dispatch_event(_make_fill_event("fresh-uuid"))
        assert "old-uuid" not in stream._fill_buffer
        assert "fresh-uuid" in stream._fill_buffer


# ---------------------------------------------------------------------------
# Auth: JWT correctness
# ---------------------------------------------------------------------------


class TestJWTAuth:
    def test_build_jwt_produces_bearer_token(self):
        """_build_jwt must return a 3-part HS256 JWT string."""
        stream = BithumbUserDataStream("test-access-key", "test-secret-key")
        token = stream._build_jwt()

        parts = token.split(".")
        assert len(parts) == 3, "JWT must be header.payload.signature"

    def test_build_jwt_payload_contains_required_fields(self):
        """JWT payload must include access_key, nonce, and timestamp."""
        import base64
        import json as _json

        stream = BithumbUserDataStream("my-access", "my-secret")
        token = stream._build_jwt()

        # Decode payload (second segment) — add padding as needed.
        payload_b64 = token.split(".")[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = _json.loads(base64.urlsafe_b64decode(payload_b64))

        assert payload["access_key"] == "my-access"
        assert "nonce" in payload
        assert "timestamp" in payload

    def test_build_jwt_unique_nonce(self):
        """Successive JWT builds must use distinct nonces."""
        import base64
        import json as _json

        stream = BithumbUserDataStream("my-access", "my-secret")

        def _get_nonce(tok: str) -> str:
            seg = tok.split(".")[1]
            padding = 4 - len(seg) % 4
            if padding != 4:
                seg += "=" * padding
            return _json.loads(base64.urlsafe_b64decode(seg))["nonce"]

        nonce1 = _get_nonce(stream._build_jwt())
        nonce2 = _get_nonce(stream._build_jwt())
        assert nonce1 != nonce2


# ---------------------------------------------------------------------------
# Subscribe frame structure
# ---------------------------------------------------------------------------


class TestSubscribeFrame:
    def test_subscribe_message_shape(self):
        """Frame must be [{ticket:...}, {type:'myOrder'}] — Bithumb/Upbit shape."""
        stream = BithumbUserDataStream("access-key", "secret-key")
        frame = stream._build_subscribe_message()

        assert isinstance(frame, list)
        assert len(frame) == 2
        assert "ticket" in frame[0]
        assert frame[1]["type"] == "myOrder"

    def test_subscribe_message_with_codes(self):
        """When codes are provided they appear in the second frame element."""
        stream = BithumbUserDataStream("access-key", "secret-key",
                                       codes=["KRW-BTC", "KRW-ETH"])
        frame = stream._build_subscribe_message()
        assert frame[1].get("codes") == ["KRW-BTC", "KRW-ETH"]

    def test_subscribe_message_without_codes(self):
        """When no codes given, 'codes' key must be absent (subscribe to all)."""
        stream = BithumbUserDataStream("access-key", "secret-key")
        frame = stream._build_subscribe_message()
        assert "codes" not in frame[1]


# ---------------------------------------------------------------------------
# Lifecycle: start idempotency / stop
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_idempotent(self, monkeypatch):
        """Calling start() twice must not spawn duplicate listen tasks."""
        stream = BithumbUserDataStream("access-key", "secret-key")

        async def _noop():
            while True:
                await asyncio.sleep(1)

        monkeypatch.setattr(stream, "_listen_loop", _noop)

        await stream.start()
        first_task = stream._listen_task
        await stream.start()  # second call — must be idempotent
        assert stream._listen_task is first_task
        await stream.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_waiters_and_task(self, monkeypatch):
        """stop() cancels pending waiters and the background listen task."""
        stream = BithumbUserDataStream("access-key", "secret-key")

        async def _noop():
            while True:
                await asyncio.sleep(1)

        monkeypatch.setattr(stream, "_listen_loop", _noop)

        await stream.start()
        waiter = asyncio.get_event_loop().create_future()
        stream._fill_events["orphan"] = waiter

        await stream.stop()

        assert waiter.cancelled()
        assert stream._fill_events == {}
        assert stream._listen_task is None

    @pytest.mark.asyncio
    async def test_reconnect_on_ws_close(self, monkeypatch):
        """_listen_loop must retry after a WS exception (reconnect logic)."""
        from src.infra.exchange.ws_trade import bithumb_user_data as mod

        connect_attempts: list[int] = []

        # Patch asyncio.sleep to avoid real waiting and track it.
        async def fast_sleep(_delay):
            pass

        monkeypatch.setattr(mod.asyncio, "sleep", fast_sleep)

        stream = BithumbUserDataStream("access-key", "secret-key")

        # Patch websockets.connect to fail the first time, then cancel.
        call_count = {"n": 0}

        class _FakeCtx:
            async def __aenter__(self):
                call_count["n"] += 1
                connect_attempts.append(call_count["n"])
                if call_count["n"] == 1:
                    raise OSError("simulated WS close")
                # On second call, stop the loop to exit cleanly.
                stream._running = False
                raise asyncio.CancelledError

            async def __aexit__(self, *_):
                pass

        monkeypatch.setattr(mod.websockets, "connect", lambda *a, **kw: _FakeCtx())

        stream._running = True
        try:
            await stream._listen_loop()
        except asyncio.CancelledError:
            pass

        # At least 2 connect attempts confirms the reconnect path was taken.
        assert len(connect_attempts) >= 2
