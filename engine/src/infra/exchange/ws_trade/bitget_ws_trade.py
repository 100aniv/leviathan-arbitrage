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

_WS_URL = "wss://ws.bitget.com/v3/ws/private"
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
            # BUG-136: log full response for diagnostics
            logger.info("BitgetWSTrade login resp: %s", resp)
            # Bitget V2 success: {"event":"login","code":0,"msg":"success"}
            # code can be int 0 or string "0". event="login" alone is not enough
            # (error responses may also carry event="login" with non-zero code).
            _code = resp.get("code")
            _code_ok = _code == 0 or _code == "0"
            _event_ok = resp.get("event") == "login"
            if _code_ok and _event_ok:
                self._logged_in = True
            else:
                raise RuntimeError(
                    f"Bitget WS login rejected: code={_code} event={resp.get('event')} "
                    f"msg={resp.get('msg')} full={resp}"
                )
        except asyncio.TimeoutError:
            raise RuntimeError(f"Bitget WS login timeout after {_LOGIN_TIMEOUT_S}s")

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
                    # BUG-136: log every inbound message at DEBUG (trade responses are rare)
                    logger.debug("BitgetWSTrade inbound: %s", raw[:500] if isinstance(raw, str) else raw)
                    # Login response has event=login (no id)
                    if msg.get("event") == "login" and self._login_future and not self._login_future.done():
                        self._login_future.set_result(msg)
                        continue
                    # Error responses: {"event":"error","code":...,"msg":...} — warn + route to pending futures
                    if msg.get("event") == "error":
                        logger.warning("BitgetWSTrade error event: %s", msg)
                        # Still try to route by id so waiting place_order raises quickly
                    # Trade responses: Classic has arg[0].id, UTA has top-level id
                    req_id = msg.get("id")  # BUG-162: UTA 응답
                    if not req_id:
                        args = msg.get("arg") or []
                        req_id = args[0].get("id") if args else None
                    if req_id:
                        fut = self._futures.pop(req_id, None)
                        if fut and not fut.done():
                            fut.set_result(msg)
                    elif msg.get("event") == "error" and self._futures:
                        # error without id: route to all pending (best-effort)
                        for _rid in list(self._futures.keys()):
                            _f = self._futures.pop(_rid, None)
                            if _f and not _f.done():
                                _f.set_result(msg)
                                break  # only first pending
                except Exception as exc:
                    logger.warning("BitgetWSTrade listen err: %s raw=%r", exc, raw[:200] if isinstance(raw, str) else raw)
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
        account_mode: str = "classic",  # BUG-162: "classic" | "unified" (UTA V3)
        pos_side: Optional[str] = None,  # BUG-169: hedge mode posSide ("long" | "short")
        **extra: Any,
    ) -> dict[str, Any]:
        """Send order via WS. Returns parsed response dict (event=trade or error).

        BUG-162: account_mode="unified" 일 때 UTA V3 payload 사용:
          - instType → category
          - channel → topic
          - size → qty
          - force → timeInForce
        """
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
        # BUG-162: UTA V3 payload uses different field names
        if account_mode == "unified":
            # UTA V3 WS: category, topic, qty, timeInForce, symbol
            # BUG-169: V3 WS spec uses lowercase with hyphen for category
            if inst_type == "USDT-FUTURES":
                _category = "usdt-futures"
            elif inst_type == "COIN-FUTURES":
                _category = "coin-futures"
            elif inst_type == "USDC-FUTURES":
                _category = "usdc-futures"
            else:
                _category = "spot"
            args_inner = {
                "symbol": inst_id,
                "orderType": order_type.lower(),
                "side": side.lower(),
                "qty": str(size),  # ← size 대신 qty
                "timeInForce": force,  # ← force 대신 timeInForce
            }
            if order_type.lower() == "limit":
                args_inner["price"] = str(price)
            if client_oid:
                args_inner["clientOid"] = client_oid
            # BUG-169: hedge mode posSide ("long" | "short")
            # BUG-174: one_way mode requires explicit reduceOnly field
            if pos_side:
                args_inner["posSide"] = pos_side  # hedge mode
            else:
                # one_way mode: "no" = open position, "yes" = close position
                _reduce_only = extra.get("reduceOnly", False)
                args_inner["reduceOnly"] = "yes" if _reduce_only else "no"
            # UTA 는 marginMode/marginCoin 불필요 (자동 관리)
            for k, v in extra.items():
                if v is not None and k not in ("marginMode", "marginCoin"):
                    args_inner[k] = v
            msg = {
                "op": "trade",
                "id": req_id,
                "category": _category,  # ← instType 대체
                "topic": "place-order",  # ← channel 대체
                "args": [args_inner],
            }
        else:
            # Classic V2 (기존)
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
        # BUG-136: log request body for diagnostics (redact nothing — internal log)
        logger.info("BitgetWSTrade place_order req: %s", json.dumps(msg))
        await self._ws.send(json.dumps(msg))
        try:
            resp = await asyncio.wait_for(fut, timeout=_RESPONSE_TIMEOUT_S)
            logger.info("BitgetWSTrade place_order resp: %s", resp)
            return resp
        except asyncio.TimeoutError:
            self._futures.pop(req_id, None)
            raise RuntimeError(
                f"BitgetWSTrade timeout after {_RESPONSE_TIMEOUT_S}s "
                f"inst={inst_type}/{inst_id} side={side} size={size} req_id={req_id}"
            )
