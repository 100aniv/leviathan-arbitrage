"""Native exchange adapter base — replaces ccxt with websockets + httpx.

Provides HMAC signing, auto-reconnect WebSocket, and REST client using
only standard libraries + httpx + websockets (no ccxt dependency).
"""
from __future__ import annotations

import abc
import asyncio
import hashlib
import hmac
import json
import logging
import time
import urllib.parse
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import websockets

from src.core.models import (
    Balance,
    FeeRate,
    Order,
    OrderBook,
    OrderBookLevel,
    OrderSide,
    Position,
    Trade,
)
from src.infra.exchange.health_checker import HealthChecker
from src.infra.exchange.rate_limiter import DEFAULT_RATE_LIMITS, ExchangeRateLimiter, RateLimitConfig

logger = logging.getLogger(__name__)


class NativeAdapter(abc.ABC):
    """Abstract base for native exchange adapters.

    Implements the ExchangeAdapter protocol structurally (no Protocol inheritance needed).
    Subclasses override abstract methods for exchange-specific REST/WS logic.

    Features:
    - httpx.AsyncClient for REST with connection pooling
    - websockets for WS with auto-reconnect
    - HMAC-SHA256/SHA512 request signing
    - Per-exchange rate limiting via TokenBucket
    - Health scoring via HealthChecker
    """

    def __init__(
        self,
        exchange_id: str,
        api_key: str = "",
        api_secret: str = "",
        passphrase: str = "",
        sandbox: bool = False,
        rate_limits: dict[str, RateLimitConfig] | None = None,
        stale_threshold_seconds: float = 120.0,  # PHOENIX: 5→120s (REST adapters poll every ~30s)
        slippage_k: Decimal = Decimal("1.0"),
        slippage_gamma: Decimal = Decimal("0.5"),
    ) -> None:
        self.exchange_id = exchange_id
        self._api_key = api_key
        self._api_secret = api_secret
        self._passphrase = passphrase
        self._sandbox = sandbox
        self._slippage_k = slippage_k
        self._slippage_gamma = slippage_gamma

        self._health = HealthChecker(exchange_id, stale_threshold_seconds)
        limits = rate_limits or DEFAULT_RATE_LIMITS.get(
            exchange_id,
            {"default": RateLimitConfig(requests_per_second=5, burst=10)},
        )
        self._rate_limiter = ExchangeRateLimiter(exchange_id, limits)

        self._http: httpx.AsyncClient | None = None
        self._ws_tasks: dict[str, asyncio.Task] = {}
        self._connected = False

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Initialize HTTP client and mark connected."""
        # PHOENIX §8.3 Tier1 patch 3-6: connect timeout 5s→1s
        # (failed-fast on dead routes; 1s is generous for ap-northeast TLS handshake)
        self._http = httpx.AsyncClient(
            base_url=self._rest_base_url(),
            timeout=httpx.Timeout(10.0, connect=1.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            headers=self._default_headers(),
        )
        self._connected = True
        self._health.record_ws_connect()
        logger.info("Connected to %s (native)", self.exchange_id)

    async def disconnect(self) -> None:
        """Cancel all WS subscriptions and close HTTP client."""
        for task in self._ws_tasks.values():
            task.cancel()
        self._ws_tasks.clear()
        if self._http:
            await self._http.aclose()
            self._http = None
        self._connected = False
        self._health.record_ws_disconnect()
        logger.info("Disconnected from %s (native)", self.exchange_id)

    # ------------------------------------------------------------------
    # WebSocket subscriptions
    # ------------------------------------------------------------------

    async def subscribe_orderbook(
        self, symbol: str, callback: Callable[[OrderBook], None]
    ) -> None:
        """Subscribe to live orderbook updates. Idempotent."""
        key = f"orderbook:{symbol}"
        if key in self._ws_tasks:
            return

        async def _watch_loop() -> None:
            reconnect_delay = 1.0
            while True:
                try:
                    url = self._ws_orderbook_url(symbol)
                    async with websockets.connect(
                        url, ping_interval=20, ping_timeout=10
                    ) as ws:
                        self._health.record_ws_connect()
                        sub_msg = self._ws_subscribe_message(symbol)
                        if sub_msg:
                            await ws.send(
                                json.dumps(sub_msg) if isinstance(sub_msg, dict) else sub_msg
                            )
                        reconnect_delay = 1.0
                        async for raw in ws:
                            self._health.record_heartbeat()
                            ob = self._parse_ws_orderbook(raw, symbol)
                            if ob is not None:
                                callback(ob)
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    logger.warning("WS error %s/%s: %s", self.exchange_id, symbol, e)
                    self._health.record_ws_disconnect()
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, 60.0)

        self._ws_tasks[key] = asyncio.create_task(_watch_loop())

    async def subscribe_ticker(self, symbol: str, callback: Callable) -> None:
        """Subscribe to live ticker updates. Idempotent."""
        key = f"ticker:{symbol}"
        if key in self._ws_tasks:
            return

        async def _watch_loop() -> None:
            reconnect_delay = 1.0
            while True:
                try:
                    url = self._ws_ticker_url(symbol)
                    async with websockets.connect(
                        url, ping_interval=20, ping_timeout=10
                    ) as ws:
                        sub_msg = self._ws_ticker_subscribe_message(symbol)
                        if sub_msg:
                            await ws.send(
                                json.dumps(sub_msg) if isinstance(sub_msg, dict) else sub_msg
                            )
                        reconnect_delay = 1.0
                        async for raw in ws:
                            self._health.record_heartbeat()
                            ticker = self._parse_ws_ticker(raw, symbol)
                            if ticker is not None:
                                callback(ticker)
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    logger.warning("Ticker WS error %s/%s: %s", self.exchange_id, symbol, e)
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, 60.0)

        self._ws_tasks[key] = asyncio.create_task(_watch_loop())

    # ------------------------------------------------------------------
    # REST API calls
    # ------------------------------------------------------------------

    async def get_orderbook_snapshot(self, symbol: str, depth: int = 20) -> OrderBook:
        await self._rate_limiter.acquire("default")
        start = time.monotonic()
        try:
            data = await self._rest_get_orderbook(symbol, depth)
            self._health.record_api_latency((time.monotonic() - start) * 1000)
            return data
        except Exception as e:
            self._health.record_error()
            logger.error("OrderBook snapshot error %s/%s: %s", self.exchange_id, symbol, e)
            raise

    async def place_order(self, order: Order) -> Trade:
        await self._rate_limiter.acquire("order")
        start = time.monotonic()
        try:
            trade = await self._rest_place_order(order)
            latency_ms = (time.monotonic() - start) * 1000
            self._health.record_api_latency(latency_ms)
            self._health.record_order_fill(True)
            logger.info(
                "order_placed exchange=%s order_id=%s symbol=%s side=%s qty=%s price=%s fee=%s latency_ms=%.1f",
                self.exchange_id, trade.trade_id, order.symbol,
                order.side.value if hasattr(order.side, 'value') else order.side,
                str(trade.amount), str(trade.price), str(trade.fee), latency_ms,
            )
            return trade
        except Exception as e:
            self._health.record_error()
            self._health.record_order_fill(False)
            logger.error("order_failed exchange=%s symbol=%s side=%s error=%s",
                         self.exchange_id, order.symbol, order.side, e)
            raise

    async def cancel_order(self, order_id: str, symbol: str | None = None) -> bool:
        await self._rate_limiter.acquire("order")
        try:
            return await self._rest_cancel_order(order_id, symbol)
        except Exception as e:
            self._health.record_error()
            logger.error("Cancel order error %s/%s: %s", self.exchange_id, order_id, e)
            return False

    async def cancel_all_orders(self, symbol: str | None = None) -> int:
        await self._rate_limiter.acquire("order")
        try:
            return await self._rest_cancel_all_orders(symbol)
        except Exception as e:
            self._health.record_error()
            logger.error("Cancel all orders error %s: %s", self.exchange_id, e)
            raise

    async def get_balances(self) -> dict[str, Balance]:
        await self._rate_limiter.acquire("default")
        try:
            return await self._rest_get_balances()
        except Exception as e:
            self._health.record_error()
            logger.error("Get balances error %s: %s", self.exchange_id, e)
            raise

    async def get_positions(self) -> list[Position]:
        await self._rate_limiter.acquire("default")
        try:
            return await self._rest_get_positions()
        except Exception as e:
            self._health.record_error()
            logger.error("Get positions error %s: %s", self.exchange_id, e)
            return []

    async def get_fee_rate(self, symbol: str) -> FeeRate:
        await self._rate_limiter.acquire("default")
        try:
            return await self._rest_get_fee_rate(symbol)
        except Exception as e:
            logger.warning(
                "Get fee rate error %s/%s: %s — using defaults", self.exchange_id, symbol, e
            )
            return FeeRate(
                maker=Decimal("0.001"),
                taker=Decimal("0.001"),
                symbol=symbol,
                exchange_id=self.exchange_id,
            )

    @property
    def health_score(self) -> float:
        return self._health.health_score

    # ------------------------------------------------------------------
    # Signing utilities
    # ------------------------------------------------------------------

    def _sign_hmac_sha256(self, message: str) -> str:
        """HMAC-SHA256 signature (Binance, Bybit, Bitget)."""
        return hmac.new(
            self._api_secret.encode(), message.encode(), hashlib.sha256
        ).hexdigest()

    def _sign_hmac_sha512(self, message: str) -> str:
        """HMAC-SHA512 signature (Bithumb)."""
        return hmac.new(
            self._api_secret.encode(), message.encode(), hashlib.sha512
        ).hexdigest()

    def _build_query_string(self, params: dict[str, Any]) -> str:
        """Build URL-encoded query string (insertion order — not sorted).

        Binance requires signature to match the exact query string sent.
        httpx sends params in insertion order, so we must sign in the same order.
        """
        return urllib.parse.urlencode(params)

    def _timestamp_ms(self) -> int:
        """Current timestamp in milliseconds."""
        return int(time.time() * 1000)

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        signed: bool = False,
        headers: dict[str, str] | None = None,
    ) -> dict:
        """Execute an authenticated or public REST request."""
        if not self._http:
            raise RuntimeError(f"{self.exchange_id}: not connected — call connect() first")

        req_headers = dict(headers or {})
        if signed:
            req_headers.update(self._auth_headers(method, path, params, data))

        resp = await self._http.request(
            method,
            path,
            params=params,
            json=data if method in ("POST", "PUT", "DELETE") and data else None,
            headers=req_headers,
        )
        if resp.is_error:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            _body_str = str(body)
            # Binance benign codes: -4046/-4048 = already set, -4168 = Multi-Assets mode (no action needed)
            _binance_benign = any(c in _body_str for c in ("-4046", "-4048", "-4168"))
            if _binance_benign:
                logger.debug(
                    "http_error exchange=%s status=%s body=%s (benign — suppressed)",
                    self.exchange_id, resp.status_code, body,
                )
            else:
                logger.error(
                    "http_error exchange=%s status=%s body=%s",
                    self.exchange_id, resp.status_code, body,
                )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Orderbook parsing helpers
    # ------------------------------------------------------------------

    def _build_orderbook(
        self,
        symbol: str,
        bids: list[list],
        asks: list[list],
        sequence: int | None = None,
    ) -> OrderBook:
        """Convert raw bid/ask arrays to OrderBook model."""
        return OrderBook(
            exchange_id=self.exchange_id,
            symbol=symbol,
            bids=[
                OrderBookLevel(price=Decimal(str(b[0])), amount=Decimal(str(b[1])))
                for b in bids
            ],
            asks=[
                OrderBookLevel(price=Decimal(str(a[0])), amount=Decimal(str(a[1])))
                for a in asks
            ],
            sequence=sequence,
        )

    def _build_trade(
        self,
        order: Order,
        trade_id: str,
        price: Decimal,
        amount: Decimal,
        fee: Decimal = Decimal("0"),
        fee_currency: str | None = None,
    ) -> Trade:
        """Build Trade model from fill data."""
        return Trade(
            trade_id=trade_id,
            order_id=order.order_id or order.client_order_id,
            exchange_id=self.exchange_id,
            symbol=order.symbol,
            side=order.side,
            price=price,
            amount=amount,
            fee=fee,
            fee_currency=fee_currency,
        )

    # ------------------------------------------------------------------
    # Abstract methods — must be implemented by each exchange adapter
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def _rest_base_url(self) -> str:
        """Return the REST API base URL (e.g. 'https://api.binance.com')."""
        ...

    @abc.abstractmethod
    def _default_headers(self) -> dict[str, str]:
        """Return default HTTP headers for all requests."""
        ...

    @abc.abstractmethod
    def _auth_headers(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        data: dict[str, Any] | None,
    ) -> dict[str, str]:
        """Return authentication headers for signed requests."""
        ...

    @abc.abstractmethod
    def _ws_orderbook_url(self, symbol: str) -> str:
        """Return WebSocket URL for orderbook stream."""
        ...

    @abc.abstractmethod
    def _ws_subscribe_message(self, symbol: str) -> dict | str | None:
        """Return WS subscribe message, or None if subscription is in the URL."""
        ...

    @abc.abstractmethod
    def _parse_ws_orderbook(self, raw: str | bytes, symbol: str) -> OrderBook | None:
        """Parse WS message into OrderBook or None if not orderbook data."""
        ...

    @abc.abstractmethod
    async def _rest_get_orderbook(self, symbol: str, depth: int) -> OrderBook:
        """Fetch orderbook snapshot via REST."""
        ...

    @abc.abstractmethod
    async def _rest_place_order(self, order: Order) -> Trade:
        """Submit an order via REST and return the Trade."""
        ...

    @abc.abstractmethod
    async def _rest_cancel_order(self, order_id: str, symbol: str | None) -> bool:
        """Cancel a single order via REST."""
        ...

    @abc.abstractmethod
    async def _rest_cancel_all_orders(self, symbol: str | None) -> int:
        """Cancel all orders via REST, return count cancelled."""
        ...

    @abc.abstractmethod
    async def _rest_get_balances(self) -> dict[str, Balance]:
        """Fetch account balances via REST."""
        ...

    @abc.abstractmethod
    async def _rest_get_positions(self) -> list[Position]:
        """Fetch open positions via REST."""
        ...

    @abc.abstractmethod
    async def _rest_get_fee_rate(self, symbol: str) -> FeeRate:
        """Fetch fee rate for a symbol via REST."""
        ...

    # ------------------------------------------------------------------
    # Slippage estimation
    # ------------------------------------------------------------------

    async def estimate_slippage(self, side: OrderSide, size: Decimal, symbol: str) -> Decimal:
        """Return expected slippage as a fraction (e.g., 0.001 = 0.1%).

        Uses PowerLaw model from QUANT_MANIFESTO §8.1:
            slippage = base_slippage * k * size^gamma
        with configurable k and gamma (defaults: k=1.0, gamma=0.5),
        base_slippage=0.0001 (1bp).
        """
        base_slippage = Decimal("0.0001")
        slippage = base_slippage * self._slippage_k * (size ** self._slippage_gamma)
        return slippage

    # ------------------------------------------------------------------
    # Optional overrides
    # ------------------------------------------------------------------

    async def close_all_positions(self, timeout_ms: int = 3000) -> list[str]:
        """KillSwitchTarget: Close all open futures positions at market price.

        Returns list of closed position descriptions. Non-fatal per position.
        Spot-only adapters return [] (no positions to close).
        """
        from src.core.models import OrderType
        closed = []
        try:
            positions = await asyncio.wait_for(
                self.get_positions(), timeout=timeout_ms / 1000
            )
        except Exception as exc:
            logger.warning("close_all_positions_get_failed exchange=%s error=%s", self.exchange_id, exc)
            return []

        for pos in positions:
            if pos.size == 0:
                continue
            close_side = OrderSide.SELL if pos.size > 0 else OrderSide.BUY
            close_order = Order(
                exchange_id=self.exchange_id,
                symbol=pos.symbol,
                side=close_side,
                order_type=OrderType.MARKET,
                amount=abs(pos.size),
                metadata={"reduceOnly": True},
            )
            try:
                await asyncio.wait_for(
                    self.place_order(close_order), timeout=timeout_ms / 1000
                )
                closed.append(f"{pos.symbol}:{close_side}:{abs(pos.size)}")
                logger.info(
                    "kill_switch_tier3_closed exchange=%s symbol=%s side=%s size=%s",
                    self.exchange_id, pos.symbol, close_side, abs(pos.size),
                )
            except Exception as exc:
                logger.error(
                    "kill_switch_tier3_close_failed exchange=%s symbol=%s error=%s",
                    self.exchange_id, pos.symbol, exc,
                )
        return closed

    async def emergency_cancel_all(self, timeout_ms: int = 2000) -> list[str]:
        """KillSwitchTarget: Cancel ALL open orders across all symbols.

        Unlike cancel_all_orders(symbol=...) which requires a specific symbol,
        this fetches open orders first then cancels all of them.
        Returns list of cancelled order IDs.
        """
        cancelled = []
        try:
            if hasattr(self, 'get_open_orders'):
                orders = await asyncio.wait_for(
                    self.get_open_orders(), timeout=timeout_ms / 1000
                )
                for order in orders:
                    try:
                        symbol = getattr(order, 'symbol', None)
                        await asyncio.wait_for(
                            self.cancel_order(order.order_id, symbol=symbol),
                            timeout=timeout_ms / 1000,
                        )
                        cancelled.append(str(order.order_id))
                    except Exception as exc:
                        logger.error(
                            "kill_switch_tier2_cancel_failed exchange=%s order=%s error=%s",
                            self.exchange_id, order.order_id, exc,
                        )
            else:
                # Fallback: try cancel_all_orders if available
                count = await asyncio.wait_for(
                    self.cancel_all_orders(symbol=None), timeout=timeout_ms / 1000
                )
                cancelled = [f"batch:{count}"]
        except Exception as exc:
            logger.warning("emergency_cancel_all_failed exchange=%s error=%s", self.exchange_id, exc)
        return cancelled

    def _ws_ticker_url(self, symbol: str) -> str:
        """Return WS URL for ticker stream. Subclasses must override."""
        raise NotImplementedError(
            f"{self.__class__.__name__} does not implement _ws_ticker_url. "
            "Override this method to subscribe to ticker streams."
        )

    def _ws_ticker_subscribe_message(self, symbol: str) -> dict | str | None:
        """Return WS subscribe message for ticker. Defaults to None."""
        return None

    def _parse_ws_ticker(self, raw: str | bytes, symbol: str) -> dict | None:
        """Parse WS ticker message. Defaults to None (no-op)."""
        return None
