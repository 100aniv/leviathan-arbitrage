"""Native Upbit adapter — Korean KRW exchange via direct REST + WebSocket (no ccxt)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import urllib.parse
import uuid
from decimal import Decimal
from typing import Any

from src.core.models import Balance, FeeRate, Order, OrderBook, OrderSide, Position, Trade
from src.infra.exchange.native_adapter import NativeAdapter
from src.infra.exchange.rate_limiter import RateLimitConfig

logger = logging.getLogger(__name__)

_UPBIT_RATE_LIMITS: dict[str, RateLimitConfig] = {
    "default": RateLimitConfig(requests_per_second=10, burst=30),
    "order": RateLimitConfig(requests_per_second=8, burst=15),
}

_REST_BASE = "https://api.upbit.com"
_WS_PUBLIC = "wss://api.upbit.com/websocket/v1"


def _normalize_symbol(symbol: str) -> str:
    """'BTC/KRW' -> 'KRW-BTC'"""
    if "/" in symbol:
        base, quote = symbol.split("/", 1)
        return f"{quote}-{base}"
    return symbol


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _make_jwt(access_key: str, secret_key: str, query_params: dict | None = None) -> str:
    """Build a HS256 JWT for Upbit without PyJWT dependency."""
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload: dict[str, Any] = {
        "access_key": access_key,
        "nonce": str(uuid.uuid4()),
    }
    if query_params:
        # Upbit validates against the exact URL-encoded param string sent with the request.
        # Do NOT sort — preserve insertion order to match what httpx sends in the URL.
        qs = urllib.parse.urlencode(query_params)
        payload["query_hash"] = hashlib.sha512(qs.encode()).hexdigest()
        payload["query_hash_alg"] = "SHA512"

    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload_b64}"
    sig = _b64url(
        hmac.new(secret_key.encode(), signing_input.encode(), hashlib.sha256).digest()
    )
    return f"{signing_input}.{sig}"


class NativeUpbitAdapter(NativeAdapter):
    """Native Upbit spot adapter — direct HTTP/WebSocket, no ccxt.

    Upbit uses JWT (HS256) authentication, not HMAC headers.
    All pairs are KRW-denominated (e.g., BTC/KRW).
    """

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rate_limits", _UPBIT_RATE_LIMITS)
        super().__init__(exchange_id="upbit", **kwargs)

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
        # Include query params in JWT for authenticated requests
        query_params = params or data or None
        token = _make_jwt(self._api_key, self._api_secret, query_params)
        return {"Authorization": f"Bearer {token}"}

    def _ws_orderbook_url(self, symbol: str) -> str:
        return _WS_PUBLIC

    def _ws_subscribe_message(self, symbol: str) -> list:
        market = _normalize_symbol(symbol)
        return [
            {"ticket": str(uuid.uuid4())},
            {"type": "orderbook", "codes": [market]},
        ]

    def _parse_ws_orderbook(self, raw: str | bytes, symbol: str) -> OrderBook | None:
        try:
            if isinstance(raw, bytes):
                msg = json.loads(raw.decode())
            else:
                msg = json.loads(raw)
            if msg.get("type") != "orderbook":
                return None
            units = msg.get("orderbook_units", [])
            bids = [[u["bid_price"], u["bid_size"]] for u in units]
            asks = [[u["ask_price"], u["ask_size"]] for u in units]
            return self._build_orderbook(symbol, bids, asks)
        except Exception:
            return None

    async def _rest_get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        market = _normalize_symbol(symbol)
        resp = await self._request("GET", "/v1/orderbook", params={"markets": market})
        data = resp[0] if isinstance(resp, list) else resp
        units = data.get("orderbook_units", [])[:depth]
        bids = [[u["bid_price"], u["bid_size"]] for u in units]
        asks = [[u["ask_price"], u["ask_size"]] for u in units]
        return self._build_orderbook(symbol, bids, asks)

    async def _rest_place_order(self, order: Order) -> Trade:
        market = _normalize_symbol(order.symbol)
        side = "bid" if order.side == OrderSide.BUY else "ask"
        body: dict[str, Any] = {
            "market": market,
            "side": side,
            "ord_type": "limit" if order.price else "market",
            "volume": str(order.amount),
        }
        if order.price:
            body["price"] = str(order.price)
        if order.client_order_id:
            body["identifier"] = order.client_order_id

        # Upbit POST /v1/orders expects parameters as URL query params (not JSON body).
        # The JWT query_hash is computed from the same params in the same order.
        resp = await self._request("POST", "/v1/orders", params=body, signed=True)
        return self._build_trade(
            order,
            trade_id=resp.get("uuid", ""),
            price=order.price or Decimal("0"),
            amount=order.amount,
        )

    async def _rest_cancel_order(self, order_id: str, symbol: str | None) -> bool:
        resp = await self._request(
            "DELETE", "/v1/order", params={"uuid": order_id}, signed=True
        )
        return "uuid" in resp

    async def _rest_cancel_all_orders(self, symbol: str | None) -> int:
        # Upbit does not support bulk cancel
        return 0

    async def _rest_get_balances(self) -> dict[str, Balance]:
        resp = await self._request("GET", "/v1/accounts", signed=True)
        result: dict[str, Balance] = {}
        for item in resp:
            cur = item.get("currency", "")
            free = Decimal(str(item.get("balance", "0")))
            locked = Decimal(str(item.get("locked", "0")))
            result[cur] = Balance(currency=cur, free=free, used=locked, total=free + locked)
        return result

    async def _rest_get_positions(self) -> list[Position]:
        return []

    async def _rest_get_fee_rate(self, symbol: str) -> FeeRate:
        return FeeRate(
            maker=Decimal("0.0005"),
            taker=Decimal("0.00139"),
            symbol=symbol,
            exchange_id=self.exchange_id,
        )
