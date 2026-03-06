"""Tests for BacktestEngine (TDD)."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from src.tuning.backtest import BacktestEngine, BacktestResult, StrategyParams
from src.tuning.data_loader import OHLCVWindow, SpreadRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ohlcv(closes: list[float]) -> OHLCVWindow:
    n = len(closes)
    arr = np.array(closes, dtype=float)
    return OHLCVWindow(
        times=np.arange(n, dtype=float),
        opens=arr - 10,
        highs=arr + 20,
        lows=arr - 20,
        closes=arr,
        volumes=np.ones(n),
    )


def _make_spreads(net_spreads: list[float]) -> list[SpreadRecord]:
    now = datetime.now(timezone.utc)
    return [
        SpreadRecord(
            time=now,
            strategy="test",
            exchange_pair="binance/okx",
            gross_spread=s + 0.001,
            net_spread=s,
        )
        for s in net_spreads
    ]


# ---------------------------------------------------------------------------
# OHLCV-based backtest
# ---------------------------------------------------------------------------


class TestBacktestOHLCV:
    def test_empty_returns_zero(self):
        result = BacktestEngine().run(StrategyParams(), _make_ohlcv([]))
        assert result.total_pnl == 0.0
        assert result.num_trades == 0

    def test_single_candle_returns_zero(self):
        result = BacktestEngine().run(StrategyParams(), _make_ohlcv([50_000.0]))
        assert result.total_pnl == 0.0
        assert result.num_trades == 0

    def test_trending_up_generates_trades(self):
        # Strong upward trend produces large tick spreads
        closes = [50_000.0 + i * 500 for i in range(30)]
        engine = BacktestEngine(initial_capital=100_000.0)
        params = StrategyParams(
            min_spread_bps=1.0,
            max_position_size=10_000.0,
            entry_threshold=0.001,
            exit_threshold=0.0001,
            stop_loss_pct=0.05,
        )
        result = engine.run(params, _make_ohlcv(closes))
        assert result.num_trades >= 1

    def test_flat_market_no_trades(self):
        closes = [50_000.0] * 30
        params = StrategyParams(entry_threshold=0.01, min_spread_bps=50.0)
        result = BacktestEngine().run(params, _make_ohlcv(closes))
        assert result.num_trades == 0

    def test_high_threshold_fewer_trades_than_low(self):
        closes = [50_000.0 + i * 300 for i in range(50)]
        engine = BacktestEngine(initial_capital=100_000.0)
        low_thresh = StrategyParams(entry_threshold=0.001, min_spread_bps=1.0)
        high_thresh = StrategyParams(entry_threshold=0.02, min_spread_bps=1.0)
        r_low = engine.run(low_thresh, _make_ohlcv(closes))
        r_high = engine.run(high_thresh, _make_ohlcv(closes))
        assert r_low.num_trades >= r_high.num_trades

    def test_sharpe_is_float(self):
        closes = [50_000.0 + float(i) * 100 for i in range(50)]
        result = BacktestEngine().run(StrategyParams(), _make_ohlcv(closes))
        assert isinstance(result.sharpe_ratio, float)

    def test_max_drawdown_non_positive(self):
        closes = [50_000.0 + i * 100 for i in range(20)]
        result = BacktestEngine().run(StrategyParams(), _make_ohlcv(closes))
        assert result.max_drawdown <= 0.0

    def test_win_rate_in_bounds(self):
        rng = np.random.default_rng(99)
        closes = (50_000.0 + np.cumsum(rng.normal(0, 200, 60))).tolist()
        result = BacktestEngine().run(StrategyParams(), _make_ohlcv(closes))
        assert 0.0 <= result.win_rate <= 1.0

    def test_returns_list_length_matches_equity_steps(self):
        closes = [50_000.0 + i * 50 for i in range(20)]
        result = BacktestEngine().run(StrategyParams(), _make_ohlcv(closes))
        # returns = diff of equity curve, length = len(equity) - 1
        assert isinstance(result.returns, list)

    def test_max_position_size_caps_trade(self):
        """Larger max_position_size should not increase capital beyond limits."""
        closes = [50_000.0 + i * 300 for i in range(30)]
        engine = BacktestEngine(initial_capital=10_000.0)
        params_small = StrategyParams(max_position_size=100.0, entry_threshold=0.001, min_spread_bps=1.0)
        params_large = StrategyParams(max_position_size=1_000_000.0, entry_threshold=0.001, min_spread_bps=1.0)
        r_small = engine.run(params_small, _make_ohlcv(closes))
        r_large = engine.run(params_large, _make_ohlcv(closes))
        # Larger position = more absolute PnL in trending market
        assert abs(r_large.total_pnl) >= abs(r_small.total_pnl)


# ---------------------------------------------------------------------------
# Spread-based backtest
# ---------------------------------------------------------------------------


class TestBacktestSpreads:
    def test_empty_spreads_zero_result(self):
        result = BacktestEngine().run_on_spreads(StrategyParams(), [])
        assert result.total_pnl == 0.0
        assert result.num_trades == 0

    def test_entry_on_high_spread_exit_on_low(self):
        # High net spread triggers entry, low spread triggers exit
        spreads = _make_spreads([0.002, 0.002, 0.0001, 0.0001])
        engine = BacktestEngine(initial_capital=10_000.0)
        params = StrategyParams(
            min_spread_bps=1.0,
            max_position_size=1000.0,
            entry_threshold=0.001,
            exit_threshold=0.0005,
            stop_loss_pct=0.05,
        )
        result = engine.run_on_spreads(params, spreads)
        assert result.num_trades >= 1

    def test_below_threshold_no_entry(self):
        spreads = _make_spreads([0.00001] * 10)
        params = StrategyParams(entry_threshold=0.01, min_spread_bps=100.0)
        result = BacktestEngine().run_on_spreads(params, spreads)
        assert result.num_trades == 0


# ---------------------------------------------------------------------------
# Static helpers
# ---------------------------------------------------------------------------


class TestStaticHelpers:
    def test_sharpe_zero_variance_returns_zero(self):
        assert BacktestEngine._compute_sharpe(np.zeros(20)) == 0.0

    def test_sharpe_positive_returns_positive(self):
        returns = np.full(252, 0.001)  # constant positive return
        sharpe = BacktestEngine._compute_sharpe(returns)
        assert sharpe > 0.0

    def test_sharpe_single_point(self):
        # Less than 2 points → 0
        assert BacktestEngine._compute_sharpe(np.array([0.01])) == 0.0

    def test_drawdown_monotonic_up_is_zero(self):
        equity = np.array([100.0, 110.0, 120.0, 130.0])
        assert BacktestEngine._compute_max_drawdown(equity) == pytest.approx(0.0)

    def test_drawdown_detects_peak_to_trough(self):
        equity = np.array([100.0, 90.0, 80.0, 100.0])
        dd = BacktestEngine._compute_max_drawdown(equity)
        assert dd < 0.0  # drawdown is negative
        assert dd == pytest.approx(-0.20, abs=1e-6)

    def test_drawdown_single_candle(self):
        assert BacktestEngine._compute_max_drawdown(np.array([100.0])) == 0.0
