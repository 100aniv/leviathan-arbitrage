"""Market data normalizer — uniform ticker format across exchanges.

All prices and quantities use Decimal to avoid floating-point precision loss.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .client import RedisClient

logger = logging.getLogger(__name__)

# Known exchange symbol → common "BASE/QUOTE" mappings
_KNOWN_SYMBOLS: dict[str, str] = {
    "BTCUSDT": "BTC/USDT",
    "ETHUSDT": "ETH/USDT",
    "SOLUSDT": "SOL/USDT",
    "BNBUSDT": "BNB/USDT",
    "XRPUSDT": "XRP/USDT",
    "ADAUSDT": "ADA/USDT",
    "DOGEUSDT": "DOGE/USDT",
}

_QUOTE_CURRENCIES = ("USDT", "USDC", "BUSD", "BTC", "ETH", "BNB")


def _normalize_symbol(raw: str) -> str:
    """Convert exchange-native symbol to common BASE/QUOTE format."""
    upper = raw.upper()
    if upper in _KNOWN_SYMBOLS:
        return _KNOWN_SYMBOLS[upper]
    for quote in _QUOTE_CURRENCIES:
        if upper.endswith(quote):
            base = upper[: -len(quote)]
            return f"{base}/{quote}"
    return raw


@dataclass
class NormalizedTicker:
    """Exchange-agnostic ticker snapshot."""

    exchange: str
    symbol: str       # e.g. "BTC/USDT"
    bid: Decimal      # best bid price
    ask: Decimal      # best ask price
    last: Decimal     # last trade price
    volume: Decimal   # 24h base volume
    timestamp: int    # Unix milliseconds


class MarketDataNormalizer:
    """
    Normalizes raw exchange ticker payloads to NormalizedTicker.

    Supports Binance (bookTicker / miniTicker) and Bybit V5 formats.
    Stores/retrieves tickers in Redis hashes for cross-component sharing.
    """

    def __init__(self, client: Optional[RedisClient] = None) -> None:
        self._client = client

    def _ticker_key(self, exchange: str, symbol: str) -> str:
        sym = symbol.replace("/", "-")
        return f"leviathan:ticker:{exchange}:{sym}"

    # ── Normalization ──────────────────────────────────────────────────────────

    def normalize_binance(self, raw: dict) -> NormalizedTicker:
        """
        Normalize Binance WebSocket bookTicker or 24h miniTicker payload.

        bookTicker fields: s=symbol, b=bid, B=bidQty, a=ask, A=askQty
        miniTicker fields: s=symbol, c=close, v=volume, T=trade time
        """
        symbol = _normalize_symbol(raw["s"])
        return NormalizedTicker(
            exchange="binance",
            symbol=symbol,
            bid=Decimal(raw["b"]),
            ask=Decimal(raw["a"]),
            last=Decimal(raw.get("c", raw["a"])),
            volume=Decimal(raw.get("v", "0")),
            timestamp=int(raw.get("T", raw.get("t", int(time.time() * 1000)))),
        )

    def normalize_bybit(self, raw: dict) -> NormalizedTicker:
        """
        Normalize Bybit V5 ticker payload.

        Fields: symbol, bid1Price, bid1Size, ask1Price, ask1Size, lastPrice, volume24h
        """
        symbol = _normalize_symbol(raw["symbol"])
        return NormalizedTicker(
            exchange="bybit",
            symbol=symbol,
            bid=Decimal(raw["bid1Price"]),
            ask=Decimal(raw["ask1Price"]),
            last=Decimal(raw.get("lastPrice", raw["ask1Price"])),
            volume=Decimal(raw.get("volume24h", "0")),
            timestamp=int(time.time() * 1000),
        )

    # ── Spread ─────────────────────────────────────────────────────────────────

    def cross_exchange_spread(
        self, ticker_a: NormalizedTicker, ticker_b: NormalizedTicker
    ) -> Decimal:
        """
        Calculate cross-exchange spread assuming:
          - Buy on exchange A at ask price
          - Sell on exchange B at bid price

        Positive value = arbitrage opportunity (B.bid > A.ask).
        Negative value = no opportunity.

        NOTE: this is gross spread before fees/slippage deduction.
        """
        return ticker_b.bid - ticker_a.ask

    # ── Redis storage ──────────────────────────────────────────────────────────

    async def store_ticker(self, ticker: NormalizedTicker) -> None:
        """Persist normalized ticker to Redis hash."""
        if self._client is None:
            raise RuntimeError("No Redis client configured — pass client= to MarketDataNormalizer")
        key = self._ticker_key(ticker.exchange, ticker.symbol)
        await self._client.hset(key, mapping={
            "exchange": ticker.exchange,
            "symbol": ticker.symbol,
            "bid": str(ticker.bid),
            "ask": str(ticker.ask),
            "last": str(ticker.last),
            "volume": str(ticker.volume),
            "timestamp": str(ticker.timestamp),
        })

    async def get_ticker(self, exchange: str, symbol: str) -> Optional[NormalizedTicker]:
        """Retrieve normalized ticker from Redis. Returns None if absent."""
        if self._client is None:
            raise RuntimeError("No Redis client configured")
        key = self._ticker_key(exchange, symbol)
        raw = await self._client.hgetall(key)
        if not raw:
            return None

        def _decode(k: str) -> str:
            v = raw.get(k.encode()) or raw.get(k)
            if v is None:
                return ""
            return v.decode() if isinstance(v, bytes) else v

        return NormalizedTicker(
            exchange=_decode("exchange"),
            symbol=_decode("symbol"),
            bid=Decimal(_decode("bid")),
            ask=Decimal(_decode("ask")),
            last=Decimal(_decode("last")),
            volume=Decimal(_decode("volume")),
            timestamp=int(_decode("timestamp")),
        )
