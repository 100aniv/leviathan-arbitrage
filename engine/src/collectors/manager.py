"""Collector manager — orchestrates all exchange WebSocket collectors."""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Awaitable

import structlog

from src.collectors.base_collector import BaseCollector
from src.collectors.binance_collector import BinanceCollector
from src.collectors.bybit_collector import BybitCollector
from src.collectors.okx_collector import OKXCollector
from src.collectors.bitget_collector import BitgetCollector
from src.collectors.upbit_collector import UpbitCollector
from src.collectors.bithumb_collector import BithumbCollector
from src.collectors.coinone_collector import CoinoneCollector
from src.collectors.binance_futures_collector import BinanceFuturesCollector
from src.collectors.okx_futures_collector import OKXFuturesCollector
from src.collectors.bybit_futures_collector import BybitFuturesCollector

logger = structlog.get_logger(__name__)


class CollectorManager:
    """Manages all exchange data collectors.

    Starts/stops all collectors, provides aggregated stats,
    and routes orderbook updates to a callback.
    """

    # Default exchanges to collect from
    DEFAULT_EXCHANGES = ["binance", "bybit", "okx", "bitget", "upbit", "bithumb", "coinone", "binance_futures", "okx_futures", "bybit_futures"]

    # Korean exchanges that trade primarily in KRW (not USDT)
    KOREAN_EXCHANGES = {"upbit", "bithumb", "coinone"}

    def __init__(
        self,
        symbols: list[str] | None = None,
        exchanges: list[str] | None = None,
        on_orderbook: Callable[[str, str, list, list], Awaitable[None]] | None = None,
    ) -> None:
        """
        Args:
            symbols: Trading pairs to collect (default ["BTC/USDT"])
            exchanges: Exchange IDs to enable (default all 7)
            on_orderbook: Async callback(exchange_id, symbol, bids, asks)
        """
        self.symbols = symbols or ["BTC/USDT"]
        self._exchange_ids = exchanges or self.DEFAULT_EXCHANGES
        self._on_orderbook = on_orderbook
        self._collectors: dict[str, BaseCollector] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def _get_exchange_symbols(self, exchange_id: str) -> list[str]:
        """Map trading symbols for exchange. Korean exchanges use KRW pairs."""
        if exchange_id in self.KOREAN_EXCHANGES:
            return [s.replace("/USDT", "/KRW") for s in self.symbols]
        return self.symbols

    def _create_collector(self, exchange_id: str) -> BaseCollector | None:
        """Factory method to create a collector for the given exchange."""
        factory = {
            "binance": BinanceCollector,
            "bybit": BybitCollector,
            "okx": OKXCollector,
            "bitget": BitgetCollector,
            "upbit": UpbitCollector,
            "bithumb": BithumbCollector,
            "coinone": CoinoneCollector,
            "binance_futures": BinanceFuturesCollector,
            "okx_futures": OKXFuturesCollector,
            "bybit_futures": BybitFuturesCollector,
        }
        cls = factory.get(exchange_id)
        if cls is None:
            logger.warning("unknown_exchange", exchange=exchange_id)
            return None
        symbols = self._get_exchange_symbols(exchange_id)
        return cls(
            symbols=symbols,
            on_orderbook=self._on_orderbook,
        )

    async def start(self) -> None:
        """Start all configured collectors as background tasks."""
        for eid in self._exchange_ids:
            collector = self._create_collector(eid)
            if collector is None:
                continue
            self._collectors[eid] = collector
            task = asyncio.create_task(collector.start(), name=f"collector_{eid}")
            self._tasks[eid] = task
            logger.info("collector_manager_started", exchange=eid, symbols=self.symbols)

        logger.info("collector_manager_all_started", count=len(self._collectors))

    async def stop(self) -> None:
        """Stop all collectors gracefully."""
        for eid, collector in self._collectors.items():
            await collector.stop()
            logger.info("collector_manager_stopped", exchange=eid)

        for eid, task in self._tasks.items():
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        self._collectors.clear()
        self._tasks.clear()
        logger.info("collector_manager_all_stopped")

    @property
    def stats(self) -> dict[str, Any]:
        """Aggregate stats from all collectors."""
        return {
            eid: collector.stats
            for eid, collector in self._collectors.items()
        }

    @property
    def connected_count(self) -> int:
        """Number of currently connected collectors."""
        return sum(1 for c in self._collectors.values() if c.is_connected)

    def get_collector(self, exchange_id: str) -> BaseCollector | None:
        """Return the collector for the given exchange, or None if not running."""
        return self._collectors.get(exchange_id)
