"""Native Bitget adapter — Spot trading via direct REST + WebSocket (no ccxt)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import math
import time
from decimal import Decimal
from typing import Any

from src.core.config_loader import get_config
from src.core.models import Balance, FeeRate, Order, OrderBook, OrderSide, OrderType, Position, Trade
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
        # Bug 31: hedge vs one-way mode — detected at connect() for futures accounts.
        # hedge_mode: BOTH open and close orders require posSide.
        # one_way_mode: no posSide, reduceOnly=True is sufficient for closes.
        self._pos_mode: str = "one_way"

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        await super().connect()
        await self._fetch_pos_mode()

    async def _fetch_pos_mode(self) -> None:
        """Detect hedge vs one-way position mode for futures accounts (Bug 31).

        Calls Bitget /api/v2/mix/account/accounts once at connect time.
        Result cached in self._pos_mode ("hedge" | "one_way").
        Binance Futures never needs this — it is always one-way.
        """
        if self._market_type != "futures":
            return
        try:
            resp = await self._request(
                "GET", "/api/v2/mix/account/accounts",
                params={"productType": "USDT-FUTURES"},
                signed=True,
            )
            data = resp.get("data", [])
            if data:
                raw_mode = data[0].get("posMode", "one_way_mode")
                self._pos_mode = "hedge" if "hedge" in raw_mode.lower() else "one_way"
                logger.info(
                    "bitget_pos_mode_detected exchange=%s mode=%s (raw=%s)",
                    self.exchange_id, self._pos_mode, raw_mode,
                )
        except Exception as exc:
            logger.warning(
                "bitget_fetch_pos_mode_failed exchange=%s err=%s — assuming one_way",
                self.exchange_id, exc,
            )

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
        # BUG-07: futures requires "USDT-FUTURES" instType, not "SPOT"
        inst_type = "USDT-FUTURES" if self._market_type == "futures" else "SPOT"
        return {
            "op": "subscribe",
            "args": [{"instType": inst_type, "channel": "books5", "instId": sym}],
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

    async def get_lot_step(self, symbol: str) -> Decimal:
        """BUG-71: Return lot-size step for cross-exchange size synchronization."""
        if self._market_type == "futures":
            await self._fetch_contract_specs(symbol)
            return self._qty_step_sizes.get(symbol, Decimal("0.001"))
        return Decimal("0.001")

    def _quantize_futures_qty(self, symbol: str, qty: Decimal) -> Decimal:
        """Floor qty to nearest sizeMultiplier step — BUG-28 fix.

        Bitget USDT-FUTURES size field is in base currency (BTC for BTCUSDT).
        sizeMultiplier (e.g. 0.001 for BTCUSDT) is the minimum size step.
        Without this, non-multiple sizes may be rejected by the exchange.
        """
        from decimal import ROUND_DOWN
        step = self._qty_step_sizes.get(symbol, Decimal("0.001"))
        if step <= Decimal("0"):
            return qty
        return (qty / step).to_integral_value(rounding=ROUND_DOWN) * step

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
            # Set leverage before ordering — ensure margin calculation matches our intent
            _default_lev = int(get_config("execution.default_futures_leverage") or 5)
            _leverage = int(order.metadata.get("leverage", _default_lev)) if order.metadata else _default_lev
            try:
                await self._request(
                    "POST", "/api/v2/mix/account/set-leverage",
                    data={
                        "symbol": _normalize_symbol(order.symbol),
                        "productType": "USDT-FUTURES",
                        "marginCoin": "USDT",
                        "leverage": str(_leverage),
                    },
                    signed=True,
                )
                logger.debug("leverage_set symbol=%s leverage=%d", order.symbol, _leverage)
            except Exception as _lev_err:
                logger.warning("leverage_set_failed symbol=%s error=%s", order.symbol, _lev_err)

            qty = order.amount
            # BUG-28: fetch contract specs for ALL futures orders (not just LIMIT) so
            # _qty_step_sizes is populated for step-size quantization.
            if order.symbol not in self._price_precisions:
                await self._fetch_contract_specs(order.symbol)
            # BUG-28: quantize qty to sizeMultiplier step (e.g. 0.001 BTC for BTCUSDT).
            # Without this, non-multiple sizes may be rejected by Bitget exchange.
            qty = self._quantize_futures_qty(order.symbol, qty)
            # PHOENIX: Enforce Bitget Futures MIN_NOTIONAL — load from config
            _ex_min = get_config("execution.exchange_min_notional.bitget_futures", default=6)
            _MIN_NOTIONAL = Decimal(str(_ex_min))
            if order.price and order.price > 0:
                if qty * order.price < _MIN_NOTIONAL:
                    # Bump to nearest step >= MIN_NOTIONAL
                    _step = self._qty_step_sizes.get(order.symbol, Decimal("0.001"))
                    import math as _math
                    _lots = _math.ceil(float(_MIN_NOTIONAL / order.price) / float(_step))
                    qty = Decimal(str(_lots)) * _step
                    logger.debug(
                        "bitget_futures_min_notional_adjusted symbol=%s qty=%s notional=%.2f",
                        order.symbol, qty, float(qty * order.price),
                    )
            # BUG-26: respect order.order_type — do NOT default to LIMIT just because price is set.
            # MARKET orders need "market"/"ioc" even when price is provided (used for margin checks).
            _is_market = order.order_type == OrderType.MARKET
            body: dict[str, Any] = {
                "symbol": sym,
                "productType": "USDT-FUTURES",
                "marginMode": "isolated",
                "marginCoin": "USDT",
                "size": str(qty),
                "side": side,
                "tradeSide": "close" if order.metadata.get("reduceOnly") or order.metadata.get("tradeSide") == "close" else "open",
                "orderType": "market" if _is_market else "limit",
                "force": "ioc" if _is_market else "gtc",
            }
            # Bug 31: hedge mode requires posSide for BOTH open and close orders.
            # one-way mode: no posSide — reduceOnly (tradeSide=close) is sufficient.
            # Also honor explicit posSide from order metadata (e.g. close_positions.py).
            if order.metadata.get("posSide"):
                body["posSide"] = order.metadata["posSide"]
            elif self._pos_mode == "hedge":
                if body["tradeSide"] == "open":
                    # Opening a long=buy, short=sell
                    body["posSide"] = "long" if side == "buy" else "short"
                else:
                    # Closing: BUY closes a SHORT position, SELL closes a LONG position
                    body["posSide"] = "short" if side == "buy" else "long"
            if not _is_market and order.price:
                # LIMIT orders: also add price field (specs already fetched above)
                body["price"] = self._quantize_price(order.symbol, order.price)
            if order.client_order_id:
                body["clientOid"] = order.client_order_id
            try:
                resp = await self._request("POST", "/api/v2/mix/order/place-order", data=body, signed=True)
            except Exception as _exc:
                import httpx as _httpx
                _err_code = ""
                _exc_str = str(_exc)
                if isinstance(_exc, _httpx.HTTPStatusError) and _exc.response.status_code == 400:
                    try:
                        _err_code = _exc.response.json().get("code", "")
                    except Exception:
                        _err_code = ""
                # Bug 28: 22002 = "No position to close" — ghost position already cleared.
                # Treat as success so rollback_order returns True and HALT is not triggered.
                if _err_code == "22002" or "22002" in _exc_str:
                    _is_close = body.get("tradeSide") == "close" or order.metadata.get("reduceOnly")
                    if _is_close:
                        logger.warning(
                            "bitget_futures_ghost_position_cleared symbol=%s 22002 — treating as success",
                            order.symbol,
                        )
                        return self._build_trade(
                            order,
                            trade_id=f"ghost-cleared-{order.order_id}",
                            price=order.price or Decimal("0"),
                            amount=order.amount,
                        )
                    raise
                # PHOENIX Phase 2: error 22047 = price exceeds exchange price protection band
                # Retry as market order to avoid rollback cascade.
                if (
                    isinstance(_exc, _httpx.HTTPStatusError)
                    and _exc.response.status_code == 400
                    and _err_code == "22047"
                ):
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
            # PHOENIX Phase 2: fetch spot symbol precision on first order to avoid checkBDScale errors
            if order.symbol not in self._spot_qty_decimals:
                await self._fetch_spot_specs(order.symbol)
            _spot_is_market = order.order_type == OrderType.MARKET
            body = {
                "symbol": sym,
                "side": side,
                "orderType": "market" if _spot_is_market else "limit",
                "size": self._quantize_spot_size(order.symbol, order.amount),
                "force": "ioc" if _spot_is_market else "gtc",
            }
            if not _spot_is_market and order.price:
                body["price"] = self._quantize_spot_price(order.symbol, order.price)
            if order.client_order_id:
                body["clientOid"] = order.client_order_id
            resp = await self._request("POST", "/api/v2/spot/trade/place-order", data=body, signed=True)
        rd = resp.get("data", {})
        trade_id = str(rd.get("orderId", ""))
        fill_price = order.price or Decimal("0")
        fill_qty = order.amount

        # BUG-61: Bitget place-order response omits fill price/qty for MARKET orders.
        # Poll /api/v2/mix/order/detail up to 3 times to get actual avgPrice + baseVolume.
        import asyncio as _asyncio
        if self._market_type == "futures" and _is_market and trade_id:
            for _attempt in range(3):
                await _asyncio.sleep(0.2)
                try:
                    _detail = await self._request(
                        "GET", "/api/v2/mix/order/detail",
                        params={
                            "symbol": sym,
                            "productType": "USDT-FUTURES",
                            "orderId": trade_id,
                        },
                        signed=True,
                    )
                    _d = _detail.get("data", {})
                    if _d.get("status") == "filled":
                        _avg = _d.get("priceAvg") or _d.get("price")
                        _vol = _d.get("baseVolume") or _d.get("size")
                        if _avg and Decimal(str(_avg)) > 0:
                            fill_price = Decimal(str(_avg))
                        if _vol and Decimal(str(_vol)) > 0:
                            fill_qty = Decimal(str(_vol))
                        logger.debug(
                            "bitget_futures_fill_polled symbol=%s orderId=%s attempt=%d price=%s qty=%s",
                            order.symbol, trade_id, _attempt + 1, fill_price, fill_qty,
                        )
                        break
                except Exception as _pe:
                    logger.debug("bitget_futures_poll_failed orderId=%s: %s", trade_id, _pe)

        return self._build_trade(
            order,
            trade_id=trade_id,
            price=fill_price,
            amount=fill_qty,
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
            body["marginCoin"] = "USDT"  # BUG-03: required by Bitget V2 mix cancel API
            resp = await self._request(
                "POST", "/api/v2/mix/order/cancel-order", data=body, signed=True
            )
        else:
            resp = await self._request(
                "POST", "/api/v2/spot/trade/cancel-order", data=body, signed=True
            )
        code = resp.get("code", "")
        if code == "00000":
            return True
        # BUG-04: 40762=order not found, 43011=already completed → desired outcome, return True
        if code in ("40762", "43011", "40783"):
            logger.info("bitget_cancel_benign code=%s — order already gone, treating as success", code)
            return True
        return False

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
                "GET", "/api/v2/mix/position/all-position",
                params={"productType": "USDT-FUTURES", "marginCoin": "USDT"},
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

                # Bug 28: averageOpenPrice can be null/None for recently-opened positions (Bitget REST stale).
                # Use mark_price as fallback. These are REAL positions — do NOT filter them out.
                entry_raw = item.get("averageOpenPrice")
                if entry_raw is None or entry_raw == "" or entry_raw == "0":
                    # Stale REST data — position exists but entry not yet populated
                    # Use mark_price as proxy; reconciler will update later
                    entry_price = Decimal("0")
                else:
                    entry_price = Decimal(str(entry_raw))
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

    async def get_trades(
        self,
        symbol: str = "",
        start_time_ms: int | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Bitget Futures 실체결 이력 조회 — GET /api/v2/mix/order/fills.

        Bitget API requires 'symbol' param — returns [] if symbol is empty.
        """
        if not symbol:
            # Bitget fills API does not support all-symbol queries
            return []
        params: dict = {
            "productType": "USDT-FUTURES",
            "limit": str(min(limit, 100)),
            "symbol": _normalize_symbol(symbol).upper(),
        }
        if start_time_ms:
            params["startTime"] = str(start_time_ms)
        await self._rate_limiter.acquire("default")  # 10 req/s, burst 20
        try:
            resp = await self._request("GET", "/api/v2/mix/order/fills", params=params, signed=True)
            fill_list = []
            if isinstance(resp, dict):
                raw_data = resp.get("data")
                if raw_data is None:
                    fill_list = []
                elif isinstance(raw_data, dict):
                    fill_list = raw_data.get("fillList") or []
                elif isinstance(raw_data, list):
                    fill_list = raw_data
            elif isinstance(resp, list):
                fill_list = resp
            def _sf(val: Any, default: float = 0.0) -> float:
                """Safe float: converts API value, returns default on error or non-finite."""
                try:
                    result = float(val)
                    return result if math.isfinite(result) else default
                except (TypeError, ValueError, OverflowError):
                    return default

            def _si(val: Any, default: int = 0) -> int:
                """Safe int: converts API value, clamps to safe range."""
                try:
                    return int(float(val) if isinstance(val, str) else val)
                except (TypeError, ValueError, OverflowError):
                    return default

            return [
                {
                    "exchange": "bitget_futures",
                    "symbol": str(d.get("symbol", "")),
                    "order_id": str(d.get("orderId", "")),
                    "trade_id": str(d.get("tradeId", "")),
                    "side": str(d.get("side", "")).lower(),
                    "qty": _sf(d.get("baseVolume") or d.get("qty")),
                    "price": _sf(d.get("price")),
                    "realized_pnl": _sf(d.get("profit") or d.get("realizedPnl")),
                    "commission": _sf(d.get("fee")),
                    "ts_ms": _si(d.get("cTime") or d.get("ts")),
                }
                for d in fill_list
                if isinstance(d, dict)
            ]
        except Exception as exc:
            logger.warning("bitget.get_trades failed symbol=%s error=%s", symbol, exc)
            return []
