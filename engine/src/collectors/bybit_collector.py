"""Bybit public orderbook collector via native WebSocket."""
from __future__ import annotations

import time
from typing import Callable, Awaitable

import structlog

from src.collectors.base_collector import BaseCollector

logger = structlog.get_logger(__name__)


def _normalize_symbol(symbol: str) -> str:
    """Convert "BTC/USDT" -> "BTCUSDT"."""
    return symbol.replace("/", "").upper()


def _denormalize_symbol(symbol_upper: str) -> str:
    """Convert "BTCUSDT" -> "BTC/USDT" using a best-effort split on known quote assets."""
    quotes = ["USDT", "BUSD", "USDC", "BTC", "ETH", "BNB", "TUSD", "USD"]
    s = symbol_upper.upper()
    for q in quotes:
        if s.endswith(q):
            base = s[: -len(q)]
            return f"{base}/{q}"
    return symbol_upper


class BybitCollector(BaseCollector):
    """Collects Bybit spot orderbook data via the public V5 WebSocket.

    Endpoint: wss://stream.bybit.com/v5/public/spot

    Both snapshot and delta messages are treated as full snapshots
    (replace full local state). This is safe for arbitrage use where
    the latest top-of-book price matters more than incremental accuracy.

    No API key is required.
    """

    _WS_URL = "wss://stream.bybit.com/v5/public/spot"
    _DEPTH = 50

    def __init__(
        self,
        symbols: list[str],
        on_orderbook: Callable[[str, str, list, list], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(exchange_id="bybit", symbols=symbols, on_orderbook=on_orderbook)

    # ------------------------------------------------------------------
    # BaseCollector interface
    # ------------------------------------------------------------------

    def _ws_url(self) -> str:
        return self._WS_URL

    def _subscribe_message(self, symbol: str) -> str | dict:
        """Build the Bybit V5 subscribe frame for one symbol."""
        topic = f"orderbook.{self._DEPTH}.{_normalize_symbol(symbol)}"
        return {
            "op": "subscribe",
            "args": [topic],
        }

    def _parse_message(self, data: dict) -> tuple[str, list, list] | None:
        msg_type = data.get("type")

        # snapshot or delta — treat both as a full replace
        if msg_type in ("snapshot", "delta"):
            topic: str = data.get("topic", "")
            # topic format: "orderbook.50.BTCUSDT"
            parts = topic.split(".")
            if len(parts) < 3:
                return None
            raw_sym = parts[2]
            symbol = _denormalize_symbol(raw_sym)

            inner = data.get("data", {})
            bids: list = inner.get("b", [])
            asks: list = inner.get("a", [])
            return symbol, bids, asks

        # op: "subscribe" ack, pong frames, etc. — ignore
        return None
