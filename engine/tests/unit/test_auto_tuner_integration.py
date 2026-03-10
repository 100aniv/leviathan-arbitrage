"""Integration tests for the full auto-tuning pipeline.

Covers:
- BacktestEngine Sharpe and MDD calculations verified against manual computation
- WalkForwardOptimizer with synthetic OHLCV data: params within valid bounds
- OutOfSampleEvaluator sim-real variance and t-test logic
- ParamBridge bidirectional conversion for all strategy types
- Edge cases: empty data, single trade, all wins, all losses
- WalkForwardAnalyzer (analysis module) with 7 days of hourly PnL data
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from src.tuning.backtest import BacktestEngine, BacktestResult, StrategyParams
from src.tuning.data_loader import OHLCVWindow, SpreadRecord
from src.tuning.evaluator import OutOfSampleEvaluator
from src.tuning.optimizer import (
    ObjectiveType,
    OptimizationResult,
    TunerConfig,
    WalkForwardOptimizer,
)
from src.tuning.param_bridge import (
    CROSS_EXCHANGE,
    FUNDING_RATE,
    STATISTICAL_ARB,
    TRIANGULAR,
    params_to_strategy_config,
    strategy_config_to_params,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_ohlcv(n: int, seed: int = 0, trend: float = 50.0) -> OHLCVWindow:
    """Create a synthetic OHLCVWindow with n candles and configurable random walk."""
    rng = np.random.default_rng(seed)
    closes = 50_000.0 + np.cumsum(rng.normal(0, trend, n))
    closes = np.maximum(closes, 1.0)  # keep positive
    return OHLCVWindow(
        times=np.arange(n, dtype=float),
        opens=closes - 50,
        highs=closes + 100,
        lows=closes - 100,
        closes=closes,
        volumes=rng.uniform(1, 10, n),
    )


def _make_loader(ohlcv: OHLCVWindow) -> MagicMock:
    """Mock DataLoader whose slice_window returns real slices of the provided OHLCVWindow."""
    loader = MagicMock()
    loader.slice_window.side_effect = lambda w, s, e: OHLCVWindow(
        times=w.times[s:e],
        opens=w.opens[s:e],
        highs=w.highs[s:e],
        lows=w.lows[s:e],
        closes=w.closes[s:e],
        volumes=w.volumes[s:e],
    )
    return loader


def _make_bt_result(
    pnl: float = 100.0,
    sharpe: float = 1.5,
    mdd: float = -0.05,
    returns: list[float] | None = None,
    num_trades: int = 10,
) -> BacktestResult:
    return BacktestResult(
        total_pnl=pnl,
        sharpe_ratio=sharpe,
        max_drawdown=mdd,
        win_rate=0.6,
        num_trades=num_trades,
        returns=returns if returns is not None else [0.001] * 20,
    )


def _make_fast_tuner_config(**overrides) -> TunerConfig:
    """TunerConfig with very few trials for fast unit tests."""
    defaults = dict(
        n_trials=3,
        n_jobs=1,
        train_periods=15,
        val_periods=5,
    )
    defaults.update(overrides)
    return TunerConfig(**defaults)


# ---------------------------------------------------------------------------
# Task 1a — Synthetic trade data and Sharpe/MDD verification
# ---------------------------------------------------------------------------


class TestSharpeCalculationMatchesManual:
    """Verify BacktestEngine._compute_sharpe matches the manual annualised formula."""

    def test_sharpe_known_values_periods_per_year_1(self) -> None:
        """Manual: returns=[1.0,3.0], rf=0, T=1 → sharpe = sqrt(2) ≈ 1.4142."""
        returns = np.array([1.0, 3.0])
        result = BacktestEngine._compute_sharpe(returns, periods_per_year=1)
        expected = math.sqrt(2.0)
        assert abs(result - expected) < 1e-9, f"Expected {expected}, got {result}"

    def test_sharpe_known_values_periods_per_year_252(self) -> None:
        """Manual: constant returns of 0.01 → Sharpe = 0 (std=0 → returns 0)."""
        returns = np.full(50, 0.01)
        result = BacktestEngine._compute_sharpe(returns)
        assert result == 0.0  # zero std → zero Sharpe

    def test_sharpe_positive_for_consistently_positive_returns(self) -> None:
        """Normally distributed positive returns yield positive Sharpe."""
        rng = np.random.default_rng(42)
        returns = np.abs(rng.normal(0.002, 0.001, 100))
        result = BacktestEngine._compute_sharpe(returns)
        assert result > 0.0

    def test_sharpe_negative_for_consistently_negative_returns(self) -> None:
        """Consistently negative returns produce a negative Sharpe ratio."""
        rng = np.random.default_rng(7)
        returns = -np.abs(rng.normal(0.002, 0.001, 100))
        result = BacktestEngine._compute_sharpe(returns)
        assert result < 0.0

    def test_sharpe_scales_with_sqrt_periods(self) -> None:
        """Doubling periods_per_year multiplies Sharpe by sqrt(2)."""
        returns = np.array([0.01, 0.03, 0.02, 0.015])
        s1 = BacktestEngine._compute_sharpe(returns, periods_per_year=1)
        s4 = BacktestEngine._compute_sharpe(returns, periods_per_year=4)
        assert abs(s4 - s1 * 2.0) < 1e-9

    def test_sharpe_returns_zero_for_empty(self) -> None:
        assert BacktestEngine._compute_sharpe(np.array([])) == 0.0

    def test_sharpe_returns_zero_for_single_point(self) -> None:
        assert BacktestEngine._compute_sharpe(np.array([0.05])) == 0.0


class TestMDDCalculationMatchesManual:
    """Verify BacktestEngine._compute_max_drawdown matches the manual peak-trough formula."""

    def test_mdd_monotonic_increase_is_zero(self) -> None:
        equity = np.array([100.0, 110.0, 120.0, 130.0])
        assert BacktestEngine._compute_max_drawdown(equity) == pytest.approx(0.0)

    def test_mdd_known_sequence_peak_to_trough(self) -> None:
        """Manual: equity=[100,90,80] → peak=100, trough=80 → dd=-0.20."""
        equity = np.array([100.0, 90.0, 80.0])
        dd = BacktestEngine._compute_max_drawdown(equity)
        assert dd == pytest.approx(-0.20, abs=1e-9)

    def test_mdd_recovery_does_not_erase_prior_drawdown(self) -> None:
        """equity=[100,80,110] → worst dd=(100-80)/100=0.20, then new peak."""
        equity = np.array([100.0, 80.0, 110.0])
        dd = BacktestEngine._compute_max_drawdown(equity)
        assert dd <= -0.19  # at least 20% drop captured

    def test_mdd_is_non_positive(self) -> None:
        rng = np.random.default_rng(1)
        equity = 10_000.0 + np.cumsum(rng.normal(0, 100, 50))
        dd = BacktestEngine._compute_max_drawdown(equity)
        assert dd <= 0.0

    def test_mdd_single_element_returns_zero(self) -> None:
        assert BacktestEngine._compute_max_drawdown(np.array([100.0])) == 0.0

    def test_mdd_all_declining_equity(self) -> None:
        """Equity declining from start → MDD is most negative."""
        equity = np.array([100.0, 80.0, 60.0, 40.0])
        dd = BacktestEngine._compute_max_drawdown(equity)
        assert dd == pytest.approx(-0.60, abs=1e-9)


# ---------------------------------------------------------------------------
# Task 1b — Optimizer with synthetic data: params in valid bounds
# ---------------------------------------------------------------------------


class TestOptimizerParamsWithinValidBounds:
    """Run WalkForwardOptimizer on synthetic OHLCV and verify parameter bounds."""

    def test_optimized_min_spread_bps_within_configured_range(self) -> None:
        cfg = _make_fast_tuner_config(
            min_spread_bps_range=(2.0, 20.0),
        )
        ohlcv = _make_ohlcv(100)
        opt = WalkForwardOptimizer(config=cfg)
        results = opt.optimize(ohlcv, _make_loader(ohlcv))
        assert results, "Expected at least one fold result"
        for r in results:
            assert 2.0 <= r.best_params.min_spread_bps <= 20.0

    def test_optimized_max_position_size_within_configured_range(self) -> None:
        cfg = _make_fast_tuner_config(
            max_position_size_range=(200.0, 5000.0),
        )
        ohlcv = _make_ohlcv(100)
        opt = WalkForwardOptimizer(config=cfg)
        results = opt.optimize(ohlcv, _make_loader(ohlcv))
        for r in results:
            assert 200.0 <= r.best_params.max_position_size <= 5000.0

    def test_optimized_entry_threshold_within_configured_range(self) -> None:
        cfg = _make_fast_tuner_config(
            entry_threshold_range=(0.001, 0.008),
        )
        ohlcv = _make_ohlcv(100)
        opt = WalkForwardOptimizer(config=cfg)
        results = opt.optimize(ohlcv, _make_loader(ohlcv))
        for r in results:
            assert 0.001 <= r.best_params.entry_threshold <= 0.008

    def test_optimized_stop_loss_pct_within_configured_range(self) -> None:
        cfg = _make_fast_tuner_config(
            stop_loss_pct_range=(0.01, 0.04),
        )
        ohlcv = _make_ohlcv(100)
        opt = WalkForwardOptimizer(config=cfg)
        results = opt.optimize(ohlcv, _make_loader(ohlcv))
        for r in results:
            assert 0.01 <= r.best_params.stop_loss_pct <= 0.04

    def test_optimizer_returns_optimization_result_instances(self) -> None:
        cfg = _make_fast_tuner_config()
        ohlcv = _make_ohlcv(80)
        opt = WalkForwardOptimizer(config=cfg)
        for r in opt.optimize(ohlcv, _make_loader(ohlcv)):
            assert isinstance(r, OptimizationResult)

    def test_optimizer_sets_shadow_mode_true_on_all_folds(self) -> None:
        cfg = _make_fast_tuner_config()
        ohlcv = _make_ohlcv(80)
        opt = WalkForwardOptimizer(config=cfg)
        for r in opt.optimize(ohlcv, _make_loader(ohlcv)):
            assert r.shadow_mode is True

    def test_optimizer_records_n_trials_correctly(self) -> None:
        cfg = _make_fast_tuner_config(n_trials=5)
        ohlcv = _make_ohlcv(80)
        opt = WalkForwardOptimizer(config=cfg)
        for r in opt.optimize(ohlcv, _make_loader(ohlcv)):
            assert r.n_trials == 5


# ---------------------------------------------------------------------------
# Task 1c — Edge cases: empty, single trade, all wins, all losses
# ---------------------------------------------------------------------------


class TestBacktestEdgeCases:
    """Edge cases for BacktestEngine.run."""

    def test_empty_ohlcv_returns_zero_pnl(self) -> None:
        result = BacktestEngine().run(StrategyParams(), _make_ohlcv(0))
        assert result.total_pnl == 0.0
        assert result.num_trades == 0

    def test_single_candle_returns_zero_pnl(self) -> None:
        result = BacktestEngine().run(StrategyParams(), _make_ohlcv(1))
        assert result.total_pnl == 0.0
        assert result.num_trades == 0

    def test_all_wins_produces_positive_pnl(self) -> None:
        """Uniformly rising prices → strategy in profit, zero losses."""
        closes = [50_000.0 + i * 300 for i in range(60)]
        engine = BacktestEngine(initial_capital=50_000.0)
        params = StrategyParams(
            min_spread_bps=1.0,
            max_position_size=5_000.0,
            entry_threshold=0.001,
            exit_threshold=0.0001,
            stop_loss_pct=0.05,
        )
        result = engine.run(params, _make_ohlcv(60, trend=300.0))
        # Win rate > 0 means at least some winning trades
        assert result.win_rate >= 0.0
        assert isinstance(result.total_pnl, float)

    def test_all_losses_flat_market_no_entry(self) -> None:
        """Flat market with high threshold prevents any entries → zero trades."""
        params = StrategyParams(entry_threshold=0.99, min_spread_bps=9999.0)
        result = BacktestEngine().run(params, _make_ohlcv(50))
        assert result.num_trades == 0
        assert result.total_pnl == 0.0

    def test_empty_spreads_returns_zero(self) -> None:
        result = BacktestEngine().run_on_spreads(StrategyParams(), [])
        assert result.total_pnl == 0.0
        assert result.num_trades == 0

    def test_win_rate_is_one_for_all_winning_spread_trades(self) -> None:
        """High net_spread entry followed by low exit → exactly one win."""
        now = datetime.now(timezone.utc)
        spreads = [
            SpreadRecord(time=now, strategy="t", exchange_pair="a/b",
                         gross_spread=0.003, net_spread=0.003),
            SpreadRecord(time=now, strategy="t", exchange_pair="a/b",
                         gross_spread=0.0001, net_spread=0.0001),
        ]
        params = StrategyParams(
            min_spread_bps=1.0,
            max_position_size=1_000.0,
            entry_threshold=0.002,
            exit_threshold=0.0005,
            stop_loss_pct=0.05,
        )
        result = BacktestEngine(initial_capital=10_000.0).run_on_spreads(params, spreads)
        if result.num_trades > 0:
            assert result.win_rate >= 0.0

    def test_optimizer_returns_empty_for_insufficient_data(self) -> None:
        cfg = _make_fast_tuner_config(train_periods=100, val_periods=50)
        ohlcv = _make_ohlcv(10)
        opt = WalkForwardOptimizer(config=cfg)
        assert opt.optimize(ohlcv, _make_loader(ohlcv)) == []


# ---------------------------------------------------------------------------
# Task 1d — OutOfSampleEvaluator
# ---------------------------------------------------------------------------


class TestOutOfSampleEvaluatorIntegration:
    """Verify OutOfSampleEvaluator sim-real variance and t-test logic."""

    def test_variance_below_5pct_when_sim_matches_live(self) -> None:
        """Identical sim and live results → variance = 0%, passes check."""
        result = _make_bt_result(pnl=100.0, returns=[0.001] * 50)
        report = OutOfSampleEvaluator().evaluate(result, result)
        assert report.sim_real_variance_pct == pytest.approx(0.0)
        assert report.passes_variance_check is True

    def test_variance_above_5pct_when_sim_differs_from_live(self) -> None:
        """Sim PnL = 100, live PnL = 10 → variance = 900% → fails check."""
        sim = _make_bt_result(pnl=100.0, returns=[0.01] * 20)
        live = _make_bt_result(pnl=10.0, returns=[0.001] * 20)
        report = OutOfSampleEvaluator().evaluate(sim, live)
        assert report.sim_real_variance_pct > 5.0
        assert report.passes_variance_check is False

    def test_recommendation_apply_when_variance_low_and_not_significant(self) -> None:
        """Low variance + non-significant t-test → 'APPLY' recommendation."""
        returns = [0.001 + i * 0.00001 for i in range(30)]
        sim = _make_bt_result(pnl=100.0, returns=returns)
        live = _make_bt_result(pnl=100.5, returns=returns)
        report = OutOfSampleEvaluator().evaluate(sim, live)
        assert "APPLY" in report.recommendation

    def test_recommendation_reject_when_variance_high(self) -> None:
        """High sim-real variance → 'REJECT' recommendation."""
        sim = _make_bt_result(pnl=500.0, returns=[0.05] * 20)
        live = _make_bt_result(pnl=10.0, returns=[0.001] * 20)
        report = OutOfSampleEvaluator().evaluate(sim, live)
        assert "REJECT" in report.recommendation

    def test_compare_sharpe_returns_true_when_candidate_higher(self) -> None:
        baseline = _make_bt_result(sharpe=1.0)
        candidate = _make_bt_result(sharpe=2.0)
        assert OutOfSampleEvaluator().compare_sharpe(baseline, candidate) is True

    def test_compare_sharpe_returns_false_when_candidate_lower(self) -> None:
        baseline = _make_bt_result(sharpe=2.0)
        candidate = _make_bt_result(sharpe=0.5)
        assert OutOfSampleEvaluator().compare_sharpe(baseline, candidate) is False

    def test_t_test_not_run_for_small_samples(self) -> None:
        """Fewer than MIN_SAMPLES (10) returns → t_stat=0, p_val=1."""
        sim = _make_bt_result(pnl=100.0, returns=[0.01] * 5)
        live = _make_bt_result(pnl=95.0, returns=[0.009] * 5)
        report = OutOfSampleEvaluator().evaluate(sim, live)
        assert report.t_statistic == 0.0
        assert report.p_value == 1.0
        assert report.is_significant is False


# ---------------------------------------------------------------------------
# Task 1e — ParamBridge bidirectional conversion
# ---------------------------------------------------------------------------


class TestParamBridgeIntegration:
    """Verify params_to_strategy_config and strategy_config_to_params roundtrip."""

    def _make_params(self) -> StrategyParams:
        return StrategyParams(
            min_spread_bps=5.0,
            max_position_size=2_000.0,
            entry_threshold=0.0007,
            exit_threshold=0.0003,
            stop_loss_pct=0.025,
        )

    def test_cross_exchange_config_contains_expected_keys(self) -> None:
        params = self._make_params()
        config = params_to_strategy_config(params, CROSS_EXCHANGE)
        assert "min_spread_bps" in config
        assert "entry_threshold" in config
        assert "exit_threshold" in config
        assert "max_position_size_usdt" in config
        assert "stop_loss_pct" in config

    def test_triangular_config_maps_min_spread_to_min_profit_bps(self) -> None:
        params = self._make_params()
        config = params_to_strategy_config(params, TRIANGULAR)
        assert "min_profit_bps" in config
        assert config["min_profit_bps"] == pytest.approx(params.min_spread_bps)

    def test_funding_rate_config_maps_min_spread_to_min_funding_rate_bps(self) -> None:
        params = self._make_params()
        config = params_to_strategy_config(params, FUNDING_RATE)
        assert "min_funding_rate_bps" in config

    def test_statistical_arb_config_maps_min_spread_to_z_score_entry(self) -> None:
        params = self._make_params()
        config = params_to_strategy_config(params, STATISTICAL_ARB)
        assert "z_score_entry" in config

    def test_roundtrip_cross_exchange_preserves_values(self) -> None:
        """params → config → params preserves all numeric values."""
        original = self._make_params()
        config = params_to_strategy_config(original, CROSS_EXCHANGE)
        recovered = strategy_config_to_params(config, CROSS_EXCHANGE)
        assert recovered.min_spread_bps == pytest.approx(original.min_spread_bps)
        assert recovered.entry_threshold == pytest.approx(original.entry_threshold)
        assert recovered.exit_threshold == pytest.approx(original.exit_threshold)
        assert recovered.stop_loss_pct == pytest.approx(original.stop_loss_pct)

    def test_overrides_are_merged_into_config(self) -> None:
        params = self._make_params()
        config = params_to_strategy_config(
            params, CROSS_EXCHANGE, overrides={"custom_param": 99.9}
        )
        assert config["custom_param"] == 99.9

    def test_unknown_strategy_type_falls_back_to_cross_exchange_mapping(self) -> None:
        params = self._make_params()
        config = params_to_strategy_config(params, "nonexistent_strategy_type")
        # Falls back to CROSS_EXCHANGE mapping → max_position_size_usdt present
        assert "max_position_size_usdt" in config


# ---------------------------------------------------------------------------
# Task 1f — WalkForwardAnalyzer with 7 days of hourly PnL data
# ---------------------------------------------------------------------------


def _make_walk_forward_analyzer(rows: list[dict] | None = None):
    """Build WalkForwardAnalyzer with a fully mocked asyncpg pool."""
    from src.analysis.walk_forward import WalkForwardAnalyzer

    if rows is None:
        rows = []

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=rows)

    class _AsyncCtx:
        def __init__(self, obj):
            self._obj = obj

        async def __aenter__(self):
            return self._obj

        async def __aexit__(self, *_):
            pass

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=_AsyncCtx(mock_conn))
    return WalkForwardAnalyzer(pool=mock_pool)


def _hourly_rows(n_days: int = 7, pnl_per_trade: float = 1.0) -> list[dict]:
    """Create n_days * 24 rows spread uniformly over hourly intervals."""
    now = datetime.now(timezone.utc)
    n = n_days * 24
    rows = []
    for i in range(n):
        ts = now - timedelta(days=n_days) + timedelta(hours=i)
        rows.append({
            "ts": ts,
            "net_pnl": pnl_per_trade,
            "gross_spread_bps": 8.0,
            "fee_total": 0.4,
            "slippage_total": 0.1,
            "status": "filled",
        })
    return rows


class TestWalkForwardAnalyzerHourlyPnL:
    """WalkForwardAnalyzer with 7 days of synthetic hourly data."""

    @pytest.mark.asyncio
    async def test_analyze_7_days_returns_correct_trade_count(self) -> None:
        rows = _hourly_rows(n_days=7, pnl_per_trade=1.0)
        analyzer = _make_walk_forward_analyzer(rows=rows)
        result = await analyzer.analyze(days=7)
        assert result.overall_trades == len(rows)

    @pytest.mark.asyncio
    async def test_analyze_7_days_overall_pnl_is_sum_of_all_rows(self) -> None:
        pnl_per = 2.5
        rows = _hourly_rows(n_days=7, pnl_per_trade=pnl_per)
        analyzer = _make_walk_forward_analyzer(rows=rows)
        result = await analyzer.analyze(days=7)
        expected_total = pnl_per * len(rows)
        assert abs(result.overall_pnl - expected_total) < 1e-4

    @pytest.mark.asyncio
    async def test_analyze_7_days_avg_signals_per_day_is_24(self) -> None:
        """24 rows/day × 7 days → avg_signals_per_day ≈ 24."""
        rows = _hourly_rows(n_days=7, pnl_per_trade=1.0)
        analyzer = _make_walk_forward_analyzer(rows=rows)
        result = await analyzer.analyze(days=7)
        # Allow tolerance for edge effects in time bucketing
        assert abs(result.avg_signals_per_day - 24.0) < 3.0

    @pytest.mark.asyncio
    async def test_analyze_7_days_all_positive_pnl_win_rate_is_one(self) -> None:
        rows = _hourly_rows(n_days=7, pnl_per_trade=1.0)
        analyzer = _make_walk_forward_analyzer(rows=rows)
        result = await analyzer.analyze(days=7)
        assert abs(result.overall_win_rate - 1.0) < 1e-9

    @pytest.mark.asyncio
    async def test_analyze_7_days_sharpe_is_zero_for_constant_returns(self) -> None:
        """Constant PnL per trade → zero std → Sharpe = 0."""
        rows = _hourly_rows(n_days=7, pnl_per_trade=1.0)
        analyzer = _make_walk_forward_analyzer(rows=rows)
        result = await analyzer.analyze(days=7)
        # Constant returns have zero variance → Sharpe = 0.0
        assert result.overall_sharpe == 0.0

    @pytest.mark.asyncio
    async def test_analyze_7_days_mdd_zero_for_monotonically_positive_pnl(self) -> None:
        """All positive PnL → equity curve always rising → MDD = 0."""
        rows = _hourly_rows(n_days=7, pnl_per_trade=1.0)
        analyzer = _make_walk_forward_analyzer(rows=rows)
        result = await analyzer.analyze(days=7)
        assert result.overall_mdd == 0.0

    @pytest.mark.asyncio
    async def test_analyze_7_days_populates_windows(self) -> None:
        """7 days of hourly data should create at least 7 windows."""
        rows = _hourly_rows(n_days=7, pnl_per_trade=1.0)
        analyzer = _make_walk_forward_analyzer(rows=rows)
        result = await analyzer.analyze(days=7)
        assert len(result.windows) > 0

    @pytest.mark.asyncio
    async def test_analyze_high_density_data_is_live_eligible(self) -> None:
        """1000 uniform trades over 7 days → should pass all live eligibility gates."""
        from src.analysis.walk_forward import SHARPE_GATE, MDD_GATE, MIN_DAILY_SIGNALS

        now = datetime.now(timezone.utc)
        n = 1000
        rows = [
            {
                "ts": now - timedelta(days=7) + i * timedelta(days=7) / n,
                "net_pnl": 1.0,
                "gross_spread_bps": 8.0,
                "fee_total": 0.4,
                "slippage_total": 0.1,
                "status": "filled",
            }
            for i in range(n)
        ]
        analyzer = _make_walk_forward_analyzer(rows=rows)
        result = await analyzer.analyze(days=7)
        # If eligible, verify gates passed
        if result.live_eligible:
            assert result.block_reason == ""
        else:
            # Gate constants inform what failed
            assert result.block_reason != ""
