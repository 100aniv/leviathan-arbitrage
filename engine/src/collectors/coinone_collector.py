"""Coinone public orderbook collector via native WebSocket."""
from __future__ import annotations

from typing import Callable, Awaitable

import structlog

from src.collectors.base_collector import BaseCollector

logger = structlog.get_logger(__name__)


def _normalize_symbol(symbol: str) -> tuple[str, str]:
    """Convert 'BTC/KRW' -> (quote_currency='KRW', target_currency='BTC')."""
    if "/" in symbol:
        base, quote = symbol.split("/", 1)
        return quote, base
    return "KRW", symbol


def _denormalize_symbol(quote_currency: str, target_currency: str) -> str:
    """Convert quote_currency='KRW', target_currency='BTC' -> 'BTC/KRW'."""
    return f"{target_currency}/{quote_currency}"


class CoinoneCollector(BaseCollector):
    """Collects Coinone orderbook snapshots via the public WebSocket.

    Connects to: wss://stream.coinone.co.kr
    Subscription: JSON with request_type=SUBSCRIBE, channel=ORDERBOOK, topic.
    No API key is required for public orderbook data.
    30-minute PING keepalive required.
    """

    _WS_URL = "wss://stream.coinone.co.kr"

    def __init__(
        self,
        symbols: list[str],
        on_orderbook: Callable[[str, str, list, list], Awaitable[None]] | None = None,
    ) -> None:
        # 30-min keepalive: ping_interval=1800s
        super().__init__(
            exchange_id="coinone",
            symbols=symbols,
            on_orderbook=on_orderbook,
            ping_interval=1800,
            ping_timeout=30,
        )

    def _ws_url(self) -> str:
        return self._WS_URL

    def _subscribe_message(self, symbol: str) -> str | dict:
        """Coinone subscription message."""
        quote_currency, target_currency = _normalize_symbol(symbol)
        return {
            "request_type": "SUBSCRIBE",
            "channel": "ORDERBOOK",
            "topic": {
                "quote_currency": quote_currency,
                "target_currency": target_currency,
            },
        }

    def _parse_message(self, data: dict) -> tuple[str, list, list] | None:
        """Parse Coinone ORDERBOOK DATA message.

        Coinone format:
        {
            "response_type": "DATA",
            "channel": "ORDERBOOK",
            "data": {
                "quote_currency": "KRW",
                "target_currency": "BTC",
                "timestamp": 1234567890,
                "id": "...",
                "asks": [{"price": "50000", "qty": "0.1"}, ...],
                "bids": [{"price": "49900", "qty": "0.2"}, ...]
            }
        }
        """
        if data.get("response_type") != "DATA":
            return None
        if data.get("channel") != "ORDERBOOK":
            return None

        payload = data.get("data", {})
        if not payload:
            return None

        quote_currency = payload.get("quote_currency", "")
        target_currency = payload.get("target_currency", "")
        symbol = _denormalize_symbol(quote_currency, target_currency)

        raw_asks = payload.get("asks", [])
        raw_bids = payload.get("bids", [])

        if not raw_asks and not raw_bids:
            return None

        bids = [[str(e["price"]), str(e["qty"])] for e in raw_bids]
        asks = [[str(e["price"]), str(e["qty"])] for e in raw_asks]

        # Sort: bids descending, asks ascending
        bids.sort(key=lambda x: float(x[0]), reverse=True)
        asks.sort(key=lambda x: float(x[0]))

        return symbol, bids, asks
