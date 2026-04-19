"""Binance Futures WebSocket order placement (ws-fapi).

BUG-120 Phase 1 — minimum viable WS trade client. Connection lifecycle +
signed request + response correlation via id. Does NOT replace REST yet;
runs alongside for fallback.

Endpoint: wss://ws-fapi.binance.com/ws-fapi/v1
Method:   order.place
Auth:     HMAC-SHA256(secret, paramsString)

Reference:
  https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-api/New-Order
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import orjson
import logging
import time
import uuid
from decimal import Decimal
from typing import Any, Optional

import websockets

logger = logging.getLogger(__name__)

_WS_URL = "wss://ws-fapi.binance.com/ws-fapi/v1"
_RESPONSE_TIMEOUT_S = 5.0


class BinanceWSTrade:
    """Minimal Binance Futures WS trading client.

    Usage:
        client = BinanceWSTrade(api_key, api_secret)
        await client.connect()
        trade = await client.place_order(symbol="BTCUSDT", side="BUY",
                                          order_type="MARKET", quantity=Decimal("0.001"))
        await client.close()
    """

    def __init__(self, api_key: str, api_secret: str) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._futures: dict[str, asyncio.Future] = {}
        self._listener_task: Optional[asyncio.Task] = None
        self._running = False

    async def connect(self) -> None:
        if self._ws is not None:
            return
        self._ws = await websockets.connect(_WS_URL, ping_interval=180, ping_timeout=60, compression=None)
        self._running = True
        self._listener_task = asyncio.create_task(self._listen())
        logger.info("BinanceWSTrade connected: %s", _WS_URL)

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

    async def _listen(self) -> None:
        """Receive loop — routes responses to pending futures by id."""
        try:
            async for raw in self._ws:
                try:
                    msg = orjson.loads(raw)
                    req_id = msg.get("id")
                    fut = self._futures.pop(req_id, None) if req_id else None
                    if fut and not fut.done():
                        fut.set_result(msg)
                except Exception as exc:
                    logger.warning("BinanceWSTrade listen parse err: %s", exc)
        except Exception as exc:
            logger.warning("BinanceWSTrade listener closed: %s", exc)
            self._running = False

    def _sign(self, params_str: str) -> str:
        return hmac.new(
            self._api_secret.encode(), params_str.encode(), hashlib.sha256
        ).hexdigest()

    async def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Decimal,
        price: Optional[Decimal] = None,
        time_in_force: str = "GTC",
    ) -> dict[str, Any]:
        """Send order.place via WS. Returns parsed response dict.

        Raises TimeoutError after _RESPONSE_TIMEOUT_S if no response.
        """
        # BUG-125: auto-reconnect if WS dropped
        if (
            self._ws is None
            or not self._running
            or (self._ws is not None and getattr(self._ws, "closed", False))
        ):
            logger.info("BinanceWSTrade reconnecting before place_order")
            try:
                await self.close()
            except Exception:
                pass
            self._ws = None
            await self.connect()
        req_id = str(uuid.uuid4())
        ts = int(time.time() * 1000)
        params: dict[str, Any] = {
            "apiKey": self._api_key,
            "symbol": symbol.upper(),
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": str(quantity),
            "timestamp": ts,
        }
        if order_type.upper() == "LIMIT":
            params["price"] = str(price)
            params["timeInForce"] = time_in_force
        # BUG-194: ask Binance to return the FILLED result in the ACK itself.
        # Binance docs (developers.binance.com/docs/derivatives/usds-margined-futures/
        # trade/rest-api/New-Order): "If newOrderRespType is sent as RESULT → MARKET
        # order: the final FILLED result of the order will be returned directly."
        # This removes the need for userData wait / REST polling on the happy path.
        if order_type.upper() == "MARKET":
            params["newOrderRespType"] = "RESULT"
        # Build signature string (sorted params, ampersand-separated, no signature)
        params_str = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        params["signature"] = self._sign(params_str)

        msg = {"id": req_id, "method": "order.place", "params": params}
        fut: asyncio.Future = asyncio.Future()
        self._futures[req_id] = fut
        await self._ws.send(orjson.dumps(msg))
        try:
            return await asyncio.wait_for(fut, timeout=_RESPONSE_TIMEOUT_S)
        except asyncio.TimeoutError:
            self._futures.pop(req_id, None)
            raise

    async def cancel_order(
        self,
        symbol: str,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Cancel order via WS (BUG-127). Returns parsed response dict.

        Either order_id (exchange orderId) or client_order_id must be provided.
        Raises RuntimeError on timeout.
        """
        # BUG-125 reconnect logic mirrored from place_order
        if (
            self._ws is None
            or not self._running
            or (self._ws is not None and getattr(self._ws, "closed", False))
        ):
            logger.info("BinanceWSTrade reconnecting before cancel_order")
            try:
                await self.close()
            except Exception:
                pass
            self._ws = None
            await self.connect()
        if not order_id and not client_order_id:
            raise ValueError("cancel_order requires order_id or client_order_id")
        req_id = str(uuid.uuid4())
        ts = int(time.time() * 1000)
        params: dict[str, Any] = {
            "apiKey": self._api_key,
            "symbol": symbol.upper(),
            "timestamp": ts,
        }
        if order_id:
            params["orderId"] = order_id
        else:
            params["origClientOrderId"] = client_order_id
        params_str = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        params["signature"] = self._sign(params_str)
        msg = {"id": req_id, "method": "order.cancel", "params": params}
        fut: asyncio.Future = asyncio.Future()
        self._futures[req_id] = fut
        await self._ws.send(orjson.dumps(msg))
        try:
            return await asyncio.wait_for(fut, timeout=_RESPONSE_TIMEOUT_S)
        except asyncio.TimeoutError:
            self._futures.pop(req_id, None)
            raise RuntimeError(
                f"BinanceWSTrade cancel timeout after {_RESPONSE_TIMEOUT_S}s "
                f"symbol={symbol} order_id={order_id}"
            )
