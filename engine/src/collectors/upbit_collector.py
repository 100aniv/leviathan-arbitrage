"""Upbit public orderbook collector via native WebSocket."""
from __future__ import annotations

import json
import uuid
from typing import Callable, Awaitable

import structlog

from src.collectors.base_collector import BaseCollector

logger = structlog.get_logger(__name__)


def _normalize_symbol(symbol: str) -> str:
    """Convert 'BTC/USDT' -> 'USDT-BTC', 'BTC/KRW' -> 'KRW-BTC' (Upbit format)."""
    if "/" in symbol:
        base, quote = symbol.split("/", 1)
        return f"{quote}-{base}"
    return symbol


def _denormalize_symbol(upbit_code: str) -> str:
    """Convert 'KRW-BTC' -> 'BTC/KRW', 'USDT-BTC' -> 'BTC/USDT'."""
    if "-" in upbit_code:
        quote, base = upbit_code.split("-", 1)
        return f"{base}/{quote}"
    return upbit_code


class UpbitCollector(BaseCollector):
    """Collects Upbit orderbook snapshots via the public WebSocket.

    Connects to: wss://api.upbit.com/websocket/v1
    Subscription: JSON array with ticket + orderbook type + market codes.
    No API key is required for public orderbook data.
    """

    _WS_URL = "wss://api.upbit.com/websocket/v1"

    def __init__(
        self,
        symbols: list[str],
        on_orderbook: Callable[[str, str, list, list], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(exchange_id="upbit", symbols=symbols, on_orderbook=on_orderbook)

    def _ws_url(self) -> str:
        return self._WS_URL

    def _subscribe_message(self, symbol: str) -> str | dict:
        """Upbit requires a JSON array: [ticket, type+codes].

        Only called once per symbol; we batch all symbols in one message.
        """
        code = _normalize_symbol(symbol)
        return json.dumps([
            {"ticket": f"leviathan-{uuid.uuid4().hex[:8]}"},
            {"type": "orderbook", "codes": [code]},
        ])

    def _parse_message(self, data: dict) -> tuple[str, list, list] | None:
        """Parse Upbit orderbook message.

        Upbit orderbook format:
        {
            "type": "orderbook",
            "code": "KRW-BTC",
            "orderbook_units": [
                {"ask_price": 50000000, "bid_price": 49990000,
                 "ask_size": 0.1, "bid_size": 0.2}, ...
            ]
        }
        """
        if data.get("type") != "orderbook":
            return None

        code = data.get("code", "")
        symbol = _denormalize_symbol(code)
        units = data.get("orderbook_units", [])

        if not units:
            return None

        # Convert to [[price_str, qty_str], ...] format
        bids = [[str(u["bid_price"]), str(u["bid_size"])] for u in units]
        asks = [[str(u["ask_price"]), str(u["ask_size"])] for u in units]

        return symbol, bids, asks

    async def _handle_message(self, raw: str | bytes) -> None:
        """Override to handle Upbit's binary (bytes) messages."""
        if isinstance(raw, bytes):
            data = json.loads(raw.decode("utf-8"))
        else:
            data = json.loads(raw)

        result = self._parse_message(data)
        if result is None:
            return

        symbol, bids, asks = result
        if self._on_orderbook:
            await self._on_orderbook(self.exchange_id, symbol, bids, asks)
