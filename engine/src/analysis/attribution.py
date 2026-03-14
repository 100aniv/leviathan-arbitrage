"""Performance Attribution Engine — multi-dimensional PnL decomposition.

Breaks down PnL by: strategy, exchange, pair, hour.
Generates TimescaleDB aggregate views for dashboard consumption.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """Minimal trade record for attribution."""

    trade_id: str
    timestamp: datetime
    strategy_id: str
    exchange_buy: str
    exchange_sell: str
    pair: str
    pnl: float
    size_usd: float = 0.0


@dataclass
class AttributionBreakdown:
    """PnL breakdown for a single dimension value."""

    key: str
    total_pnl: float = 0.0
    trade_count: int = 0
    win_count: int = 0
    avg_pnl: float = 0.0
    win_rate: float = 0.0


class PerformanceAttribution:
    """by_strategy, by_exchange, by_pair, by_hour 차원별 PnL 분해."""

    def __init__(self) -> None:
        self._trades: list[TradeRecord] = []

    def add_trade(self, trade: TradeRecord) -> None:
        """Add a trade record for attribution analysis."""
        self._trades.append(trade)

    def add_trades(self, trades: list[TradeRecord]) -> None:
        """Add multiple trade records."""
        self._trades.extend(trades)

    async def load_from_db(self, pool) -> int:
        """Load historical trades from TimescaleDB execution_log.

        Args:
            pool: asyncpg connection pool

        Returns:
            Number of trades loaded
        """
        query = """
            SELECT ts, strategy_id, signal_id, buy_exchange, sell_exchange,
                   symbol, buy_price, sell_price, size, gross_spread_bps,
                   fee_total, slippage_total, net_pnl, status
            FROM execution_log
            WHERE status = 'filled'
            ORDER BY ts DESC
            LIMIT 10000
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(query)

        loaded = 0
        for row in rows:
            trade = TradeRecord(
                trade_id=row['signal_id'] or "",
                timestamp=row['ts'],
                strategy_id=row['strategy_id'],
                exchange_buy=row['buy_exchange'],
                exchange_sell=row['sell_exchange'],
                pair=row['symbol'],
                pnl=float(row['net_pnl'] or 0),
                size_usd=float(row['size'] or 0),
            )
            self._trades.append(trade)
            loaded += 1

        logger.info("Loaded %d historical trades from TimescaleDB", loaded)
        return loaded

    _ALLOWED_VIEWS = frozenset({
        "strategy_daily_pnl",
        "exchange_daily_pnl",
        "pair_daily_pnl",
    })

    async def refresh_views(self, pool) -> None:
        """Refresh materialized views for attribution analysis."""
        async with pool.acquire() as conn:
            for view in self._ALLOWED_VIEWS:
                try:
                    await conn.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {view}")
                    logger.info("Refreshed materialized view: %s", view)
                except Exception as exc:
                    logger.warning("Failed to refresh %s: %s", view, exc)

    @property
    def trade_count(self) -> int:
        return len(self._trades)

    def by_strategy(self) -> list[AttributionBreakdown]:
        """PnL breakdown by strategy."""
        return self._group_by(lambda t: t.strategy_id)

    def by_exchange(self) -> list[AttributionBreakdown]:
        """PnL breakdown by exchange (both buy and sell sides)."""
        groups: dict[str, list[float]] = defaultdict(list)
        for t in self._trades:
            # Attribute half PnL to each exchange
            half = t.pnl / 2.0
            groups[t.exchange_buy].append(half)
            groups[t.exchange_sell].append(half)
        return self._build_breakdowns(groups)

    def by_pair(self) -> list[AttributionBreakdown]:
        """PnL breakdown by trading pair."""
        return self._group_by(lambda t: t.pair)

    def by_hour(self) -> list[AttributionBreakdown]:
        """PnL breakdown by hour of day (UTC)."""
        return self._group_by(lambda t: f"{t.timestamp.hour:02d}:00")

    def _group_by(self, key_fn) -> list[AttributionBreakdown]:
        """Generic grouping helper."""
        groups: dict[str, list[float]] = defaultdict(list)
        for t in self._trades:
            groups[key_fn(t)].append(t.pnl)
        return self._build_breakdowns(groups)

    @staticmethod
    def _build_breakdowns(groups: dict[str, list[float]]) -> list[AttributionBreakdown]:
        """Build AttributionBreakdown list from grouped PnL lists."""
        results = []
        for key, pnls in sorted(groups.items()):
            total = sum(pnls)
            count = len(pnls)
            wins = sum(1 for p in pnls if p > 0)
            results.append(
                AttributionBreakdown(
                    key=key,
                    total_pnl=total,
                    trade_count=count,
                    win_count=wins,
                    avg_pnl=total / count if count > 0 else 0.0,
                    win_rate=wins / count if count > 0 else 0.0,
                )
            )
        return results

    def summary(self) -> dict:
        """Full attribution summary across all dimensions."""
        return {
            "total_trades": self.trade_count,
            "total_pnl": sum(t.pnl for t in self._trades),
            "by_strategy": [
                {"key": b.key, "pnl": b.total_pnl, "trades": b.trade_count, "wr": b.win_rate}
                for b in self.by_strategy()
            ],
            "by_exchange": [
                {"key": b.key, "pnl": b.total_pnl, "trades": b.trade_count, "wr": b.win_rate}
                for b in self.by_exchange()
            ],
            "by_pair": [
                {"key": b.key, "pnl": b.total_pnl, "trades": b.trade_count, "wr": b.win_rate}
                for b in self.by_pair()
            ],
            "by_hour": [
                {"key": b.key, "pnl": b.total_pnl, "trades": b.trade_count, "wr": b.win_rate}
                for b in self.by_hour()
            ],
        }

    @staticmethod
    def migration_sql() -> str:
        """TimescaleDB aggregate views DDL."""
        return """
        -- Strategy daily PnL
        CREATE MATERIALIZED VIEW IF NOT EXISTS strategy_daily_pnl AS
        SELECT
            time_bucket('1 day', ts) AS day,
            strategy_id,
            SUM(net_pnl) AS total_pnl,
            COUNT(*) AS trade_count,
            SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END)::float / COUNT(*) AS win_rate
        FROM execution_log
        GROUP BY day, strategy_id
        ORDER BY day DESC;

        -- Exchange daily PnL
        CREATE MATERIALIZED VIEW IF NOT EXISTS exchange_daily_pnl AS
        SELECT
            time_bucket('1 day', ts) AS day,
            buy_exchange AS exchange_id,
            SUM(net_pnl) / 2 AS total_pnl,
            COUNT(*) AS trade_count
        FROM execution_log
        GROUP BY day, buy_exchange
        ORDER BY day DESC;

        -- Pair daily PnL
        CREATE MATERIALIZED VIEW IF NOT EXISTS pair_daily_pnl AS
        SELECT
            time_bucket('1 day', ts) AS day,
            symbol,
            SUM(net_pnl) AS total_pnl,
            COUNT(*) AS trade_count,
            SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END)::float / COUNT(*) AS win_rate
        FROM execution_log
        GROUP BY day, symbol
        ORDER BY day DESC;
        """
