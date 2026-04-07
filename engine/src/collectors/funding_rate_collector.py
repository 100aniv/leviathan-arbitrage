"""FundingRateCollector — REST polling collector for funding rates across futures exchanges.

Supports any futures exchange configured in engine.json (exchanges.active, *_futures suffix).
Symbol discovery is fully dynamic: fetches available perpetual contracts from each exchange
at runtime and returns the intersection — no hardcoded symbol lists.

Adding a new exchange:
  1. Add it to engine.json exchanges.active with a ``_futures`` suffix.
  2. Add a handler branch in ``_fetch_exchange_symbols()`` for the exchange name.
  3. Add a branch in ``_fetch()`` dispatch (rate polling).
  No other changes needed — auto-discovery handles the rest.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Exchange endpoints (rate polling — per-symbol REST calls)
# ---------------------------------------------------------------------------

EXCHANGE_ENDPOINTS: dict[str, str] = {
    "binance_futures": "https://fapi.binance.com/fapi/v1/premiumIndex",
    "bybit": "https://api.bybit.com/v5/market/tickers",
    "bybit_futures": "https://api.bybit.com/v5/market/tickers",
    "okx": "https://www.okx.com/api/v5/public/funding-rate",
    "okx_futures": "https://www.okx.com/api/v5/public/funding-rate",
    "bitget": "https://api.bitget.com/api/v2/mix/market/current-fund-rate",
    "bitget_futures": "https://api.bitget.com/api/v2/mix/market/current-fund-rate",
}

DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT"]
DEFAULT_EXCHANGES = ["binance_futures", "bybit", "okx", "bitget"]

# Registry of futures exchanges this collector knows how to query for symbol lists.
# Engine.json naming convention (e.g. "binance_futures", "bitget_futures").
# To add support for a new exchange: add its engine.json name here and a handler
# in _fetch_exchange_symbols().
_SUPPORTED_FUTURES_EXCHANGES: frozenset[str] = frozenset({
    "binance_futures",
    "bitget_futures",
    "bybit_futures",
    "okx_futures",
})


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

        Requests are executed concurrently (semaphore-limited to 30) to avoid
        sequential bottleneck when symbols list is large (100+).

        Returns the newly fetched entries (also stored in self.store).
        Lazily creates an HTTP client if none was provided or start() wasn't called.
        """
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=10.0)
            self._owns_client = True

        semaphore = asyncio.Semaphore(30)

        async def _fetch_safe(exchange: str, symbol: str) -> tuple[str, str, FundingRateEntry | None]:
            async with semaphore:
                try:
                    return exchange, symbol, await self._fetch(exchange, symbol)
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "funding_rate_collector.fetch_failed",
                        exchange=exchange,
                        symbol=symbol,
                        error=str(exc),
                    )
                    return exchange, symbol, None

        tasks = [
            _fetch_safe(exchange, symbol)
            for symbol in self.symbols
            for exchange in self.exchanges
        ]
        results = await asyncio.gather(*tasks)

        fetched: dict[str, dict[str, FundingRateEntry]] = {}
        for exchange, symbol, entry in results:
            if entry is not None:
                self.store.set_rate(exchange, symbol, entry.rate, entry.next_funding_time)
                fetched.setdefault(exchange, {})[symbol] = entry

        if fetched:
            logger.debug(
                "funding_rate_collector.updated",
                exchanges=list(fetched.keys()),
                symbol_count=len(self.symbols),
            )

        return fetched

    # -----------------------------------------------------------------------
    # Dynamic exchange / symbol discovery
    # -----------------------------------------------------------------------

    @staticmethod
    def _get_active_futures_exchanges() -> list[str]:
        """Read engine.json and return active futures exchanges (engine.json naming).

        Filters ``exchanges.active`` to entries with a ``_futures`` suffix that this
        collector knows how to query.  Falls back to DEFAULT_EXCHANGES on any error.
        """
        try:
            config_path = os.path.normpath(
                os.path.join(os.path.dirname(__file__), "..", "..", "config", "engine.json")
            )
            with open(config_path) as f:
                cfg = json.load(f)
            active: list[str] = cfg.get("exchanges", {}).get("active", [])
            result = [ex for ex in active if ex in _SUPPORTED_FUTURES_EXCHANGES]
            return result if result else list(DEFAULT_EXCHANGES)
        except Exception as exc:
            logger.warning(
                "funding_rate_collector.engine_config_read_failed",
                error=str(exc),
            )
            return list(DEFAULT_EXCHANGES)

    @staticmethod
    async def _fetch_exchange_symbols(
        client: httpx.AsyncClient,
        exchange: str,
    ) -> set[str]:
        """Fetch all active perpetual USDT symbols for one futures exchange.

        ``exchange`` uses engine.json naming (e.g. ``"binance_futures"``).
        Returns symbols in ``"BASE/USDT"`` format, or an empty set on failure.

        To support a new exchange: add a branch here matching its engine.json name.
        """
        try:
            if exchange == "binance_futures":
                resp = await client.get("https://fapi.binance.com/fapi/v1/exchangeInfo")
                resp.raise_for_status()
                return {
                    s["baseAsset"] + "/USDT"
                    for s in resp.json().get("symbols", [])
                    if s.get("quoteAsset") == "USDT"
                    and s.get("status") == "TRADING"
                    and s.get("contractType") == "PERPETUAL"
                }
            elif exchange == "bitget_futures":
                resp = await client.get(
                    "https://api.bitget.com/api/v2/mix/market/contracts",
                    params={"productType": "USDT-FUTURES"},
                )
                resp.raise_for_status()
                return {
                    d["baseCoin"] + "/USDT"
                    for d in resp.json().get("data", [])
                    if d.get("quoteCoin") == "USDT"
                }
            elif exchange == "bybit_futures":
                resp = await client.get(
                    "https://api.bybit.com/v5/market/instruments-info",
                    params={"category": "linear", "limit": 1000},
                )
                resp.raise_for_status()
                return {
                    item["baseCoin"] + "/USDT"
                    for item in resp.json().get("result", {}).get("list", [])
                    if item.get("quoteCoin") == "USDT"
                    and item.get("status") == "Trading"
                    and item.get("contractType") == "LinearPerpetual"
                }
            elif exchange == "okx_futures":
                resp = await client.get(
                    "https://www.okx.com/api/v5/public/instruments",
                    params={"instType": "SWAP"},
                )
                resp.raise_for_status()
                return {
                    item["ctValCcy"] + "/USDT"
                    for item in resp.json().get("data", [])
                    if item.get("settleCcy") == "USDT"
                    and item.get("state") == "live"
                    and item.get("instId", "").endswith("-USDT-SWAP")
                }
            else:
                logger.warning(
                    "funding_rate_collector.unsupported_futures_exchange_for_symbols",
                    exchange=exchange,
                )
                return set()
        except Exception as exc:
            logger.warning(
                "funding_rate_collector.symbol_fetch_failed",
                exchange=exchange,
                error=str(exc),
            )
            return set()

    @staticmethod
    async def fetch_paired_symbols(
        exchanges: list[str] | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> list[str]:
        """Dynamically discover futures symbols tradeable on ALL active futures exchanges.

        When ``exchanges`` is ``None`` (the default), auto-discovers the exchange list
        from ``engine.json`` (``exchanges.active`` filtered by ``_futures`` suffix).
        Returns the intersection of available perpetual symbols in ``"BASE/USDT"`` format.
        Falls back to ``DEFAULT_SYMBOLS`` on network failure or empty intersection.

        Usage (no hardcoding required)::

            symbols = await FundingRateCollector.fetch_paired_symbols()
        """
        if exchanges is None:
            exchanges = FundingRateCollector._get_active_futures_exchanges()

        if not exchanges:
            logger.warning("funding_rate_collector.no_futures_exchanges_configured")
            return list(DEFAULT_SYMBOLS)

        own_client = http_client is None
        client = http_client or httpx.AsyncClient(timeout=15.0)
        try:
            results = await asyncio.gather(
                *[FundingRateCollector._fetch_exchange_symbols(client, ex) for ex in exchanges],
                return_exceptions=True,
            )

            symbol_sets: list[set[str]] = [
                r for r in results if isinstance(r, set) and r
            ]

            if not symbol_sets:
                logger.warning(
                    "funding_rate_collector.no_symbols_discovered",
                    exchanges=exchanges,
                )
                return list(DEFAULT_SYMBOLS)

            intersection = set.intersection(*symbol_sets) if len(symbol_sets) > 1 else symbol_sets[0]
            result = sorted(intersection)
            logger.info(
                "funding_rate_collector.paired_symbols_ready",
                total=len(result),
                exchanges=exchanges,
            )
            return result if result else list(DEFAULT_SYMBOLS)

        except Exception as exc:
            logger.warning(
                "funding_rate_collector.fetch_paired_symbols_failed",
                error=str(exc),
            )
            return list(DEFAULT_SYMBOLS)
        finally:
            if own_client:
                await client.aclose()

    @classmethod
    def get_poll_exchanges(cls) -> list[str]:
        """Return the list of exchanges to poll funding rates from.

        Reads ``engine.json`` dynamically — no hardcoding.  Adding a new futures
        exchange to ``engine.json`` automatically includes it here.
        """
        return cls._get_active_futures_exchanges()

    # -----------------------------------------------------------------------
    # Internal dispatch
    # -----------------------------------------------------------------------

    async def _fetch(self, exchange: str, symbol: str) -> FundingRateEntry | None:
        """Dispatch to the exchange-specific fetcher.

        Accepts both legacy names (``"bybit"``) and engine.json names
        (``"bybit_futures"``) for forward compatibility.
        """
        if exchange == "binance_futures":
            return await self._fetch_binance(symbol)
        elif exchange in ("bybit", "bybit_futures"):
            return await self._fetch_bybit(symbol)
        elif exchange in ("okx", "okx_futures"):
            return await self._fetch_okx(symbol)
        elif exchange in ("bitget", "bitget_futures"):
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
