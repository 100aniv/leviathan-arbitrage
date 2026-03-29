"""Tests for PerformanceAttribution wiring — US-284-b, US-282."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.analysis.attribution import AttributionBreakdown, PerformanceAttribution, TradeRecord


def _trade(
    strategy_id: str = "cross_exchange",
    exchange_buy: str = "binance",
    exchange_sell: str = "bybit",
    pnl: float = 1.0,
    pair: str = "BTC/USDT",
) -> TradeRecord:
    return TradeRecord(
        trade_id="t1",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        strategy_id=strategy_id,
        exchange_buy=exchange_buy,
        exchange_sell=exchange_sell,
        pair=pair,
        pnl=pnl,
    )


# ---------------------------------------------------------------------------
# Wiring: EngineContext has attribution attribute
# ---------------------------------------------------------------------------

class TestEngineContextWiring:
    def test_attribution_set_on_engine_context(self) -> None:
        """EngineContext must have attribution field (US-284-b)."""
        from src.api.server import EngineContext
        assert "attribution" in EngineContext.__dataclass_fields__


# ---------------------------------------------------------------------------
# Trade ingestion
# ---------------------------------------------------------------------------

class TestTradeIngestion:
    def test_add_trade_increments_trades(self) -> None:
        pa = PerformanceAttribution()
        assert pa.trade_count == 0
        pa.add_trade(_trade())
        assert pa.trade_count == 1

    def test_add_trades_bulk(self) -> None:
        pa = PerformanceAttribution()
        pa.add_trades([_trade(), _trade(), _trade()])
        assert pa.trade_count == 3


# ---------------------------------------------------------------------------
# Breakdowns
# ---------------------------------------------------------------------------

class TestGetReport:
    def test_get_report_empty_trades(self) -> None:
        pa = PerformanceAttribution()
        result = pa.summary()
        assert result["total_trades"] == 0
        assert result["total_pnl"] == pytest.approx(0.0)
        assert result["by_strategy"] == []

    def test_get_report_by_strategy(self) -> None:
        pa = PerformanceAttribution()
        pa.add_trade(_trade(strategy_id="strat_a", pnl=2.0))
        pa.add_trade(_trade(strategy_id="strat_a", pnl=3.0))
        pa.add_trade(_trade(strategy_id="strat_b", pnl=-1.0))
        breakdowns = pa.by_strategy()
        keys = {b.key: b for b in breakdowns}
        assert "strat_a" in keys
        assert keys["strat_a"].total_pnl == pytest.approx(5.0)
        assert keys["strat_a"].trade_count == 2
        assert "strat_b" in keys
        assert keys["strat_b"].total_pnl == pytest.approx(-1.0)

    def test_get_report_by_exchange(self) -> None:
        """Each trade contributes half-PnL to buy and sell exchanges."""
        pa = PerformanceAttribution()
        pa.add_trade(_trade(exchange_buy="binance", exchange_sell="bybit", pnl=2.0))
        breakdowns = pa.by_exchange()
        keys = {b.key: b for b in breakdowns}
        assert keys["binance"].total_pnl == pytest.approx(1.0)
        assert keys["bybit"].total_pnl == pytest.approx(1.0)

    def test_win_rate_calculation(self) -> None:
        pa = PerformanceAttribution()
        pa.add_trade(_trade(pnl=1.0))
        pa.add_trade(_trade(pnl=2.0))
        pa.add_trade(_trade(pnl=-1.0))
        breakdowns = pa.by_strategy()
        assert len(breakdowns) == 1
        assert breakdowns[0].win_rate == pytest.approx(2 / 3)

    def test_summary_contains_all_dimensions(self) -> None:
        pa = PerformanceAttribution()
        pa.add_trade(_trade())
        s = pa.summary()
        assert "by_strategy" in s
        assert "by_exchange" in s
        assert "by_pair" in s
        assert "by_hour" in s
