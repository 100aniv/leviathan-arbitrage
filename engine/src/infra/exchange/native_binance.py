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
_REST_FUTURES_URL = "https://fapi.binance.com"
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
        self._step_sizes: dict[str, Decimal] = {}  # PHOENIX: LOT_SIZE cache (symbol → stepSize)

    # ------------------------------------------------------------------
    # URL / header overrides
    # ------------------------------------------------------------------

    def _rest_base_url(self) -> str:
        if self._sandbox:
            return _REST_SANDBOX_URL
        if self._market_type == "futures":
            return _REST_FUTURES_URL
        return _REST_URL

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
        path = "/fapi/v1/depth" if self._market_type == "futures" else "/api/v3/depth"
        raw = await self._request(
            "GET",
            path,
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

    async def _get_lot_step(self, symbol: str) -> Decimal:
        """Fetch and cache LOT_SIZE stepSize for futures symbol."""
        binance_sym = _symbol_to_binance(symbol)
        if binance_sym not in self._step_sizes:
            try:
                path = "/fapi/v1/exchangeInfo"
                await self._rate_limiter.acquire("default")  # BUG-02: rate limit exchangeInfo
                resp = await self._http.get(path, params={"symbol": binance_sym})
                resp.raise_for_status()
                info = resp.json()
                for s in info.get("symbols", []):
                    if s["symbol"] == binance_sym:
                        for f in s.get("filters", []):
                            if f["filterType"] == "LOT_SIZE":
                                self._step_sizes[binance_sym] = Decimal(str(f["stepSize"]))
            except Exception as exc:
                logger.debug("lot_size_fetch_failed symbol=%s: %s", symbol, exc)
            if binance_sym not in self._step_sizes:
                self._step_sizes[binance_sym] = Decimal("0.001")  # safe default
        return self._step_sizes[binance_sym]

    async def _get_spot_lot_step(self, symbol: str) -> Decimal:
        """Fetch and cache LOT_SIZE stepSize for spot symbol."""
        binance_sym = _symbol_to_binance(symbol)
        cache_key = f"spot_{binance_sym}"
        if cache_key not in self._step_sizes:
            try:
                path = "/api/v3/exchangeInfo"
                await self._rate_limiter.acquire("default")  # BUG-02: rate limit exchangeInfo
                resp = await self._http.get(path, params={"symbol": binance_sym})
                resp.raise_for_status()
                info = resp.json()
                for s in info.get("symbols", []):
                    if s["symbol"] == binance_sym:
                        for f in s.get("filters", []):
                            if f["filterType"] == "LOT_SIZE":
                                self._step_sizes[cache_key] = Decimal(str(f["stepSize"]))
            except Exception as exc:
                logger.debug("spot_lot_size_fetch_failed symbol=%s: %s", symbol, exc)
            if cache_key not in self._step_sizes:
                self._step_sizes[cache_key] = Decimal("0.00000001")  # safe default
        return self._step_sizes[cache_key]

    def _quantize_qty(self, qty: Decimal, step: Decimal) -> Decimal:
        """Floor qty to nearest step_size multiple (avoid LOT_SIZE 400 errors)."""
        if step <= 0:
            return qty
        return (qty // step) * step

    async def _rest_place_order(self, order: Order) -> Trade:
        side = "BUY" if order.side == OrderSide.BUY else "SELL"
        order_type = "LIMIT" if order.order_type == OrderType.LIMIT else "MARKET"

        # Set marginType=ISOLATED + leverage before ordering (futures only)
        # Production pattern: marginType → leverage → order (per-symbol, idempotent)
        if self._market_type == "futures":
            from src.core.config_loader import get_config as _get_config
            _default_lev = int(_get_config("execution.default_futures_leverage") or 5)
            _leverage = int(order.metadata.get("leverage", _default_lev)) if order.metadata else _default_lev
            _sym_bn = _symbol_to_binance(order.symbol)
            # Step 1: Set ISOLATED margin type
            # Note: -4046/-4048/-4168 benign codes are handled silently in _request() (returns {})
            try:
                await self._signed_request("POST", "/fapi/v1/marginType", params={
                    "symbol": _sym_bn,
                    "marginType": "ISOLATED",
                })
                logger.debug("margin_type_set symbol=%s type=ISOLATED", order.symbol)
            except Exception as _mt_err:
                # Extract Binance error code from embedded body (set by native_adapter._request)
                _mt_str = str(_mt_err)
                _mt_code = ""
                if "[body=" in _mt_str:
                    import re as _re
                    _code_m = _re.search(r"'code':\s*(-?\d+)", _mt_str)
                    _mt_code = _code_m.group(1) if _code_m else ""
                # -4059: MARGIN_TYPE_IS_NOT_SUPPORTED (symbol only supports CROSS) — non-fatal
                # -4046: already set to ISOLATED — non-fatal
                # -4048: already set to CROSS — non-fatal
                # -4168: Multi-Assets Mode active — marginType change not allowed, order proceeds
                if _mt_code in ("-4059", "-4046", "-4048"):
                    logger.info(
                        "margin_type_not_supported symbol=%s code=%s — CROSS margin 유지, 주문 계속",
                        order.symbol, _mt_code,
                    )
                elif _mt_code == "-4168":
                    logger.info(
                        "binance_multi_assets_mode symbol=%s — marginType 변경 불필요, 주문 계속",
                        order.symbol,
                    )
                else:
                    logger.warning(
                        "margin_type_set_failed symbol=%s code=%s error=%s",
                        order.symbol, _mt_code or "unknown", _mt_err,
                    )
            # Step 2: Set leverage
            try:
                await self._signed_request("POST", "/fapi/v1/leverage", params={
                    "symbol": _sym_bn,
                    "leverage": str(_leverage),
                })
                logger.debug("leverage_set symbol=%s leverage=%d", order.symbol, _leverage)
            except Exception as _lev_err:
                logger.warning("leverage_set_failed symbol=%s error=%s", order.symbol, _lev_err)

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
            # MARKET order
            # Futures: always use quantity (base asset) — quoteOrderQty not supported on fapi
            # Spot BUY: use quoteOrderQty (USD) to avoid LOT_SIZE issues
            if side == "BUY" and order.price and order.price > 0 and self._market_type != "futures":
                quote_qty = order.amount * order.price
                params["quoteOrderQty"] = str(round(float(quote_qty), 2))
            else:
                qty = order.amount
                if self._market_type == "futures":
                    step = await self._get_lot_step(order.symbol)
                    qty = self._quantize_qty(qty, step)
                    # PHOENIX: Enforce Binance Futures MIN_NOTIONAL ($5) — use $6 safety buffer
                    if order.price and order.price > 0:
                        _MIN_NOTIONAL = Decimal("6")
                        while qty * order.price < _MIN_NOTIONAL and step > 0:
                            qty = qty + step
                        logger.debug(
                            "futures_qty_adjusted symbol=%s qty=%s notional=%.2f",
                            order.symbol, qty, float(qty * order.price),
                        )
                else:
                    # Spot SELL: quantize to LOT_SIZE to avoid decimal precision / LOT_SIZE errors
                    step = await self._get_spot_lot_step(order.symbol)
                    qty = self._quantize_qty(qty, step)
                params["quantity"] = str(qty)
        if order.client_order_id:
            params["newClientOrderId"] = order.client_order_id

        # Bug 25c: Honor reduceOnly for futures position rollback/close orders.
        # Without this, unwind orders open a new position instead of closing one.
        if self._market_type == "futures" and order.metadata.get("reduceOnly"):
            params["reduceOnly"] = "true"

        import asyncio as _asyncio
        path = "/fapi/v1/order" if self._market_type == "futures" else "/api/v3/order"
        try:
            raw = await self._signed_request("POST", path, params=params)
        except Exception as _place_exc:
            # BUG-43: -2022 "ReduceOnly Order is rejected" — no position to close.
            # This happens when we try to unwind an exit fill that already closed the position.
            # Treat as benign no-op: the position is already resolved.
            if "-2022" in str(_place_exc) and order.metadata.get("reduceOnly"):
                # -2022 "ReduceOnly Order is rejected" = no position to close.
                # The desired outcome (position = 0) is already achieved.
                # Return full amount so the executor treats this as a successful close,
                # consistent with Bitget 22002 handling (ghost-cleared).
                logger.info(
                    "binance_reduce_only_rejected_no_position symbol=%s side=%s — "
                    "position already closed, treating as ghost-cleared success",
                    order.symbol, order.side,
                )
                return self._build_trade(
                    order=order,
                    trade_id=f"ghost-cleared-{order.order_id}",
                    price=order.price or Decimal("0"),
                    amount=order.amount,
                )
            raise

        trade_id = str(raw.get("orderId", ""))

        # PHOENIX: Binance Futures MARKET orders return status="NEW" executedQty="0" initially.
        # Poll order status up to 3 times (200ms each) until status="FILLED".
        if (
            self._market_type == "futures"
            and order.order_type == OrderType.MARKET
            and raw.get("status") in ("NEW", None)
            and trade_id
        ):
            for _attempt in range(3):
                await _asyncio.sleep(0.2)
                try:
                    _qp = {"symbol": _symbol_to_binance(order.symbol), "orderId": trade_id}
                    raw = await self._signed_request("GET", path, params=_qp)
                    if raw.get("status") == "FILLED":
                        logger.debug(
                            "futures_market_fill_polled symbol=%s orderId=%s attempt=%d",
                            order.symbol, trade_id, _attempt + 1,
                        )
                        break
                except Exception as _pe:
                    logger.debug("futures_poll_failed orderId=%s: %s", trade_id, _pe)

        filled_qty = Decimal(str(raw.get("executedQty", order.amount)))
        # Futures MARKET orders: fill price is in avgPrice, not price (price=0 for MARKET).
        # BUG-49: Binance returns avgPrice="0.00000" (truthy string) when order not yet filled.
        # Must check numeric value > 0, not string truthiness.
        def _nonzero_price_str(val: str | None) -> str | None:
            if not val:
                return None
            try:
                return val if Decimal(val) > 0 else None
            except Exception:
                return None
        _price_field = (
            _nonzero_price_str(raw.get("avgPrice"))
            or _nonzero_price_str(raw.get("price"))
            or str(order.price or "0")
        )
        fill_price = Decimal(str(_price_field))
        # If still executedQty=0 after polling, fall back to requested amount (async fill confirmed)
        if filled_qty == Decimal("0") and order.order_type == OrderType.MARKET and trade_id:
            filled_qty = order.amount
            logger.warning(
                "futures_executedQty_zero_fallback symbol=%s orderId=%s — using order.amount",
                order.symbol, trade_id,
            )
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
        path = "/fapi/v1/order" if self._market_type == "futures" else "/api/v3/order"
        params: dict[str, Any] = {"symbol": _symbol_to_binance(symbol)}
        # Binance orderId is an integer; UUID strings use origClientOrderId
        try:
            int(order_id)
            params["orderId"] = order_id
        except (ValueError, TypeError):
            params["origClientOrderId"] = order_id
        await self._signed_request("DELETE", path, params=params)
        return True

    async def _rest_cancel_all_orders(self, symbol: str | None) -> int:
        if symbol:
            path = "/fapi/v1/allOpenOrders" if self._market_type == "futures" else "/api/v3/openOrders"
            raw = await self._signed_request(
                "DELETE",
                path,
                params={"symbol": _symbol_to_binance(symbol)},
            )
            return len(raw) if isinstance(raw, list) else 0
        # symbol=None: 전체 취소 — 열린 주문 조회 후 심볼별 DELETE
        if self._market_type != "futures":
            raise ValueError("Binance spot cancel_all_orders requires symbol")
        all_orders = await self._signed_request("GET", "/fapi/v1/openOrders")
        if not all_orders:
            return 0
        syms = {o["symbol"] for o in all_orders if isinstance(o, dict)}
        total = 0
        for sym in syms:
            try:
                raw = await self._signed_request(
                    "DELETE", "/fapi/v1/allOpenOrders", params={"symbol": sym}
                )
                total += len(raw) if isinstance(raw, list) else 0
            except Exception as exc:
                logger.warning("cancel_all_orders_sym_failed symbol=%s error=%s", sym, exc)
        return total

    async def _rest_get_balances(self) -> dict[str, Balance]:
        balances: dict[str, Balance] = {}
        if self._market_type == "futures":
            raw = await self._signed_request("GET", "/fapi/v2/balance")
            for asset in raw:
                total = Decimal(str(asset.get("balance", "0")))
                free = Decimal(str(asset.get("availableBalance", str(total))))
                used = total - free
                if total > 0:
                    balances[asset["asset"]] = Balance(
                        currency=asset["asset"],
                        free=free,
                        used=used,
                        total=total,
                    )
        else:
            raw = await self._signed_request("GET", "/api/v3/account")
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
        path = "/fapi/v1/order" if self._market_type == "futures" else "/api/v3/order"
        raw = await self._signed_request("POST", path, params=params)
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

    async def get_trades(
        self,
        symbol: str = "",
        start_time_ms: int | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Binance Futures 실체결 이력 조회 — GET /fapi/v1/userTrades.

        BUG-63: Binance Futures requires 'symbol' — empty symbol returns 400 error.
        Return [] immediately if no symbol provided (reconciler skips empty-symbol calls).
        """
        if not symbol:
            return []
        params: dict = {"limit": min(limit, 1000)}
        params["symbol"] = _symbol_to_binance(symbol)
        if start_time_ms:
            params["startTime"] = start_time_ms
        await self._rate_limiter.acquire("default")  # weight=5 per call
        try:
            data = await self._signed_request("GET", "/fapi/v1/userTrades", params=params)
            if not isinstance(data, list):
                return []
            return [
                {
                    "exchange": "binance_futures",
                    "symbol": d.get("symbol", ""),
                    "order_id": str(d.get("orderId", "")),
                    "trade_id": str(d.get("id", "")),
                    "side": d.get("side", "BUY").lower(),  # BUG-01: use explicit "side" field, not "buyer" boolean
                    "qty": float(d.get("qty", 0)),
                    "price": float(d.get("price", 0)),
                    "realized_pnl": float(d.get("realizedPnl", 0)),
                    "commission": float(d.get("commission", 0)),
                    "ts_ms": int(d.get("time", 0)),
                }
                for d in data
            ]
        except Exception as exc:
            logger.warning("binance.get_trades failed symbol=%s error=%s", symbol, exc)
            return []
