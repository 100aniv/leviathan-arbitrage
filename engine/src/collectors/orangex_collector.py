"""OrangeX public orderbook collector via native WebSocket."""
from __future__ import annotations

from typing import Callable, Awaitable

import structlog

from src.collectors.base_collector import BaseCollector

logger = structlog.get_logger(__name__)


def _normalize_symbol(symbol: str) -> str:
    """Convert canonical "BTC/USDT" -> OrangeX format "BTC_USDT"."""
    return symbol.replace("/", "_").upper()


def _denormalize_symbol(ws_symbol: str) -> str:
    """Convert OrangeX format "BTC_USDT" -> canonical "BTC/USDT"."""
    parts = ws_symbol.upper().split("_", 1)
    if len(parts) == 2:
        return f"{parts[0]}/{parts[1]}"
    return ws_symbol.upper()


class OrangeXCollector(BaseCollector):
    """Collects OrangeX spot orderbook data via the public WebSocket API.

    Endpoint: wss://ws.orangex.com/ws/v1

    Subscribes to the depth channel per symbol (20 levels).
    OrangeX uses a JSON-based subscribe protocol similar to Bybit V5.
    No API key is required for public orderbook data.

    References:
        https://orangex.com/docs/#websocket
    """

    _WS_URL = "wss://ws.orangex.com/ws/v1"
    _DEPTH = 20

    def __init__(
        self,
        symbols: list[str],
        on_orderbook: Callable[[str, str, list, list], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(exchange_id="orangex", symbols=symbols, on_orderbook=on_orderbook)

    # ------------------------------------------------------------------
    # BaseCollector interface
    # ------------------------------------------------------------------

    def _ws_url(self) -> str:
        return self._WS_URL

    def _subscribe_message(self, symbol: str) -> str | dict:
        """Build the OrangeX subscribe frame for one symbol."""
        orangex_symbol = _normalize_symbol(symbol)
        return {
            "op": "subscribe",
            "args": [f"depth.{self._DEPTH}.{orangex_symbol}"],
        }

    def _parse_message(self, data: dict) -> tuple[str, list, list] | None:
        """Parse OrangeX depth update message.

        OrangeX depth message format (Bybit V5-style):
        {
            "topic": "depth.20.BTC_USDT",
            "type": "snapshot",
            "data": {
                "b": [["price", "qty"], ...],
                "a": [["price", "qty"], ...]
            },
            "ts": 1234567890123
        }

        Subscription ack format:
        {
            "op": "subscribe",
            "success": true,
            "ret_msg": ""
        }
        """
        # Subscription ack / pong — ignore
        op = data.get("op")
        if op in ("subscribe", "unsubscribe", "pong"):
            return None
        if data.get("ret_msg") == "pong":
            return None

        # Only handle depth snapshot/delta updates
        topic: str = data.get("topic", "")
        if not topic or not topic.startswith("depth."):
            return None

        # topic format: "depth.20.BTC_USDT"
        parts = topic.split(".", 2)
        if len(parts) < 3:
            return None

        raw_sym = parts[2]
        symbol = _denormalize_symbol(raw_sym)

        inner = data.get("data", {})
        if not inner:
            return None

        # Both snapshot and delta treated as full replace (top-of-book arbitrage)
        bids: list = inner.get("b", [])
        asks: list = inner.get("a", [])

        return symbol, bids, asks
