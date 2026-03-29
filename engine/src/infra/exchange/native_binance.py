"""Binance native adapter — REST + WebSocket, no ccxt dependency.

Auth: HMAC-SHA256 via timestamp + recvWindow query params.
Spot:   https://api.binance.com  /  wss://stream.binance.com:9443
Testnet: https://testnet.binance.vision
"""
from __future__ import annotations

import json
import logging
import zlib
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

logger = logging.getLogger(__name__)

_REST_URL = "https://api.binance.com"
_REST_SANDBOX_URL = "https://testnet.binance.vision"
_WS_BASE = "wss://stream.binance.com:9443"
_WS_SANDBOX_BASE = "wss://testnet.binance.vision"


def _symbol_to_binance(symbol: str) -> str:
    """'BTC/USDT' → 'BTCUSDT'"""
    return symbol.replace("/", "").upper()


def _symbol_from_binance(symbol: str) -> str:
    """'BTCUSDT' → 'BTC/USDT' — best-effort via known quote assets."""
    quotes = ["USDT", "BUSD", "USDC", "TUSD", "BTC", "ETH", "BNB", "USD"]
    s = symbol.upper()
    for q in quotes:
        if s.endswith(q):
            base = s[: -len(q)]
            return f"{base}/{q}"
    return symbol


class BinanceNativeAdapter(NativeAdapter):
    """Native Binance adapter (spot + futures) using httpx + websockets."""

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        sandbox: bool = False,
        market_type: str = "spot",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            exchange_id="binance",
            api_key=api_key,
            api_secret=api_secret,
            sandbox=sandbox,
            **kwargs,
        )
        self._market_type = market_type  # "spot" or "futures"

    # ------------------------------------------------------------------
    # URL / header overrides
    # ------------------------------------------------------------------

    def _rest_base_url(self) -> str:
        return _REST_SANDBOX_URL if self._sandbox else _REST_URL

    def _default_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._api_key:
            headers["X-MBX-APIKEY"] = self._api_key
        return headers

    def _auth_headers(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        data: dict[str, Any] | None,
    ) -> dict[str, str]:
        # Binance auth uses query-param signature, not headers.
        # Signing is done manually in _signed_request; API key is in _default_headers.
        return {}

    # ------------------------------------------------------------------
    # Signed request helper
    # ------------------------------------------------------------------

    async def _signed_request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        """Append timestamp + HMAC-SHA256 signature and execute the request."""
        p = dict(params or {})
        p["timestamp"] = self._timestamp_ms()
        p["recvWindow"] = 5000
        query_str = self._build_query_string(p)
        p["signature"] = self._sign_hmac_sha256(query_str)
        return await self._request(method, path, params=p, data=data)

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------

    def _ws_orderbook_url(self, symbol: str) -> str:
        stream = f"{_symbol_to_binance(symbol).lower()}@depth20@100ms"
        base = _WS_SANDBOX_BASE if self._sandbox else _WS_BASE
        return f"{base}/ws/{stream}"

    def _ws_subscribe_message(self, symbol: str) -> dict | str | None:
        # Subscription is encoded in the URL; no subscribe frame needed.
        return None

    def _parse_ws_orderbook(self, raw: str | bytes, symbol: str) -> OrderBook | None:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return None

        # Combined stream: {"stream": "btcusdt@depth20@100ms", "data": {...}}
        if "stream" in data and "data" in data:
            payload = data["data"]
        elif "bids" in data and "asks" in data:
            # Single-symbol stream
            payload = data
        else:
            return None

        bids = payload.get("bids", [])
        asks = payload.get("asks", [])
        return self._build_orderbook(symbol, bids, asks)

    # ------------------------------------------------------------------
    # REST implementations
    # ------------------------------------------------------------------

    async def _rest_get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        raw = await self._request(
            "GET",
            "/api/v3/depth",
            params={"symbol": _symbol_to_binance(symbol), "limit": depth},
        )
        ob = self._build_orderbook(
            symbol, raw["bids"], raw["asks"], sequence=raw.get("lastUpdateId")
        )
        if "checksum" in raw:
            self._validate_checksum(ob, raw["checksum"])
        return ob

    def _validate_checksum(self, orderbook: OrderBook, expected: int) -> None:
        """Validate orderbook integrity via CRC32 (ported from BinanceAdapter)."""
        parts: list[str] = []
        levels = max(len(orderbook.bids), len(orderbook.asks))
        for i in range(min(levels, 100)):
            if i < len(orderbook.bids):
                b = orderbook.bids[i]
                parts.append(f"{b.price}:{b.amount}")
            if i < len(orderbook.asks):
                a = orderbook.asks[i]
                parts.append(f"{a.price}:{a.amount}")
        computed = zlib.crc32(":".join(parts).encode()) & 0xFFFFFFFF
        if computed != (expected & 0xFFFFFFFF):
            logger.warning(
                "Binance orderbook checksum mismatch for %s: computed=%d expected=%d",
                orderbook.symbol,
                computed,
                expected,
            )

    async def _rest_place_order(self, order: Order) -> Trade:
        side = "BUY" if order.side == OrderSide.BUY else "SELL"
        order_type = "LIMIT" if order.order_type == OrderType.LIMIT else "MARKET"

        params: dict[str, Any] = {
            "symbol": _symbol_to_binance(order.symbol),
            "side": side,
            "type": order_type,
        }
        if order.order_type == OrderType.LIMIT:
            params["quantity"] = str(order.amount)
            if order.price is not None:
                params["price"] = str(order.price)
            params["timeInForce"] = "GTC"
        else:
            # MARKET order: use quoteOrderQty (USD) for BUY to avoid LOT_SIZE issues
            # For SELL, must use quantity (base asset)
            if side == "BUY" and order.price and order.price > 0:
                quote_qty = order.amount * order.price
                params["quoteOrderQty"] = str(round(float(quote_qty), 2))
            else:
                params["quantity"] = str(order.amount)
        if order.client_order_id:
            params["newClientOrderId"] = order.client_order_id

        raw = await self._signed_request("POST", "/api/v3/order", params=params)

        trade_id = str(raw.get("orderId", ""))
        filled_qty = Decimal(str(raw.get("executedQty", order.amount)))
        fill_price = Decimal(str(raw.get("price", order.price or "0")))
        fee = Decimal("0")
        fee_currency: str | None = None
        if raw.get("fills"):
            fill = raw["fills"][0]
            fee = Decimal(str(fill.get("commission", "0")))
            fee_currency = fill.get("commissionAsset")

        return self._build_trade(
            order=order,
            trade_id=trade_id,
            price=fill_price,
            amount=filled_qty,
            fee=fee,
            fee_currency=fee_currency,
        )

    async def _rest_cancel_order(self, order_id: str, symbol: str | None) -> bool:
        if not symbol:
            raise ValueError("Binance cancel_order requires symbol")
        await self._signed_request(
            "DELETE",
            "/api/v3/order",
            params={"symbol": _symbol_to_binance(symbol), "orderId": order_id},
        )
        return True

    async def _rest_cancel_all_orders(self, symbol: str | None) -> int:
        if not symbol:
            raise ValueError("Binance cancel_all_orders requires symbol")
        raw = await self._signed_request(
            "DELETE",
            "/api/v3/openOrders",
            params={"symbol": _symbol_to_binance(symbol)},
        )
        return len(raw) if isinstance(raw, list) else 0

    async def _rest_get_balances(self) -> dict[str, Balance]:
        raw = await self._signed_request("GET", "/api/v3/account")
        balances: dict[str, Balance] = {}
        for asset in raw.get("balances", []):
            free = Decimal(asset["free"])
            locked = Decimal(asset["locked"])
            total = free + locked
            if total > 0:
                balances[asset["asset"]] = Balance(
                    currency=asset["asset"],
                    free=free,
                    used=locked,
                    total=total,
                )
        return balances

    async def _rest_get_positions(self) -> list[Position]:
        if self._market_type != "futures":
            return []
        raw = await self._signed_request("GET", "/fapi/v2/positionRisk")
        positions: list[Position] = []
        for pos in raw:
            size = Decimal(pos.get("positionAmt", "0"))
            if size == 0:
                continue
            positions.append(
                Position(
                    exchange_id=self.exchange_id,
                    symbol=_symbol_from_binance(pos["symbol"]),
                    size=size,
                    entry_price=Decimal(pos.get("entryPrice", "0")),
                    mark_price=Decimal(pos.get("markPrice", "0")),
                    unrealized_pnl=Decimal(pos.get("unRealizedProfit", "0")),
                    leverage=int(pos.get("leverage", 1)),
                )
            )
        return positions

    async def place_ioc_limit(
        self, symbol: str, side: str, price: Decimal, size: Decimal
    ) -> OrderResult:
        """Submit an IOC limit order and return fill result (partial fills allowed)."""
        if price <= Decimal("0") or size <= Decimal("0"):
            raise ValueError(f"IOC price/size must be positive: price={price}, size={size}")
        import time
        start = time.monotonic()
        params: dict = {
            "symbol": _symbol_to_binance(symbol),
            "side": side.upper(),
            "type": "LIMIT",
            "timeInForce": "IOC",
            "price": str(price),
            "quantity": str(size),
        }
        raw = await self._signed_request("POST", "/api/v3/order", params=params)
        filled_qty = Decimal(str(raw.get("executedQty", "0")))
        # Compute avg price from fills if available, otherwise fallback to requested price
        fills = raw.get("fills", [])
        if fills and filled_qty > 0:
            total_cost = sum(
                Decimal(str(f["price"])) * Decimal(str(f["qty"])) for f in fills
            )
            avg_price = total_cost / filled_qty
        else:
            avg_price = price
        return OrderResult(
            filled_size=filled_qty,
            avg_price=avg_price,
            order_type="ioc_limit",
            latency_ms=(time.monotonic() - start) * 1000,
        )

    async def _rest_get_fee_rate(self, symbol: str) -> FeeRate:
        raw = await self._signed_request("GET", "/api/v3/account")
        # Binance returns basis points (e.g., 10 = 0.10%)
        maker = Decimal(str(raw.get("makerCommission", 10))) / Decimal("10000")
        taker = Decimal(str(raw.get("takerCommission", 10))) / Decimal("10000")
        return FeeRate(
            maker=maker,
            taker=taker,
            symbol=symbol,
            exchange_id=self.exchange_id,
        )
