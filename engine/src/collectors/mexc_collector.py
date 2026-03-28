"""MEXC public orderbook collector via native WebSocket."""
from __future__ import annotations

import asyncio
import json
from typing import Callable, Awaitable

import structlog

from src.collectors.base_collector import BaseCollector

logger = structlog.get_logger(__name__)

_QUOTES = ["USDT", "USDC", "BUSD", "TUSD", "BTC", "ETH", "BNB", "USD"]


def _normalize_symbol(symbol: str) -> str:
    """Convert canonical "BTC/USDT" -> MEXC symbol "BTCUSDT"."""
    return symbol.replace("/", "")


def _denormalize_symbol(raw: str) -> str:
    """Convert MEXC symbol "BTCUSDT" -> canonical "BTC/USDT"."""
    s = raw.upper()
    for q in _QUOTES:
        if s.endswith(q):
            base = s[: -len(q)]
            return f"{base}/{q}"
    return raw


class MexcCollector(BaseCollector):
    """Collects MEXC spot orderbook data via the public WebSocket.

    Endpoint: wss://wbs.mexc.com/ws

    Subscribes to the spot limit depth channel (top-20 levels).
    PING/PONG keepalive is handled via custom JSON frames.

    No API key is required.
    """

    _WS_URL = "wss://wbs.mexc.com/ws"
    _DEPTH_LEVEL = 20

    def __init__(
        self,
        symbols: list[str],
        on_orderbook: Callable[[str, str, list, list], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(exchange_id="mexc", symbols=symbols, on_orderbook=on_orderbook)
        self._ping_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._running = True
        self._ping_task = asyncio.create_task(self._json_ping_loop())
        await super().start()

    async def stop(self) -> None:
        if self._ping_task:
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass
        await super().stop()

    async def _json_ping_loop(self) -> None:
        """MEXC requires JSON PING every 15 seconds to keep WS alive."""
        while self._running:
            await asyncio.sleep(15)
            if hasattr(self, '_ws') and self._ws:
                try:
                    await self._ws.send(json.dumps({"method": "PING"}))
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # BaseCollector interface
    # ------------------------------------------------------------------

    def _ws_url(self) -> str:
        return self._WS_URL

    def _subscribe_message(self, symbol: str) -> str | dict:
        """Build the MEXC subscribe frame for one symbol."""
        inst = _normalize_symbol(symbol)
        return {
            "method": "SUBSCRIPTION",
            "params": [f"spot@public.limit.depth.v3.api@{inst}@{self._DEPTH_LEVEL}"],
        }

    def _parse_message(self, data: dict) -> tuple[str, list, list] | None:
        # PONG keepalive response — ignore
        if data.get("method") == "PONG":
            return None

        # Subscription ack or other control messages — ignore
        channel: str = data.get("c", "")
        if not channel:
            return None

        # Channel format: "spot@public.limit.depth.v3.api@BTCUSDT@20"
        parts = channel.split("@")
        if len(parts) < 3:
            return None

        raw_symbol = parts[2]
        symbol = _denormalize_symbol(raw_symbol)

        d = data.get("d", {})
        if not d:
            return None

        # Level format: [{"p": price_str, "v": qty_str}, ...]
        raw_bids = d.get("bids", [])
        raw_asks = d.get("asks", [])

        bids = [[entry["p"], entry["v"]] for entry in raw_bids if "p" in entry and "v" in entry]
        asks = [[entry["p"], entry["v"]] for entry in raw_asks if "p" in entry and "v" in entry]

        return symbol, bids, asks
