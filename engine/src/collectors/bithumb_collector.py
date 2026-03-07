"""Bithumb public orderbook collector via native WebSocket."""
from __future__ import annotations

import json
from typing import Callable, Awaitable

import structlog

from src.collectors.base_collector import BaseCollector

logger = structlog.get_logger(__name__)


def _normalize_symbol(symbol: str) -> str:
    """Convert 'BTC/KRW' -> 'BTC_KRW' (Bithumb format)."""
    return symbol.replace("/", "_")


def _denormalize_symbol(bithumb_sym: str) -> str:
    """Convert 'BTC_KRW' -> 'BTC/KRW'."""
    return bithumb_sym.replace("_", "/")


class BithumbCollector(BaseCollector):
    """Collects Bithumb orderbook snapshots via the public WebSocket.

    Connects to: wss://pubwss.bithumb.com/pub/ws
    Subscription: JSON with type=orderbookdepth + symbols + tickTypes.
    No API key is required for public orderbook data.
    """

    _WS_URL = "wss://pubwss.bithumb.com/pub/ws"

    def __init__(
        self,
        symbols: list[str],
        on_orderbook: Callable[[str, str, list, list], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(exchange_id="bithumb", symbols=symbols, on_orderbook=on_orderbook)

    def _ws_url(self) -> str:
        return self._WS_URL

    def _subscribe_message(self, symbol: str) -> str | dict:
        """Bithumb subscription message."""
        bithumb_sym = _normalize_symbol(symbol)
        return {
            "type": "orderbookdepth",
            "symbols": [bithumb_sym],
            "tickTypes": ["1H"],
        }

    def _parse_message(self, data: dict) -> tuple[str, list, list] | None:
        """Parse Bithumb orderbookdepth message.

        Bithumb format:
        {
            "type": "orderbookdepth",
            "content": {
                "list": [
                    {"symbol": "BTC_KRW", "orderType": "ask",
                     "price": "50000000", "quantity": "0.1"},
                    {"symbol": "BTC_KRW", "orderType": "bid",
                     "price": "49990000", "quantity": "0.2"},
                    ...
                ]
            }
        }
        """
        msg_type = data.get("type")
        if msg_type != "orderbookdepth":
            # Check for status/connected messages
            return None

        content = data.get("content", {})
        entries = content.get("list", [])

        if not entries:
            return None

        # Determine symbol from first entry
        raw_sym = entries[0].get("symbol", "")
        symbol = _denormalize_symbol(raw_sym)

        bids: list[list[str]] = []
        asks: list[list[str]] = []

        for entry in entries:
            price = str(entry.get("price", "0"))
            qty = str(entry.get("quantity", "0"))
            order_type = entry.get("orderType", "")

            if order_type == "bid":
                bids.append([price, qty])
            elif order_type == "ask":
                asks.append([price, qty])

        # Sort: bids descending, asks ascending
        bids.sort(key=lambda x: float(x[0]), reverse=True)
        asks.sort(key=lambda x: float(x[0]))

        return symbol, bids, asks
