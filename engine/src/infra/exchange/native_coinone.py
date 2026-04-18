"""Native Coinone adapter — Korean KRW exchange via direct REST + WebSocket."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from decimal import Decimal
from typing import Any

from src.core.models import Balance, FeeRate, Order, OrderBook, OrderSide, Position, Trade
from src.infra.exchange.native_adapter import NativeAdapter
from src.infra.exchange.rate_limiter import RateLimitConfig

logger = logging.getLogger(__name__)

_COINONE_RATE_LIMITS: dict[str, RateLimitConfig] = {
    "default": RateLimitConfig(requests_per_second=10, burst=20),
    "order": RateLimitConfig(requests_per_second=10, burst=20),
}

_REST_BASE = "https://api.coinone.co.kr"
_WS_PUBLIC = "wss://stream.coinone.co.kr"


def _normalize_symbol(symbol: str) -> str:
    """'BTC/KRW' -> 'btc' (Coinone uses lowercase currency, quote is always KRW)."""
    if "/" in symbol:
        base, _ = symbol.split("/", 1)
        return base.lower()
    return symbol.lower()


class NativeCoinoneAdapter(NativeAdapter):
    """Native Coinone spot adapter — direct HTTP/WebSocket.

    Coinone uses HMAC-SHA512 authentication with base64-encoded payload.
    All pairs are KRW-denominated (e.g., BTC/KRW).
    Auth: payload = base64(json({"access_token": token, "nonce": ms_timestamp}))
          signature = HMAC-SHA512(payload, secret_key).hexdigest().upper()
    """

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rate_limits", _COINONE_RATE_LIMITS)
        super().__init__(exchange_id="coinone", **kwargs)

    # ------------------------------------------------------------------
    # Abstract implementations
    # ------------------------------------------------------------------

    def _rest_base_url(self) -> str:
        return _REST_BASE

    def _default_headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def _auth_headers(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        data: dict[str, Any] | None,
    ) -> dict[str, str]:
        nonce = int(time.time() * 1000)
        payload_data: dict[str, Any] = {
            "access_token": self._api_key,
            "nonce": nonce,
        }
        # Merge any extra body fields into the payload
        if data:
            payload_data.update(data)

        payload_json = json.dumps(payload_data, separators=(",", ":"))
        payload_b64 = base64.b64encode(payload_json.encode()).decode()
        signature = hmac.new(
            self._api_secret.encode(),
            payload_b64.encode(),
            hashlib.sha512,
        ).hexdigest().upper()

        return {
            "X-COINONE-PAYLOAD": payload_b64,
            "X-COINONE-SIGNATURE": signature,
        }

    def _ws_orderbook_url(self, symbol: str) -> str:
        return _WS_PUBLIC

    def _ws_subscribe_message(self, symbol: str) -> dict:
        currency = _normalize_symbol(symbol)
        return {
            "event": "subscribe",
            "topic": "orderbook",
            "currency": currency.upper(),
        }

    def _parse_ws_orderbook(self, raw: str | bytes, symbol: str) -> OrderBook | None:
        try:
            if isinstance(raw, bytes):
                msg = json.loads(raw.decode())
            else:
                msg = json.loads(raw)
            if msg.get("event") != "data" or msg.get("topic") != "orderbook":
                return None
            data = msg.get("data", {})
            raw_bids = data.get("bids", [])
            raw_asks = data.get("asks", [])
            # Each entry: {"price": "...", "qty": "..."}
            bids = [[b["price"], b["qty"]] for b in raw_bids]
            asks = [[a["price"], a["qty"]] for a in raw_asks]
            return self._build_orderbook(symbol, bids, asks)
        except Exception:
            return None

    async def _rest_get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        currency = _normalize_symbol(symbol)
        resp = await self._request(
            "GET",
            "/orderbook",
            params={"currency": currency.upper()},
        )
        # Coinone v2 orderbook: {"bid": [{"price","qty"},...], "ask": [...]}
        bids = [[e["price"], e["qty"]] for e in resp.get("bid", [])[:depth]]
        asks = [[e["price"], e["qty"]] for e in resp.get("ask", [])[:depth]]
        return self._build_orderbook(symbol, bids, asks)

    async def _rest_place_order(self, order: Order) -> Trade:
        currency = _normalize_symbol(order.symbol)
        side = "BUY" if order.side == OrderSide.BUY else "SELL"

        body: dict[str, Any] = {
            "target_currency": currency,
            "order_type": "LIMIT",
            "side": side,
            "price": str(int(order.price)) if order.price else "0",
            "quantity": str(order.amount),
        }

        resp = await self._request("POST", "/v2.1/order", data=body, signed=True)
        if resp.get("result") != "success":
            error_msg = resp.get("errorMsg", "unknown error")
            raise RuntimeError(f"Coinone place_order failed: {error_msg}")

        order_id = str(resp.get("orderId", ""))
        return self._build_trade(
            order,
            trade_id=order_id,
            price=order.price or Decimal("0"),
            amount=order.amount,
            fee=order.amount * order.price * Decimal("0.0002") if order.price else Decimal("0"),
            fee_currency="KRW",
        )

    async def _rest_cancel_order(self, order_id: str, symbol: str | None) -> bool:
        if not symbol:
            logger.warning("Coinone cancel_order requires symbol; skipping cancel for %s", order_id)
            return False
        currency = _normalize_symbol(symbol)
        # Coinone cancel requires order_id, price, qty, is_ask, currency.
        # Since we don't have price/qty/is_ask here, attempt with minimal fields.
        body: dict[str, Any] = {
            "order_id": order_id,
            "currency": currency,
        }
        resp = await self._request("POST", "/v2.1/order/cancel", data=body, signed=True)
        return resp.get("result") == "success"

    async def _rest_cancel_all_orders(self, symbol: str | None) -> int:
        # Coinone does not provide a bulk cancel endpoint; return 0.
        return 0

    async def _rest_get_balances(self) -> dict[str, Balance]:
        resp = await self._request("POST", "/v2.1/account/balance/all", data={}, signed=True)
        if resp.get("result") != "success":
            logger.error("Coinone get_balances raw response: %s", resp)
            error_msg = (
                resp.get("error_msg")
                or resp.get("errorMsg")
                or resp.get("message")
                or f"code={resp.get('error_code') or resp.get('errorCode', 'unknown')}"
            )
            raise RuntimeError(f"Coinone get_balances failed: {error_msg}")

        result: dict[str, Balance] = {}
        # v2.1 response: {"balances": [{"currency": "BTC", "available": "...", "limit": "..."}, ...]}
        # "limit" = locked/reserved amount
        for wallet in resp.get("balances", resp.get("normalWallets", [])):
            cur = wallet.get("currency", "").upper()
            if not cur:
                continue
            free = Decimal(str(wallet.get("available", wallet.get("avail", "0"))))
            locked = Decimal(str(wallet.get("limit", wallet.get("balance", "0"))))
            total = free + locked
            result[cur] = Balance(currency=cur, free=free, used=locked, total=total)
        return result

    async def _rest_get_positions(self) -> list[Position]:
        # Coinone is spot-only; no positions.
        return []

    async def _rest_get_fee_rate(self, symbol: str) -> FeeRate:
        # Coinone API discount: 0.02% maker / 0.02% taker
        return FeeRate(
            maker=Decimal("0.0002"),
            taker=Decimal("0.0002"),
            symbol=symbol,
            exchange_id=self.exchange_id,
        )

    # ------------------------------------------------------------------
    # Override _request for Coinone POST auth pattern
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        signed: bool = False,
        headers: dict[str, str] | None = None,
    ) -> dict:
        """Coinone-specific request override.

        For signed POST requests Coinone embeds the auth payload directly into
        the request body (not as separate headers), so we rebuild the body here.
        """
        if not self._http:
            raise RuntimeError(f"{self.exchange_id}: not connected — call connect() first")

        req_headers = dict(headers or {})

        if signed:
            # Build merged payload (auth fields + body fields)
            # nonce: v2.1 공식 방식은 UUID string
            nonce = str(uuid.uuid4())
            payload_data: dict[str, Any] = {
                "access_token": self._api_key,
                "nonce": nonce,
            }
            if data:
                payload_data.update(data)

            payload_json = json.dumps(payload_data, separators=(",", ":"))
            payload_b64_bytes = base64.b64encode(payload_json.encode())
            payload_b64 = payload_b64_bytes.decode()
            # Coinone: 공식 문서 기준 — secret raw bytes 그대로 (upper() 금지)
            signature = hmac.new(
                self._api_secret.encode(),
                payload_b64_bytes,
                hashlib.sha512,
            ).hexdigest()

            req_headers["X-COINONE-PAYLOAD"] = payload_b64
            req_headers["X-COINONE-SIGNATURE"] = signature

            # Send payload_b64 as body (old style) or no body (공식 문서)
            resp = await self._http.request(
                method,
                path,
                params=params,
                content=payload_b64_bytes,
                headers=req_headers,
            )
        else:
            resp = await self._http.request(
                method,
                path,
                params=params,
                json=data if method in ("POST", "PUT", "DELETE") and data else None,
                headers=req_headers,
            )

        resp.raise_for_status()
        return resp.json()
