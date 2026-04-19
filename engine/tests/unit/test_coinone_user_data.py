"""Unit tests for CoinoneUserDataStream (BUG-192).

Mirrors test_binance_user_data.py structure. Covers the event-driven fill
confirmation path via the Coinone private WebSocket MYORDER channel.
WebSocket connection is mocked via direct _dispatch_event injection.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from src.infra.exchange.ws_trade.coinone_user_data import (
    CoinoneUserDataStream,
    _normalize_short_fields,
)


# ---------------------------------------------------------------------------
# wait_for_order_fill resolution
# ---------------------------------------------------------------------------


class TestWaitForOrderFillResolves:
    @pytest.mark.asyncio
    async def test_resolves_when_matching_event_dispatched(self):
        stream = CoinoneUserDataStream("access", "secret")

        async def _fire_event() -> None:
            await asyncio.sleep(0.01)
            stream._dispatch_event(
                json.dumps(
                    {
                        "response_type": "DATA",
                        "channel": "MYORDER",
                        "data": {
                            "quote_currency": "KRW",
                            "target_currency": "BTC",
                            "order_id": "oid-1",
                            "status": "trade_done",
                            "side": "BID",
                            "executed_price": "63000000",
                            "executed_qty": "0.01",
                        },
                    }
                )
            )

        fire_task = asyncio.create_task(_fire_event())
        result = await stream.wait_for_order_fill("oid-1", timeout=0.5)
        await fire_task

        assert result is not None
        assert result["executed_qty"] == "0.01"
        assert result["executed_price"] == "63000000"
        assert result["status"] == "trade_done"

    @pytest.mark.asyncio
    async def test_accepts_bytes_payload(self):
        stream = CoinoneUserDataStream("access", "secret")

        async def _fire() -> None:
            await asyncio.sleep(0.005)
            raw = json.dumps(
                {
                    "response_type": "DATA",
                    "channel": "MYORDER",
                    "data": {
                        "order_id": "oid-2",
                        "status": "trade_done",
                        "executed_qty": "1",
                        "executed_price": "100",
                    },
                }
            ).encode("utf-8")
            stream._dispatch_event(raw)

        task = asyncio.create_task(_fire())
        result = await stream.wait_for_order_fill("oid-2", timeout=0.5)
        await task
        assert result is not None
        assert result["order_id"] == "oid-2"
        assert result["status"] == "trade_done"

    @pytest.mark.asyncio
    async def test_resolves_short_format_event(self):
        """SHORT-format messages (r/c/d, st, oi, eq, ep) must normalize correctly."""
        stream = CoinoneUserDataStream("access", "secret")

        async def _fire() -> None:
            await asyncio.sleep(0.005)
            stream._dispatch_event(
                json.dumps(
                    {
                        "r": "DATA",
                        "c": "MYORDER",
                        "d": {
                            "oi": "oid-short",
                            "st": "trade_done",
                            "eq": "0.5",
                            "ep": "2500",
                            "qc": "KRW",
                            "tc": "ETH",
                        },
                    }
                )
            )

        task = asyncio.create_task(_fire())
        result = await stream.wait_for_order_fill("oid-short", timeout=0.5)
        await task
        assert result is not None
        # After normalization both SHORT and DEFAULT fields should resolve.
        assert result["order_id"] == "oid-short"
        assert result["status"] == "trade_done"
        assert result["executed_qty"] == "0.5"
        assert result["executed_price"] == "2500"

    @pytest.mark.asyncio
    async def test_trade_status_ignored_only_trade_done_resolves(self):
        """BUG-213: ``status='trade'`` (per-execution partial) MUST NOT resolve
        the waiter. Only the terminal ``status='trade_done'`` carries the final
        cumulative fill. Resolving on the first partial causes subsequent
        executions to become ghost inventory.
        """
        stream = CoinoneUserDataStream("access", "secret")

        async def _fire_partial_then_done() -> None:
            # Partial fill first — must be ignored.
            await asyncio.sleep(0.01)
            stream._dispatch_event(
                json.dumps(
                    {
                        "response_type": "DATA",
                        "channel": "MYORDER",
                        "data": {
                            "order_id": "oid-seq",
                            "status": "trade",
                            "executed_qty": "0.4",
                            "executed_price": "63000000",
                        },
                    }
                )
            )
            # Terminal fill with the full cumulative qty — must resolve.
            await asyncio.sleep(0.01)
            stream._dispatch_event(
                json.dumps(
                    {
                        "response_type": "DATA",
                        "channel": "MYORDER",
                        "data": {
                            "order_id": "oid-seq",
                            "status": "trade_done",
                            "executed_qty": "1.0",
                            "executed_price": "63000000",
                        },
                    }
                )
            )

        task = asyncio.create_task(_fire_partial_then_done())
        result = await stream.wait_for_order_fill("oid-seq", timeout=0.5)
        await task

        assert result is not None
        # Must see the terminal event's cumulative qty, NOT the first partial.
        assert result["status"] == "trade_done"
        assert result["executed_qty"] == "1.0"


# ---------------------------------------------------------------------------
# Timeout behaviour
# ---------------------------------------------------------------------------


class TestWaitForOrderFillTimeout:
    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self):
        stream = CoinoneUserDataStream("access", "secret")
        result = await stream.wait_for_order_fill("missing", timeout=0.05)
        assert result is None
        # Waiter must be cleaned up.
        assert "missing" not in stream._fill_events

    @pytest.mark.asyncio
    async def test_non_fill_status_ignored(self):
        """wait/cancel statuses must not resolve the waiter."""
        stream = CoinoneUserDataStream("access", "secret")

        async def _fire() -> None:
            await asyncio.sleep(0.01)
            stream._dispatch_event(
                json.dumps(
                    {
                        "response_type": "DATA",
                        "channel": "MYORDER",
                        "data": {
                            "order_id": "oid-wait",
                            "status": "wait",
                        },
                    }
                )
            )

        task = asyncio.create_task(_fire())
        result = await stream.wait_for_order_fill("oid-wait", timeout=0.05)
        await task
        assert result is None

    @pytest.mark.asyncio
    async def test_non_myorder_channel_ignored(self):
        """MYASSET or other channels must not resolve MYORDER waiters."""
        stream = CoinoneUserDataStream("access", "secret")

        async def _fire() -> None:
            await asyncio.sleep(0.01)
            stream._dispatch_event(
                json.dumps(
                    {
                        "response_type": "DATA",
                        "channel": "MYASSET",
                        "data": {
                            "order_id": "oid-other",
                            "status": "trade",
                        },
                    }
                )
            )

        task = asyncio.create_task(_fire())
        result = await stream.wait_for_order_fill("oid-other", timeout=0.05)
        await task
        assert result is None


# ---------------------------------------------------------------------------
# Out-of-order buffering
# ---------------------------------------------------------------------------


class TestOutOfOrderBuffering:
    @pytest.mark.asyncio
    async def test_event_before_wait_is_buffered_and_returned(self):
        """Fill dispatched before caller awaits must still resolve."""
        stream = CoinoneUserDataStream("access", "secret")
        stream._dispatch_event(
            json.dumps(
                {
                    "response_type": "DATA",
                    "channel": "MYORDER",
                    "data": {
                        "order_id": "oid-buf",
                        "status": "trade_done",
                        "executed_qty": "2.0",
                        "executed_price": "2500",
                    },
                }
            )
        )
        assert "oid-buf" in stream._fill_buffer

        result = await stream.wait_for_order_fill("oid-buf", timeout=0.01)
        assert result is not None
        assert result["executed_qty"] == "2.0"
        assert "oid-buf" not in stream._fill_buffer

    @pytest.mark.asyncio
    async def test_stale_buffer_entries_are_pruned(self):
        """Buffered events older than _BUFFER_TTL_S are dropped on next dispatch."""
        from src.infra.exchange.ws_trade import coinone_user_data as mod

        stream = CoinoneUserDataStream("access", "secret")
        stale_ts = mod.time.monotonic() - (mod._BUFFER_TTL_S + 1.0)
        stream._fill_buffer["old"] = (stale_ts, {"order_id": "old", "status": "trade"})

        stream._dispatch_event(
            json.dumps(
                {
                    "response_type": "DATA",
                    "channel": "MYORDER",
                    "data": {
                        "order_id": "fresh",
                        "status": "trade_done",
                        "executed_qty": "1",
                        "executed_price": "10",
                    },
                }
            )
        )
        assert "old" not in stream._fill_buffer
        assert "fresh" in stream._fill_buffer


# ---------------------------------------------------------------------------
# Auth + lifecycle
# ---------------------------------------------------------------------------


class TestAuthHeaders:
    def test_build_auth_headers_produces_both_headers(self):
        """_build_auth_headers returns the two Coinone private WS headers."""
        stream = CoinoneUserDataStream("access-token-uuid", "secret-key-bytes")
        headers = stream._build_auth_headers()

        assert set(headers.keys()) == {"X-COINONE-PAYLOAD", "X-COINONE-SIGNATURE"}
        # PAYLOAD is base64(JSON(access_token + nonce + timestamp)) — must decode.
        import base64 as _b64
        decoded = _b64.b64decode(headers["X-COINONE-PAYLOAD"]).decode("utf-8")
        parsed = json.loads(decoded)
        assert parsed["access_token"] == "access-token-uuid"
        assert "nonce" in parsed
        assert "timestamp" in parsed
        # SIGNATURE is a lowercase HMAC-SHA512 hex digest (128 chars).
        sig = headers["X-COINONE-SIGNATURE"]
        assert len(sig) == 128
        assert all(c in "0123456789abcdef" for c in sig)

    def test_build_auth_headers_unique_nonce(self):
        """Successive header builds must use distinct nonces."""
        stream = CoinoneUserDataStream("access-token-uuid", "secret-key-bytes")
        import base64 as _b64

        h1 = stream._build_auth_headers()
        h2 = stream._build_auth_headers()
        nonce1 = json.loads(_b64.b64decode(h1["X-COINONE-PAYLOAD"]).decode())["nonce"]
        nonce2 = json.loads(_b64.b64decode(h2["X-COINONE-PAYLOAD"]).decode())["nonce"]
        assert nonce1 != nonce2


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_idempotent(self, monkeypatch):
        """Calling start() twice must not spawn duplicate listen tasks."""
        stream = CoinoneUserDataStream("access", "secret")

        async def _noop():
            while True:
                await asyncio.sleep(1)

        # Replace internal loops with no-ops so start() doesn't open a real WS.
        monkeypatch.setattr(stream, "_listen_loop", _noop)
        monkeypatch.setattr(stream, "_ping_loop", _noop)

        await stream.start()
        first_listen = stream._listen_task
        first_ping = stream._ping_task
        await stream.start()
        assert stream._listen_task is first_listen
        assert stream._ping_task is first_ping
        await stream.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_waiters_and_tasks(self, monkeypatch):
        """stop() cancels pending waiters and background tasks."""
        stream = CoinoneUserDataStream("access", "secret")

        async def _noop():
            while True:
                await asyncio.sleep(1)

        monkeypatch.setattr(stream, "_listen_loop", _noop)
        monkeypatch.setattr(stream, "_ping_loop", _noop)

        await stream.start()
        waiter = asyncio.get_event_loop().create_future()
        stream._fill_events["orphan"] = waiter

        await stream.stop()

        assert waiter.cancelled()
        assert stream._fill_events == {}
        assert stream._listen_task is None
        assert stream._ping_task is None


# ---------------------------------------------------------------------------
# SHORT → DEFAULT normalisation helper
# ---------------------------------------------------------------------------


class TestNormalizeShortFields:
    def test_short_fields_mapped_to_default(self):
        short = {
            "oi": "oid-x",
            "st": "trade_done",
            "eq": "0.1",
            "ep": "100",
            "qc": "KRW",
            "tc": "BTC",
        }
        out = _normalize_short_fields(short)
        assert out["order_id"] == "oid-x"
        assert out["status"] == "trade_done"
        assert out["executed_qty"] == "0.1"
        assert out["executed_price"] == "100"
        assert out["quote_currency"] == "KRW"
        assert out["target_currency"] == "BTC"

    def test_default_fields_pass_through(self):
        default = {
            "order_id": "oid-y",
            "status": "trade_done",
            "executed_qty": "0.2",
            "executed_price": "200",
        }
        out = _normalize_short_fields(default)
        # Default passes through unchanged (besides being a copy).
        assert out == default
        assert out is not default
