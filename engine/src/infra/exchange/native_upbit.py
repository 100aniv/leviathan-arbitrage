"""Native Upbit adapter — Korean KRW exchange via direct REST + WebSocket."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import urllib.parse
import uuid
from decimal import ROUND_DOWN, ROUND_UP, Decimal
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


def _quote_from_symbol(symbol: str) -> str:
    """'BTC/USDT' -> 'USDT', 'KRW-BTC' -> 'KRW'."""
    if "/" in symbol:
        return symbol.split("/", 1)[1].upper()
    if "-" in symbol:
        return symbol.split("-", 1)[0].upper()
    return ""


# Upbit KRW market tick table — source: Upbit docs (호가 정책).
# (price_lower_inclusive, tick_size). Sorted descending by threshold.
_UPBIT_KRW_TICK_TABLE: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("2000000"), Decimal("1000")),
    (Decimal("1000000"), Decimal("500")),
    (Decimal("500000"), Decimal("100")),
    (Decimal("100000"), Decimal("50")),
    (Decimal("10000"), Decimal("10")),
    (Decimal("1000"), Decimal("1")),
    (Decimal("100"), Decimal("1")),
    (Decimal("10"), Decimal("0.1")),
    (Decimal("1"), Decimal("0.01")),
    (Decimal("0.1"), Decimal("0.001")),
    (Decimal("0"), Decimal("0.0001")),
)

# Upbit USDT market tick table — source: Upbit docs (USDT Market Order Price Unit).
# https://global-docs.upbit.com/docs/usdt-market-info
_UPBIT_USDT_TICK_TABLE: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("10"), Decimal("0.01")),
    (Decimal("1"), Decimal("0.001")),
    (Decimal("0.1"), Decimal("0.0001")),
    (Decimal("0.01"), Decimal("0.00001")),
    (Decimal("0.001"), Decimal("0.000001")),
    (Decimal("0.0001"), Decimal("0.0000001")),
    (Decimal("0"), Decimal("0.00000001")),
)


def _upbit_tick_size(symbol: str, price: Decimal) -> Decimal:
    """Return the applicable tick size for the given (symbol, price).

    Upbit tick tables vary by quote currency and price band. BTC market
    currently uses a fixed small tick (0.00000001 BTC).
    """
    quote = _quote_from_symbol(symbol)
    if quote == "KRW":
        table = _UPBIT_KRW_TICK_TABLE
    elif quote == "USDT":
        table = _UPBIT_USDT_TICK_TABLE
    else:
        # BTC market and fallback — smallest unit
        return Decimal("0.00000001")
    for threshold, tick in table:
        if price >= threshold:
            return tick
    return table[-1][1]


def _align_upbit_price(
    symbol: str, price: Decimal, side: OrderSide | None = None
) -> Decimal:
    """Round price to the nearest valid Upbit tick for this symbol/band.

    BUG-221: Upbit rejects orders with prices not aligned to the tick grid
    (``invalid_price_ask``). Truncate BUY prices down (do not overpay) and
    round SELL prices down as well — aligning to the lower tick preserves
    the intended fill side without exceeding the quoted price.
    """
    if price <= 0:
        return price
    tick = _upbit_tick_size(symbol, price)
    if tick <= 0:
        return price
    # Use ROUND_DOWN (truncate) — safe for both sides:
    #   BUY: pay no more than requested
    #   SELL: never raise the ask above the requested level
    quantized = (price / tick).to_integral_value(rounding=ROUND_DOWN) * tick
    # Normalise exponent to the tick's scale for a clean string repr.
    return quantized.quantize(tick)


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
    """Native Upbit spot adapter — direct HTTP/WebSocket.

    Upbit uses JWT (HS256) authentication, not HMAC headers.
    All pairs are KRW-denominated (e.g., BTC/KRW).
    """

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rate_limits", _UPBIT_RATE_LIMITS)
        super().__init__(exchange_id="upbit", **kwargs)
        # BUG-190: userData WS stream for event-driven myOrder fills
        self._user_stream: Any = None
        self._user_stream_lock = asyncio.Lock()
        self._user_stream_start_failed_until: float = 0.0  # BUG-211: cooldown

    async def _get_user_stream(self) -> Any:
        """Lazy-start Upbit private userData stream (BUG-190).

        Returns None if start() fails — callers fall back to REST-only path.
        BUG-211: 30s cooldown after start() failure to avoid retry storm.
        """
        import time
        if not self._api_key or not self._api_secret:
            return None
        if time.monotonic() < self._user_stream_start_failed_until:
            return None
        if self._user_stream is not None:
            return self._user_stream
        async with self._user_stream_lock:
            if self._user_stream is not None:
                return self._user_stream
            if time.monotonic() < self._user_stream_start_failed_until:
                return None
            try:
                from src.infra.exchange.ws_trade import UpbitUserDataStream
                stream = UpbitUserDataStream(self._api_key, self._api_secret)
                await stream.start()
                self._user_stream = stream
                logger.info("upbit_user_data_stream_started")
            except Exception as exc:
                logger.warning(
                    "upbit_user_data_stream_start_failed err=%s — REST fallback for 30s",
                    exc,
                )
                self._user_stream = None
                self._user_stream_start_failed_until = time.monotonic() + 30.0
        return self._user_stream

    async def disconnect(self) -> None:
        """Stop userData stream before the base adapter teardown."""
        if self._user_stream is not None:
            try:
                await self._user_stream.stop()
            except Exception as exc:
                logger.debug("upbit_user_data_stop_failed err=%s", exc)
            self._user_stream = None
        await super().disconnect()

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
            # BUG-221: Upbit rejects prices not aligned to the tick grid.
            aligned_price = _align_upbit_price(order.symbol, order.price, order.side)
            if aligned_price != order.price:
                logger.debug(
                    "upbit_price_tick_aligned symbol=%s raw=%s aligned=%s tick=%s",
                    order.symbol, order.price, aligned_price,
                    _upbit_tick_size(order.symbol, order.price),
                )
            body["price"] = str(aligned_price)
        if order.client_order_id:
            body["identifier"] = order.client_order_id

        # Upbit POST /v1/orders expects parameters as URL query params (not JSON body).
        # The JWT query_hash is computed from the same params in the same order.
        resp = await self._request("POST", "/v1/orders", params=body, signed=True)
        order_uuid = resp.get("uuid", "")

        # BUG-190: Event-driven fill confirmation via userData myOrder WS.
        # Upbit REST POST /v1/orders returns immediately with state="wait" — fills
        # land asynchronously. Use private WS myOrder (state="done") to confirm
        # actual fill volume/price. REST-only path preserved as fallback when WS
        # is unavailable or the event times out.
        _fill_price = order.price or Decimal("0")
        _fill_amount = order.amount
        if order_uuid:
            try:
                _user_stream = await self._get_user_stream()
            except Exception as _us_err:
                logger.debug("upbit_user_stream_unavailable err=%s", _us_err)
                _user_stream = None
            if _user_stream is not None:
                try:
                    _fill = await _user_stream.wait_for_order_fill(
                        order_uuid, timeout=0.3
                    )
                except Exception as _wf_err:
                    logger.debug(
                        "upbit_user_stream_wait_failed uuid=%s err=%s",
                        order_uuid, _wf_err,
                    )
                    _fill = None
                if _fill is not None:
                    # BUG-213: Upbit spec — `volume` is the original requested
                    # size (주문량); `executed_volume` is the actual cumulative
                    # filled quantity (체결량). Use executed_volume to match the
                    # sibling KRW adapters (Bithumb `executed_volume`, Coinone
                    # `executed_qty`). Fallback to `volume` preserves behaviour
                    # for payloads that omit the executed field.
                    _vol_str = _fill.get("executed_volume") or _fill.get("volume")
                    _px_str = _fill.get("price")
                    try:
                        if _vol_str:
                            _v = Decimal(str(_vol_str))
                            if _v > 0:
                                _fill_amount = _v
                        if _px_str:
                            _p = Decimal(str(_px_str))
                            if _p > 0:
                                _fill_price = _p
                        logger.debug(
                            "upbit_user_data_fill_confirmed symbol=%s uuid=%s qty=%s px=%s",
                            order.symbol, order_uuid, _fill_amount, _fill_price,
                        )
                    except Exception as _pe:
                        logger.debug("upbit_fill_parse_failed uuid=%s err=%s", order_uuid, _pe)
        return self._build_trade(
            order,
            trade_id=order_uuid,
            price=_fill_price,
            amount=_fill_amount,
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
