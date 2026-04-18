"""DEPRECATED — ccxt-based legacy adapter. NOT used by live engine.

Engine live/paper modes use native_* adapters exclusively (ccxt-free per PHOENIX
§7 "shadow 모드 없음"). This file and its subclasses (okx.py, bybit.py, upbit.py,
bithumb.py) are kept only for historical compatibility. Do NOT import in new code.

For live trading WebSocket order placement (BUG-120 planned), see:
  - native_binance.py (Binance Futures WS — wss://ws-fapi.binance.com/ws-fapi/v1)
  - native_bitget.py  (Bitget V2 WS — wss://ws.bitget.com/v2/ws/private)
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import ccxt.pro as ccxtpro

from src.core.models import (
    Balance,
    FeeRate,
    Order,
    OrderBook,
    OrderBookLevel,
    Position,
    Trade,
)
from src.infra.exchange.health_checker import HealthChecker
from src.infra.exchange.rate_limiter import DEFAULT_RATE_LIMITS, ExchangeRateLimiter, RateLimitConfig

logger = logging.getLogger(__name__)


class CCXTAdapter:
    """
    Generic exchange adapter using ccxt.pro for WebSocket and REST.

    Implements ExchangeAdapter protocol structurally (no explicit inheritance needed).
    Subclass and override _parse_* methods for exchange-specific behaviour.
    """

    def __init__(
        self,
        exchange_id: str,
        api_key: str = "",
        api_secret: str = "",
        passphrase: str = "",
        sandbox: bool = False,
        rate_limits: dict[str, RateLimitConfig] | None = None,
        stale_threshold_seconds: float = 5.0,
        extra_config: dict[str, Any] | None = None,
    ) -> None:
        self.exchange_id = exchange_id
        self._sandbox = sandbox
        self._health = HealthChecker(exchange_id, stale_threshold_seconds)

        limits = rate_limits or DEFAULT_RATE_LIMITS.get(
            exchange_id,
            {"default": RateLimitConfig(requests_per_second=5, burst=10)},
        )
        self._rate_limiter = ExchangeRateLimiter(exchange_id, limits)

        exchange_class = getattr(ccxtpro, exchange_id, None)
        if exchange_class is None:
            raise ValueError(f"Exchange '{exchange_id}' not supported by ccxt.pro")

        config: dict[str, Any] = {
            "apiKey": api_key,
            "secret": api_secret,
            "password": passphrase,
            "enableRateLimit": False,  # we manage rate limiting ourselves
            "sandbox": sandbox,
        }
        if extra_config:
            config.update(extra_config)

        self._exchange: Any = exchange_class(config)
        self._subscriptions: dict[str, asyncio.Task] = {}
        self._connected = False

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Load markets and mark connected."""
        await self._exchange.load_markets()
        self._connected = True
        self._health.record_ws_connect()
        logger.info("Connected to %s", self.exchange_id)

    async def disconnect(self) -> None:
        """Cancel all subscriptions and close the exchange."""
        for task in self._subscriptions.values():
            task.cancel()
        self._subscriptions.clear()
        await self._exchange.close()
        self._connected = False
        self._health.record_ws_disconnect()
        logger.info("Disconnected from %s", self.exchange_id)

    # ------------------------------------------------------------------
    # WebSocket subscriptions
    # ------------------------------------------------------------------

    async def subscribe_orderbook(
        self, symbol: str, callback: Callable[[OrderBook], None]
    ) -> None:
        """Subscribe to live orderbook updates. Idempotent — ignores duplicate calls."""
        key = f"orderbook:{symbol}"
        if key in self._subscriptions:
            return

        async def _watch_loop() -> None:
            reconnect_delay = 1.0
            while True:
                try:
                    while True:
                        raw = await self._exchange.watch_order_book(symbol)
                        self._health.record_heartbeat()
                        callback(self._parse_orderbook(raw, symbol))
                        reconnect_delay = 1.0
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    logger.warning("OrderBook WS error %s/%s: %s", self.exchange_id, symbol, e)
                    self._health.record_ws_disconnect()
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, 60.0)
                    try:
                        await self._exchange.load_markets()
                        self._health.record_ws_connect()
                    except Exception:
                        pass

        self._subscriptions[key] = asyncio.create_task(_watch_loop())

    async def subscribe_ticker(self, symbol: str, callback: Callable) -> None:
        """Subscribe to live ticker updates. Idempotent — ignores duplicate calls."""
        key = f"ticker:{symbol}"
        if key in self._subscriptions:
            return

        async def _watch_loop() -> None:
            reconnect_delay = 1.0
            while True:
                try:
                    while True:
                        raw = await self._exchange.watch_ticker(symbol)
                        self._health.record_heartbeat()
                        callback(raw)
                        reconnect_delay = 1.0
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    logger.warning("Ticker WS error %s/%s: %s", self.exchange_id, symbol, e)
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, 60.0)

        self._subscriptions[key] = asyncio.create_task(_watch_loop())

    # ------------------------------------------------------------------
    # REST API calls
    # ------------------------------------------------------------------

    async def get_orderbook_snapshot(self, symbol: str, depth: int = 20) -> OrderBook:
        await self._rate_limiter.acquire("default")
        start = time.monotonic()
        try:
            raw = await self._exchange.fetch_order_book(symbol, depth)
            self._health.record_api_latency((time.monotonic() - start) * 1000)
            return self._parse_orderbook(raw, symbol)
        except Exception as e:
            self._health.record_error()
            logger.error("OrderBook snapshot error %s/%s: %s", self.exchange_id, symbol, e)
            raise

    async def place_order(self, order: Order) -> Trade:
        await self._rate_limiter.acquire("order")
        start = time.monotonic()
        try:
            price = float(order.price) if order.price is not None else None
            result = await self._exchange.create_order(
                order.symbol,
                order.order_type.value,
                order.side.value,
                float(order.amount),
                price,
            )
            self._health.record_api_latency((time.monotonic() - start) * 1000)
            self._health.record_order_fill(True)
            return self._parse_trade_from_order(result, order)
        except Exception as e:
            self._health.record_error()
            self._health.record_order_fill(False)
            logger.error("Place order error %s: %s", self.exchange_id, e)
            raise

    async def cancel_order(self, order_id: str, symbol: str | None = None) -> bool:
        await self._rate_limiter.acquire("order")
        try:
            await self._exchange.cancel_order(order_id, symbol)
            return True
        except Exception as e:
            self._health.record_error()
            logger.error("Cancel order error %s/%s: %s", self.exchange_id, order_id, e)
            return False

    async def cancel_all_orders(self, symbol: str | None = None) -> int:
        await self._rate_limiter.acquire("order")
        try:
            if symbol is not None:
                result = await self._exchange.cancel_all_orders(symbol)
            else:
                result = await self._exchange.cancel_all_orders()
            return len(result) if isinstance(result, list) else 0
        except Exception as e:
            self._health.record_error()
            logger.error("Cancel all orders error %s: %s", self.exchange_id, e)
            raise

    async def get_balances(self) -> dict[str, Balance]:
        await self._rate_limiter.acquire("default")
        try:
            raw = await self._exchange.fetch_balance()
            return self._parse_balances(raw)
        except Exception as e:
            self._health.record_error()
            logger.error("Get balances error %s: %s", self.exchange_id, e)
            raise

    async def get_positions(self) -> list[Position]:
        await self._rate_limiter.acquire("default")
        try:
            if not self._exchange.has.get("fetchPositions"):
                return []
            raw = await self._exchange.fetch_positions()
            return [self._parse_position(p) for p in raw if p.get("contracts")]
        except Exception as e:
            self._health.record_error()
            logger.error("Get positions error %s: %s", self.exchange_id, e)
            return []

    async def get_fee_rate(self, symbol: str) -> FeeRate:
        await self._rate_limiter.acquire("default")
        try:
            raw = await self._exchange.fetch_trading_fee(symbol)
            return FeeRate(
                maker=Decimal(str(raw.get("maker", "0.001"))),
                taker=Decimal(str(raw.get("taker", "0.001"))),
                symbol=symbol,
                exchange_id=self.exchange_id,
            )
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
    # Parsing helpers (override in subclasses for exchange-specific logic)
    # ------------------------------------------------------------------

    def _parse_orderbook(self, raw: dict, symbol: str) -> OrderBook:
        return OrderBook(
            exchange_id=self.exchange_id,
            symbol=symbol,
            bids=[
                OrderBookLevel(price=Decimal(str(b[0])), amount=Decimal(str(b[1])))
                for b in raw.get("bids", [])
            ],
            asks=[
                OrderBookLevel(price=Decimal(str(a[0])), amount=Decimal(str(a[1])))
                for a in raw.get("asks", [])
            ],
        )

    def _parse_balances(self, raw: dict) -> dict[str, Balance]:
        balances: dict[str, Balance] = {}
        for currency, total_val in raw.get("total", {}).items():
            if total_val and float(total_val) > 0:
                free = Decimal(str(raw.get("free", {}).get(currency, 0) or 0))
                used = Decimal(str(raw.get("used", {}).get(currency, 0) or 0))
                total = Decimal(str(total_val))
                balances[currency] = Balance(
                    currency=currency, free=free, used=used, total=total
                )
        return balances

    def _parse_position(self, raw: dict) -> Position:
        return Position(
            exchange_id=self.exchange_id,
            symbol=raw.get("symbol", ""),
            size=Decimal(str(raw.get("contracts", 0))),
            entry_price=Decimal(str(raw.get("entryPrice", 0) or 0)),
            mark_price=(
                Decimal(str(raw["markPrice"])) if raw.get("markPrice") else None
            ),
            unrealized_pnl=Decimal(str(raw.get("unrealizedPnl", 0) or 0)),
            leverage=int(raw.get("leverage", 1) or 1),
        )

    def _parse_trade_from_order(self, raw: dict, original: Order) -> Trade:
        price_raw = raw.get("price") or raw.get("average") or 0
        amount_raw = raw.get("filled") or raw.get("amount") or 0
        fee_data = raw.get("fee") or {}
        return Trade(
            trade_id=str(raw.get("id", "")),
            order_id=str(raw.get("id")) if raw.get("id") else None,
            exchange_id=self.exchange_id,
            symbol=original.symbol,
            side=original.side,
            price=Decimal(str(price_raw)),
            amount=Decimal(str(amount_raw)),
            fee=Decimal(str(fee_data.get("cost", 0) or 0)),
            fee_currency=fee_data.get("currency"),
        )
