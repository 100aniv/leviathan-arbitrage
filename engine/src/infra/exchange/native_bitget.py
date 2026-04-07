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

from src.core.config_loader import get_config
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


def _denormalize_symbol(symbol: str) -> str:
    """'BTCUSDT' -> 'BTC/USDT' (best-effort: assumes USDT quote)."""
    if "/" in symbol:
        return symbol
    for quote in ("USDT", "USDC", "BTC", "ETH", "BNB"):
        if symbol.endswith(quote):
            base = symbol[: -len(quote)]
            return f"{base}/{quote}"
    return symbol


class NativeBitgetAdapter(NativeAdapter):
    """Native Bitget spot adapter — direct HTTP/WebSocket, no ccxt."""

    def __init__(self, exchange_id: str = "bitget", **kwargs: Any) -> None:
        kwargs.setdefault("rate_limits", _BITGET_RATE_LIMITS)
        super().__init__(exchange_id=exchange_id, **kwargs)
        self._market_type: str = "spot"  # set to "futures" by create_native_adapter
        self._price_precisions: dict[str, int] = {}  # symbol → decimal places (futures)
        self._qty_step_sizes: dict[str, Decimal] = {}  # symbol → step size (futures)
        self._spot_qty_decimals: dict[str, int] = {}  # symbol → base qty decimal places (spot)
        self._spot_price_decimals: dict[str, int] = {}  # symbol → price decimal places (spot)

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

    async def _fetch_contract_specs(self, symbol: str) -> None:
        """Fetch and cache price/qty precision for a futures symbol."""
        if symbol in self._price_precisions:
            return
        try:
            sym = _normalize_symbol(symbol)
            resp = await self._request(
                "GET",
                "/api/v2/mix/market/contracts",
                params={"symbol": sym, "productType": "USDT-FUTURES"},
            )
            for contract in resp.get("data", []):
                if contract.get("symbol") == sym:
                    self._price_precisions[symbol] = int(contract.get("pricePlace", 6))
                    self._qty_step_sizes[symbol] = Decimal(str(contract.get("sizeMultiplier", "0.0001")))
                    return
            # Fallback: infer from price magnitude
            self._price_precisions[symbol] = 6
        except Exception as exc:
            logger.debug("bitget_contract_specs_fetch_failed symbol=%s: %s", symbol, exc)
            self._price_precisions[symbol] = 6  # safe default

    def _quantize_price(self, symbol: str, price: Decimal) -> str:
        decimals = self._price_precisions.get(symbol, 6)
        quantizer = Decimal(10) ** (-decimals)
        return str(price.quantize(quantizer))

    async def _fetch_spot_specs(self, symbol: str) -> None:
        """Fetch and cache base qty / price decimal places for a spot symbol."""
        if symbol in self._spot_qty_decimals:
            return
        try:
            sym = _normalize_symbol(symbol)
            resp = await self._request(
                "GET",
                "/api/v2/spot/public/symbols",
                params={"symbol": sym},
            )
            for info in resp.get("data", []):
                if info.get("symbol") == sym:
                    self._spot_qty_decimals[symbol] = int(info.get("quantityPrecision", info.get("basePrecision", 6)))
                    self._spot_price_decimals[symbol] = int(info.get("pricePrecision", info.get("quotePrecision", 6)))
                    return
            self._spot_qty_decimals[symbol] = 6  # safe default
            self._spot_price_decimals[symbol] = 6
        except Exception as exc:
            logger.debug("bitget_spot_specs_fetch_failed symbol=%s: %s", symbol, exc)
            self._spot_qty_decimals[symbol] = 6
            self._spot_price_decimals[symbol] = 6

    def _quantize_spot_size(self, symbol: str, size: Decimal) -> str:
        decimals = self._spot_qty_decimals.get(symbol, 6)
        quantizer = Decimal(10) ** (-decimals)
        # ROUND_DOWN: never exceed available balance / signal size
        from decimal import ROUND_DOWN
        return str(size.quantize(quantizer, rounding=ROUND_DOWN))

    def _quantize_spot_price(self, symbol: str, price: Decimal) -> str:
        decimals = self._spot_price_decimals.get(symbol, 6)
        quantizer = Decimal(10) ** (-decimals)
        return str(price.quantize(quantizer))

    async def _rest_place_order(self, order: Order) -> Trade:
        sym = _normalize_symbol(order.symbol)
        side = "buy" if order.side == OrderSide.BUY else "sell"

        if self._market_type == "futures":
            qty = order.amount
            # PHOENIX: Enforce Bitget Futures MIN_NOTIONAL — load from config
            _ex_min = get_config("execution.exchange_min_notional.bitget_futures", default=6)
            _MIN_NOTIONAL = Decimal(str(_ex_min))
            if order.price and order.price > 0:
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
                "tradeSide": "close" if order.metadata.get("reduceOnly") or order.metadata.get("tradeSide") == "close" else "open",
                "orderType": "limit" if order.price else "market",
                "force": "gtc",
            }
            # hedge_mode: posSide must match the position being closed
            # BUY+close = closing SHORT posSide, SELL+close = closing LONG posSide
            if body["tradeSide"] == "close":
                body["posSide"] = "short" if side == "buy" else "long"
            if order.price:
                # PHOENIX: Fetch contract specs on first order for this symbol
                if order.symbol not in self._price_precisions:
                    await self._fetch_contract_specs(order.symbol)
                body["price"] = self._quantize_price(order.symbol, order.price)
            if order.client_order_id:
                body["clientOid"] = order.client_order_id
            try:
                resp = await self._request("POST", "/api/v2/mix/order/place-order", data=body, signed=True)
            except Exception as _exc:
                # PHOENIX Phase 2: error 22047 = price exceeds exchange price protection band
                # Retry as market order to avoid rollback cascade.
                import httpx as _httpx
                if (
                    isinstance(_exc, _httpx.HTTPStatusError)
                    and _exc.response.status_code == 400
                ):
                    try:
                        _err_code = _exc.response.json().get("code", "")
                    except Exception:
                        _err_code = ""
                    if _err_code == "22047":
                        logger.warning(
                            "bitget_futures_price_limit_exceeded symbol=%s — retrying as market",
                            order.symbol,
                        )
                        body["orderType"] = "market"
                        body.pop("price", None)
                        resp = await self._request(
                            "POST", "/api/v2/mix/order/place-order", data=body, signed=True
                        )
                    else:
                        raise
                else:
                    raise
        else:
            # PHOENIX Phase 2: fetch spot symbol precision on first order to avoid checkBDScale errors
            if order.symbol not in self._spot_qty_decimals:
                await self._fetch_spot_specs(order.symbol)
            body = {
                "symbol": sym,
                "side": side,
                "orderType": "limit" if order.price else "market",
                "size": self._quantize_spot_size(order.symbol, order.amount),
                "force": "gtc",
            }
            if order.price:
                body["price"] = self._quantize_spot_price(order.symbol, order.price)
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
        if self._market_type == "futures":
            body: dict[str, Any] = {"productType": "USDT-FUTURES"}
            if symbol:
                body["symbol"] = _normalize_symbol(symbol)
            resp = await self._request(
                "POST", "/api/v2/mix/order/cancel-all-orders", data=body, signed=True
            )
            # Response: {"data": {"successList": [...], "failureList": [...]}}
            data = resp.get("data", {})
            if isinstance(data, dict):
                return len(data.get("successList", []))
            return 0
        body = {}
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
        if self._market_type != "futures":
            return []
        try:
            resp = await self._request(
                "GET", "/api/v2/mix/position/all-position",  # PHOENIX: allPosition→all-position (correct v2 endpoint)
                params={"productType": "USDT-FUTURES"},
                signed=True,
            )
            positions = []
            for item in resp.get("data", []):
                symbol_raw = item.get("symbol", "")
                symbol = _denormalize_symbol(symbol_raw)
                hold_side = item.get("holdSide", "long")
                total = Decimal(str(item.get("total", "0")))
                if total == 0:
                    continue
                size = total if hold_side == "long" else -total
                entry_price = Decimal(str(item.get("averageOpenPrice", "0")))
                unrealized_pnl = Decimal(str(item.get("unrealizedPL", "0")))
                mark_price_str = item.get("markPrice", item.get("averageOpenPrice", "0"))
                mark_price = Decimal(str(mark_price_str))
                positions.append(Position(
                    exchange_id=self.exchange_id,
                    symbol=symbol,
                    size=size,
                    entry_price=entry_price,
                    mark_price=mark_price,
                    unrealized_pnl=unrealized_pnl,
                    leverage=int(item.get("leverage", 1)),
                ))
            return positions
        except Exception as exc:
            logger.warning("bitget_get_positions_failed: %s", exc)
            return []

    async def _rest_get_fee_rate(self, symbol: str) -> FeeRate:
        return FeeRate(
            maker=Decimal("0.001"),
            taker=Decimal("0.001"),
            symbol=symbol,
            exchange_id=self.exchange_id,
        )
