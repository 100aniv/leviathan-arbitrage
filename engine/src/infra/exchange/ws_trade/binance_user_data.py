"""Binance Futures userData WebSocket stream (BUG-189).

Event-driven order fill confirmation via ``ORDER_TRADE_UPDATE`` events,
replacing the REST polling path (BUG-166/188) in native_binance._ws_place_order.

Flow:
  1. POST /fapi/v1/listenKey (signed via adapter) → listen_key (60-minute TTL).
  2. Open WS ``wss://fstream.binance.com/ws/{listen_key}``.
  3. Consume ``ORDER_TRADE_UPDATE`` messages; route fill payloads to
     awaiters registered via ``wait_for_order_fill(order_id)``.
  4. PUT /fapi/v1/listenKey every 30 minutes to keep the key alive.
  5. On disconnect/error, reconnect with exponential backoff and reuse the
     buffered late-arrival events (5 s TTL).

Reference:
  https://developers.binance.com/docs/derivatives/usds-margined-futures/user-data-streams
"""
from __future__ import annotations

import asyncio
import orjson
import logging
import time
from typing import Any, Awaitable, Callable, Optional

import websockets

from src.infra.exchange.ws_trade._socket_opts import set_tcp_nodelay

logger = logging.getLogger(__name__)

_LISTEN_KEY_PATH = "/fapi/v1/listenKey"
_WS_BASE = "wss://fstream.binance.com/ws"
_KEEPALIVE_INTERVAL_S = 30 * 60  # 30 minutes (Binance spec)
_BUFFER_TTL_S = 5.0  # out-of-order event grace window
_RECONNECT_BACKOFF_INITIAL_S = 1.0
_RECONNECT_BACKOFF_MAX_S = 30.0
_WS_PING_INTERVAL_S = 180
_WS_PING_TIMEOUT_S = 60


class BinanceUserDataStream:
    """Binance Futures userData WebSocket stream for event-driven fills.

    Usage:
        stream = BinanceUserDataStream(api_key, adapter._signed_request)
        await stream.start()
        fill = await stream.wait_for_order_fill("12345", timeout=0.3)
        if fill is not None:
            exec_qty = fill["z"]
            avg_px = fill["ap"]
        await stream.stop()
    """

    def __init__(
        self,
        api_key: str,
        signed_request_fn: Callable[..., Awaitable[Any]],
    ) -> None:
        self._api_key = api_key
        # signed_request_fn(method, path, params=None, data=None) — adapter's _signed_request.
        self._signed_request = signed_request_fn
        self._listen_key: Optional[str] = None
        self._ws: Any = None
        self._listen_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        self._running = False
        # order_id (str) → Future resolving to the "o" dict from ORDER_TRADE_UPDATE
        self._fill_events: dict[str, asyncio.Future[dict]] = {}
        # Buffer late-arriving fills keyed by order_id → (ts, payload).
        # Used when a fill event arrives before wait_for_order_fill() is called.
        self._fill_buffer: dict[str, tuple[float, dict]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Fetch listen_key, open WS, launch listen + keepalive tasks."""
        if self._running:
            return
        self._listen_key = await self._create_listen_key()
        logger.info(
            "binance_user_data_listen_key_obtained key_prefix=%s",
            self._listen_key[:8] if self._listen_key else "<none>",
        )
        self._running = True
        self._listen_task = asyncio.create_task(self._listen_loop())
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def stop(self) -> None:
        """Cancel background tasks, close WS, DELETE listen_key."""
        self._running = False
        for task in (self._listen_task, self._keepalive_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._listen_task = None
        self._keepalive_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._listen_key:
            try:
                await self._signed_request("DELETE", _LISTEN_KEY_PATH)
            except Exception as exc:
                logger.debug("binance_user_data_listen_key_delete_failed err=%s", exc)
        self._listen_key = None
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
        self, order_id: str, timeout: float = 0.5
    ) -> Optional[dict]:
        """Wait up to ``timeout`` seconds for FILLED ORDER_TRADE_UPDATE.

        Returns the ``o`` payload dict on fill, or ``None`` on timeout. If the
        matching event already arrived (buffered within TTL), returns
        immediately.
        """
        key = str(order_id)
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
                "binance_user_data_wait_timeout order_id=%s timeout=%.3f",
                key,
                timeout,
            )
            return None
        except asyncio.CancelledError:
            self._fill_events.pop(key, None)
            raise

    # ------------------------------------------------------------------
    # Internal: listen_key REST helpers
    # ------------------------------------------------------------------

    async def _create_listen_key(self) -> str:
        resp = await self._signed_request("POST", _LISTEN_KEY_PATH)
        key = resp.get("listenKey") if isinstance(resp, dict) else None
        if not key:
            raise RuntimeError(
                f"binance_user_data: listenKey missing from response {resp!r}"
            )
        return key

    async def _keepalive_once(self) -> None:
        await self._signed_request("PUT", _LISTEN_KEY_PATH)

    # ------------------------------------------------------------------
    # Internal: background loops
    # ------------------------------------------------------------------

    async def _listen_loop(self) -> None:
        """Connect WS and dispatch ORDER_TRADE_UPDATE events. Auto-reconnects."""
        backoff = _RECONNECT_BACKOFF_INITIAL_S
        while self._running:
            try:
                if not self._listen_key:
                    self._listen_key = await self._create_listen_key()
                url = f"{_WS_BASE}/{self._listen_key}"
                async with websockets.connect(
                    url,
                    ping_interval=_WS_PING_INTERVAL_S,
                    ping_timeout=_WS_PING_TIMEOUT_S,
                    compression=None,
                ) as ws:
                    self._ws = ws
                    set_tcp_nodelay(ws)  # BUG-196: disable Nagle
                    backoff = _RECONNECT_BACKOFF_INITIAL_S
                    async for raw in ws:
                        try:
                            self._dispatch_event(raw)
                        except Exception as exc:
                            logger.warning(
                                "binance_user_data_parse_err err=%s", exc
                            )
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning(
                    "binance_user_data_ws_closed err=%s backoff=%.1fs",
                    exc,
                    backoff,
                )
                self._ws = None
                # Force listen_key refresh on reconnect — may have expired.
                self._listen_key = None
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    return
                backoff = min(backoff * 2, _RECONNECT_BACKOFF_MAX_S)

    async def _keepalive_loop(self) -> None:
        """PUT /fapi/v1/listenKey every _KEEPALIVE_INTERVAL_S seconds."""
        while self._running:
            try:
                await asyncio.sleep(_KEEPALIVE_INTERVAL_S)
            except asyncio.CancelledError:
                return
            if not self._running:
                return
            try:
                await self._keepalive_once()
                logger.debug("binance_user_data_keepalive_ok")
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("binance_user_data_keepalive_failed err=%s", exc)

    # ------------------------------------------------------------------
    # Internal: dispatch
    # ------------------------------------------------------------------

    def _dispatch_event(self, raw: Any) -> None:
        if not raw:
            return
        msg = orjson.loads(raw)
        if not isinstance(msg, dict):
            return
        event_type = msg.get("e")
        if event_type != "ORDER_TRADE_UPDATE":
            return
        order = msg.get("o") or {}
        if not isinstance(order, dict):
            return
        status = order.get("X")
        if status != "FILLED":
            return
        order_id = order.get("i")
        if order_id is None:
            return
        key = str(order_id)
        logger.debug(
            "binance_user_data_fill_received order_id=%s qty=%s avg_px=%s",
            key,
            order.get("z"),
            order.get("ap"),
        )
        fut = self._fill_events.pop(key, None)
        if fut is not None and not fut.done():
            fut.set_result(order)
            return
        # No awaiter yet — buffer briefly for out-of-order arrival.
        self._prune_buffer()
        self._fill_buffer[key] = (time.monotonic(), order)

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
