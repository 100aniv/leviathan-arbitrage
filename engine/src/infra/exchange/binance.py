"""Binance adapter — orderbook checksum validation, spot + futures."""
from __future__ import annotations

import logging
import zlib
from typing import Any

from src.core.models import OrderBook
from src.infra.exchange.ccxt_adapter import CCXTAdapter
from src.infra.exchange.rate_limiter import RateLimitConfig

logger = logging.getLogger(__name__)


class BinanceAdapter(CCXTAdapter):
    """
    Binance-specific adapter.

    Supports both spot (binance) and USD-M futures (binanceusdm).
    Adds orderbook CRC32 checksum validation to catch data corruption.
    """

    def __init__(
        self,
        market_type: str = "spot",
        **kwargs: Any,
    ) -> None:
        exchange_id = "binanceusdm" if market_type == "futures" else "binance"
        super().__init__(exchange_id=exchange_id, **kwargs)
        self._market_type = market_type

    def _parse_orderbook(self, raw: dict, symbol: str) -> OrderBook:
        ob = super()._parse_orderbook(raw, symbol)
        if raw.get("checksum"):
            self._validate_checksum(ob, raw["checksum"])
        return ob

    def _validate_checksum(self, orderbook: OrderBook, expected: int) -> None:
        """Validate orderbook integrity using Binance CRC32 checksum."""
        parts: list[str] = []
        levels = max(len(orderbook.bids), len(orderbook.asks))
        for i in range(min(levels, 100)):
            if i < len(orderbook.bids):
                b = orderbook.bids[i]
                parts.append(f"{b.price}:{b.amount}")
            if i < len(orderbook.asks):
                a = orderbook.asks[i]
                parts.append(f"{a.price}:{a.amount}")

        computed = zlib.crc32(":".join(parts).encode()) & 0xFFFFFFFF
        if computed != (expected & 0xFFFFFFFF):
            logger.warning(
                "Binance orderbook checksum mismatch for %s: computed=%d expected=%d",
                orderbook.symbol,
                computed,
                expected,
            )
