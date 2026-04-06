"""Native Bithumb adapter — Korean KRW exchange via direct REST + WebSocket (no ccxt).

Bithumb API v2: JWT HS256 인증 (Authorization: Bearer {token})
  payload = {access_key, nonce(UUID), timestamp(ms), [query_hash, query_hash_alg]}
  token = jwt.encode(payload, secret_key, algorithm='HS256')
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
import urllib.parse
from decimal import Decimal
from typing import Any

try:
    import jwt as _pyjwt
except ImportError:  # pragma: no cover
    _pyjwt = None  # type: ignore[assignment]

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


def _to_market(symbol: str) -> str:
    """'BTC/KRW' → 'KRW-BTC' (Bithumb v2 market format)."""
    if "/" in symbol:
        base, quote = symbol.split("/", 1)
        return f"{quote}-{base}"
    return symbol


def _coin_from_symbol(symbol: str) -> str:
    """'BTC/KRW' → 'BTC'"""
    if "/" in symbol:
        return symbol.split("/")[0]
    return symbol.split("_")[0]


class NativeBithumbAdapter(NativeAdapter):
    """Native Bithumb spot adapter — Bithumb API v2, JWT HS256."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rate_limits", _BITHUMB_RATE_LIMITS)
        super().__init__(exchange_id="bithumb", **kwargs)

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
        return self._make_jwt_headers(query_params=params)

    def _make_jwt_headers(
        self,
        query_params: dict[str, Any] | None = None,
        body_data: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Bithumb v2 JWT 인증 헤더 생성.

        GET: query_hash = SHA512(urlencode(params))
        POST/DELETE: query_hash = SHA512(json.dumps(body))
        """
        if _pyjwt is None:
            raise RuntimeError("PyJWT is required: pip install PyJWT")

        payload: dict[str, Any] = {
            "access_key": self._api_key,
            "nonce": str(uuid.uuid4()),
            "timestamp": round(time.time() * 1000),
        }
        if body_data:
            # POST: URL-encoded body hash (Bithumb v2 spec)
            query = urllib.parse.urlencode(body_data)
            h = hashlib.sha512()
            h.update(query.encode())
            payload["query_hash"] = h.hexdigest()
            payload["query_hash_alg"] = "SHA512"
        elif query_params:
            # GET: urlencode query string hash
            query = urllib.parse.urlencode(query_params)
            h = hashlib.sha512()
            h.update(query.encode())
            payload["query_hash"] = h.hexdigest()
            payload["query_hash_alg"] = "SHA512"

        token = _pyjwt.encode(payload, self._api_secret, algorithm="HS256")
        return {"Authorization": f"Bearer {token}"}

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
    # Override _request for Bithumb v2 JWT pattern
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        signed: bool = False,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Bithumb v2: GET→query params with JWT, POST/DELETE→JSON body with JWT."""
        if not self._http:
            raise RuntimeError(f"{self.exchange_id}: not connected — call connect() first")

        req_headers = {**self._default_headers(), **(headers or {})}

        if signed:
            if method in ("POST", "PUT") and data:
                req_headers.update(self._make_jwt_headers(body_data=data))
            else:
                req_headers.update(self._make_jwt_headers(query_params=params))

        if method in ("POST", "PUT", "DELETE") and data:
            resp = await self._http.request(
                method, path, params=params,
                content=json.dumps(data).encode(),
                headers=req_headers,
            )
        else:
            resp = await self._http.request(
                method, path, params=params, headers=req_headers,
            )

        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # REST implementations
    # ------------------------------------------------------------------

    async def _rest_get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        market = _to_market(symbol)
        resp = await self._request(
            "GET", "/v1/orderbook", params={"markets": market}
        )
        # resp: [{"market": "KRW-BTC", "orderbook_units": [{"ask_price","bid_price","ask_size","bid_size"}, ...]}]
        bids: list[list] = []
        asks: list[list] = []
        if isinstance(resp, list) and resp:
            for unit in resp[0].get("orderbook_units", [])[:depth]:
                bids.append([str(unit["bid_price"]), str(unit["bid_size"])])
                asks.append([str(unit["ask_price"]), str(unit["ask_size"])])
        return self._build_orderbook(symbol, bids, asks)

    async def _rest_place_order(self, order: Order) -> Trade:
        market = _to_market(order.symbol)
        side = "bid" if order.side == OrderSide.BUY else "ask"
        body: dict[str, Any] = {
            "market": market,
            "side": side,
            "volume": str(order.amount),
            "ord_type": "limit",
        }
        if order.price:
            body["price"] = str(int(order.price))

        resp = await self._request("POST", "/v1/orders", data=body, signed=True)
        order_id = str(resp.get("uuid", ""))
        return self._build_trade(
            order,
            trade_id=order_id,
            price=order.price or Decimal("0"),
            amount=order.amount,
        )

    async def _rest_cancel_order(self, order_id: str, symbol: str | None) -> bool:
        try:
            resp = await self._request(
                "DELETE", "/v1/order", params={"uuid": order_id}, signed=True
            )
        except Exception as exc:
            # 404 = order already filled/cancelled (treat as success)
            if "404" in str(exc):
                return True
            raise
        return isinstance(resp, dict) and (
            resp.get("uuid") == order_id or resp.get("state") == "cancel"
        )

    async def _rest_cancel_all_orders(self, symbol: str | None) -> int:
        return 0

    async def _rest_get_balances(self) -> dict[str, Balance]:
        resp = await self._request("GET", "/v1/accounts", signed=True)
        # resp: [{"currency":"KRW","balance":"14","locked":"0",...}, ...]
        result: dict[str, Balance] = {}
        if not isinstance(resp, list):
            return result
        for item in resp:
            cur = item.get("currency", "").upper()
            if not cur:
                continue
            free = Decimal(str(item.get("balance", "0")))
            locked = Decimal(str(item.get("locked", "0")))
            total = free + locked
            result[cur] = Balance(currency=cur, free=free, used=locked, total=total)
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
