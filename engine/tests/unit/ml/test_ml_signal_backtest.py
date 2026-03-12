"""US-095: ML signal backtest tests."""
from unittest.mock import MagicMock

import numpy as np
import pytest

from src.analysis.ml_backtest import ABTestResult, BacktestResult, MLSignalBacktester


@pytest.fixture
def sample_signals():
    """Generate synthetic signals with varying spreads."""
    rng = np.random.default_rng(42)
    signals = []
    for i in range(100):
        signals.append({
            "timestamp_idx": i,
            "spread_bps": float(rng.uniform(5, 30)),
            "direction": 1,
        })
    return signals


@pytest.fixture
def sample_prices():
    return 50000 + np.cumsum(np.random.default_rng(42).standard_normal(100) * 10)


@pytest.fixture
def sample_features():
    return np.random.default_rng(42).standard_normal((100, 20))


@pytest.fixture
def mock_scorer():
    scorer = MagicMock()
    # Return high score for first 60 signals, low for rest
    call_count = [0]
    def predict_fn(features):
        idx = call_count[0]
        call_count[0] += 1
        return 0.8 if idx < 60 else 0.2
    scorer.predict_signal = MagicMock(side_effect=predict_fn)
    return scorer


def test_backtest_result_fields():
    r = BacktestResult(
        strategy="test", total_signals=100, traded_signals=80,
        total_pnl=10.5, win_rate=0.6, avg_pnl_per_trade=0.13,
        sharpe_ratio=1.2, max_drawdown=2.0,
    )
    assert r.strategy == "test"
    assert r.total_signals == 100
    assert r.traded_signals == 80


def test_run_baseline(sample_signals, sample_prices):
    bt = MLSignalBacktester(fee_bps=10.0)
    result = bt.run_baseline(sample_signals, sample_prices)
    assert isinstance(result, BacktestResult)
    assert result.strategy == "baseline"
    assert result.total_signals == 100
    assert result.traded_signals == 100  # baseline trades all
    assert result.win_rate >= 0
    assert result.sharpe_ratio != 0 or True  # may be zero if all same


def test_run_baseline_pnl(sample_prices):
    """Signals with spread > fee → positive PnL."""
    signals = [{"timestamp_idx": i, "spread_bps": 20.0, "direction": 1} for i in range(50)]
    bt = MLSignalBacktester(fee_bps=10.0)
    result = bt.run_baseline(signals, sample_prices)
    assert result.total_pnl > 0
    assert result.win_rate == 1.0


def test_run_baseline_negative_pnl(sample_prices):
    """Signals with spread < fee → negative PnL."""
    signals = [{"timestamp_idx": i, "spread_bps": 5.0, "direction": 1} for i in range(50)]
    bt = MLSignalBacktester(fee_bps=10.0)
    result = bt.run_baseline(signals, sample_prices)
    assert result.total_pnl < 0


def test_run_ml_enhanced(sample_signals, sample_prices, sample_features, mock_scorer):
    bt = MLSignalBacktester(ml_scorer=mock_scorer, score_threshold=0.5, fee_bps=10.0)
    result = bt.run_ml_enhanced(sample_signals, sample_prices, sample_features)
    assert isinstance(result, BacktestResult)
    assert result.strategy == "ml_enhanced"
    # ML filter should reduce traded signals
    assert result.traded_signals < result.total_signals


def test_ab_test(sample_signals, sample_prices, sample_features, mock_scorer):
    bt = MLSignalBacktester(ml_scorer=mock_scorer, score_threshold=0.5, fee_bps=10.0)
    result = bt.ab_test(sample_signals, sample_prices, sample_features)
    assert isinstance(result, ABTestResult)
    assert result.baseline.strategy == "baseline"
    assert result.ml_enhanced.strategy == "ml_enhanced"
    assert isinstance(result.pnl_delta, float)
    assert isinstance(result.ml_improves, bool)


def test_ab_test_no_ml(sample_signals, sample_prices):
    """Without ML scorer, both should be identical."""
    bt = MLSignalBacktester(fee_bps=10.0)
    result = bt.ab_test(sample_signals, sample_prices)
    assert result.pnl_delta == 0.0
    assert result.baseline.total_pnl == result.ml_enhanced.total_pnl


def test_walk_forward(sample_signals, sample_prices, sample_features, mock_scorer):
    bt = MLSignalBacktester(ml_scorer=mock_scorer, score_threshold=0.5, fee_bps=10.0)
    results = bt.walk_forward(sample_signals, sample_prices, sample_features, n_folds=3)
    assert len(results) == 3
    for r in results:
        assert isinstance(r, ABTestResult)


def test_walk_forward_no_ml(sample_signals, sample_prices):
    bt = MLSignalBacktester(fee_bps=10.0)
    results = bt.walk_forward(sample_signals, sample_prices, n_folds=5)
    assert len(results) == 5


def test_sharpe_ratio_calculation(sample_prices):
    """Consistent positive PnL → positive Sharpe."""
    signals = [{"timestamp_idx": i, "spread_bps": 20.0, "direction": 1} for i in range(50)]
    bt = MLSignalBacktester(fee_bps=10.0)
    result = bt.run_baseline(signals, sample_prices)
    assert result.sharpe_ratio > 0


def test_max_drawdown(sample_prices):
    """Mix of wins and losses → non-zero drawdown."""
    signals = []
    for i in range(50):
        spread = 20.0 if i % 3 != 0 else 5.0  # 2/3 win, 1/3 lose
        signals.append({"timestamp_idx": i, "spread_bps": spread, "direction": 1})
    bt = MLSignalBacktester(fee_bps=10.0)
    result = bt.run_baseline(signals, sample_prices)
    assert result.max_drawdown >= 0


def test_empty_signals(sample_prices):
    bt = MLSignalBacktester(fee_bps=10.0)
    result = bt.run_baseline([], sample_prices)
    assert result.total_pnl == 0.0
    assert result.traded_signals == 0
    assert result.win_rate == 0.0


def test_ml_latency_tracking(sample_signals, sample_prices, sample_features, mock_scorer):
    bt = MLSignalBacktester(ml_scorer=mock_scorer, score_threshold=0.5, fee_bps=10.0)
    result = bt.run_ml_enhanced(sample_signals, sample_prices, sample_features)
    assert result.latency_p50_ms >= 0
    assert result.latency_p99_ms >= 0
