"""DataFeedPort — Phase 5.1.4 (2026-04-26).

WS data feed 추상화. real WS (binance/upbit/...) / synthetic GBM (paper) 통합.

산업 표준 비교:
- Nautilus DataEngine + DataClient
- LEAN IDataFeed (LiveTradingDataFeed vs BacktestingDataFeed)
- Hummingbot OrderBookTracker + UserStreamTracker

LEVIATHAN 책임:
- subscribe(symbols, exchanges): 구독 시작
- on_orderbook(callback): orderbook update callback
- on_trade(callback): trade tick callback
- get_book(exchange, symbol): 현재 OrderBook 조회
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol, runtime_checkable


@runtime_checkable
class DataFeedPort(Protocol):
    """Hexagonal port for market data feed."""

    async def subscribe(
        self,
        symbols: list[str],
        exchanges: list[str],
    ) -> None:
        """심볼 + 거래소 구독 시작 (idempotent)."""
        ...

    async def unsubscribe(self) -> None:
        """전체 구독 해제 + WS 정리."""
        ...

    def on_orderbook(
        self,
        callback: Callable[[str, str, Any], Awaitable[None]],
    ) -> None:
        """orderbook update callback 등록.

        callback signature: (exchange_id, symbol, orderbook) -> None
        """
        ...

    def on_trade(
        self,
        callback: Callable[[str, str, Any], Awaitable[None]],
    ) -> None:
        """trade tick callback 등록.

        callback signature: (exchange_id, symbol, trade_event) -> None
        """
        ...

    def get_book(self, exchange_id: str, symbol: str) -> Any | None:
        """현재 OrderBook 조회. 없으면 None."""
        ...
