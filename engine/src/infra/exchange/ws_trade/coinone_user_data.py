"""Coinone private WebSocket userData stream (BUG-192).

Event-driven order fill confirmation via MYORDER stream, replacing the REST
polling path in native_coinone._rest_place_order.

Flow:
  1. Open WS ``wss://stream.coinone.co.kr/v1/private`` with HMAC-SHA512
     authentication headers (same scheme as Coinone REST private API).
  2. Send SUBSCRIBE request_type for the MYORDER channel (no topic filter →
     all symbols). Optionally SUBSCRIBE MYASSET for balance updates.
  3. Consume DATA messages on MYORDER; route fill payloads (status=``trade``
     or ``trade_done``) to awaiters registered via ``wait_for_order_fill``.
  4. Send ``{"request_type":"PING"}`` every 20 minutes to keep the session
     alive (server disconnects after 30 min idle).
  5. On disconnect/error, reconnect with exponential backoff and reuse the
     buffered late-arrival events (5 s TTL).

Reference:
  https://docs.coinone.co.kr/reference/private-websocket-1
  https://docs.coinone.co.kr/reference/private-websocket-1-myorder
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from typing import Any, Optional

import websockets

logger = logging.getLogger(__name__)

_WS_URL = "wss://stream.coinone.co.kr/v1/private"
_PING_INTERVAL_S = 20 * 60  # 20 minutes (server disconnects at 30 min idle)
_BUFFER_TTL_S = 5.0
_RECONNECT_BACKOFF_INITIAL_S = 1.0
_RECONNECT_BACKOFF_MAX_S = 30.0
_WS_PING_INTERVAL_S = 180
_WS_PING_TIMEOUT_S = 60

# MYORDER status values that indicate a terminal fill (see docs §"주문 진행 상태")
_FILL_STATUSES = frozenset({"trade", "trade_done"})


class CoinoneUserDataStream:
    """Coinone private WS stream for MYORDER fill confirmation.

    Usage::

        stream = CoinoneUserDataStream(access_key, secret_key)
        await stream.start()
        fill = await stream.wait_for_order_fill(order_id, timeout=0.5)
        if fill is not None:
            exec_qty = fill["executed_qty"]
            exec_px = fill["executed_price"]
        await stream.stop()
    """

    def __init__(self, access_key: str, secret_key: str) -> None:
        self._access_key = access_key
        # Coinone SDK example treats SECRET_KEY as raw bytes — encode once.
        self._secret_key = (
            secret_key.encode("utf-8") if isinstance(secret_key, str) else secret_key
        )
        self._ws: Any = None
        self._listen_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._running = False
        # order_id (str) → Future resolving to the MYORDER data dict.
        self._fill_events: dict[str, asyncio.Future[dict]] = {}
        # Buffer late-arriving fills keyed by order_id → (ts, payload).
        self._fill_buffer: dict[str, tuple[float, dict]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Launch listen + ping tasks. Returns immediately; connection is async."""
        if self._running:
            return
        self._running = True
        self._listen_task = asyncio.create_task(self._listen_loop())
        self._ping_task = asyncio.create_task(self._ping_loop())
        logger.info("coinone_user_data_stream_started")

    async def stop(self) -> None:
        """Cancel background tasks, close WS."""
        self._running = False
        for task in (self._listen_task, self._ping_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._listen_task = None
        self._ping_task = None
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
        self, order_id: str, timeout: float = 0.5
    ) -> Optional[dict]:
        """Wait up to ``timeout`` seconds for a MYORDER fill event.

        Returns the MYORDER ``data`` dict on fill (status in trade/trade_done),
        or ``None`` on timeout. If the matching event already arrived (buffered
        within TTL), returns immediately.
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
            self._fill_events.pop(key, None)
            logger.debug(
                "coinone_user_data_wait_timeout order_id=%s timeout=%.3f",
                key,
                timeout,
            )
            return None
        except asyncio.CancelledError:
            self._fill_events.pop(key, None)
            raise

    # ------------------------------------------------------------------
    # Internal: auth helpers
    # ------------------------------------------------------------------

    def _build_auth_headers(self) -> dict[str, str]:
        """Build X-COINONE-PAYLOAD + X-COINONE-SIGNATURE per Coinone private WS spec."""
        payload = {
            "access_token": self._access_key,
            "nonce": str(uuid.uuid4()),
            "timestamp": int(time.time() * 1000),
        }
        payload_json = json.dumps(payload)
        encoded_payload = base64.b64encode(payload_json.encode("utf-8")).decode("utf-8")
        signature = hmac.new(
            self._secret_key,
            encoded_payload.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()
        return {
            "X-COINONE-PAYLOAD": encoded_payload,
            "X-COINONE-SIGNATURE": signature,
        }

    # ------------------------------------------------------------------
    # Internal: background loops
    # ------------------------------------------------------------------

    async def _listen_loop(self) -> None:
        """Connect WS, subscribe MYORDER, dispatch events. Auto-reconnects."""
        backoff = _RECONNECT_BACKOFF_INITIAL_S
        while self._running:
            try:
                headers = self._build_auth_headers()
                async with websockets.connect(
                    _WS_URL,
                    additional_headers=headers,
                    ping_interval=_WS_PING_INTERVAL_S,
                    ping_timeout=_WS_PING_TIMEOUT_S,
                    compression=None,
                ) as ws:
                    self._ws = ws
                    backoff = _RECONNECT_BACKOFF_INITIAL_S
                    # Subscribe MYORDER (no topic filter → all symbols).
                    await ws.send(
                        json.dumps(
                            {
                                "request_type": "SUBSCRIBE",
                                "channel": "MYORDER",
                            }
                        )
                    )
                    logger.info("coinone_user_data_subscribed channel=MYORDER")
                    async for raw in ws:
                        try:
                            self._dispatch_event(raw)
                        except Exception as exc:
                            logger.warning(
                                "coinone_user_data_parse_err err=%s", exc
                            )
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning(
                    "coinone_user_data_ws_closed err=%s backoff=%.1fs",
                    exc,
                    backoff,
                )
                self._ws = None
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    return
                backoff = min(backoff * 2, _RECONNECT_BACKOFF_MAX_S)

    async def _ping_loop(self) -> None:
        """Send application-level PING every _PING_INTERVAL_S to keep session alive."""
        while self._running:
            try:
                await asyncio.sleep(_PING_INTERVAL_S)
            except asyncio.CancelledError:
                return
            if not self._running:
                return
            if self._ws is None:
                continue
            try:
                await self._ws.send(json.dumps({"request_type": "PING"}))
                logger.debug("coinone_user_data_ping_sent")
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("coinone_user_data_ping_failed err=%s", exc)

    # ------------------------------------------------------------------
    # Internal: dispatch
    # ------------------------------------------------------------------

    def _dispatch_event(self, raw: Any) -> None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        if not raw:
            return
        msg = json.loads(raw)
        if not isinstance(msg, dict):
            return
        # Response envelope uses either DEFAULT ("response_type"/"channel"/"data")
        # or SHORT ("r"/"c"/"d"). We only subscribe DEFAULT, but handle both.
        response_type = msg.get("response_type") or msg.get("r")
        channel = msg.get("channel") or msg.get("c")
        if response_type != "DATA" or channel != "MYORDER":
            return
        data = msg.get("data") if "data" in msg else msg.get("d")
        if not isinstance(data, dict):
            return
        # Normalize SHORT → DEFAULT field names so callers see a consistent dict.
        normalized = _normalize_short_fields(data)
        status = normalized.get("status")
        if status not in _FILL_STATUSES:
            return
        order_id = normalized.get("order_id")
        if order_id is None:
            return
        key = str(order_id)
        logger.debug(
            "coinone_user_data_fill_received order_id=%s qty=%s px=%s status=%s",
            key,
            normalized.get("executed_qty"),
            normalized.get("executed_price"),
            status,
        )
        fut = self._fill_events.pop(key, None)
        if fut is not None and not fut.done():
            fut.set_result(normalized)
            return
        # No awaiter yet — buffer briefly for out-of-order arrival.
        self._prune_buffer()
        self._fill_buffer[key] = (time.monotonic(), normalized)

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


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

_SHORT_TO_DEFAULT = {
    "qc": "quote_currency",
    "tc": "target_currency",
    "oi": "order_id",
    "t": "type",
    "st": "status",
    "s": "side",
    "op": "order_price",
    "oq": "order_qty",
    "oa": "order_amount",
    "ti": "trade_id",
    "im": "is_maker",
    "ep": "executed_price",
    "eq": "executed_qty",
    "ef": "executed_fee",
    "rq": "remain_qty",
    "ra": "remain_amount",
    "ui": "user_order_id",
    "pq": "prevented_qty",
    "et": "executed_timestamp",
    "ot": "order_timestamp",
    "ts": "timestamp",
}


def _normalize_short_fields(data: dict) -> dict:
    """Map SHORT-format field codes to DEFAULT names; pass-through if already DEFAULT."""
    if "order_id" in data or "status" in data:
        return dict(data)
    normalized = dict(data)
    for short, default in _SHORT_TO_DEFAULT.items():
        if short in data and default not in normalized:
            normalized[default] = data[short]
    return normalized
