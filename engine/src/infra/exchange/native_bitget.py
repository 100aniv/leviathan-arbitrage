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
        # BUG-120 Phase 5c: WS trade client (lazy)
        self._ws_trade: Any = None
        # BUG-107: margin mode for USDT-FUTURES orders. Detected at connect() via
        # /api/v2/mix/account/accounts (same endpoint as posMode). Default "crossed"
        # (USDT-M cross-margin is the Bitget default). Can be overridden via config
        # key "execution.bitget_futures_margin_mode" for accounts using isolated mode.
        from src.core.config_loader import get_config as _gc
        self._margin_mode: str = _gc("execution.bitget_futures_margin_mode", default="crossed")
        # BUG-104: map internal UUID order_id → Bitget's numeric orderId for cancel
        self._exchange_order_id_map: dict[str, str] = {}

    async def _get_ws_trade(self) -> Any:
        """Lazy-connect Bitget V2 WS trade client (BUG-120)."""
        if self._ws_trade is None:
            from src.infra.exchange.ws_trade import BitgetWSTrade
            self._ws_trade = BitgetWSTrade(
                self._api_key, self._api_secret, self._passphrase,
            )
            await self._ws_trade.connect()
            logger.info("Bitget WS trade connected + authenticated (%s)", self._market_type)
        return self._ws_trade

    async def _ws_place_order(self, order: Order) -> Trade:
        """Place order via Bitget V2 WS (BUG-120). Raises on failure for REST fallback."""
        from datetime import datetime, timezone
        client = await self._get_ws_trade()
        inst_type = "USDT-FUTURES" if self._market_type == "futures" else "SPOT"
        # Bitget instId is e.g. BTCUSDT (no slash)
        inst_id = order.symbol.replace("/", "")
        side = "buy" if order.side == OrderSide.BUY else "sell"
        otype = "limit" if order.order_type == OrderType.LIMIT else "market"
        # BUG-121/123: Bitget V2 futures WS needs marginMode + marginCoin
        extra_params: dict[str, Any] = {}
        if self._market_type == "futures":
            extra_params["marginMode"] = getattr(self, "_margin_mode", "crossed")
            extra_params["marginCoin"] = "USDT"  # USDT-M futures
        resp = await client.place_order(
            inst_type=inst_type,
            inst_id=inst_id,
            order_type=otype,
            side=side,
            size=order.amount,
            price=order.price if otype == "limit" else None,
            force="gtc",
            **extra_params,
        )
        if resp.get("code") not in (0, "0") and resp.get("event") != "trade":
            raise RuntimeError(f"bitget ws_place_order rejected: {resp}")
        arg = (resp.get("arg") or [{}])[0]
        params = arg.get("params") or {}
        return Trade(
            trade_id=str(params.get("orderId", "")),
            symbol=order.symbol,
            side=order.side,
            amount=order.amount,
            price=order.price or Decimal("0"),
            fee=Decimal("0"),
            timestamp=datetime.now(timezone.utc),
        )

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
                # BUG-107: also detect marginMode from same endpoint.
                # Only override if config hasn't explicitly set it (default="crossed").
                from src.core.config_loader import get_config as _gc2
                _cfg_margin = _gc2("execution.bitget_futures_margin_mode", default=None)
                if _cfg_margin is None:
                    raw_margin = data[0].get("marginMode", "crossed")
                    if raw_margin and raw_margin.lower() in ("crossed", "isolated"):
                        self._margin_mode = raw_margin.lower()
                logger.info(
                    "bitget_pos_mode_detected exchange=%s pos_mode=%s margin_mode=%s (raw_pos=%s)",
                    self.exchange_id, self._pos_mode, self._margin_mode, raw_mode,
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
            # BUG-82: MIN_NOTIONAL ceil bump removed — it desynchronizes cross-exchange
            # leg quantities after executor lot_size_sync. The executor rejects
            # sub-notional trades at executor.py:871 (BUG-71 check).
            # BUG-26: respect order.order_type — do NOT default to LIMIT just because price is set.
            # MARKET orders need "market"/"ioc" even when price is provided (used for margin checks).
            _is_market = order.order_type == OrderType.MARKET
            body: dict[str, Any] = {
                "symbol": sym,
                "productType": "USDT-FUTURES",
                # BUG-107: marginMode detected at connect() via _fetch_pos_mode.
                # Default "crossed"; configurable via "execution.bitget_futures_margin_mode".
                "marginMode": self._margin_mode,
                "marginCoin": "USDT",
                "size": str(qty),
                "side": side,
                "tradeSide": "close" if order.metadata.get("reduceOnly") or order.metadata.get("tradeSide") == "close" else "open",
                "orderType": "market" if _is_market else "limit",
                # BUG-103: Bitget Futures does NOT support force:ioc for market orders.
                # Sending force:ioc with orderType:market causes Bitget to accept the order
                # (returns orderId) but immediately cancel it with 0 fill.
                # Market orders always fill at best available price — use gtc.
                "force": "gtc",
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

            # BUG-NEW: Bitget place-order with tradeSide=close returns 22002 even when
            # position exists. Use /close-positions endpoint for all close orders instead.
            if body.get("tradeSide") == "close":
                _hold_side = "long" if side == "sell" else "short"
                _close_body = {
                    "symbol": sym,
                    "productType": "USDT-FUTURES",
                    "holdSide": _hold_side,
                }
                try:
                    _close_resp = await self._request(
                        "POST", "/api/v2/mix/order/close-positions",
                        data=_close_body, signed=True,
                    )
                    _success = (_close_resp.get("data") or {}).get("successList", [])
                    _order_id = str(_success[0].get("orderId", "")) if _success else f"close-{sym}"
                    logger.info(
                        "bitget_futures_close_positions_ok symbol=%s holdSide=%s orderId=%s",
                        order.symbol, _hold_side, _order_id,
                    )
                    # Retrieve actual fill price for PnL accounting.
                    # close-positions is a market order so order.price=0; query fill history.
                    _close_fill_price = Decimal("0")
                    if _order_id and not _order_id.startswith("close-"):
                        import asyncio as _asyncio_cp
                        for _cp_attempt in range(3):
                            if _cp_attempt > 0:
                                await _asyncio_cp.sleep(0.3)
                            try:
                                _cp_fills_resp = await self._request(
                                    "GET", "/api/v2/mix/order/fills",
                                    params={"symbol": sym, "productType": "USDT-FUTURES", "orderId": _order_id},
                                    signed=True,
                                )
                                _cp_fills = _cp_fills_resp.get("data") or {}
                                if isinstance(_cp_fills, dict):
                                    _cp_fills = _cp_fills.get("fillList") or _cp_fills.get("list") or []
                                if isinstance(_cp_fills, list) and _cp_fills:
                                    _cp_qty = Decimal("0")
                                    _cp_wprice = Decimal("0")
                                    for _cpf in _cp_fills:
                                        _q = Decimal(str(_cpf.get("baseVolume") or _cpf.get("size") or _cpf.get("qty") or "0"))
                                        _p = Decimal(str(_cpf.get("price") or _cpf.get("priceAvg") or "0"))
                                        _cp_qty += _q
                                        _cp_wprice += _q * _p
                                    if _cp_qty > 0:
                                        _close_fill_price = _cp_wprice / _cp_qty
                                        logger.info(
                                            "bitget_futures_close_fill_price_recovered symbol=%s orderId=%s price=%s",
                                            order.symbol, _order_id, _close_fill_price,
                                        )
                                        break
                            except Exception as _cpfe:
                                logger.debug("bitget_futures_close_fill_query_failed attempt=%d: %s", _cp_attempt + 1, _cpfe)
                    return self._build_trade(
                        order,
                        trade_id=_order_id,
                        price=_close_fill_price if _close_fill_price > 0 else (order.price or Decimal("0")),
                        amount=order.amount,
                    )
                except Exception as _close_exc:
                    _close_str = str(_close_exc)
                    if "22002" in _close_str:
                        logger.warning(
                            "bitget_futures_ghost_position_cleared symbol=%s 22002 (close-positions) — treating as success",
                            order.symbol,
                        )
                        return self._build_trade(
                            order,
                            trade_id=f"ghost-cleared-{order.order_id}",
                            price=order.price or Decimal("0"),
                            amount=order.amount,
                        )
                    raise

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
        rd = resp.get("data", {}) or {}
        trade_id = str(rd.get("orderId", ""))
        # BUG-108: validate Bitget soft-error responses.
        # Bitget returns HTTP 200 with error codes in body (e.g. {"code":"40012","data":{}}).
        # Without this check, failed orders appear as success (empty trade_id, fill_qty=0),
        # executor calls on_execution_success(), position is removed from tracking,
        # but the exchange never received the order → position permanently stranded.
        _resp_code = resp.get("code", "")
        if _resp_code and _resp_code != "00000":
            _is_close = body.get("tradeSide") == "close" or (order.metadata or {}).get("reduceOnly")
            # 22002 = "No position to close" — ghost position already gone, treat as success
            if _resp_code == "22002" and _is_close:
                logger.warning(
                    "bitget_futures_ghost_position_cleared symbol=%s 22002(soft) — treating as success",
                    order.symbol,
                )
                return self._build_trade(
                    order,
                    trade_id=f"ghost-cleared-{order.order_id}",
                    price=order.price or Decimal("0"),
                    amount=order.amount,
                )
            raise RuntimeError(
                f"bitget_place_order_failed code={_resp_code} "
                f"msg={resp.get('msg', '')} symbol={order.symbol} "
                f"tradeSide={body.get('tradeSide', '?')}"
            )
        logger.debug(
            "bitget_place_order_resp symbol=%s tradeSide=%s size=%s orderId=%s resp_code=%s",
            order.symbol, body.get("tradeSide", "?"), body.get("size", "?"),
            trade_id, resp.get("code", "?"),
        )
        # BUG-104: store UUID→exchange orderId mapping so rollback can cancel correctly.
        # order.order_id is our internal UUID; trade_id is Bitget's numeric orderId.
        # _rest_cancel_order resolves UUID → exchange orderId before calling cancel API.
        if order.order_id and trade_id:
            self._exchange_order_id_map[order.order_id] = trade_id
            if len(self._exchange_order_id_map) > 2000:
                # prune oldest 500 to avoid unbounded growth
                for _k in list(self._exchange_order_id_map.keys())[:500]:
                    del self._exchange_order_id_map[_k]
        fill_price = order.price or Decimal("0")
        fill_fee = Decimal("0")  # BUG-C: initialized here, updated in poll/fill-history paths
        # BUG-93: fill_qty default depends on market type AND order type:
        # - Futures MARKET: default 0 → polling block may update it; 0 on poll failure → rollback
        # - Futures LIMIT:  default 0 → limit may not have filled (no polling block runs)
        # - Spot MARKET:    default order.amount → market fills at intended size (no polling block)
        # - Spot LIMIT:     default 0 → limit may not have filled (no polling block runs)
        _order_is_market = order.order_type == OrderType.MARKET
        fill_qty = order.amount if (self._market_type != "futures" and _order_is_market) else Decimal("0")

        # BUG-61: Bitget place-order response omits fill price/qty for MARKET orders.
        # Poll /api/v2/mix/order/detail up to 3 times to get actual avgPrice + baseVolume.
        # BUG-37: Originally 3→5 attempts at 0.3s; reduced to 3×0.2s — MARKET fills are near-instant,
        # polling is confirmation only. Fill-history fallback handles edge cases.
        import asyncio as _asyncio
        _order_not_in_active = False  # BUG-105: set True when 43001 received
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
                    _status = _d.get("status", "")
                    if _status in ("filled", "partially_filled"):
                        _avg = _d.get("priceAvg") or _d.get("price")
                        _vol = _d.get("baseVolume") or _d.get("size")
                        if _avg and Decimal(str(_avg)) > 0:
                            fill_price = Decimal(str(_avg))
                        if _vol and Decimal(str(_vol)) > 0:
                            fill_qty = Decimal(str(_vol))
                        # BUG-C: Extract fee from poll response (was silently ignored)
                        _fee_str = _d.get("fee") or _d.get("totalFee") or "0"
                        try:
                            fill_fee = abs(Decimal(str(_fee_str)))
                        except Exception:
                            fill_fee = Decimal("0")
                        logger.debug(
                            "bitget_futures_fill_polled symbol=%s orderId=%s attempt=%d status=%s price=%s qty=%s fee=%s",
                            order.symbol, trade_id, _attempt + 1, _status, fill_price, fill_qty, fill_fee,
                        )
                        if _status == "filled":
                            break  # stop polling on full fill; keep polling partial
                except Exception as _pe:
                    _pe_str = str(_pe)
                    if "43001" in _pe_str:
                        # BUG-105: 43001 = "order does not exist in active orders" — market order
                        # filled immediately and was archived before polling started.
                        # Fall back to fill history endpoint to recover actual qty/price.
                        _order_not_in_active = True
                        logger.debug("bitget_futures_order_archived orderId=%s — using fill history", trade_id)
                        break  # no point retrying order/detail; switch to fill history below
                    logger.debug("bitget_futures_poll_failed orderId=%s: %s", trade_id, _pe)

        # BUG-113: polling ended (5 attempts) without fill detection AND no 43001 received.
        # Bitget MARKET orders sometimes return status="init"/"new" for several seconds even after fill.
        # Force fill history fallback so we don't misclassify a filled order as "not filled".
        if (
            self._market_type == "futures"
            and _is_market
            and trade_id
            and fill_qty == Decimal("0")
            and not _order_not_in_active
        ):
            _order_not_in_active = True
            logger.info(
                "bitget_futures_poll_timeout_forcing_history orderId=%s symbol=%s — "
                "5 polls returned non-filled status; switching to fill history",
                trade_id, order.symbol,
            )

        # BUG-105 fallback: query /api/v2/mix/order/fills to recover fill data for archived orders.
        # BUG-105b: retry up to 3x with 0.5s backoff — fills may not be indexed immediately.
        # BUG-105b: use `or {}` instead of `.get("data", {})` — data key may map to None.
        # BUG-112: increased to 8x1.5s (12s total) — Bitget fill indexing delay observed to be >1.5s,
        # causing market orders that DID fill to appear as "not filled" → ghost LONG positions.
        if _order_not_in_active and fill_qty == Decimal("0") and trade_id:
            for _fill_attempt in range(8):
                if _fill_attempt > 0:
                    await _asyncio.sleep(1.5)
                try:
                    _fills_resp = await self._request(
                        "GET", "/api/v2/mix/order/fills",
                        params={
                            "symbol": sym,
                            "productType": "USDT-FUTURES",
                            "orderId": trade_id,
                        },
                        signed=True,
                    )
                    _fills = _fills_resp.get("data") or {}
                    if isinstance(_fills, dict):
                        _fills = _fills.get("fillList") or _fills.get("list") or []
                    if isinstance(_fills, list) and _fills:
                        _total_qty = Decimal("0")
                        _weighted_price = Decimal("0")
                        _total_fee = Decimal("0")
                        for _f in _fills:
                            _fq = Decimal(str(_f.get("baseVolume") or _f.get("size") or _f.get("qty") or "0"))
                            _fp = Decimal(str(_f.get("price") or _f.get("priceAvg") or "0"))
                            _ff = abs(Decimal(str(_f.get("fee") or _f.get("totalFee") or "0")))
                            _total_qty += _fq
                            _weighted_price += _fq * _fp
                            _total_fee += _ff
                        if _total_qty > 0:
                            fill_qty = _total_qty
                            fill_price = _weighted_price / _total_qty
                            fill_fee = _total_fee
                            logger.info(
                                "bitget_futures_fill_recovered orderId=%s symbol=%s qty=%s price=%s attempt=%d",
                                trade_id, order.symbol, fill_qty, fill_price, _fill_attempt + 1,
                            )
                            break
                    else:
                        logger.debug(
                            "bitget_futures_fill_history_empty orderId=%s attempt=%d — retrying",
                            trade_id, _fill_attempt + 1,
                        )
                except Exception as _fe:
                    # BUG-105b: continue to retry on transient errors (network/timeout);
                    # a permanent error (auth/param) will also fail on retries and exhaust naturally.
                    logger.warning("bitget_futures_fill_history_failed orderId=%s attempt=%d: %s", trade_id, _fill_attempt + 1, _fe)
                    continue

            # BUG-112: time-based fallback — if orderId filter returned empty, query recent fills
            # by time window and match by orderId (orderId-indexed query may be delayed).
            if fill_qty == Decimal("0") and trade_id:
                import time as _time
                try:
                    _ts_from = str(int((_time.time() - 300) * 1000))  # last 5 minutes
                    _ts_to = str(int(_time.time() * 1000))
                    _fb_resp = await self._request(
                        "GET", "/api/v2/mix/order/fills",
                        params={
                            "symbol": sym,
                            "productType": "USDT-FUTURES",
                            "startTime": _ts_from,
                            "endTime": _ts_to,
                        },
                        signed=True,
                    )
                    _fb_data = _fb_resp.get("data") or {}
                    if isinstance(_fb_data, dict):
                        _fb_data = _fb_data.get("fillList") or _fb_data.get("list") or []
                    if isinstance(_fb_data, list):
                        # CRITICAL-1: aggregate ALL partial fills for this orderId (mirror lines 551-560)
                        _tb_total_qty = Decimal("0")
                        _tb_weighted_price = Decimal("0")
                        _tb_total_fee = Decimal("0")
                        for _f in _fb_data:
                            if str(_f.get("orderId", "")) == trade_id:
                                _fq = Decimal(str(_f.get("baseVolume") or _f.get("size") or _f.get("qty") or "0"))
                                _fp = Decimal(str(_f.get("price") or _f.get("priceAvg") or "0"))
                                _ff = abs(Decimal(str(_f.get("fee") or _f.get("totalFee") or "0")))
                                _tb_total_qty += _fq
                                _tb_weighted_price += _fq * _fp
                                _tb_total_fee += _ff
                        if _tb_total_qty > 0:
                            fill_qty = _tb_total_qty
                            fill_price = _tb_weighted_price / _tb_total_qty
                            fill_fee = _tb_total_fee
                            logger.info(
                                "bitget_futures_fill_recovered_time_query orderId=%s symbol=%s qty=%s price=%s",
                                trade_id, order.symbol, fill_qty, fill_price,
                            )
                    if fill_qty == Decimal("0"):
                        logger.warning(
                            "bitget_futures_fill_confirmed_empty orderId=%s symbol=%s — "
                            "order genuinely not filled (async-cancelled by Bitget risk engine)",
                            trade_id, order.symbol,
                        )
                except Exception as _tbe:
                    logger.warning("bitget_futures_fill_time_fallback_failed orderId=%s: %s", trade_id, _tbe)

        return self._build_trade(
            order,
            trade_id=trade_id,
            price=fill_price,
            amount=fill_qty,
            fee=fill_fee,
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
        # BUG-104: resolve internal UUID → Bitget's numeric orderId.
        # Rollback passes order.order_id (UUID); Bitget's cancel API requires its own numeric orderId.
        # Use .get() (not .pop()) so the mapping survives a non-success response —
        # retry paths would otherwise fall back to raw UUID and get error 40017 again.
        resolved_id = self._exchange_order_id_map.get(order_id, order_id)
        body: dict[str, Any] = {"orderId": resolved_id}
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
            self._exchange_order_id_map.pop(order_id, None)  # clean up on confirmed success
            return True
        # BUG-04: 40762=order not found, 43011=already completed → desired outcome, return True
        if code in ("40762", "43011", "40783"):
            logger.info("bitget_cancel_benign code=%s — order already gone, treating as success", code)
            self._exchange_order_id_map.pop(order_id, None)  # clean up — order is gone
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
        # BUG-85: Bitget Futures USDT-M VIP0: maker=0.02%, taker=0.06% (not spot 0.10%)
        # Source: SSOT math-models.md §4.2
        if self._market_type == "futures":
            return FeeRate(
                maker=Decimal("0.0002"),
                taker=Decimal("0.0006"),
                symbol=symbol,
                exchange_id=self.exchange_id,
            )
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
        if self._market_type != "futures":
            # BUG-MEDIUM: /api/v2/mix/order/fills is futures-only endpoint
            logger.debug("bitget.get_trades skipped: market_type=%s", self._market_type)
            return []
        if not symbol:
            # Bitget fills API does not support all-symbol queries
            return []
        params: dict = {
            "productType": "USDT-FUTURES",
            "limit": str(min(limit, 500)),  # Bitget v2 supports up to 500 per page
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
                    "side": str(d.get("side") or d.get("tradeSide") or "").lower(),
                    "qty": _sf(d.get("baseVolume") or d.get("qty")),
                    "price": _sf(d.get("price")),
                    "realized_pnl": _sf(d.get("profit") or d.get("realizedPnl")),
                    "commission": abs(_sf(d.get("fee"))),
                    "ts_ms": _si(d.get("cTime") or d.get("ts")),
                }
                for d in fill_list
                if isinstance(d, dict)
            ]
        except Exception as exc:
            logger.warning("bitget.get_trades failed symbol=%s error=%s", symbol, exc)
            return []
