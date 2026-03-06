"""Native Bithumb adapter — Korean KRW exchange via direct REST + WebSocket (no ccxt)."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import urllib.parse
from decimal import Decimal
from typing import Any

import httpx

from src.core.models import Balance, FeeRate, Order, OrderBook, OrderSide, Position, Trade
from src.infra.exchange.native_adapter import NativeAdapter
from src.infra.exchange.rate_limiter import RateLimitConfig

logger = logging.getLogger(__name__)

_BITHUMB_RATE_LIMITS: dict[str, RateLimitConfig] = {
    "default": RateLimitConfig(requests_per_second=5, burst=15),
    "order": RateLimitConfig(requests_per_second=5, burst=10),
}

_REST_BASE = "https://api.bithumb.com"
_WS_PUBLIC = "wss://pubwss.bithumb.com/pub/ws"


def _normalize_symbol(symbol: str) -> str:
    """'BTC/KRW' -> 'BTC_KRW'"""
    return symbol.replace("/", "_")


def _coin_from_symbol(symbol: str) -> str:
    """'BTC/KRW' -> 'BTC',  'BTC_KRW' -> 'BTC'"""
    if "/" in symbol:
        return symbol.split("/")[0]
    return symbol.split("_")[0]


class NativeBithumbAdapter(NativeAdapter):
    """Native Bithumb spot adapter — direct HTTP/WebSocket, no ccxt.

    Bithumb uses HMAC-SHA512 authentication with form-encoded POST bodies.
    """

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rate_limits", _BITHUMB_RATE_LIMITS)
        super().__init__(exchange_id="bithumb", **kwargs)

    # ------------------------------------------------------------------
    # Abstract implementations
    # ------------------------------------------------------------------

    def _rest_base_url(self) -> str:
        return _REST_BASE

    def _default_headers(self) -> dict[str, str]:
        return {"Content-Type": "application/x-www-form-urlencoded"}

    def _auth_headers(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        data: dict[str, Any] | None,
    ) -> dict[str, str]:
        nonce = str(int(time.time() * 1000))
        form_data = params or data or {}
        query_str = urllib.parse.urlencode(sorted(form_data.items()))
        prehash = path + chr(0) + query_str + chr(0) + nonce
        sig = hmac.new(
            self._api_secret.encode(), prehash.encode(), hashlib.sha512
        ).hexdigest()
        return {
            "Api-Key": self._api_key,
            "Api-Sign": sig,
            "Api-Nonce": nonce,
        }

    def _ws_orderbook_url(self, symbol: str) -> str:
        return _WS_PUBLIC

    def _ws_subscribe_message(self, symbol: str) -> dict:
        coin = _coin_from_symbol(symbol)
        return {
            "type": "orderbooksnapshot",
            "symbols": [f"{coin}_KRW"],
        }

    def _parse_ws_orderbook(self, raw: str | bytes, symbol: str) -> OrderBook | None:
        try:
            msg = json.loads(raw)
            content = msg.get("content", {})
            bids = [[item["price"], item["quantity"]] for item in content.get("bids", [])]
            asks = [[item["price"], item["quantity"]] for item in content.get("asks", [])]
            if not bids and not asks:
                return None
            return self._build_orderbook(symbol, bids, asks)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Override _request to send form-encoded POST bodies
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
        """Override to send POST data as form-encoded, not JSON."""
        if not self._http:
            raise RuntimeError(f"{self.exchange_id}: not connected — call connect() first")

        req_headers = dict(headers or {})
        if signed:
            req_headers.update(self._auth_headers(method, path, params, data))

        if method in ("POST", "PUT") and data:
            resp = await self._http.request(
                method,
                path,
                params=params,
                content=urllib.parse.urlencode(data).encode(),
                headers=req_headers,
            )
        else:
            resp = await self._http.request(
                method,
                path,
                params=params,
                headers=req_headers,
            )
        resp.raise_for_status()
        return resp.json()

    async def _rest_get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        coin = _coin_from_symbol(symbol)
        resp = await self._request(
            "GET", f"/public/orderbook/{coin}_KRW", params={"count": depth}
        )
        data = resp.get("data", {})
        bids = [[item["price"], item["quantity"]] for item in data.get("bids", [])]
        asks = [[item["price"], item["quantity"]] for item in data.get("asks", [])]
        return self._build_orderbook(symbol, bids, asks)

    async def _rest_place_order(self, order: Order) -> Trade:
        coin = _coin_from_symbol(order.symbol)
        side = "bid" if order.side == OrderSide.BUY else "ask"
        body: dict[str, Any] = {
            "order_currency": coin,
            "payment_currency": "KRW",
            "type": side,
            "units": str(order.amount),
        }
        if order.price:
            body["price"] = str(order.price)

        resp = await self._request("POST", "/trade/place", data=body, signed=True)
        rd = resp.get("data", {})
        return self._build_trade(
            order,
            trade_id=str(rd.get("order_id", "")),
            price=order.price or Decimal("0"),
            amount=order.amount,
        )

    async def _rest_cancel_order(self, order_id: str, symbol: str | None) -> bool:
        coin = _coin_from_symbol(symbol) if symbol else ""
        body: dict[str, Any] = {"order_id": order_id, "type": "bid"}
        if coin:
            body["order_currency"] = coin
        resp = await self._request("POST", "/trade/cancel", data=body, signed=True)
        return resp.get("status") == "0000"

    async def _rest_cancel_all_orders(self, symbol: str | None) -> int:
        # Bithumb does not support bulk cancel
        return 0

    async def _rest_get_balances(self) -> dict[str, Balance]:
        resp = await self._request(
            "POST", "/info/balance", data={"currency": "ALL"}, signed=True
        )
        data = resp.get("data", {})
        result: dict[str, Balance] = {}
        # Keys: available_btc, total_btc, in_use_btc, ...
        currencies: set[str] = set()
        for key in data:
            if key.startswith("available_"):
                currencies.add(key[len("available_"):].upper())
        for cur in currencies:
            c = cur.lower()
            free = Decimal(str(data.get(f"available_{c}", "0")))
            total = Decimal(str(data.get(f"total_{c}", "0")))
            used = total - free
            result[cur] = Balance(currency=cur, free=free, used=used, total=total)
        return result

    async def _rest_get_positions(self) -> list[Position]:
        return []

    async def _rest_get_fee_rate(self, symbol: str) -> FeeRate:
        return FeeRate(
            maker=Decimal("0.0025"),
            taker=Decimal("0.0025"),
            symbol=symbol,
            exchange_id=self.exchange_id,
        )
