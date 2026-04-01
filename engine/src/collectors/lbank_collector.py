"""LBank public orderbook collector via native WebSocket (V2)."""
from __future__ import annotations

from typing import Callable, Awaitable

import structlog

from src.collectors.base_collector import BaseCollector

logger = structlog.get_logger(__name__)


def _normalize_symbol(symbol: str) -> str:
    """Convert canonical "BTC/USDT" -> LBank format "btc_usdt" (lowercase)."""
    return symbol.replace("/", "_").lower()


def _denormalize_symbol(ws_symbol: str) -> str:
    """Convert LBank format "btc_usdt" -> canonical "BTC/USDT"."""
    parts = ws_symbol.upper().split("_", 1)
    if len(parts) == 2:
        return f"{parts[0]}/{parts[1]}"
    return ws_symbol.upper()


class LBankCollector(BaseCollector):
    """Collects LBank spot orderbook data via the public WebSocket V2 API.

    Endpoint: wss://www.lbkex.net/ws/V2/

    Subscribes to the orderBook depth channel per symbol (20 levels).
    LBank uses a JSON-based subscribe/response protocol.
    No API key is required for public orderbook data.

    References:
        https://www.lbank.com/en-US/docs/index.html#websocket-market
    """

    _WS_URL = "wss://www.lbkex.net/ws/V2/"
    _DEPTH = 20

    def __init__(
        self,
        symbols: list[str],
        on_orderbook: Callable[[str, str, list, list], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(exchange_id="lbank", symbols=symbols, on_orderbook=on_orderbook)

    # ------------------------------------------------------------------
    # BaseCollector interface
    # ------------------------------------------------------------------

    def _ws_url(self) -> str:
        return self._WS_URL

    def _subscribe_message(self, symbol: str) -> str | dict:
        """Build the LBank V2 subscribe frame for one symbol."""
        lbank_symbol = _normalize_symbol(symbol)
        return {
            "action": "subscribe",
            "subscribe": "orderBook",
            "depth": str(self._DEPTH),
            "pair": lbank_symbol,
        }

    def _parse_message(self, data: dict) -> tuple[str, list, list] | None:
        """Parse LBank orderBook update message.

        LBank depth message format:
        {
            "type": "orderBook",
            "pair": "btc_usdt",
            "depth": "20",
            "asks": [["price", "qty"], ...],
            "bids": [["price", "qty"], ...]
        }

        Subscription ack format:
        {
            "status": "success",
            "action": "subscribe",
            "subscribe": "orderBook",
            "pair": "btc_usdt"
        }
        """
        # Subscription ack or ping — ignore
        msg_type = data.get("type")
        action = data.get("action")

        if action in ("subscribe", "unsubscribe"):
            return None
        if data.get("ping"):
            return None
        if msg_type == "ping":
            return None

        # Only handle orderBook updates
        if msg_type != "orderBook":
            return None

        raw_sym: str = data.get("pair", "")
        if not raw_sym:
            return None

        symbol = _denormalize_symbol(raw_sym)

        bids: list = data.get("bids", [])
        asks: list = data.get("asks", [])

        return symbol, bids, asks
