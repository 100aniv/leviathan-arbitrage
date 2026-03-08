"""FundingRateCollector — REST polling collector for funding rates across 4 exchanges.

Supports Binance Futures, Bybit, OKX, and Bitget.
Stores results in FundingRateStore for use by ShadowMode signal generation.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Exchange endpoints
# ---------------------------------------------------------------------------

EXCHANGE_ENDPOINTS: dict[str, str] = {
    "binance_futures": "https://fapi.binance.com/fapi/v1/premiumIndex",
    "bybit": "https://api.bybit.com/v5/market/tickers",
    "okx": "https://www.okx.com/api/v5/public/funding-rate",
    "bitget": "https://api.bitget.com/api/v2/mix/market/current-fund-rate",
}

DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT"]
DEFAULT_EXCHANGES = ["binance_futures", "bybit", "okx", "bitget"]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class FundingRateEntry:
    """Single funding rate snapshot for one exchange+symbol."""

    rate: float
    next_funding_time: float | None  # Unix timestamp seconds, or None if unknown
    updated_at: float = field(default_factory=time.time)


class FundingRateStore:
    """Thread-safe (asyncio) store for funding rate data.

    Keyed by exchange_id -> symbol -> FundingRateEntry.
    """

    def __init__(self) -> None:
        self._data: dict[str, dict[str, FundingRateEntry]] = {}

    def set_rate(
        self,
        exchange: str,
        symbol: str,
        rate: float,
        next_funding_time: float | None = None,
    ) -> None:
        """Store or update a funding rate entry."""
        if exchange not in self._data:
            self._data[exchange] = {}
        self._data[exchange][symbol] = FundingRateEntry(
            rate=rate,
            next_funding_time=next_funding_time,
            updated_at=time.time(),
        )

    def get_rate(self, exchange: str, symbol: str) -> FundingRateEntry | None:
        """Return the entry for a specific exchange+symbol, or None."""
        return self._data.get(exchange, {}).get(symbol)

    def get_all_rates(self) -> dict[str, dict[str, FundingRateEntry]]:
        """Return a shallow copy of the full store."""
        return {ex: dict(syms) for ex, syms in self._data.items()}

    def get_rate_diff(self, symbol: str, exchange_a: str, exchange_b: str) -> float | None:
        """Return rate_a - rate_b for a symbol across two exchanges.

        Returns None if either rate is missing.
        """
        entry_a = self.get_rate(exchange_a, symbol)
        entry_b = self.get_rate(exchange_b, symbol)
        if entry_a is None or entry_b is None:
            return None
        return entry_a.rate - entry_b.rate


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


class FundingRateCollector:
    """REST polling collector for funding rates across 4 exchanges.

    Does NOT inherit BaseCollector (which is WebSocket-based).
    Runs an asyncio polling loop every `poll_interval` seconds.

    Usage::

        collector = FundingRateCollector(
            symbols=["BTC/USDT", "ETH/USDT"],
            http_client=shared_client,
        )
        asyncio.create_task(collector.start())
        # Later:
        entry = collector.store.get_rate("binance_futures", "BTC/USDT")
    """

    def __init__(
        self,
        symbols: list[str] | None = None,
        exchanges: list[str] | None = None,
        poll_interval: float = 60.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.symbols = symbols if symbols is not None else list(DEFAULT_SYMBOLS)
        self.exchanges = exchanges if exchanges is not None else list(DEFAULT_EXCHANGES)
        self.poll_interval = poll_interval
        self.store = FundingRateStore()

        self._http_client = http_client
        self._owns_client = http_client is None
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the polling loop. Runs until stop() is called."""
        if self._owns_client:
            self._http_client = httpx.AsyncClient(timeout=10.0)
        self._running = True
        try:
            while self._running:
                await self.poll_once()
                await asyncio.sleep(self.poll_interval)
        except asyncio.CancelledError:
            pass
        finally:
            if self._owns_client and self._http_client is not None:
                await self._http_client.aclose()
                self._http_client = None

    async def stop(self) -> None:
        """Signal the polling loop to stop."""
        self._running = False
        if self._task is not None:
            self._task.cancel()

    async def poll_once(self) -> dict[str, dict[str, FundingRateEntry]]:
        """Fetch funding rates from all configured exchanges for all symbols.

        Returns the newly fetched entries (also stored in self.store).
        """
        fetched: dict[str, dict[str, FundingRateEntry]] = {}

        for symbol in self.symbols:
            for exchange in self.exchanges:
                try:
                    entry = await self._fetch(exchange, symbol)
                    if entry is not None:
                        self.store.set_rate(exchange, symbol, entry.rate, entry.next_funding_time)
                        fetched.setdefault(exchange, {})[symbol] = entry
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "funding_rate_collector.fetch_failed",
                        exchange=exchange,
                        symbol=symbol,
                        error=str(exc),
                    )

        if fetched:
            logger.debug(
                "funding_rate_collector.updated",
                exchanges=list(fetched.keys()),
                symbols=self.symbols,
            )

        return fetched

    # -----------------------------------------------------------------------
    # Internal dispatch
    # -----------------------------------------------------------------------

    async def _fetch(self, exchange: str, symbol: str) -> FundingRateEntry | None:
        """Dispatch to the exchange-specific fetcher."""
        if exchange == "binance_futures":
            return await self._fetch_binance(symbol)
        elif exchange == "bybit":
            return await self._fetch_bybit(symbol)
        elif exchange == "okx":
            return await self._fetch_okx(symbol)
        elif exchange == "bitget":
            return await self._fetch_bitget(symbol)
        else:
            logger.warning("funding_rate_collector.unknown_exchange", exchange=exchange)
            return None

    # -----------------------------------------------------------------------
    # Symbol format helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _to_linear_symbol(symbol: str) -> str:
        """Convert 'BTC/USDT' -> 'BTCUSDT' (Binance/Bybit/Bitget format)."""
        return symbol.replace("/", "").upper()

    @staticmethod
    def _to_okx_symbol(symbol: str) -> str:
        """Convert 'BTC/USDT' -> 'BTC-USDT-SWAP' (OKX format)."""
        return symbol.replace("/", "-").upper() + "-SWAP"

    # -----------------------------------------------------------------------
    # Exchange-specific fetchers
    # -----------------------------------------------------------------------

    async def _fetch_binance(self, symbol: str) -> FundingRateEntry | None:
        """Fetch from Binance Futures: GET /fapi/v1/premiumIndex.

        Response: {"lastFundingRate": "0.00010000", "nextFundingTime": 1710979200000}
        """
        assert self._http_client is not None
        linear_symbol = self._to_linear_symbol(symbol)
        resp = await self._http_client.get(
            EXCHANGE_ENDPOINTS["binance_futures"],
            params={"symbol": linear_symbol},
        )
        if resp.status_code != 200:
            return None
        data: dict[str, Any] = resp.json()
        rate = float(data.get("lastFundingRate", 0))
        raw_nft = data.get("nextFundingTime")
        next_funding_time = float(raw_nft) / 1000.0 if raw_nft else None
        return FundingRateEntry(rate=rate, next_funding_time=next_funding_time)

    async def _fetch_bybit(self, symbol: str) -> FundingRateEntry | None:
        """Fetch from Bybit: GET /v5/market/tickers?category=linear.

        Response: {"result": {"list": [{"fundingRate": "0.00012345", "nextFundingTime": "..."}]}}
        """
        assert self._http_client is not None
        linear_symbol = self._to_linear_symbol(symbol)
        resp = await self._http_client.get(
            EXCHANGE_ENDPOINTS["bybit"],
            params={"category": "linear", "symbol": linear_symbol},
        )
        if resp.status_code != 200:
            return None
        data: dict[str, Any] = resp.json()
        result_list = data.get("result", {}).get("list", [])
        if not result_list:
            return None
        item = result_list[0]
        rate = float(item.get("fundingRate", 0))
        raw_nft = item.get("nextFundingTime")
        next_funding_time = float(raw_nft) / 1000.0 if raw_nft else None
        return FundingRateEntry(rate=rate, next_funding_time=next_funding_time)

    async def _fetch_okx(self, symbol: str) -> FundingRateEntry | None:
        """Fetch from OKX: GET /api/v5/public/funding-rate.

        Response: {"data": [{"fundingRate": "0.00015000", "nextFundingTime": "..."}]}
        """
        assert self._http_client is not None
        okx_symbol = self._to_okx_symbol(symbol)
        resp = await self._http_client.get(
            EXCHANGE_ENDPOINTS["okx"],
            params={"instId": okx_symbol},
        )
        if resp.status_code != 200:
            return None
        data: dict[str, Any] = resp.json()
        items = data.get("data", [])
        if not items:
            return None
        item = items[0]
        rate = float(item.get("fundingRate", 0))
        raw_nft = item.get("nextFundingTime")
        next_funding_time = float(raw_nft) / 1000.0 if raw_nft else None
        return FundingRateEntry(rate=rate, next_funding_time=next_funding_time)

    async def _fetch_bitget(self, symbol: str) -> FundingRateEntry | None:
        """Fetch from Bitget: GET /api/v2/mix/market/current-fund-rate.

        Response: {"data": [{"fundingRate": "0.00008000"}]}
        """
        assert self._http_client is not None
        linear_symbol = self._to_linear_symbol(symbol)
        resp = await self._http_client.get(
            EXCHANGE_ENDPOINTS["bitget"],
            params={"symbol": linear_symbol, "productType": "USDT-FUTURES"},
        )
        if resp.status_code != 200:
            return None
        data: dict[str, Any] = resp.json()
        items = data.get("data", [])
        if not items:
            return None
        item = items[0]
        rate = float(item.get("fundingRate", 0))
        return FundingRateEntry(rate=rate, next_funding_time=None)
