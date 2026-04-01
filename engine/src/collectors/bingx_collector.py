"""BingX public orderbook collector via native WebSocket (Spot V2)."""
from __future__ import annotations

import gzip
import time
from typing import Callable, Awaitable

import structlog

from src.collectors.base_collector import BaseCollector

logger = structlog.get_logger(__name__)


def _normalize_symbol(symbol: str) -> str:
    """Convert canonical "BTC/USDT" -> BingX format "BTC-USDT"."""
    return symbol.replace("/", "-").upper()


def _denormalize_symbol(ws_symbol: str) -> str:
    """Convert BingX format "BTC-USDT" -> canonical "BTC/USDT"."""
    return ws_symbol.replace("-", "/", 1)


class BingXCollector(BaseCollector):
    """Collects BingX spot orderbook data via the public WebSocket API.

    Endpoint: wss://open-api-ws.bingx.com/market

    Subscribes to the spot depth (20 level) channel per symbol.
    BingX WebSocket uses gzip-compressed messages.
    No API key is required for public orderbook data.

    References:
        https://bingx-api.github.io/docs/#/en-us/spot/socket/market.html
    """

    _WS_URL = "wss://open-api-ws.bingx.com/market"
    _DEPTH = 20

    def __init__(
        self,
        symbols: list[str],
        on_orderbook: Callable[[str, str, list, list], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(exchange_id="bingx", symbols=symbols, on_orderbook=on_orderbook)

    # ------------------------------------------------------------------
    # BaseCollector interface
    # ------------------------------------------------------------------

    def _ws_url(self) -> str:
        return self._WS_URL

    def _subscribe_message(self, symbol: str) -> str | dict:
        """Build the BingX subscribe frame for one symbol."""
        bingx_symbol = _normalize_symbol(symbol)
        return {
            "id": f"sub-{bingx_symbol}-{int(time.time() * 1000)}",
            "reqType": "sub",
            "dataType": f"{bingx_symbol}@depth{self._DEPTH}",
        }

    def _parse_message(self, data: dict) -> tuple[str, list, list] | None:
        """Parse BingX depth update message.

        BingX depth message format:
        {
            "code": 0,
            "dataType": "BTC-USDT@depth20",
            "data": {
                "bids": [["price", "qty"], ...],
                "asks": [["price", "qty"], ...]
            }
        }
        """
        # Handle ping/pong and subscription acks
        if "ping" in data or data.get("reqType") in ("sub", "unsub"):
            return None

        # Check for error code
        code = data.get("code")
        if code is not None and code != 0:
            logger.warning(
                "bingx_collector.error_response",
                code=code,
                msg=data.get("msg", ""),
            )
            return None

        data_type: str = data.get("dataType", "")
        if not data_type or "@depth" not in data_type:
            return None

        # Extract symbol from dataType: "BTC-USDT@depth20" -> "BTC-USDT"
        raw_sym = data_type.split("@")[0]
        symbol = _denormalize_symbol(raw_sym)

        inner = data.get("data", {})
        if not inner:
            return None

        bids: list = inner.get("bids", [])
        asks: list = inner.get("asks", [])

        return symbol, bids, asks

    async def _handle_message(self, raw: str | bytes) -> None:
        """Handle potentially gzip-compressed BingX messages."""
        # BingX compresses some messages with gzip
        if isinstance(raw, bytes):
            try:
                raw = gzip.decompress(raw).decode("utf-8")
            except (gzip.BadGzipFile, OSError):
                # Not gzip compressed — treat as raw string
                raw = raw.decode("utf-8", errors="replace")

        await super()._handle_message(raw)
