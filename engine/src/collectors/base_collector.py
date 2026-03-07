"""Base collector — abstract WebSocket orderbook collector with auto-reconnect."""
from __future__ import annotations

import abc
import asyncio
import time
from typing import Any, Callable, Awaitable

import structlog

logger = structlog.get_logger(__name__)


class BaseCollector(abc.ABC):
    """Abstract base for exchange-specific WebSocket orderbook collectors.

    Features:
    - Auto-reconnect with exponential backoff (1s → 2s → 4s → ... max 60s)
    - Heartbeat / ping-pong handling
    - Structured logging with exchange context
    - Callback-based orderbook delivery

    Subclasses must implement:
    - _ws_url() -> str
    - _subscribe_message(symbol) -> str | dict
    - _parse_message(raw) -> parsed orderbook data or None
    """

    MAX_RECONNECT_DELAY = 60.0
    INITIAL_RECONNECT_DELAY = 1.0

    def __init__(
        self,
        exchange_id: str,
        symbols: list[str],
        on_orderbook: Callable[[str, str, list, list], Awaitable[None]] | None = None,
        ping_interval: int = 20,
        ping_timeout: int = 10,
    ) -> None:
        """
        Args:
            exchange_id: Exchange identifier (e.g. "binance")
            symbols: List of trading pairs (e.g. ["BTC/USDT"])
            on_orderbook: Async callback(exchange_id, symbol, bids, asks)
                         bids/asks are list of [price_str, qty_str]
            ping_interval: WebSocket ping interval in seconds (default 20)
            ping_timeout: WebSocket ping timeout in seconds (default 10)
        """
        self.exchange_id = exchange_id
        self.symbols = symbols
        self._on_orderbook = on_orderbook
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self._running = False
        self._ws = None
        self._reconnect_delay = self.INITIAL_RECONNECT_DELAY
        self._connected = False
        self._message_count = 0
        self._last_message_time = 0.0

    async def start(self) -> None:
        """Start the collector. Runs until stop() is called."""
        self._running = True
        while self._running:
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                if not self._running:
                    break
                logger.error("collector_error", exchange=self.exchange_id, error=str(exc))
                await self._backoff()

    async def stop(self) -> None:
        """Stop the collector gracefully."""
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._connected = False

    async def _connect_and_listen(self) -> None:
        """Connect to WebSocket and process messages."""
        import websockets

        url = self._ws_url()
        logger.info("collector_connecting", exchange=self.exchange_id, url=url)

        async with websockets.connect(url, ping_interval=self.ping_interval, ping_timeout=self.ping_timeout) as ws:
            self._ws = ws
            self._connected = True
            self._reconnect_delay = self.INITIAL_RECONNECT_DELAY
            logger.info("collector_connected", exchange=self.exchange_id)

            # Subscribe to channels
            for symbol in self.symbols:
                msg = self._subscribe_message(symbol)
                if isinstance(msg, dict):
                    import json
                    await ws.send(json.dumps(msg))
                else:
                    await ws.send(str(msg))
                logger.info("collector_subscribed", exchange=self.exchange_id, symbol=symbol)

            # Listen for messages
            async for raw in ws:
                if not self._running:
                    break
                self._last_message_time = time.monotonic()
                self._message_count += 1
                try:
                    await self._handle_message(raw)
                except Exception as exc:
                    logger.warning("collector_parse_error", exchange=self.exchange_id, error=str(exc))

        self._connected = False

    async def _handle_message(self, raw: str | bytes) -> None:
        """Parse and dispatch a WebSocket message."""
        import json
        data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw

        result = self._parse_message(data)
        if result is None:
            return  # heartbeat, ack, or irrelevant message

        symbol, bids, asks = result
        if self._on_orderbook:
            await self._on_orderbook(self.exchange_id, symbol, bids, asks)

    async def _backoff(self) -> None:
        """Exponential backoff between reconnection attempts."""
        logger.info("collector_reconnecting", exchange=self.exchange_id, delay_s=self._reconnect_delay)
        await asyncio.sleep(self._reconnect_delay)
        self._reconnect_delay = min(self._reconnect_delay * 2, self.MAX_RECONNECT_DELAY)

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "exchange": self.exchange_id,
            "connected": self._connected,
            "message_count": self._message_count,
            "last_message_age_s": time.monotonic() - self._last_message_time if self._last_message_time else None,
        }

    # --- Abstract methods ---

    @abc.abstractmethod
    def _ws_url(self) -> str:
        """Return the WebSocket endpoint URL."""
        ...

    @abc.abstractmethod
    def _subscribe_message(self, symbol: str) -> str | dict:
        """Return the subscription message for a symbol."""
        ...

    @abc.abstractmethod
    def _parse_message(self, data: dict) -> tuple[str, list, list] | None:
        """Parse a WebSocket message into (symbol, bids, asks) or None if not orderbook data.

        bids/asks format: [[price_str, qty_str], ...]
        """
        ...
