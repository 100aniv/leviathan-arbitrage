"""Base collector — abstract WebSocket orderbook collector with auto-reconnect."""
from __future__ import annotations

import abc
import asyncio
import random
import time
from collections import deque
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
        ping_timeout: int = 30,
    ) -> None:
        """
        Args:
            exchange_id: Exchange identifier (e.g. "binance")
            symbols: List of trading pairs (e.g. ["BTC/USDT"])
            on_orderbook: Async callback(exchange_id, symbol, bids, asks)
                         bids/asks are list of [price_str, qty_str]
            ping_interval: WebSocket ping interval in seconds (default 20)
            ping_timeout: WebSocket ping timeout in seconds (default 30)
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
        self._ws_latencies: deque[float] = deque(maxlen=1000)

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

            # Subscribe to channels (batch or per-symbol)
            import json as _json
            batch = self._subscribe_all_messages()
            if batch is not None:
                for msg in batch:
                    if isinstance(msg, dict):
                        await ws.send(_json.dumps(msg))
                    else:
                        await ws.send(str(msg))
                for symbol in self.symbols:
                    logger.info("collector_subscribed", exchange=self.exchange_id, symbol=symbol)
            else:
                for symbol in self.symbols:
                    msg = self._subscribe_message(symbol)
                    if isinstance(msg, dict):
                        await ws.send(_json.dumps(msg))
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
        local_recv_ts = time.time()
        data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw

        self._record_ws_latency(data, local_recv_ts)

        result = self._parse_message(data)
        if result is None:
            return  # heartbeat, ack, or irrelevant message

        symbol, bids, asks = result
        if self._on_orderbook:
            await self._on_orderbook(self.exchange_id, symbol, bids, asks)

    def _extract_exchange_ts_ms(self, data: dict) -> float | None:
        """Extract exchange-provided timestamp (ms) from common WS message fields.

        Checks top-level keys first (T, ts, timestamp, time), then nested
        data[0]['ts'] used by OKX-style messages.
        Returns None if no timestamp is found or the value is invalid.
        """
        for field in ("T", "ts", "timestamp", "time"):
            val = data.get(field)
            if isinstance(val, (int, float)) and val > 0:
                return float(val)
        # OKX / some exchanges embed ts inside data[0]
        data_list = data.get("data")
        if isinstance(data_list, list) and data_list:
            entry = data_list[0]
            if isinstance(entry, dict):
                val = entry.get("ts")
                if val is not None:
                    try:
                        ts = float(val)
                        if ts > 0:
                            return ts
                    except (ValueError, TypeError):
                        pass
        return None

    def _record_ws_latency(self, data: dict, local_recv_ts: float | None = None) -> None:
        """Compute and record WS message latency if exchange timestamp is present.

        Latency = local receipt time − exchange timestamp.
        Negative values (clock skew) are clamped to 0.
        Samples are stored in a bounded deque for median/percentile queries.
        A Prometheus histogram observation is also emitted when available.
        """
        exchange_ts_ms = self._extract_exchange_ts_ms(data)
        if exchange_ts_ms is None:
            return
        if local_recv_ts is None:
            local_recv_ts = time.time()
        latency_ms = max(0.0, (local_recv_ts - exchange_ts_ms / 1000.0) * 1000.0)
        self._ws_latencies.append(latency_ms)
        try:
            from src.infra.metrics import WS_MESSAGE_LATENCY
            WS_MESSAGE_LATENCY.labels(exchange=self.exchange_id).observe(latency_ms / 1000.0)
        except Exception:
            pass

    def ws_latency_stats(self) -> dict[str, float | int | None]:
        """Return WS message latency statistics (ms) from the last 1000 samples.

        Returns dict with keys: median_ms, p95_ms, p99_ms, sample_count.
        All latency values are None when no samples have been recorded.
        """
        if not self._ws_latencies:
            return {"median_ms": None, "p95_ms": None, "p99_ms": None, "sample_count": 0}
        sorted_lats = sorted(self._ws_latencies)
        n = len(sorted_lats)

        def _pct(p: float) -> float:
            idx = min(int(p / 100.0 * n), n - 1)
            return sorted_lats[idx]

        return {
            "median_ms": _pct(50),
            "p95_ms": _pct(95),
            "p99_ms": _pct(99),
            "sample_count": n,
        }

    async def _backoff(self) -> None:
        """Exponential backoff with ±25% jitter between reconnection attempts."""
        jitter = random.uniform(0.75, 1.25)
        delay = self._reconnect_delay * jitter
        logger.info("collector_reconnecting", exchange=self.exchange_id, delay_s=round(delay, 2))
        await asyncio.sleep(delay)
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

    def _subscribe_all_messages(self) -> list[str | dict] | None:
        """Return batch subscription messages for all symbols at once.

        Override in subclasses where the exchange requires a single message
        containing all symbols (e.g. Upbit, Bithumb). Return None to use
        the default per-symbol _subscribe_message() fallback.
        """
        return None

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
