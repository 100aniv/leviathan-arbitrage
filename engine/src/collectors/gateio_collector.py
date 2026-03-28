"""Gate.io public orderbook collector via native WebSocket (Spot V4)."""
from __future__ import annotations

import time
from typing import Callable, Awaitable

import structlog

from src.collectors.base_collector import BaseCollector

logger = structlog.get_logger(__name__)


def _normalize_symbol(symbol: str) -> str:
    """Convert canonical "BTC/USDT" -> Gate.io format "BTC_USDT"."""
    return symbol.replace("/", "_")


def _denormalize_symbol(ws_symbol: str) -> str:
    """Convert Gate.io format "BTC_USDT" -> canonical "BTC/USDT"."""
    return ws_symbol.replace("_", "/", 1)


class GateioCollector(BaseCollector):
    """Collects Gate.io spot orderbook data via the public Spot V4 WebSocket.

    Endpoint: wss://api.gateio.ws/ws/v4/

    Subscribes to the "spot.order_book_update" channel (20 levels, 100ms).
    No API key is required for public orderbook data.
    """

    _WS_URL = "wss://api.gateio.ws/ws/v4/"
    _CHANNEL = "spot.order_book_update"
    _DEPTH = "20"
    _INTERVAL = "100ms"

    def __init__(
        self,
        symbols: list[str],
        on_orderbook: Callable[[str, str, list, list], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(exchange_id="gateio", symbols=symbols, on_orderbook=on_orderbook)

    # ------------------------------------------------------------------
    # BaseCollector interface
    # ------------------------------------------------------------------

    def _ws_url(self) -> str:
        return self._WS_URL

    def _subscribe_message(self, symbol: str) -> str | dict:
        """Build the Gate.io V4 subscribe frame for one symbol."""
        return {
            "time": int(time.time()),
            "channel": self._CHANNEL,
            "event": "subscribe",
            "payload": [_normalize_symbol(symbol), self._DEPTH, self._INTERVAL],
        }

    def _parse_message(self, data: dict) -> tuple[str, list, list] | None:
        # Subscription ack: {"event": "subscribe", ...} — ignore
        event = data.get("event")
        if event in ("subscribe", "unsubscribe"):
            return None

        # Only handle update events on the expected channel
        if data.get("channel") != self._CHANNEL:
            return None
        if event != "update":
            return None

        result = data.get("result")
        if not result:
            return None

        ws_symbol: str = result.get("s", "")
        if not ws_symbol:
            return None

        symbol = _denormalize_symbol(ws_symbol)

        # Gate.io incremental update: b=bids, a=asks, [[price, qty], ...]
        bids: list = result.get("b", [])
        asks: list = result.get("a", [])

        return symbol, bids, asks
