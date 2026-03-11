"""Coinone public orderbook collector via native WebSocket."""
from __future__ import annotations

import asyncio
import json as _json
import time
from typing import Callable, Awaitable

import structlog

from src.collectors.base_collector import BaseCollector

logger = structlog.get_logger(__name__)

_GAP_CHECK_INTERVAL_S = 30        # 갭 감시 주기 (초)
_GAP_RECONNECT_THRESHOLD_S = 120  # 재연결 임계값 (초)
_APP_PING_INTERVAL_S = 1500       # 애플리케이션 PING 주기 (25분)


def _normalize_symbol(symbol: str) -> tuple[str, str]:
    """Convert 'BTC/KRW' -> (quote_currency='KRW', target_currency='BTC')."""
    if "/" in symbol:
        base, quote = symbol.split("/", 1)
        return quote, base
    return "KRW", symbol


def _denormalize_symbol(quote_currency: str, target_currency: str) -> str:
    """Convert quote_currency='KRW', target_currency='BTC' -> 'BTC/KRW'."""
    return f"{target_currency}/{quote_currency}"


class CoinoneCollector(BaseCollector):
    """Collects Coinone orderbook snapshots via the public WebSocket.

    Connects to: wss://stream.coinone.co.kr
    Subscription: JSON with request_type=SUBSCRIBE, channel=ORDERBOOK, topic.
    No API key is required for public orderbook data.
    30-minute PING keepalive required.

    Stability features:
    - Data gap watchdog: closes WS if no messages for 120s (zombie reconnect)
    - Application-level PING: sends JSON PING every 25 minutes
    - Symbol stale detection: is_symbol_stale() for per-symbol monitoring
    """

    _WS_URL = "wss://stream.coinone.co.kr"

    def __init__(
        self,
        symbols: list[str],
        on_orderbook: Callable[[str, str, list, list], Awaitable[None]] | None = None,
    ) -> None:
        # 30-min keepalive: ping_interval=1800s
        super().__init__(
            exchange_id="coinone",
            symbols=symbols,
            on_orderbook=on_orderbook,
            ping_interval=1800,
            ping_timeout=30,
        )
        self._last_symbol_time: dict[str, float] = {}

    def _ws_url(self) -> str:
        return self._WS_URL

    def _subscribe_message(self, symbol: str) -> str | dict:
        """Coinone subscription message."""
        quote_currency, target_currency = _normalize_symbol(symbol)
        return {
            "request_type": "SUBSCRIBE",
            "channel": "ORDERBOOK",
            "topic": {
                "quote_currency": quote_currency,
                "target_currency": target_currency,
            },
        }

    async def _connect_and_listen(self) -> None:
        """Override to add data gap watchdog and application ping tasks."""
        import websockets

        url = self._ws_url()
        logger.info("collector_connecting", exchange=self.exchange_id, url=url)

        async with websockets.connect(
            url,
            ping_interval=self.ping_interval,
            ping_timeout=self.ping_timeout,
        ) as ws:
            self._ws = ws
            self._connected = True
            self._reconnect_delay = self.INITIAL_RECONNECT_DELAY
            logger.info("collector_connected", exchange=self.exchange_id)

            # Subscribe per-symbol
            for symbol in self.symbols:
                msg = self._subscribe_message(symbol)
                await ws.send(_json.dumps(msg) if isinstance(msg, dict) else msg)
                logger.info("collector_subscribed", exchange=self.exchange_id, symbol=symbol)

            # Start parallel background tasks
            watchdog_task = asyncio.create_task(self._data_gap_watchdog(ws))
            ping_task = asyncio.create_task(self._application_ping_loop(ws))

            try:
                async for raw in ws:
                    if not self._running:
                        break
                    self._last_message_time = time.monotonic()
                    self._message_count += 1
                    try:
                        await self._handle_message(raw)
                    except Exception as exc:
                        logger.warning(
                            "collector_parse_error",
                            exchange=self.exchange_id,
                            error=str(exc),
                        )
            finally:
                watchdog_task.cancel()
                ping_task.cancel()
                await asyncio.gather(watchdog_task, ping_task, return_exceptions=True)

        self._connected = False

    async def _data_gap_watchdog(self, ws) -> None:
        """Monitor for data gaps; close WS if no messages for threshold seconds."""
        while True:
            await asyncio.sleep(_GAP_CHECK_INTERVAL_S)
            if self._last_message_time == 0.0:
                continue  # No messages yet (right after connect)
            age = time.monotonic() - self._last_message_time
            if age > _GAP_RECONNECT_THRESHOLD_S:
                logger.warning(
                    "coinone_data_gap_detected",
                    exchange=self.exchange_id,
                    gap_seconds=round(age, 1),
                )
                await ws.close()
                return

    async def _application_ping_loop(self, ws) -> None:
        """Send application-level JSON PING every 25 minutes."""
        while True:
            await asyncio.sleep(_APP_PING_INTERVAL_S)
            try:
                await ws.send(_json.dumps({"request_type": "PING"}))
                logger.debug("coinone_app_ping_sent", exchange=self.exchange_id)
            except Exception as exc:
                logger.warning(
                    "coinone_app_ping_failed",
                    exchange=self.exchange_id,
                    error=str(exc),
                )
                return

    def is_symbol_stale(self, symbol: str, max_age_s: float = 300.0) -> bool:
        """Check if a symbol's data is stale (no update in max_age_s seconds)."""
        last = self._last_symbol_time.get(symbol)
        if last is None:
            return True
        return (time.monotonic() - last) > max_age_s

    def _parse_message(self, data: dict) -> tuple[str, list, list] | None:
        """Parse Coinone ORDERBOOK DATA message.

        Coinone format:
        {
            "response_type": "DATA",
            "channel": "ORDERBOOK",
            "data": {
                "quote_currency": "KRW",
                "target_currency": "BTC",
                "timestamp": 1234567890,
                "id": "...",
                "asks": [{"price": "50000", "qty": "0.1"}, ...],
                "bids": [{"price": "49900", "qty": "0.2"}, ...]
            }
        }
        """
        response_type = data.get("response_type")

        # Application-level PONG response
        if response_type == "PONG":
            logger.debug("coinone_pong_received", exchange=self.exchange_id)
            return None

        if response_type != "DATA":
            return None
        if data.get("channel") != "ORDERBOOK":
            return None

        payload = data.get("data", {})
        if not payload:
            return None

        quote_currency = payload.get("quote_currency", "")
        target_currency = payload.get("target_currency", "")
        symbol = _denormalize_symbol(quote_currency, target_currency)

        raw_asks = payload.get("asks", [])
        raw_bids = payload.get("bids", [])

        if not raw_asks and not raw_bids:
            return None

        # Update per-symbol timestamp
        self._last_symbol_time[symbol] = time.monotonic()

        bids = [[str(e["price"]), str(e["qty"])] for e in raw_bids]
        asks = [[str(e["price"]), str(e["qty"])] for e in raw_asks]

        # Sort: bids descending, asks ascending
        bids.sort(key=lambda x: float(x[0]), reverse=True)
        asks.sort(key=lambda x: float(x[0]))

        return symbol, bids, asks
