"""Bitget V2 WebSocket trading client (BUG-120 Phase 2).

Endpoint: wss://ws.bitget.com/v2/ws/private
Login:    op=login, args=[{apiKey, passphrase, timestamp, sign}]
Order:    op=trade, args=[{id, instType, instId, channel=place-order, params}]
Sign:     base64(HMAC-SHA256(secret, timestamp + 'GET' + '/user/verify'))

Reference:
  https://www.bitget.com/api-doc/spot/websocket/private/Place-Order-Channel
  Note: "contact BD/RM to apply" — may require market-maker access.

Expected latency: REST 1000ms → WS 200-300ms (-75%).
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
from decimal import Decimal
from typing import Any, Optional

import websockets

logger = logging.getLogger(__name__)

_WS_URL = "wss://ws.bitget.com/v2/ws/private"
_RESPONSE_TIMEOUT_S = 5.0
_LOGIN_TIMEOUT_S = 10.0


class BitgetWSTrade:
    """Minimal Bitget V2 WS trading client.

    Usage:
        client = BitgetWSTrade(api_key, api_secret, passphrase)
        await client.connect()  # also logs in
        resp = await client.place_order(
            instType="USDT-FUTURES", instId="BTCUSDT",
            order_type="market", side="buy", size=Decimal("0.001"),
        )
        await client.close()
    """

    def __init__(self, api_key: str, api_secret: str, passphrase: str) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._passphrase = passphrase
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._futures: dict[str, asyncio.Future] = {}
        self._login_future: Optional[asyncio.Future] = None
        self._listener_task: Optional[asyncio.Task] = None
        self._running = False
        self._logged_in = False

    async def connect(self) -> None:
        if self._ws is not None and self._logged_in:
            return
        self._ws = await websockets.connect(_WS_URL, ping_interval=20, ping_timeout=10)
        self._running = True
        self._listener_task = asyncio.create_task(self._listen())
        await self._login()
        logger.info("BitgetWSTrade connected + authenticated")

    async def _login(self) -> None:
        ts = str(int(time.time()))
        sign_str = ts + "GET" + "/user/verify"
        mac = hmac.new(self._api_secret.encode(), sign_str.encode(), hashlib.sha256).digest()
        sign = base64.b64encode(mac).decode()
        msg = {
            "op": "login",
            "args": [{
                "apiKey": self._api_key,
                "passphrase": self._passphrase,
                "timestamp": ts,
                "sign": sign,
            }],
        }
        self._login_future = asyncio.Future()
        await self._ws.send(json.dumps(msg))
        try:
            resp = await asyncio.wait_for(self._login_future, timeout=_LOGIN_TIMEOUT_S)
            if resp.get("code") == "0" or resp.get("event") == "login":
                self._logged_in = True
            else:
                raise RuntimeError(f"Bitget WS login failed: {resp}")
        except asyncio.TimeoutError:
            raise RuntimeError("Bitget WS login timeout")

    async def close(self) -> None:
        self._running = False
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._logged_in = False

    async def _listen(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                    # Login response has event=login (no id)
                    if msg.get("event") == "login" and self._login_future and not self._login_future.done():
                        self._login_future.set_result(msg)
                        continue
                    # Trade responses have arg[0].id
                    args = msg.get("arg") or []
                    req_id = args[0].get("id") if args else None
                    if req_id:
                        fut = self._futures.pop(req_id, None)
                        if fut and not fut.done():
                            fut.set_result(msg)
                except Exception as exc:
                    logger.warning("BitgetWSTrade listen err: %s", exc)
        except Exception as exc:
            logger.warning("BitgetWSTrade listener closed: %s", exc)
            self._running = False
            self._logged_in = False

    async def place_order(
        self,
        inst_type: str,
        inst_id: str,
        order_type: str,
        side: str,
        size: Decimal,
        price: Optional[Decimal] = None,
        force: str = "gtc",
        client_oid: Optional[str] = None,
        marginMode: Optional[str] = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """Send order via WS. Returns parsed response dict (event=trade or error)."""
        # BUG-125: auto-reconnect if WS dropped (listener exited or ws closed)
        if (
            not self._logged_in
            or self._ws is None
            or (self._ws is not None and getattr(self._ws, "closed", False))
            or not self._running
        ):
            logger.info("BitgetWSTrade reconnecting before place_order")
            try:
                await self.close()
            except Exception:
                pass
            self._ws = None
            self._logged_in = False
            await self.connect()
        req_id = str(uuid.uuid4())
        params: dict[str, Any] = {
            "orderType": order_type.lower(),
            "side": side.lower(),
            "size": str(size),
            "force": force,
        }
        if order_type.lower() == "limit":
            params["price"] = str(price)
        if client_oid:
            params["clientOid"] = client_oid
        if marginMode:
            params["marginMode"] = marginMode
        for k, v in extra.items():
            if v is not None:
                params[k] = v

        msg = {
            "op": "trade",
            "args": [{
                "id": req_id,
                "instType": inst_type,
                "instId": inst_id,
                "channel": "place-order",
                "params": params,
            }],
        }
        fut: asyncio.Future = asyncio.Future()
        self._futures[req_id] = fut
        await self._ws.send(json.dumps(msg))
        try:
            return await asyncio.wait_for(fut, timeout=_RESPONSE_TIMEOUT_S)
        except asyncio.TimeoutError:
            self._futures.pop(req_id, None)
            raise
