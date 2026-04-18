"""Bybit native adapter — replaces  for Bybit exchange.

REST base: https://api.bybit.com (sandbox: https://api-testnet.bybit.com)
WS: wss://stream.bybit.com/v5/public/spot
Auth: HMAC-SHA256, headers X-BAPI-API-KEY / X-BAPI-TIMESTAMP / X-BAPI-SIGN / X-BAPI-RECV-WINDOW
"""
from __future__ import annotations

import json
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

_RECV_WINDOW = 5000


class NativeBybitAdapter(NativeAdapter):
    """Bybit native adapter using httpx + websockets."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(exchange_id="bybit", **kwargs)

    # ------------------------------------------------------------------
    # Symbol normalization
    # ------------------------------------------------------------------

    def _normalize_symbol(self, symbol: str) -> str:
        """BTC/USDT → BTCUSDT."""
        return symbol.replace("/", "")

    # ------------------------------------------------------------------
    # Connection config
    # ------------------------------------------------------------------

    def _rest_base_url(self) -> str:
        if self._sandbox:
            return "https://api-testnet.bybit.com"
        return "https://api.bybit.com"

    def _default_headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _auth_headers(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        data: dict[str, Any] | None,
    ) -> dict[str, str]:
        ts = str(self._timestamp_ms())
        recv_window = str(_RECV_WINDOW)

        if method.upper() == "GET" and params:
            param_str = self._build_query_string(params)
        elif method.upper() in ("POST", "PUT", "DELETE") and data:
            param_str = json.dumps(data, separators=(",", ":"))
        else:
            param_str = ""

        sign_msg = ts + self._api_key + recv_window + param_str
        signature = self._sign_hmac_sha256(sign_msg)

        return {
            "X-BAPI-API-KEY": self._api_key,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-SIGN": signature,
            "X-BAPI-RECV-WINDOW": recv_window,
        }

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------

    def _ws_orderbook_url(self, symbol: str) -> str:
        return "wss://stream.bybit.com/v5/public/spot"

    def _ws_subscribe_message(self, symbol: str) -> dict | None:
        sym = self._normalize_symbol(symbol)
        return {"op": "subscribe", "args": [f"orderbook.50.{sym}"]}

    def _parse_ws_orderbook(self, raw: str | bytes, symbol: str) -> OrderBook | None:
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return None

        if msg.get("topic", "").startswith("orderbook"):
            data = msg.get("data", {})
            bids = data.get("b", [])
            asks = data.get("a", [])
            seq = data.get("seq")
            return self._build_orderbook(symbol, bids, asks, sequence=seq)
        return None

    # ------------------------------------------------------------------
    # REST: Orderbook
    # ------------------------------------------------------------------

    async def _rest_get_orderbook(self, symbol: str, depth: int = 50) -> OrderBook:
        sym = self._normalize_symbol(symbol)
        data = await self._request(
            "GET",
            "/v5/market/orderbook",
            params={"category": "spot", "symbol": sym, "limit": depth},
        )
        result = data["result"]
        bids = result.get("b", [])
        asks = result.get("a", [])
        seq = result.get("seq")
        return self._build_orderbook(symbol, bids, asks, sequence=seq)

    # ------------------------------------------------------------------
    # REST: Orders
    # ------------------------------------------------------------------

    async def _rest_place_order(self, order: Order) -> Trade:
        sym = self._normalize_symbol(order.symbol)
        side = "Buy" if order.side == OrderSide.BUY else "Sell"
        order_type = "Market" if order.order_type == OrderType.MARKET else "Limit"

        body: dict[str, Any] = {
            "category": "spot",
            "symbol": sym,
            "side": side,
            "orderType": order_type,
            "qty": str(order.amount),
        }
        if order.price is not None:
            body["price"] = str(order.price)
        if order.client_order_id:
            body["orderLinkId"] = order.client_order_id

        resp = await self._request("POST", "/v5/order/create", data=body, signed=True)
        result = resp["result"]
        order_id = result.get("orderId", "")
        fill_price = order.price or Decimal("0")
        return self._build_trade(order, trade_id=order_id, price=fill_price, amount=order.amount)

    async def _rest_cancel_order(self, order_id: str, symbol: str | None) -> bool:
        body: dict[str, Any] = {"category": "spot", "orderId": order_id}
        if symbol:
            body["symbol"] = self._normalize_symbol(symbol)
        resp = await self._request("POST", "/v5/order/cancel", data=body, signed=True)
        return resp.get("retCode", -1) == 0

    async def _rest_cancel_all_orders(self, symbol: str | None) -> int:
        body: dict[str, Any] = {"category": "spot"}
        if symbol:
            body["symbol"] = self._normalize_symbol(symbol)
        resp = await self._request("POST", "/v5/order/cancel-all", data=body, signed=True)
        cancelled = resp.get("result", {}).get("list", [])
        return len(cancelled)

    # ------------------------------------------------------------------
    # REST: Account
    # ------------------------------------------------------------------

    async def _rest_get_balances(self) -> dict[str, Balance]:
        resp = await self._request(
            "GET",
            "/v5/account/wallet-balance",
            params={"accountType": "UNIFIED"},
            signed=True,
        )
        balances: dict[str, Balance] = {}
        for account in resp.get("result", {}).get("list", []):
            for coin in account.get("coin", []):
                currency = coin["coin"]
                free = Decimal(coin.get("availableToWithdraw", "0") or "0")
                total = Decimal(coin.get("walletBalance", "0") or "0")
                used = max(total - free, Decimal("0"))
                balances[currency] = Balance(currency=currency, free=free, used=used, total=total)
        return balances

    async def _rest_get_positions(self) -> list[Position]:
        """Bybit spot has no positions."""
        return []

    async def place_ioc_limit(
        self, symbol: str, side: str, price: Decimal, size: Decimal
    ) -> OrderResult:
        """Submit an IOC limit order and return fill result (partial fills allowed)."""
        if price <= Decimal("0") or size <= Decimal("0"):
            raise ValueError(f"IOC price/size must be positive: price={price}, size={size}")
        import time
        start = time.monotonic()
        body: dict = {
            "category": "spot",
            "symbol": self._normalize_symbol(symbol),
            "side": "Buy" if side.upper() == "BUY" else "Sell",
            "orderType": "Limit",
            "price": str(price),
            "qty": str(size),
            "timeInForce": "IOC",
        }
        resp = await self._request("POST", "/v5/order/create", data=body, signed=True)
        result = resp.get("result", {})
        filled_qty = Decimal(str(result.get("cumExecQty", "0")))
        avg_price_str = result.get("avgPrice", "0") or "0"
        avg_price = Decimal(avg_price_str) if avg_price_str != "0" else price
        return OrderResult(
            filled_size=filled_qty,
            avg_price=avg_price,
            order_type="ioc_limit",
            latency_ms=(time.monotonic() - start) * 1000,
        )

    async def _rest_get_fee_rate(self, symbol: str) -> FeeRate:
        sym = self._normalize_symbol(symbol)
        resp = await self._request(
            "GET",
            "/v5/account/fee-rate",
            params={"category": "spot", "symbol": sym},
            signed=True,
        )
        rows = resp.get("result", {}).get("list", [])
        if rows:
            row = rows[0]
            return FeeRate(
                maker=Decimal(row.get("makerFeeRate", "0.001")),
                taker=Decimal(row.get("takerFeeRate", "0.001")),
                symbol=symbol,
                exchange_id=self.exchange_id,
            )
        return FeeRate(
            maker=Decimal("0.001"),
            taker=Decimal("0.001"),
            symbol=symbol,
            exchange_id=self.exchange_id,
        )
