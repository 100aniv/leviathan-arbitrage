"""Tests for PerformanceAttribution (US-051)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.analysis.attribution import (
    AttributionBreakdown,
    PerformanceAttribution,
    TradeRecord,
)


def _trade(
    tid: str = "t1",
    strategy: str = "cross_exchange_v1",
    buy: str = "binance",
    sell: str = "upbit",
    pair: str = "BTC/USDT",
    pnl: float = 1.0,
    hour: int = 10,
) -> TradeRecord:
    return TradeRecord(
        trade_id=tid,
        timestamp=datetime(2026, 3, 9, hour, 0, tzinfo=timezone.utc),
        strategy_id=strategy,
        exchange_buy=buy,
        exchange_sell=sell,
        pair=pair,
        pnl=pnl,
    )


class TestByStrategy:
    def test_single_strategy(self):
        pa = PerformanceAttribution()
        pa.add_trades([_trade(pnl=1.0), _trade(pnl=2.0), _trade(pnl=-0.5)])
        result = pa.by_strategy()
        assert len(result) == 1
        b = result[0]
        assert b.key == "cross_exchange_v1"
        assert abs(b.total_pnl - 2.5) < 1e-10
        assert b.trade_count == 3
        assert b.win_count == 2

    def test_multiple_strategies(self):
        pa = PerformanceAttribution()
        pa.add_trades([
            _trade(strategy="cross_exchange_v1", pnl=5.0),
            _trade(strategy="latency_arb_v1", pnl=3.0),
            _trade(strategy="latency_arb_v1", pnl=-1.0),
        ])
        result = pa.by_strategy()
        by_key = {b.key: b for b in result}
        assert by_key["cross_exchange_v1"].total_pnl == 5.0
        assert by_key["latency_arb_v1"].total_pnl == 2.0
        assert by_key["latency_arb_v1"].win_rate == 0.5


class TestByExchange:
    def test_pnl_split_between_buy_and_sell(self):
        pa = PerformanceAttribution()
        pa.add_trade(_trade(buy="binance", sell="upbit", pnl=2.0))
        result = pa.by_exchange()
        by_key = {b.key: b for b in result}
        assert abs(by_key["binance"].total_pnl - 1.0) < 1e-10
        assert abs(by_key["upbit"].total_pnl - 1.0) < 1e-10

    def test_multiple_exchanges(self):
        pa = PerformanceAttribution()
        pa.add_trades([
            _trade(buy="binance", sell="upbit", pnl=4.0),
            _trade(buy="binance", sell="bithumb", pnl=2.0),
        ])
        result = pa.by_exchange()
        by_key = {b.key: b for b in result}
        assert abs(by_key["binance"].total_pnl - 3.0) < 1e-10  # 2+1
        assert abs(by_key["upbit"].total_pnl - 2.0) < 1e-10
        assert abs(by_key["bithumb"].total_pnl - 1.0) < 1e-10


class TestByPair:
    def test_pair_grouping(self):
        pa = PerformanceAttribution()
        pa.add_trades([
            _trade(pair="BTC/USDT", pnl=3.0),
            _trade(pair="ETH/USDT", pnl=1.0),
            _trade(pair="BTC/USDT", pnl=-0.5),
        ])
        result = pa.by_pair()
        by_key = {b.key: b for b in result}
        assert abs(by_key["BTC/USDT"].total_pnl - 2.5) < 1e-10
        assert by_key["BTC/USDT"].trade_count == 2
        assert by_key["ETH/USDT"].total_pnl == 1.0


class TestByHour:
    def test_hour_grouping(self):
        pa = PerformanceAttribution()
        pa.add_trades([
            _trade(hour=10, pnl=1.0),
            _trade(hour=10, pnl=2.0),
            _trade(hour=14, pnl=0.5),
        ])
        result = pa.by_hour()
        by_key = {b.key: b for b in result}
        assert abs(by_key["10:00"].total_pnl - 3.0) < 1e-10
        assert by_key["14:00"].trade_count == 1


class TestWinRate:
    def test_all_wins(self):
        pa = PerformanceAttribution()
        pa.add_trades([_trade(pnl=1.0), _trade(pnl=2.0)])
        b = pa.by_strategy()[0]
        assert b.win_rate == 1.0

    def test_all_losses(self):
        pa = PerformanceAttribution()
        pa.add_trades([_trade(pnl=-1.0), _trade(pnl=-2.0)])
        b = pa.by_strategy()[0]
        assert b.win_rate == 0.0

    def test_mixed(self):
        pa = PerformanceAttribution()
        pa.add_trades([_trade(pnl=1.0), _trade(pnl=-1.0), _trade(pnl=0.5), _trade(pnl=-0.5)])
        b = pa.by_strategy()[0]
        assert abs(b.win_rate - 0.5) < 1e-10


class TestSummary:
    def test_summary_includes_all_dimensions(self):
        pa = PerformanceAttribution()
        pa.add_trades([_trade(pnl=1.0), _trade(pnl=2.0)])
        s = pa.summary()
        assert s["total_trades"] == 2
        assert abs(s["total_pnl"] - 3.0) < 1e-10
        assert "by_strategy" in s
        assert "by_exchange" in s
        assert "by_pair" in s
        assert "by_hour" in s


class TestEmptyAttribution:
    def test_empty_returns_empty_lists(self):
        pa = PerformanceAttribution()
        assert pa.by_strategy() == []
        assert pa.by_exchange() == []
        assert pa.by_pair() == []
        assert pa.by_hour() == []
        assert pa.trade_count == 0

    def test_summary_empty(self):
        pa = PerformanceAttribution()
        s = pa.summary()
        assert s["total_trades"] == 0
        assert s["total_pnl"] == 0


class TestMigrationSql:
    def test_migration_contains_views(self):
        sql = PerformanceAttribution.migration_sql()
        assert "strategy_daily_pnl" in sql
        assert "exchange_daily_pnl" in sql
        assert "pair_daily_pnl" in sql
