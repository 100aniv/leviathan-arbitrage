"""Tests for US-147: PerformanceAttribution TimescaleDB load_from_db() + refresh_views().

Verifies:
- load_from_db() uses asyncpg pool to restore trades from execution_log
- refresh_views() executes REFRESH MATERIALIZED VIEW for all 3 views
- Empty DB handled gracefully (returns 0 loaded trades)
- TradeRecord fields mapped correctly from DB columns

Run:
    cd engine && python -m pytest tests/test_attribution_db.py -x --tb=short -v
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from src.analysis.attribution import PerformanceAttribution, TradeRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(rows: list[dict]) -> MagicMock:
    """Build a mock asyncpg connection pool that returns given rows."""
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=rows)
    mock_conn.execute = AsyncMock()

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_pool, mock_conn


def _make_db_row(
    signal_id: str = "sig-001",
    strategy_id: str = "cross_exchange_v1",
    buy_exchange: str = "binance",
    sell_exchange: str = "upbit",
    symbol: str = "BTC/USDT",
    net_pnl: float = 1.23,
    size: float = 100.0,
) -> dict:
    return {
        "ts": datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        "strategy_id": strategy_id,
        "signal_id": signal_id,
        "buy_exchange": buy_exchange,
        "sell_exchange": sell_exchange,
        "symbol": symbol,
        "buy_price": 50000.0,
        "sell_price": 50100.0,
        "size": size,
        "gross_spread_bps": 20.0,
        "fee_total": 0.5,
        "slippage_total": 0.1,
        "net_pnl": net_pnl,
        "status": "filled",
    }


# ===========================================================================
# load_from_db()
# ===========================================================================


class TestLoadFromDB:
    """Tests for PerformanceAttribution.load_from_db()."""

    @pytest.mark.asyncio
    async def test_loads_trades_from_execution_log(self):
        """load_from_db() restores trades from execution_log and returns count."""
        row = _make_db_row(net_pnl=2.0)
        pool, _ = _make_pool([row])

        attr = PerformanceAttribution()
        loaded = await attr.load_from_db(pool)

        assert loaded == 1
        assert attr.trade_count == 1

    @pytest.mark.asyncio
    async def test_maps_db_row_to_trade_record_fields(self):
        """DB row columns mapped correctly to TradeRecord attributes."""
        row = _make_db_row(
            signal_id="sig-xyz",
            strategy_id="triangular_v1",
            buy_exchange="bybit",
            sell_exchange="coinone",
            symbol="ETH/USDT",
            net_pnl=0.75,
            size=50.0,
        )
        pool, _ = _make_pool([row])

        attr = PerformanceAttribution()
        await attr.load_from_db(pool)

        trade = attr._trades[0]
        assert trade.trade_id == "sig-xyz"
        assert trade.strategy_id == "triangular_v1"
        assert trade.exchange_buy == "bybit"
        assert trade.exchange_sell == "coinone"
        assert trade.pair == "ETH/USDT"
        assert trade.pnl == pytest.approx(0.75)
        assert trade.size_usd == pytest.approx(50.0)

    @pytest.mark.asyncio
    async def test_returns_zero_trades_on_empty_db(self):
        """load_from_db() returns 0 and leaves trades list empty when DB has no rows."""
        pool, _ = _make_pool([])

        attr = PerformanceAttribution()
        loaded = await attr.load_from_db(pool)

        assert loaded == 0
        assert attr.trade_count == 0

    @pytest.mark.asyncio
    async def test_loads_multiple_trades_correctly(self):
        """load_from_db() loads all rows and accumulates them in _trades."""
        rows = [
            _make_db_row(signal_id="sig-001", net_pnl=1.0),
            _make_db_row(signal_id="sig-002", net_pnl=-0.5),
            _make_db_row(signal_id="sig-003", net_pnl=2.0),
        ]
        pool, _ = _make_pool(rows)

        attr = PerformanceAttribution()
        loaded = await attr.load_from_db(pool)

        assert loaded == 3
        assert attr.trade_count == 3
        pnls = [t.pnl for t in attr._trades]
        assert sum(pnls) == pytest.approx(2.5)

    @pytest.mark.asyncio
    async def test_handles_none_net_pnl_as_zero(self):
        """load_from_db() treats None net_pnl as 0.0 (NULL DB field)."""
        row = _make_db_row(net_pnl=0.0)
        row["net_pnl"] = None
        pool, _ = _make_pool([row])

        attr = PerformanceAttribution()
        await attr.load_from_db(pool)

        assert attr._trades[0].pnl == pytest.approx(0.0)


# ===========================================================================
# refresh_views()
# ===========================================================================


class TestRefreshViews:
    """Tests for PerformanceAttribution.refresh_views()."""

    @pytest.mark.asyncio
    async def test_refresh_views_executes_for_all_three_views(self):
        """refresh_views() issues REFRESH MATERIALIZED VIEW for each of the 3 views."""
        pool, mock_conn = _make_pool([])

        attr = PerformanceAttribution()
        await attr.refresh_views(pool)

        assert mock_conn.execute.call_count == 3

    @pytest.mark.asyncio
    async def test_refresh_views_targets_correct_view_names(self):
        """refresh_views() refreshes strategy_daily_pnl, exchange_daily_pnl, pair_daily_pnl."""
        pool, mock_conn = _make_pool([])

        attr = PerformanceAttribution()
        await attr.refresh_views(pool)

        executed_sqls = [str(c.args[0]) for c in mock_conn.execute.call_args_list]
        assert any("strategy_daily_pnl" in sql for sql in executed_sqls)
        assert any("exchange_daily_pnl" in sql for sql in executed_sqls)
        assert any("pair_daily_pnl" in sql for sql in executed_sqls)

    @pytest.mark.asyncio
    async def test_refresh_views_uses_refresh_materialized_view_command(self):
        """refresh_views() uses REFRESH MATERIALIZED VIEW SQL command."""
        pool, mock_conn = _make_pool([])

        attr = PerformanceAttribution()
        await attr.refresh_views(pool)

        executed_sqls = [str(c.args[0]).upper() for c in mock_conn.execute.call_args_list]
        assert all("REFRESH MATERIALIZED VIEW" in sql for sql in executed_sqls)

    @pytest.mark.asyncio
    async def test_refresh_views_continues_on_single_view_failure(self):
        """refresh_views() continues refreshing remaining views if one raises."""
        mock_conn = AsyncMock()
        call_count = [0]

        async def _execute_side_effect(sql):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("View not found")

        mock_conn.execute = _execute_side_effect

        pool = MagicMock()
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        attr = PerformanceAttribution()
        # Must not raise; continues after first failure
        await attr.refresh_views(pool)

        assert call_count[0] == 3  # All 3 views attempted
