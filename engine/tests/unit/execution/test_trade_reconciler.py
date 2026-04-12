"""Unit tests for TradeReconciler — symbol normalization, timestamp matching, IS calc, error paths."""
from __future__ import annotations

import time
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.execution.trade_reconciler import TradeReconciler, _normalize_symbol


# ── _normalize_symbol ─────────────────────────────────────────────────────────

class TestNormalizeSymbol:
    def test_already_normalized(self):
        assert _normalize_symbol("BTC/USDT") == "BTC/USDT"

    def test_raw_usdt(self):
        assert _normalize_symbol("BTCUSDT") == "BTC/USDT"

    def test_raw_usdc(self):
        assert _normalize_symbol("ETHUSDC") == "ETH/USDC"

    def test_raw_eth(self):
        assert _normalize_symbol("BNBETH") == "BNB/ETH"

    def test_raw_btc(self):
        assert _normalize_symbol("ETHBTC") == "ETH/BTC"

    def test_futures_perpetual_suffix(self):
        """BTC/USDT:USDT (futures perpetual) should strip the ':USDT' suffix."""
        assert _normalize_symbol("BTC/USDT:USDT") == "BTC/USDT"

    def test_futures_inverse_suffix(self):
        """BTC/USD:BTC (inverse perpetual) should strip the ':BTC' suffix."""
        assert _normalize_symbol("BTC/USD:BTC") == "BTC/USD"

    def test_unknown_symbol_unchanged(self):
        """Symbols with no known quote suffix are returned as-is."""
        assert _normalize_symbol("UNKNOWNPAIR") == "UNKNOWNPAIR"

    def test_no_double_slash(self):
        """Already-normalized symbols with slash are returned as-is (no extra slash added)."""
        result = _normalize_symbol("SBTCS/USDT")
        assert result == "SBTCS/USDT"

    def test_fdusd_suffix(self):
        assert _normalize_symbol("BTCFDUSD") == "BTC/FDUSD"

    def test_empty_string(self):
        assert _normalize_symbol("") == ""


# ── TradeReconciler.reconcile_period ─────────────────────────────────────────

def _make_datetime_mock(ts: float):
    """Return a mock with .timestamp() == ts (mimics asyncpg datetime row)."""
    m = MagicMock()
    m.timestamp.return_value = ts
    return m


@pytest.fixture
def reconciler_no_db():
    return TradeReconciler(db_pool=None, telegram=None)


@pytest.fixture
def mock_db():
    db = AsyncMock()
    # BUG-77: production code uses self._db.pool.fetch(); make pool self-referential
    # so existing mock_db.fetch assignments are accessible via mock_db.pool.fetch too.
    db.pool = db
    return db


@pytest.fixture
def reconciler_with_db(mock_db):
    return TradeReconciler(db_pool=mock_db, telegram=None)


class TestReconcilerNoDbPool:
    @pytest.mark.asyncio
    async def test_no_db_pool_returns_empty_report(self, reconciler_no_db):
        adapter = AsyncMock()
        adapter.get_trades = AsyncMock(return_value=[{
            "symbol": "BTCUSDT", "ts_ms": 1_000_000, "price": 50000.0, "side": "buy",
        }])
        report = await reconciler_no_db.reconcile_period(
            exchange_adapter=adapter, exchange_id="binance_futures", since_ms=900_000
        )
        assert report.matched == 0
        assert report.unmatched_internal == []

    @pytest.mark.asyncio
    async def test_no_get_trades_returns_early(self, reconciler_no_db):
        adapter = object()  # no get_trades attribute
        report = await reconciler_no_db.reconcile_period(
            exchange_adapter=adapter, exchange_id="binance_futures", since_ms=0
        )
        assert report.matched == 0


class TestReconcilerSymbolNormalization:
    @pytest.mark.asyncio
    async def test_futures_symbol_matches_db_symbol(self, reconciler_with_db, mock_db):
        """Exchange returns raw 'BTCUSDT', DB has 'BTC/USDT:USDT' — should match."""
        ts_now = time.time()
        db_ts = _make_datetime_mock(ts_now - 1.0)  # 1s before exchange fill

        mock_db.fetch = AsyncMock(return_value=[{
            "ts": db_ts,
            "symbol": "BTC/USDT",  # DB stores canonical form (already stripped by normalizer)
            "buy_exchange": "binance_futures",
            "sell_exchange": "bitget_futures",
            "buy_price": 50000.0,
            "sell_price": 50005.0,
            "size": 0.001,
            "slippage_total": None,
        }])
        mock_db.execute = AsyncMock()

        adapter = AsyncMock()
        adapter.get_trades = AsyncMock(return_value=[{
            "symbol": "BTCUSDT",  # raw Binance format
            "ts_ms": int(ts_now * 1000),
            "price": 50001.0,
            "side": "buy",
        }])

        report = await reconciler_with_db.reconcile_period(
            exchange_adapter=adapter, exchange_id="binance_futures", since_ms=0
        )
        assert report.matched == 1
        assert report.unmatched_exchange == []

    @pytest.mark.asyncio
    async def test_futures_perpetual_symbol_normalized(self, reconciler_with_db, mock_db):
        """Exchange returns 'BTC/USDT:USDT', should normalize to 'BTC/USDT' for DB lookup."""
        ts_now = time.time()
        db_ts = _make_datetime_mock(ts_now)

        mock_db.fetch = AsyncMock(return_value=[{
            "ts": db_ts,
            "symbol": "BTC/USDT",
            "buy_exchange": "binance_futures",
            "sell_exchange": "bitget_futures",
            "buy_price": 50000.0,
            "sell_price": 50005.0,
            "size": 0.001,
            "slippage_total": None,
        }])
        mock_db.execute = AsyncMock()

        adapter = AsyncMock()
        adapter.get_trades = AsyncMock(return_value=[{
            "symbol": "BTC/USDT:USDT",  # CCXT futures format
            "ts_ms": int(ts_now * 1000),
            "price": 50001.0,
            "side": "buy",
        }])

        report = await reconciler_with_db.reconcile_period(
            exchange_adapter=adapter, exchange_id="binance_futures", since_ms=0
        )
        assert report.matched == 1


class TestReconcilerTimestampMatching:
    @pytest.mark.asyncio
    async def test_within_30s_window_matches(self, reconciler_with_db, mock_db):
        ts_now = time.time()
        db_ts = _make_datetime_mock(ts_now - 29.9)  # 29.9s earlier — within 30s window

        mock_db.fetch = AsyncMock(return_value=[{
            "ts": db_ts,
            "symbol": "ETH/USDT",
            "buy_exchange": "binance_futures",
            "sell_exchange": "bitget_futures",
            "buy_price": 2000.0,
            "sell_price": 2001.0,
            "size": 0.1,
            "slippage_total": None,
        }])
        mock_db.execute = AsyncMock()

        adapter = AsyncMock()
        adapter.get_trades = AsyncMock(return_value=[{
            "symbol": "ETHUSDT",
            "ts_ms": int(ts_now * 1000),
            "price": 2000.5,
            "side": "buy",
        }])

        report = await reconciler_with_db.reconcile_period(
            exchange_adapter=adapter, exchange_id="binance_futures", since_ms=0
        )
        assert report.matched == 1

    @pytest.mark.asyncio
    async def test_outside_30s_window_unmatched(self, reconciler_with_db, mock_db):
        ts_now = time.time()
        db_ts = _make_datetime_mock(ts_now - 31.0)  # 31s earlier — outside 30s window

        mock_db.fetch = AsyncMock(return_value=[{
            "ts": db_ts,
            "symbol": "ETH/USDT",
            "buy_exchange": "binance_futures",
            "sell_exchange": "bitget_futures",
            "buy_price": 2000.0,
            "sell_price": 2001.0,
            "size": 0.1,
            "slippage_total": None,
        }])
        mock_db.execute = AsyncMock()

        adapter = AsyncMock()
        adapter.get_trades = AsyncMock(return_value=[{
            "symbol": "ETHUSDT",
            "ts_ms": int(ts_now * 1000),
            "price": 2000.5,
            "side": "buy",
        }])

        report = await reconciler_with_db.reconcile_period(
            exchange_adapter=adapter, exchange_id="binance_futures", since_ms=0
        )
        assert report.matched == 0
        assert len(report.unmatched_exchange) == 1


class TestReconcilerUnmatchedInternal:
    @pytest.mark.asyncio
    async def test_db_row_with_no_exchange_fill_is_unmatched_internal(
        self, reconciler_with_db, mock_db
    ):
        """DB has a record but exchange has no matching fill → unmatched_internal."""
        ts_now = time.time()
        db_ts = _make_datetime_mock(ts_now - 1.0)

        mock_db.fetch = AsyncMock(return_value=[{
            "ts": db_ts,
            "symbol": "BTC/USDT",
            "buy_exchange": "binance_futures",
            "sell_exchange": "bitget_futures",
            "buy_price": 50000.0,
            "sell_price": 50005.0,
            "size": 0.001,
            "slippage_total": None,
        }])
        mock_db.execute = AsyncMock()

        adapter = AsyncMock()
        # Exchange has fills for a DIFFERENT symbol
        adapter.get_trades = AsyncMock(return_value=[{
            "symbol": "ETHUSDT",
            "ts_ms": int(ts_now * 1000),
            "price": 2000.0,
            "side": "buy",
        }])

        report = await reconciler_with_db.reconcile_period(
            exchange_adapter=adapter, exchange_id="binance_futures", since_ms=0,
            symbols=["ETH/USDT"],
        )
        assert report.matched == 0
        assert len(report.unmatched_internal) == 1
        assert report.unmatched_internal[0]["symbol"] == "BTC/USDT"

    @pytest.mark.asyncio
    async def test_telegram_called_on_unmatched_internal(self, mock_db):
        """DB phantom record (no matching exchange fill) triggers 내부 미매칭 Telegram alert."""
        telegram = AsyncMock()
        telegram.send_alert = AsyncMock()
        reconciler = TradeReconciler(db_pool=mock_db, telegram=telegram)

        ts_now = time.time()
        db_ts = _make_datetime_mock(ts_now - 1.0)

        mock_db.fetch = AsyncMock(return_value=[{
            "ts": db_ts,
            "symbol": "BTC/USDT",
            "buy_exchange": "binance_futures",
            "sell_exchange": "bitget_futures",
            "buy_price": 50000.0,
            "sell_price": 50005.0,
            "size": 0.001,
            "slippage_total": None,
        }])
        mock_db.execute = AsyncMock()

        adapter = AsyncMock()
        # Exchange has ETH fill (different symbol from DB's BTC/USDT row):
        #   - ETH fill → unmatched_exchange (no matching DB row)
        #   - BTC/USDT DB row → unmatched_internal (no matching exchange fill)
        # Both Telegram alerts fire; verify the internal-mismatch alert is among them.
        adapter.get_trades = AsyncMock(return_value=[{
            "symbol": "ETHUSDT",
            "ts_ms": int(ts_now * 1000),
            "price": 2000.0,
            "side": "buy",
        }])

        await reconciler.reconcile_period(
            exchange_adapter=adapter, exchange_id="binance_futures", since_ms=0,
            symbols=["ETH/USDT"],
        )
        assert telegram.send_alert.call_count >= 1
        all_messages = [call[0][0] for call in telegram.send_alert.call_args_list]
        assert any("내부 미매칭" in msg for msg in all_messages)


class TestReconcilerEmptyFillsWithDBRows:
    """CRITICAL fix: when exchange returns [] but DB has rows, must flag as unmatched_internal."""

    @pytest.mark.asyncio
    async def test_empty_fills_with_db_rows_flagged_as_unmatched_internal(
        self, reconciler_with_db, mock_db
    ):
        """Exchange returns [] (silent API failure) but DB has a row → unmatched_internal populated."""
        ts_now = time.time()
        db_ts = _make_datetime_mock(ts_now - 1.0)

        mock_db.fetch = AsyncMock(return_value=[{
            "ts": db_ts,
            "symbol": "BTC/USDT",
            "buy_exchange": "binance_futures",
            "sell_exchange": "bitget_futures",
            "buy_price": 50000.0,
            "sell_price": 50005.0,
            "size": 0.001,
            "slippage_total": None,
        }])
        mock_db.execute = AsyncMock()

        adapter = AsyncMock()
        adapter.get_trades = AsyncMock(return_value=[])  # silent API failure

        report = await reconciler_with_db.reconcile_period(
            exchange_adapter=adapter, exchange_id="binance_futures", since_ms=0,
            symbols=["BTC/USDT"],
        )
        assert report.matched == 0
        assert len(report.unmatched_internal) == 1
        assert report.unmatched_internal[0]["symbol"] == "BTC/USDT"

    @pytest.mark.asyncio
    async def test_empty_fills_no_db_rows_returns_clean(self, reconciler_with_db, mock_db):
        """Exchange returns [] AND DB has no rows → clean report (no false phantom)."""
        mock_db.fetch = AsyncMock(return_value=[])
        mock_db.execute = AsyncMock()

        adapter = AsyncMock()
        adapter.get_trades = AsyncMock(return_value=[])

        report = await reconciler_with_db.reconcile_period(
            exchange_adapter=adapter, exchange_id="binance_futures", since_ms=0,
            symbols=["BTC/USDT"],
        )
        assert report.matched == 0
        assert report.unmatched_internal == []

    @pytest.mark.asyncio
    async def test_empty_fills_with_db_rows_sends_telegram(self, mock_db):
        """Silent API failure with DB rows triggers Telegram warning alert."""
        telegram = AsyncMock()
        telegram.send_alert = AsyncMock()
        reconciler = TradeReconciler(db_pool=mock_db, telegram=telegram)

        ts_now = time.time()
        db_ts = _make_datetime_mock(ts_now - 1.0)
        mock_db.fetch = AsyncMock(return_value=[{
            "ts": db_ts,
            "symbol": "BTC/USDT",
            "buy_exchange": "binance_futures",
            "sell_exchange": "bitget_futures",
            "buy_price": 50000.0,
            "sell_price": 50005.0,
            "size": 0.001,
            "slippage_total": None,
        }])
        mock_db.execute = AsyncMock()

        adapter = AsyncMock()
        adapter.get_trades = AsyncMock(return_value=[])

        await reconciler.reconcile_period(
            exchange_adapter=adapter, exchange_id="binance_futures", since_ms=0,
            symbols=["BTC/USDT"],
        )
        telegram.send_alert.assert_called_once()
        msg = telegram.send_alert.call_args[0][0]
        assert "팬텀" in msg or "API" in msg or "0건" in msg


class TestReconcilerISCalculation:
    @pytest.mark.asyncio
    async def test_is_bps_calculated_for_matched_fill(self, reconciler_with_db, mock_db):
        """IS = |fill_price - db_price| / db_price * 10000 bps."""
        ts_now = time.time()
        db_ts = _make_datetime_mock(ts_now - 0.5)

        mock_db.fetch = AsyncMock(return_value=[{
            "ts": db_ts,
            "symbol": "BTC/USDT",
            "buy_exchange": "binance_futures",
            "sell_exchange": "bitget_futures",
            "buy_price": 50000.0,   # expected price
            "sell_price": 50010.0,
            "size": 0.001,
            "slippage_total": None,
        }])
        mock_db.execute = AsyncMock()

        adapter = AsyncMock()
        adapter.get_trades = AsyncMock(return_value=[{
            "symbol": "BTCUSDT",
            "ts_ms": int(ts_now * 1000),
            "price": 50005.0,  # actual fill = 5 bps above expected
            "side": "buy",
        }])

        report = await reconciler_with_db.reconcile_period(
            exchange_adapter=adapter, exchange_id="binance_futures", since_ms=0
        )
        assert report.matched == 1
        assert report.is_p50_bps is not None
        # IS = |50005 - 50000| / 50000 * 10000 = 1.0 bps
        assert abs(report.is_p50_bps - 1.0) < 0.01
        assert report.is_p95_bps is not None


    @pytest.mark.asyncio
    async def test_is_bps_uses_sell_price_for_sell_leg(self, reconciler_with_db, mock_db):
        """When exchange_id matches sell_exchange, sell_price is used for IS calc (not buy_price)."""
        ts_now = time.time()
        db_ts = _make_datetime_mock(ts_now - 0.5)

        mock_db.fetch = AsyncMock(return_value=[{
            "ts": db_ts,
            "symbol": "BTC/USDT",
            "buy_exchange": "binance_futures",
            "sell_exchange": "bitget_futures",
            "buy_price": 50000.0,
            "sell_price": 50010.0,   # expected sell price
            "size": 0.001,
            "slippage_total": None,
        }])
        mock_db.execute = AsyncMock()

        adapter = AsyncMock()
        adapter.get_trades = AsyncMock(return_value=[{
            "symbol": "BTCUSDT",
            "ts_ms": int(ts_now * 1000),
            "price": 50007.5,  # actual fill: 2.5 bps below expected sell price 50010
            "side": "sell",
        }])

        # exchange_id=bitget_futures → sell leg → db_price = sell_price = 50010
        report = await reconciler_with_db.reconcile_period(
            exchange_adapter=adapter, exchange_id="bitget_futures", since_ms=0,
            symbols=["BTC/USDT"],
        )
        assert report.matched == 1
        # IS = |50007.5 - 50010| / 50010 * 10000 ≈ 0.5 bps
        assert report.is_p50_bps is not None
        assert abs(report.is_p50_bps - 0.5) < 0.1


class TestReconcilerDBFailurePath:
    @pytest.mark.asyncio
    async def test_db_query_failure_leaves_matched_at_zero(self, reconciler_with_db, mock_db):
        """DB failure must NOT inflate matched count (anti-false-positive)."""
        mock_db.fetch = AsyncMock(side_effect=Exception("DB connection lost"))

        adapter = AsyncMock()
        adapter.get_trades = AsyncMock(return_value=[{
            "symbol": "BTCUSDT",
            "ts_ms": 1_000_000,
            "price": 50000.0,
            "side": "buy",
        }])

        report = await reconciler_with_db.reconcile_period(
            exchange_adapter=adapter, exchange_id="binance_futures", since_ms=0
        )
        # MUST stay at 0, never inflate to len(exchange_fills)
        assert report.matched == 0
        assert report.is_p95_bps is None

    @pytest.mark.asyncio
    async def test_get_trades_failure_returns_empty_report(self, reconciler_with_db, mock_db):
        adapter = AsyncMock()
        adapter.get_trades = AsyncMock(side_effect=Exception("network error"))

        report = await reconciler_with_db.reconcile_period(
            exchange_adapter=adapter, exchange_id="binance_futures", since_ms=0
        )
        assert report.matched == 0

    @pytest.mark.asyncio
    async def test_no_exchange_fills_returns_empty_report(self, reconciler_with_db, mock_db):
        adapter = AsyncMock()
        adapter.get_trades = AsyncMock(return_value=[])

        report = await reconciler_with_db.reconcile_period(
            exchange_adapter=adapter, exchange_id="binance_futures", since_ms=0
        )
        assert report.matched == 0


class TestReconcilerTelegramAlerts:
    @pytest.mark.asyncio
    async def test_telegram_called_on_unmatched_exchange(self, mock_db):
        telegram = AsyncMock()
        telegram.send_alert = AsyncMock()
        reconciler = TradeReconciler(db_pool=mock_db, telegram=telegram)

        ts_now = time.time()
        # DB has NO matching rows for the exchange fill
        mock_db.fetch = AsyncMock(return_value=[])
        mock_db.execute = AsyncMock()

        adapter = AsyncMock()
        adapter.get_trades = AsyncMock(return_value=[{
            "symbol": "BTCUSDT",
            "ts_ms": int(ts_now * 1000),
            "price": 50000.0,
            "side": "buy",
        }])

        await reconciler.reconcile_period(
            exchange_adapter=adapter, exchange_id="binance_futures", since_ms=0,
            symbols=["BTC/USDT"],  # bypass DB symbol derivation (mock_db.fetch=[] exits early)
        )
        # unmatched_exchange should trigger Telegram alert
        telegram.send_alert.assert_called_once()
        assert "거래소 미매칭" in telegram.send_alert.call_args[0][0]
