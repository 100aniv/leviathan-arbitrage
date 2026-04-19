"""Bithumb private userData WebSocket stream (BUG-191).

Event-driven order fill confirmation via ``myOrder`` events on Bithumb v2's
new private WebSocket channel, replacing the REST polling path for order
status. Pairs with ``BinanceUserDataStream`` (BUG-189) and
``UpbitUserDataStream`` (BUG-190).

Flow:
  1. Build a JWT (HS256, access_key + nonce + timestamp_ms) per connection.
  2. Open WS ``wss://ws-api.bithumb.com/websocket/v1/private`` with the JWT
     passed via the ``authorization`` header (Bithumb v2 spec).
  3. Subscribe with ``[{ticket}, {type:"myOrder", codes:[...]}]`` — a batch
     request identical in shape to Upbit's private WS.
  4. Consume ``myOrder`` messages; route events with ``state in {done, trade}``
     to awaiters registered via ``wait_for_order_fill(order_uuid)``.
  5. Emit a PING frame every ~120 s to satisfy keepalive requirements.
  6. On disconnect/error, reconnect with exponential backoff and reuse the
     buffered late-arrival events (5 s TTL).

Verified reference URLs (2026-04 confirmation via exa.ai):
  - https://apidocs.bithumb.com/changelog/...private-websocket-오픈-myorder-myasset-지원-안내
  - https://apidocs.bithumb.com/v2.1.5/reference/내-주문-및-체결-myorder
  - https://apidocs.bithumb.com/v2.1.5/reference/내-자산-myasset
  - CCXT pro bithumb.ts (PR #27138, merged 2025-10-27):
    urls.api.ws.privateV2 = "wss://ws-api.bithumb.com/websocket/v1/private"
"""
from __future__ import annotations

import asyncio
import orjson
import time
import uuid
from typing import Any, Optional

import jwt
import structlog
import websockets

from src.infra.exchange.ws_trade._socket_opts import set_tcp_nodelay

logger = structlog.get_logger(__name__)

_WS_PRIVATE_URL = "wss://ws-api.bithumb.com/websocket/v1/private"
_BUFFER_TTL_S = 5.0  # out-of-order event grace window
_RECONNECT_BACKOFF_INITIAL_S = 1.0
_RECONNECT_BACKOFF_MAX_S = 30.0
_WS_PING_INTERVAL_S = 120  # match Upbit cadence; Bithumb accepts standard WS ping
_WS_PING_TIMEOUT_S = 30
# Bithumb state values: wait | trade | done | cancel.
# "done" = fully filled, "trade" = execution reported (may be partial on route
# to done). Both indicate a fill has occurred for the order.
_FILL_STATES: frozenset[str] = frozenset({"done", "trade"})


class BithumbUserDataStream:
    """Bithumb v2 private userData WebSocket stream for event-driven fills.

    Usage:
        stream = BithumbUserDataStream(access_key, secret_key)
        await stream.start()
        fill = await stream.wait_for_order_fill("C0101000...", timeout=0.3)
        if fill is not None:
            volume = fill["executed_volume"]
            price = fill["price"]
        await stream.stop()
    """

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        codes: Optional[list[str]] = None,
    ) -> None:
        self._access_key = access_key
        self._secret_key = secret_key
        # Optional subset of KRW markets (e.g. ["KRW-BTC"]). None → all.
        self._codes = list(codes) if codes else None
        self._ws: Any = None
        self._listen_task: Optional[asyncio.Task] = None
        self._running = False
        # order_uuid (str) → Future resolving to the myOrder payload dict.
        self._fill_events: dict[str, asyncio.Future[dict]] = {}
        # Buffer late-arriving fills keyed by order_uuid → (ts, payload).
        # Used when a fill event arrives before wait_for_order_fill() is called.
        self._fill_buffer: dict[str, tuple[float, dict]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Open WS + subscribe; launch listen loop."""
        if self._running:
            return
        self._running = True
        self._listen_task = asyncio.create_task(self._listen_loop())
        logger.info("bithumb_user_data_stream_starting")

    async def stop(self) -> None:
        """Cancel background task, close WS."""
        self._running = False
        if self._listen_task is not None:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except (asyncio.CancelledError, Exception):
                pass
            self._listen_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        # Fail any pending waiters so callers unblock on shutdown.
        for fut in list(self._fill_events.values()):
            if not fut.done():
                fut.cancel()
        self._fill_events.clear()
        self._fill_buffer.clear()

    # ------------------------------------------------------------------
    # Public: fill await API
    # ------------------------------------------------------------------

    async def wait_for_order_fill(
        self, order_uuid: str, timeout: float = 0.5
    ) -> Optional[dict]:
        """Wait up to ``timeout`` seconds for a fill myOrder event.

        Returns the payload dict when a ``state in {done, trade}`` event is
        observed, or ``None`` on timeout. If the matching event already
        arrived (buffered within TTL), returns immediately.
        """
        key = str(order_uuid)
        # Fast path: already-buffered event
        buffered = self._fill_buffer.pop(key, None)
        if buffered is not None:
            ts, payload = buffered
            if (time.monotonic() - ts) <= _BUFFER_TTL_S:
                return payload
        # Register waiter
        fut = self._fill_events.get(key)
        if fut is None or fut.done():
            fut = asyncio.get_event_loop().create_future()
            self._fill_events[key] = fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            # Drop the awaiter so a stale future doesn't linger.
            self._fill_events.pop(key, None)
            logger.debug(
                "bithumb_user_data_wait_timeout",
                order_uuid=key,
                timeout=timeout,
            )
            return None
        except asyncio.CancelledError:
            self._fill_events.pop(key, None)
            raise

    # ------------------------------------------------------------------
    # Internal: JWT
    # ------------------------------------------------------------------

    def _build_jwt(self) -> str:
        """Build a HS256 JWT for Bithumb v2 private WS auth.

        Bithumb v2 private WS requires the full v2 JWT payload
        (access_key + nonce + timestamp_ms). No query_hash is needed for the
        WS handshake itself. Confirmed via CCXT PR #27138 (merged 2025-10).
        """
        payload = {
            "access_key": self._access_key,
            "nonce": str(uuid.uuid4()),
            "timestamp": round(time.time() * 1000),
        }
        token = jwt.encode(payload, self._secret_key, algorithm="HS256")
        # PyJWT < 2 returns bytes, >= 2 returns str — normalise to str.
        if isinstance(token, bytes):
            token = token.decode("utf-8")
        return token

    def _build_subscribe_message(self) -> list[dict]:
        """Bithumb subscribe frame for myOrder events.

        Format: [{ticket}, {type:"myOrder", codes:[...]}] — identical shape
        to Upbit's private WS, per CCXT bithumb.ts ``watchOrders``.
        """
        frame: dict[str, Any] = {"type": "myOrder"}
        if self._codes:
            frame["codes"] = list(self._codes)
        return [{"ticket": str(uuid.uuid4())}, frame]

    # ------------------------------------------------------------------
    # Internal: background loop
    # ------------------------------------------------------------------

    async def _listen_loop(self) -> None:
        """Connect WS and dispatch myOrder events. Auto-reconnects."""
        backoff = _RECONNECT_BACKOFF_INITIAL_S
        while self._running:
            try:
                token = self._build_jwt()
                headers = [("authorization", f"Bearer {token}")]
                async with websockets.connect(
                    _WS_PRIVATE_URL,
                    additional_headers=headers,
                    ping_interval=_WS_PING_INTERVAL_S,
                    ping_timeout=_WS_PING_TIMEOUT_S,
                    compression=None,
                ) as ws:
                    self._ws = ws
                    set_tcp_nodelay(ws)  # BUG-196: disable Nagle
                    backoff = _RECONNECT_BACKOFF_INITIAL_S
                    # Send subscribe frame.
                    await ws.send(orjson.dumps(self._build_subscribe_message()))
                    logger.info("bithumb_user_data_ws_connected")
                    async for raw in ws:
                        try:
                            self._dispatch_event(raw)
                        except Exception as exc:
                            logger.warning(
                                "bithumb_user_data_parse_err",
                                err=str(exc),
                            )
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning(
                    "bithumb_user_data_ws_closed",
                    err=str(exc),
                    backoff=backoff,
                )
                self._ws = None
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    return
                backoff = min(backoff * 2, _RECONNECT_BACKOFF_MAX_S)

    # ------------------------------------------------------------------
    # Internal: dispatch
    # ------------------------------------------------------------------

    def _dispatch_event(self, raw: Any) -> None:
        if not raw:
            return
        msg = orjson.loads(raw)
        if not isinstance(msg, dict):
            return
        event_type = msg.get("type")
        if event_type != "myOrder":
            return
        state = msg.get("state")
        if state not in _FILL_STATES:
            return
        order_uuid = msg.get("uuid")
        if not order_uuid:
            return
        key = str(order_uuid)
        logger.debug(
            "bithumb_user_data_fill_received",
            order_uuid=key,
            state=state,
            executed_volume=msg.get("executed_volume"),
            price=msg.get("price"),
        )
        fut = self._fill_events.pop(key, None)
        if fut is not None and not fut.done():
            fut.set_result(msg)
            return
        # No awaiter yet — buffer briefly for out-of-order arrival.
        self._prune_buffer()
        self._fill_buffer[key] = (time.monotonic(), msg)

    def _prune_buffer(self) -> None:
        if not self._fill_buffer:
            return
        now = time.monotonic()
        stale = [
            k for k, (ts, _) in self._fill_buffer.items()
            if (now - ts) > _BUFFER_TTL_S
        ]
        for k in stale:
            self._fill_buffer.pop(k, None)
