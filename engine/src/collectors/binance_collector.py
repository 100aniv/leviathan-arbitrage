"""Binance public orderbook collector via native WebSocket."""
from __future__ import annotations

import asyncio
import json
from typing import Callable, Awaitable

import structlog

from src.collectors.base_collector import BaseCollector

logger = structlog.get_logger(__name__)


def _normalize_symbol(symbol: str) -> str:
    """Convert "BTC/USDT" -> "btcusdt"."""
    return symbol.replace("/", "").lower()


def _denormalize_symbol(symbol_lower: str) -> str:
    """Convert "btcusdt" -> "BTC/USDT" using a best-effort split on known quote assets."""
    # Quote assets ordered longest-first to avoid greedy mismatch (e.g. USDT before USD)
    quotes = ["usdt", "busd", "usdc", "btc", "eth", "bnb", "tusd", "usd"]
    s = symbol_lower.lower()
    for q in quotes:
        if s.endswith(q):
            base = s[: -len(q)]
            return f"{base.upper()}/{q.upper()}"
    # Fallback: return as-is uppercased
    return symbol_lower.upper()


class BinanceCollector(BaseCollector):
    """Collects Binance spot orderbook snapshots via the public depth stream.

    Connects to the combined stream endpoint when multiple symbols are given:
        wss://stream.binance.com:9443/stream?streams=btcusdt@depth20@100ms/ethusdt@depth20@100ms

    For a single symbol the per-symbol stream is used:
        wss://stream.binance.com:9443/ws/btcusdt@depth20@100ms

    No API key is required.
    """

    _BASE_WS = "wss://stream.binance.com:9443"

    def __init__(
        self,
        symbols: list[str],
        on_orderbook: Callable[[str, str, list, list], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(exchange_id="binance", symbols=symbols, on_orderbook=on_orderbook)

    # ------------------------------------------------------------------
    # BaseCollector interface
    # ------------------------------------------------------------------

    def _ws_url(self) -> str:
        streams = "/".join(
            f"{_normalize_symbol(s)}@depth20@100ms" for s in self.symbols
        )
        if len(self.symbols) == 1:
            return f"{self._BASE_WS}/ws/{streams}"
        return f"{self._BASE_WS}/stream?streams={streams}"

    def _subscribe_message(self, symbol: str) -> str | dict:
        # Subscription is encoded in the URL path; no subscribe frame needed.
        # Return an empty string so BaseCollector skips sending it.
        return ""

    def _parse_message(self, data: dict) -> tuple[str, list, list] | None:
        # Combined stream wraps payload: {"stream": "btcusdt@depth20@100ms", "data": {...}}
        if "stream" in data and "data" in data:
            stream_name: str = data["stream"]
            payload: dict = data["data"]
            # stream_name format: "btcusdt@depth20@100ms"
            raw_sym = stream_name.split("@")[0]
            symbol = _denormalize_symbol(raw_sym)
            bids = payload.get("bids", [])
            asks = payload.get("asks", [])
            return symbol, bids, asks

        # Single-symbol stream: {"lastUpdateId": ..., "bids": [...], "asks": [...]}
        if "bids" in data and "asks" in data:
            # We can derive the symbol from the URL but it is simpler to store it at init.
            # For single-symbol collectors self.symbols has exactly one entry.
            symbol = self.symbols[0] if self.symbols else "UNKNOWN"
            return symbol, data["bids"], data["asks"]

        return None

    # ------------------------------------------------------------------
    # Override _connect_and_listen to skip empty subscribe frames
    # ------------------------------------------------------------------

    async def _connect_and_listen(self) -> None:
        """Connect and listen; skip sending empty subscribe messages."""
        import websockets

        url = self._ws_url()
        logger.info("collector_connecting", exchange=self.exchange_id, url=url)

        async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
            self._ws = ws
            self._connected = True
            self._reconnect_delay = self.INITIAL_RECONNECT_DELAY
            logger.info("collector_connected", exchange=self.exchange_id)

            # Binance encodes subscription in URL; log symbols but don't send frames
            for symbol in self.symbols:
                logger.info("collector_subscribed", exchange=self.exchange_id, symbol=symbol)

            import time as _time
            async for raw in ws:
                if not self._running:
                    break
                self._last_message_time = _time.monotonic()
                self._message_count += 1
                try:
                    await self._handle_message(raw)
                except Exception as exc:
                    logger.warning("collector_parse_error", exchange=self.exchange_id, error=str(exc))

        self._connected = False
