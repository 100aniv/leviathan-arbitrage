"""Signal analyzer — offline analysis of real orderbook data from TimescaleDB.

Loads stored orderbook snapshots, replays them through SignalGenerator,
and produces signal distribution statistics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import asyncpg
import structlog

from src.core.order_book import OrderBook
from src.core.signal import SignalConfig, SignalGenerator
from src.core.price_hub import PriceHub
from src.friction.cost_calculator import CostCalculator
from src.friction.fee_model import FeeModel
from src.friction.slippage_model import CEXOrderbookSlippage

logger = structlog.get_logger(__name__)


@dataclass
class SignalStats:
    """Aggregated signal statistics from a replay run."""
    total_updates: int = 0           # total orderbook updates processed
    raw_spread_positive: int = 0     # updates where raw spread > 0
    signals_generated: int = 0       # signals passing all gates
    signals_per_hour: float = 0.0
    avg_spread_bps: float = 0.0
    avg_net_edge_bps: float = 0.0
    avg_net_profit_usd: float = 0.0
    max_spread_bps: float = 0.0
    time_range_hours: float = 0.0
    exchange_pairs: dict[str, int] = field(default_factory=dict)  # "binance→okx": count


class SignalAnalyzer:
    """Replays stored orderbook snapshots through SignalGenerator for offline analysis.

    Usage:
        analyzer = SignalAnalyzer(pool)
        stats = await analyzer.analyze(
            symbol="BTC/USDT",
            start=datetime(...),
            end=datetime(...),
        )
        print(stats)
    """

    def __init__(self, pool: asyncpg.Pool, signal_config: SignalConfig | None = None) -> None:
        self._pool = pool
        self._signal_config = signal_config or SignalConfig()

    async def analyze(
        self,
        symbol: str = "BTC/USDT",
        start: datetime | None = None,
        end: datetime | None = None,
        trade_size: Decimal = Decimal("0.001"),
    ) -> SignalStats:
        """Run offline signal analysis on stored orderbook data.

        Args:
            symbol: Trading pair to analyze
            start: Start time (default: 72h ago)
            end: End time (default: now)
            trade_size: Simulated trade size in base units

        Returns:
            SignalStats with aggregated results
        """
        # Default time range: last 72 hours
        if end is None:
            end = datetime.now(timezone.utc)
        if start is None:
            from datetime import timedelta
            start = end - timedelta(hours=72)

        # Build signal pipeline
        hub = PriceHub()
        try:
            fee_model = FeeModel()
            slippage_model = CEXOrderbookSlippage()
            calc = CostCalculator(fee_model=fee_model, slippage_model=slippage_model)
        except Exception:
            calc = None

        gen = SignalGenerator(
            price_hub=hub,
            cost_calculator=calc,
            config=self._signal_config,
            event_bus=None,  # no publishing during analysis
        )

        stats = SignalStats()
        spreads: list[float] = []
        net_edges: list[float] = []
        net_profits: list[float] = []

        # Query orderbook snapshots ordered by time
        query = """
            SELECT ts, exchange, symbol, bids_json, asks_json
            FROM orderbook_snapshots
            WHERE symbol = $1 AND ts >= $2 AND ts <= $3
            ORDER BY ts ASC
        """

        all_books: dict[str, OrderBook] = {}
        first_ts = None
        last_ts = None

        async with self._pool.acquire() as conn:
            async for row in conn.cursor(query, symbol, start, end):
                ts = row["ts"]
                exchange = row["exchange"]

                if first_ts is None:
                    first_ts = ts
                last_ts = ts

                stats.total_updates += 1

                # Reconstruct OrderBook from stored data
                import json
                bids_data = json.loads(row["bids_json"]) if isinstance(row["bids_json"], str) else row["bids_json"]
                asks_data = json.loads(row["asks_json"]) if isinstance(row["asks_json"], str) else row["asks_json"]

                book = OrderBook(symbol=symbol, exchange=exchange)
                book.apply_snapshot(
                    [(str(b[0]), str(b[1])) for b in bids_data],
                    [(str(a[0]), str(a[1])) for a in asks_data],
                )
                all_books[exchange] = book

                # Check raw spread
                bb = book.best_bid()
                ba = book.best_ask()
                if bb and ba and bb > ba:
                    stats.raw_spread_positive += 1

                # Run through signal generator
                if len(all_books) >= 2:
                    signal = await gen.on_orderbook_update(
                        book=book,
                        books=all_books,
                        trade_size=trade_size,
                    )

                    if signal is not None:
                        stats.signals_generated += 1
                        spread_bps = float(signal.spread_pct) * 10000
                        spreads.append(spread_bps)

                        net_edge = float(signal.metadata.get("net_edge_pct", "0"))
                        net_edges.append(net_edge)

                        net_profit = float(signal.metadata.get("net_profit", "0"))
                        net_profits.append(net_profit)

                        pair = f"{signal.buy_exchange}→{signal.sell_exchange}"
                        stats.exchange_pairs[pair] = stats.exchange_pairs.get(pair, 0) + 1

        # Compute summary statistics
        if first_ts and last_ts:
            stats.time_range_hours = (last_ts - first_ts).total_seconds() / 3600

        if stats.time_range_hours > 0:
            stats.signals_per_hour = stats.signals_generated / stats.time_range_hours

        if spreads:
            stats.avg_spread_bps = sum(spreads) / len(spreads)
            stats.max_spread_bps = max(spreads)

        if net_edges:
            stats.avg_net_edge_bps = sum(net_edges) / len(net_edges)

        if net_profits:
            stats.avg_net_profit_usd = sum(net_profits) / len(net_profits)

        logger.info(
            "signal_analysis_complete",
            total_updates=stats.total_updates,
            signals=stats.signals_generated,
            signals_per_hour=f"{stats.signals_per_hour:.2f}",
            avg_spread_bps=f"{stats.avg_spread_bps:.2f}",
            time_range_hours=f"{stats.time_range_hours:.1f}",
        )

        return stats
