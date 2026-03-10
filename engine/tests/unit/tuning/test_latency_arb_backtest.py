"""Tests for latency_arb backtesting integration (US-068 TDD)."""
from __future__ import annotations

import random

import pytest

from src.tuning.backtest import BacktestResult, StrategyParams
from src.tuning.strategy_backtest import STRATEGY_TYPES, StrategyBacktestEngine


# ---------------------------------------------------------------------------
# Signal generator
# ---------------------------------------------------------------------------


class TestLatencyArbSignals:
    def test_make_latency_arb_signals_count(self):
        """latency_arb signal generator produces one signal per candle."""
        from src.tuning.strategy_backtest import _SIGNAL_GENERATORS

        gen = _SIGNAL_GENERATORS.get("latency_arb")
        assert gen is not None, "latency_arb must be registered in _SIGNAL_GENERATORS"

        params = StrategyParams(min_spread_bps=5.0, max_position_size=1000.0)
        rng = random.Random(42)
        closes = [50_000.0 + i * 10 for i in range(100)]

        signals = gen(closes, params, rng)

        assert len(signals) == 100


# ---------------------------------------------------------------------------
# StrategyBacktestEngine integration
# ---------------------------------------------------------------------------


class TestLatencyArbBacktestIntegration:
    def test_latency_arb_strategy_integration(self):
        """StrategyBacktestEngine accepts strategy_type='latency_arb' without error."""
        engine = StrategyBacktestEngine(strategy_type="latency_arb")
        params = StrategyParams(min_spread_bps=5.0, max_position_size=1000.0)

        result = engine.run_with_synthetic_data(params, n_candles=80)

        assert isinstance(result, BacktestResult)

    def test_latency_arb_produces_trades(self):
        """latency_arb backtest produces at least one trade over 200 candles."""
        engine = StrategyBacktestEngine(strategy_type="latency_arb", seed=42)
        params = StrategyParams(min_spread_bps=3.0, max_position_size=1000.0)

        result = engine.run_with_synthetic_data(params, n_candles=200)

        assert result.num_trades > 0, (
            "latency_arb should generate trades when spread exceeds threshold"
        )

    def test_latency_arb_different_params_different_results(self):
        """Tight vs wide min_spread_bps yields different trade counts."""
        engine = StrategyBacktestEngine(strategy_type="latency_arb", seed=42)
        params_tight = StrategyParams(min_spread_bps=2.0, max_position_size=1000.0)
        params_wide = StrategyParams(min_spread_bps=50.0, max_position_size=1000.0)

        result_tight = engine.run_with_synthetic_data(params_tight, n_candles=200)
        result_wide = engine.run_with_synthetic_data(params_wide, n_candles=200)

        assert result_tight.num_trades != result_wide.num_trades, (
            "tight threshold should trade more than wide threshold"
        )
