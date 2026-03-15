"""OKX native adapter — replaces ccxt for OKX exchange.

REST base: https://www.okx.com (sandbox: same URL + x-simulated-trading: 1 header)
WS: wss://ws.okx.com:8443/ws/v5/public
Auth: HMAC-SHA256, base64 encoded. Headers: OK-ACCESS-KEY / OK-ACCESS-SIGN /
      OK-ACCESS-TIMESTAMP / OK-ACCESS-PASSPHRASE
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.parse
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from src.core.models import (
    Balance,
    FeeRate,
    Order,
    OrderBook,
    OrderSide,
    OrderType,
    Position,
    Trade,
)
from src.execution.atomic import OrderResult
from src.infra.exchange.native_adapter import NativeAdapter


class NativeOKXAdapter(NativeAdapter):
    """OKX native adapter using httpx + websockets (no ccxt)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(exchange_id="okx", **kwargs)

    # ------------------------------------------------------------------
    # Symbol normalization
    # ------------------------------------------------------------------

    def _normalize_symbol(self, symbol: str) -> str:
        """BTC/USDT → BTC-USDT."""
        return symbol.replace("/", "-")

    # ------------------------------------------------------------------
    # Connection config
    # ------------------------------------------------------------------

    def _rest_base_url(self) -> str:
        return "https://www.okx.com"

    def _default_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._sandbox:
            headers["x-simulated-trading"] = "1"
        return headers

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _okx_timestamp(self) -> str:
        """ISO 8601 UTC timestamp with millisecond precision."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def _sign_okx(self, prehash: str) -> str:
        """OKX HMAC-SHA256, base64 encoded."""
        mac = hmac.new(self._api_secret.encode(), prehash.encode(), hashlib.sha256)
        return base64.b64encode(mac.digest()).decode()

    def _auth_headers(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        data: dict[str, Any] | None,
    ) -> dict[str, str]:
        ts = self._okx_timestamp()
        meth = method.upper()

        if meth == "GET" and params:
            full_path = path + "?" + urllib.parse.urlencode(params)
            body_str = ""
        else:
            full_path = path
            body_str = json.dumps(data) if data else ""

        prehash = ts + meth + full_path + body_str
        signature = self._sign_okx(prehash)

        headers: dict[str, str] = {
            "OK-ACCESS-KEY": self._api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": self._passphrase,
        }
        if self._sandbox:
            headers["x-simulated-trading"] = "1"
        return headers

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------

    def _ws_orderbook_url(self, symbol: str) -> str:
        return "wss://ws.okx.com:8443/ws/v5/public"

    def _ws_subscribe_message(self, symbol: str) -> dict | None:
        sym = self._normalize_symbol(symbol)
        return {"op": "subscribe", "args": [{"channel": "books", "instId": sym}]}

    def _parse_ws_orderbook(self, raw: str | bytes, symbol: str) -> OrderBook | None:
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return None

        if msg.get("arg", {}).get("channel") == "books" and msg.get("data"):
            data = msg["data"][0]
            # OKX format: [price, size, liquidated_orders, order_count]
            bids = [[b[0], b[1]] for b in data.get("bids", [])]
            asks = [[a[0], a[1]] for a in data.get("asks", [])]
            return self._build_orderbook(symbol, bids, asks)
        return None

    # ------------------------------------------------------------------
    # REST: Orderbook
    # ------------------------------------------------------------------

    async def _rest_get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        sym = self._normalize_symbol(symbol)
        data = await self._request(
            "GET",
            "/api/v5/market/books",
            params={"instId": sym, "sz": depth},
        )
        rows = data.get("data", [])
        if not rows:
            return self._build_orderbook(symbol, [], [])
        row = rows[0]
        bids = [[b[0], b[1]] for b in row.get("bids", [])]
        asks = [[a[0], a[1]] for a in row.get("asks", [])]
        return self._build_orderbook(symbol, bids, asks)

    # ------------------------------------------------------------------
    # REST: Orders
    # ------------------------------------------------------------------

    async def _rest_place_order(self, order: Order) -> Trade:
        sym = self._normalize_symbol(order.symbol)
        side = "buy" if order.side == OrderSide.BUY else "sell"
        ord_type = "market" if order.order_type == OrderType.MARKET else "limit"

        body: dict[str, Any] = {
            "instId": sym,
            "tdMode": "cash",
            "side": side,
            "ordType": ord_type,
            "sz": str(order.amount),
        }
        if order.price is not None:
            body["px"] = str(order.price)
        if order.client_order_id:
            body["clOrdId"] = order.client_order_id

        resp = await self._request("POST", "/api/v5/trade/order", data=body, signed=True)
        result = resp.get("data", [{}])[0]
        order_id = result.get("ordId", "")
        fill_price = order.price or Decimal("0")
        return self._build_trade(order, trade_id=order_id, price=fill_price, amount=order.amount)

    async def place_ioc_limit(
        self, symbol: str, side: str, price: Decimal, size: Decimal
    ) -> OrderResult:
        """Submit an IOC limit order and return fill result (partial fills allowed)."""
        import time
        start = time.monotonic()
        if price <= Decimal("0") or size <= Decimal("0"):
            raise ValueError(f"IOC price/size must be positive: price={price}, size={size}")
        body: dict = {
            "instId": self._normalize_symbol(symbol),
            "tdMode": "cash",
            "side": side.lower(),
            "ordType": "ioc",
            "px": str(price),
            "sz": str(size),
        }
        resp = await self._request("POST", "/api/v5/trade/order", data=body, signed=True)
        data = resp.get("data", [{}])[0]
        filled_qty = Decimal(str(data.get("fillSz", "0")))
        fill_px_str = data.get("fillPx", "0") or "0"
        avg_price = Decimal(fill_px_str) if fill_px_str != "0" else price
        return OrderResult(
            filled_size=filled_qty,
            avg_price=avg_price,
            order_type="ioc_limit",
            latency_ms=(time.monotonic() - start) * 1000,
        )

    async def _rest_cancel_order(self, order_id: str, symbol: str | None) -> bool:
        body: dict[str, Any] = {"ordId": order_id}
        if symbol:
            body["instId"] = self._normalize_symbol(symbol)
        resp = await self._request("POST", "/api/v5/trade/cancel-order", data=body, signed=True)
        data = resp.get("data", [{}])
        return bool(data) and data[0].get("sCode", "1") == "0"

    async def _rest_cancel_all_orders(self, symbol: str | None) -> int:
        """Fetch pending orders then batch-cancel."""
        params: dict[str, Any] = {"instType": "SPOT"}
        if symbol:
            params["instId"] = self._normalize_symbol(symbol)

        orders_resp = await self._request(
            "GET", "/api/v5/trade/orders-pending", params=params, signed=True
        )
        orders = orders_resp.get("data", [])
        if not orders:
            return 0

        # Build cancel list and sign manually — body is a JSON array
        cancel_list = [{"instId": o["instId"], "ordId": o["ordId"]} for o in orders]
        ts = self._okx_timestamp()
        body_str = json.dumps(cancel_list)
        path = "/api/v5/trade/cancel-batch-orders"
        prehash = ts + "POST" + path + body_str
        signature = self._sign_okx(prehash)

        extra_headers: dict[str, str] = {
            "OK-ACCESS-KEY": self._api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": self._passphrase,
        }
        if self._sandbox:
            extra_headers["x-simulated-trading"] = "1"

        if not self._http:
            raise RuntimeError("okx: not connected — call connect() first")
        resp = await self._http.post(path, json=cancel_list, headers=extra_headers)
        resp.raise_for_status()
        results = resp.json().get("data", [])
        return sum(1 for r in results if r.get("sCode") == "0")

    # ------------------------------------------------------------------
    # REST: Account
    # ------------------------------------------------------------------

    async def _rest_get_balances(self) -> dict[str, Balance]:
        resp = await self._request("GET", "/api/v5/account/balance", signed=True)
        balances: dict[str, Balance] = {}
        for account in resp.get("data", []):
            for detail in account.get("details", []):
                currency = detail["ccy"]
                free = Decimal(detail.get("availBal", "0") or "0")
                frozen = Decimal(detail.get("frozenBal", "0") or "0")
                total = free + frozen
                balances[currency] = Balance(currency=currency, free=free, used=frozen, total=total)
        return balances

    async def _rest_get_positions(self) -> list[Position]:
        """OKX spot has no positions."""
        return []

    async def _rest_get_fee_rate(self, symbol: str) -> FeeRate:
        sym = self._normalize_symbol(symbol)
        resp = await self._request(
            "GET",
            "/api/v5/account/trade-fee",
            params={"instType": "SPOT", "instId": sym},
            signed=True,
        )
        rows = resp.get("data", [])
        if rows:
            row = rows[0]
            # OKX maker fee is negative (rebate); take absolute value
            maker_str = row.get("maker", "-0.0008").lstrip("-")
            taker_str = row.get("taker", "-0.001").lstrip("-")
            return FeeRate(
                maker=Decimal(maker_str),
                taker=Decimal(taker_str),
                symbol=symbol,
                exchange_id=self.exchange_id,
            )
        return FeeRate(
            maker=Decimal("0.0008"),
            taker=Decimal("0.001"),
            symbol=symbol,
            exchange_id=self.exchange_id,
        )
