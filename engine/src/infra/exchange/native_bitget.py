"""Native Bitget adapter — Spot trading via direct REST + WebSocket (no ccxt)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from decimal import Decimal
from typing import Any

from src.core.models import Balance, FeeRate, Order, OrderBook, OrderSide, Position, Trade
from src.infra.exchange.native_adapter import NativeAdapter
from src.infra.exchange.rate_limiter import RateLimitConfig

logger = logging.getLogger(__name__)

_BITGET_RATE_LIMITS: dict[str, RateLimitConfig] = {
    "default": RateLimitConfig(requests_per_second=10, burst=20),
    "order": RateLimitConfig(requests_per_second=10, burst=20),
}

_REST_BASE = "https://api.bitget.com"
_WS_PUBLIC = "wss://ws.bitget.com/v2/ws/public"


def _normalize_symbol(symbol: str) -> str:
    """'BTC/USDT' -> 'BTCUSDT'"""
    return symbol.replace("/", "")


class NativeBitgetAdapter(NativeAdapter):
    """Native Bitget spot adapter — direct HTTP/WebSocket, no ccxt."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rate_limits", _BITGET_RATE_LIMITS)
        super().__init__(exchange_id="bitget", **kwargs)
        self._market_type: str = "spot"  # set to "futures" by create_native_adapter

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
        ts = str(int(time.time() * 1000))
        body_str = json.dumps(data, separators=(",", ":")) if data else ""
        qs = ""
        if params:
            qs = "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        prehash = ts + method.upper() + path + qs + body_str
        sign = base64.b64encode(
            hmac.new(self._api_secret.encode(), prehash.encode(), hashlib.sha256).digest()
        ).decode()
        return {
            "ACCESS-KEY": self._api_key,
            "ACCESS-SIGN": sign,
            "ACCESS-TIMESTAMP": ts,
            "ACCESS-PASSPHRASE": self._passphrase,
        }

    def _ws_orderbook_url(self, symbol: str) -> str:
        return _WS_PUBLIC

    def _ws_subscribe_message(self, symbol: str) -> dict | None:
        sym = _normalize_symbol(symbol)
        return {
            "op": "subscribe",
            "args": [{"instType": "SPOT", "channel": "books5", "instId": sym}],
        }

    def _parse_ws_orderbook(self, raw: str | bytes, symbol: str) -> OrderBook | None:
        try:
            msg = json.loads(raw)
            if msg.get("action") not in ("snapshot", "update"):
                return None
            data = msg.get("data", [{}])[0]
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            return self._build_orderbook(symbol, bids, asks)
        except Exception:
            return None

    async def _rest_get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        sym = _normalize_symbol(symbol)
        if self._market_type == "futures":
            resp = await self._request(
                "GET",
                "/api/v2/mix/market/merge-depth",
                params={"symbol": sym, "productType": "USDT-FUTURES", "precision": "scale0", "limit": str(depth)},
            )
            data = resp.get("data", {})
            return self._build_orderbook(symbol, data.get("bids", []), data.get("asks", []))
        resp = await self._request(
            "GET",
            "/api/v2/spot/market/orderbook",
            params={"symbol": sym, "type": "step0", "limit": str(depth)},
        )
        data = resp["data"]
        return self._build_orderbook(symbol, data["bids"], data["asks"])

    async def _rest_place_order(self, order: Order) -> Trade:
        sym = _normalize_symbol(order.symbol)
        side = "buy" if order.side == OrderSide.BUY else "sell"

        if self._market_type == "futures":
            qty = order.amount
            # PHOENIX: Enforce Bitget Futures MIN_NOTIONAL ($5) — use $6 safety buffer
            if order.price and order.price > 0:
                _MIN_NOTIONAL = Decimal("6")
                if qty * order.price < _MIN_NOTIONAL:
                    qty = (_MIN_NOTIONAL / order.price).quantize(Decimal("0.000001"))
                    logger.debug(
                        "bitget_futures_min_notional_adjusted symbol=%s qty=%s notional=%.2f",
                        order.symbol, qty, float(qty * order.price),
                    )
            body: dict[str, Any] = {
                "symbol": sym,
                "productType": "USDT-FUTURES",
                "marginMode": "crossed",
                "marginCoin": "USDT",
                "size": str(qty),
                "side": side,
                "tradeSide": "open",
                "orderType": "limit" if order.price else "market",
                "force": "gtc",
            }
            if order.price:
                body["price"] = str(order.price)
            if order.client_order_id:
                body["clientOid"] = order.client_order_id
            resp = await self._request("POST", "/api/v2/mix/order/place-order", data=body, signed=True)
        else:
            body = {
                "symbol": sym,
                "side": side,
                "orderType": "limit" if order.price else "market",
                "size": str(order.amount),
                "force": "gtc",
            }
            if order.price:
                body["price"] = str(order.price)
            if order.client_order_id:
                body["clientOid"] = order.client_order_id
            resp = await self._request("POST", "/api/v2/spot/trade/place-order", data=body, signed=True)
        rd = resp.get("data", {})
        return self._build_trade(
            order,
            trade_id=str(rd.get("orderId", "")),
            price=order.price or Decimal("0"),
            amount=order.amount,
        )

    async def place_ioc_limit(
        self, symbol: str, side: str, price: Decimal, size: Decimal
    ) -> "OrderResult":
        """Submit an IOC limit order and return fill result (partial fills allowed)."""
        if price <= Decimal("0") or size <= Decimal("0"):
            raise ValueError(f"IOC price/size must be positive: price={price}, size={size}")
        from src.execution.atomic import OrderResult
        import time as _time
        start = _time.monotonic()
        body: dict[str, Any] = {
            "symbol": _normalize_symbol(symbol),
            "side": "buy" if side.upper() == "BUY" else "sell",
            "orderType": "limit",
            "size": str(size),
            "price": str(price),
            "force": "ioc",
        }
        resp = await self._request("POST", "/api/v2/spot/trade/place-order", data=body, signed=True)
        rd = resp.get("data", {})
        filled_qty = Decimal(str(rd.get("baseVolume", "0"))) if rd.get("baseVolume") else size
        avg_price = Decimal(str(rd.get("avgPrice", "0"))) if rd.get("avgPrice") else price
        return OrderResult(
            filled_size=filled_qty,
            avg_price=avg_price,
            order_type="ioc_limit",
            latency_ms=(_time.monotonic() - start) * 1000,
        )

    async def _rest_cancel_order(self, order_id: str, symbol: str | None) -> bool:
        body: dict[str, Any] = {"orderId": order_id}
        if symbol:
            body["symbol"] = _normalize_symbol(symbol)
        if self._market_type == "futures":
            body["productType"] = "USDT-FUTURES"
            resp = await self._request(
                "POST", "/api/v2/mix/order/cancel-order", data=body, signed=True
            )
        else:
            resp = await self._request(
                "POST", "/api/v2/spot/trade/cancel-order", data=body, signed=True
            )
        return resp.get("code") == "00000"

    async def _rest_cancel_all_orders(self, symbol: str | None) -> int:
        body: dict[str, Any] = {}
        if symbol:
            body["symbol"] = _normalize_symbol(symbol)
        resp = await self._request(
            "POST", "/api/v2/spot/trade/cancel-batch-orders", data=body, signed=True
        )
        cancelled = resp.get("data", [])
        return len(cancelled) if isinstance(cancelled, list) else 0

    async def _rest_get_balances(self) -> dict[str, Balance]:
        if self._market_type == "futures":
            resp = await self._request(
                "GET", "/api/v2/mix/account/accounts",
                params={"productType": "USDT-FUTURES"}, signed=True,
            )
            result: dict[str, Balance] = {}
            for item in resp.get("data", []):
                cur = item.get("marginCoin", "")
                free = Decimal(str(item.get("available", "0")))
                frozen = Decimal(str(item.get("frozen", "0")))
                result[cur] = Balance(currency=cur, free=free, used=frozen, total=free + frozen)
            return result
        resp = await self._request("GET", "/api/v2/spot/account/assets", signed=True)
        result = {}
        for item in resp.get("data", []):
            cur = item.get("coin", "")
            free = Decimal(str(item.get("available", "0")))
            frozen = Decimal(str(item.get("frozen", "0")))
            result[cur] = Balance(currency=cur, free=free, used=frozen, total=free + frozen)
        return result

    async def _rest_get_positions(self) -> list[Position]:
        return []

    async def _rest_get_fee_rate(self, symbol: str) -> FeeRate:
        return FeeRate(
            maker=Decimal("0.001"),
            taker=Decimal("0.001"),
            symbol=symbol,
            exchange_id=self.exchange_id,
        )
