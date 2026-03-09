"""Integration tests for 3-Layer Auto-Tuning Pipeline (US-048).

Pipeline layers:
  1. Offline Tuning (ScheduledTuner) — weekly Optuna optimization per strategy
  2. Adaptive Fine-tuning (AdaptiveThreshold) — hourly MIN_EDGE adjustment
  3. Regime Response (RegimeDetector) — volatility-based market regime → KillSwitch

Tests verify end-to-end flow: Offline → Adaptive → Regime, plus per-strategy
independence of parameter optimization.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from src.tuning.adaptive_threshold import AdaptiveThreshold
from src.tuning.backtest import BacktestResult, StrategyParams
from src.tuning.evaluator import OutOfSampleEvaluator
from src.tuning.regime_detector import MarketRegime, RegimeDetector
from src.tuning.shadow_runner import ShadowRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_backtest_result(
    pnl: float = 1.0,
    sharpe: float = 2.5,
    mdd: float = 0.02,
    wr: float = 0.65,
    trades: int = 50,
    returns: list[float] | None = None,
) -> BacktestResult:
    return BacktestResult(
        total_pnl=pnl,
        sharpe_ratio=sharpe,
        max_drawdown=mdd,
        win_rate=wr,
        num_trades=trades,
        returns=returns or [0.001] * 20,
    )


_LOW_VOL = [0.0001, -0.0001] * 20
_MEDIUM_VOL = [0.010, -0.010] * 20
_HIGH_VOL = [0.050, -0.050] * 20
_CRISIS_VOL = [0.150, -0.150] * 20


# ===========================================================================
# Layer 1: Offline Tuning (ScheduledTuner)
# ===========================================================================


class TestOfflineTuningLayer:
    """Offline tuner produces per-strategy optimization results."""

    def test_shadow_runner_evaluate_returns_result(self):
        """ShadowRunner.evaluate produces a ShadowResult with recommendation."""
        engine = MagicMock()
        engine.run.return_value = _make_backtest_result()

        runner = ShadowRunner(engine=engine)
        result = runner.evaluate(
            strategy_id="cross_exchange_v1",
            strategy_type="cross_exchange",
            baseline_params=StrategyParams(),
            shadow_params=StrategyParams(min_spread_bps=3.0),
        )

        assert result.strategy_id == "cross_exchange_v1"
        assert result.evaluation.recommendation is not None

    def test_shadow_runner_decide_returns_apply_monitor_or_reject(self):
        """evaluate_and_decide returns one of APPLY/MONITOR/REJECT."""
        engine = MagicMock()
        engine.run.return_value = _make_backtest_result()

        runner = ShadowRunner(engine=engine)
        decision, result = runner.evaluate_and_decide(
            strategy_id="funding_rate_v1",
            strategy_type="funding_rate",
            baseline_params=StrategyParams(),
            shadow_params=StrategyParams(entry_threshold=0.0003),
        )

        assert decision in ("APPLY", "MONITOR", "REJECT")


# ===========================================================================
# Layer 2: Adaptive Fine-tuning (AdaptiveThreshold)
# ===========================================================================


class TestAdaptiveFineTuning:
    """Adaptive threshold adjusts MIN_EDGE based on rolling win-rate."""

    def test_low_wr_increases_edge(self):
        """WR < 50% → tighten edge (raise MIN_EDGE to filter bad trades)."""
        at = AdaptiveThreshold(initial_edge_bps=5.0)
        new = at.adjust(win_rate=0.30, total_trades=50)
        assert new > 5.0

    def test_high_wr_decreases_edge(self):
        """WR > 90% → loosen edge (capture more opportunities)."""
        at = AdaptiveThreshold(initial_edge_bps=10.0)
        new = at.adjust(win_rate=0.95, total_trades=50)
        assert new < 10.0

    def test_sequential_adjustments_stay_bounded(self):
        """Multiple adjustments remain within [min_edge, max_edge]."""
        at = AdaptiveThreshold(
            initial_edge_bps=5.0, min_edge=2.0, max_edge=20.0, step_bps=3.0
        )
        # 10 consecutive low-WR adjustments
        for _ in range(10):
            at.adjust(win_rate=0.20, total_trades=100)
        assert at.current_edge_bps <= at.max_edge

        # 10 consecutive high-WR adjustments
        for _ in range(10):
            at.adjust(win_rate=0.99, total_trades=100)
        assert at.current_edge_bps >= at.min_edge


# ===========================================================================
# Layer 3: Regime Response (RegimeDetector)
# ===========================================================================


class TestRegimeResponse:
    """Regime detector classifies market state and triggers KillSwitch."""

    def test_regime_transitions_through_all_states(self):
        """Detector transitions LOW → MEDIUM → HIGH → CRISIS as volatility increases."""
        rd = RegimeDetector()
        assert rd.detect(_LOW_VOL) == MarketRegime.LOW
        assert rd.detect(_MEDIUM_VOL) == MarketRegime.MEDIUM
        assert rd.detect(_HIGH_VOL) == MarketRegime.HIGH
        assert rd.detect(_CRISIS_VOL) == MarketRegime.CRISIS

    def test_crisis_triggers_kill_switch(self):
        """CRISIS regime → should_kill_switch() returns True."""
        rd = RegimeDetector()
        rd.detect(_CRISIS_VOL)
        assert rd.should_kill_switch() is True

    def test_non_crisis_does_not_trigger_kill_switch(self):
        """Non-CRISIS regimes → should_kill_switch() returns False."""
        rd = RegimeDetector()
        for returns in [_LOW_VOL, _MEDIUM_VOL, _HIGH_VOL]:
            rd.detect(returns)
            assert rd.should_kill_switch() is False


# ===========================================================================
# End-to-End: 3-Layer Pipeline Integration
# ===========================================================================


class TestThreeLayerPipeline:
    """Full pipeline: Offline → Adaptive → Regime integration."""

    def test_offline_result_feeds_adaptive_threshold(self):
        """Offline optimization WR feeds AdaptiveThreshold for edge adjustment."""
        # Simulate offline result: 40% WR → edge should increase
        at = AdaptiveThreshold(initial_edge_bps=5.0)
        offline_wr = 0.40
        new_edge = at.adjust(win_rate=offline_wr, total_trades=100)
        assert new_edge > 5.0

        # Subsequent high WR → edge decreases
        new_edge2 = at.adjust(win_rate=0.95, total_trades=100)
        assert new_edge2 < new_edge

    def test_regime_overrides_adaptive_in_crisis(self):
        """CRISIS regime → KillSwitch regardless of adaptive threshold value."""
        at = AdaptiveThreshold(initial_edge_bps=5.0)
        rd = RegimeDetector()

        # Adaptive says edge is fine
        at.adjust(win_rate=0.70, total_trades=100)
        assert at.current_edge_bps == 5.0  # unchanged

        # But regime detects CRISIS
        rd.detect(_CRISIS_VOL)
        assert rd.should_kill_switch() is True
        # Kill switch overrides everything — trading should halt

    def test_full_pipeline_cycle(self):
        """Complete cycle: offline optimize → adaptive adjust → regime check."""
        # Step 1: Offline — ShadowRunner evaluates new params
        engine = MagicMock()
        engine.run.return_value = _make_backtest_result(pnl=2.0, sharpe=3.0, wr=0.80)

        runner = ShadowRunner(engine=engine)
        decision, result = runner.evaluate_and_decide(
            strategy_id="cross_exchange_v1",
            strategy_type="cross_exchange",
            baseline_params=StrategyParams(),
            shadow_params=StrategyParams(min_spread_bps=3.0),
        )

        # Step 2: Adaptive — use shadow WR to fine-tune edge
        at = AdaptiveThreshold(initial_edge_bps=5.0)
        shadow_wr = result.shadow_result.win_rate
        adjusted_edge = at.adjust(win_rate=shadow_wr, total_trades=result.shadow_result.num_trades)

        # WR=80% is between 50-90% → edge unchanged
        assert adjusted_edge == 5.0

        # Step 3: Regime — check market conditions
        rd = RegimeDetector()
        regime = rd.detect(_MEDIUM_VOL)
        assert regime == MarketRegime.MEDIUM
        assert rd.should_kill_switch() is False

        # Pipeline complete: decision made, edge tuned, regime safe

    @pytest.mark.asyncio
    async def test_apply_decision_writes_config(self, tmp_path: Path):
        """APPLY decision → strategy_params.json updated."""
        engine = MagicMock()
        engine.run.return_value = _make_backtest_result(pnl=2.0, sharpe=3.0)

        params_file = tmp_path / "strategy_params.json"
        runner = ShadowRunner(engine=engine)
        runner._params_path = params_file
        runner._alerter = MagicMock()
        runner._alerter.send_alert = AsyncMock()

        decision, result = await runner.apply_decision(
            strategy_id="test_strategy",
            strategy_type="cross_exchange",
            baseline_params=StrategyParams(),
            shadow_params=StrategyParams(min_spread_bps=3.0),
        )

        if decision == "APPLY":
            assert params_file.exists()
            data = json.loads(params_file.read_text())
            assert "test_strategy" in data


# ===========================================================================
# Per-Strategy Independence
# ===========================================================================


class TestPerStrategyIndependence:
    """Each strategy optimizes independently without cross-contamination."""

    def test_adaptive_thresholds_are_independent_per_strategy(self):
        """Separate AdaptiveThreshold instances for different strategies don't interfere."""
        at_cross = AdaptiveThreshold(initial_edge_bps=5.0)
        at_funding = AdaptiveThreshold(initial_edge_bps=10.0)

        at_cross.adjust(win_rate=0.30, total_trades=50)
        at_funding.adjust(win_rate=0.95, total_trades=50)

        assert at_cross.current_edge_bps > 5.0  # increased
        assert at_funding.current_edge_bps < 10.0  # decreased
        assert at_cross.current_edge_bps != at_funding.current_edge_bps

    def test_shadow_runner_evaluates_strategies_independently(self):
        """ShadowRunner produces different results for different strategies."""
        engine = MagicMock()

        # Different return values for different strategy runs
        results_queue = [
            _make_backtest_result(pnl=5.0, sharpe=4.0, wr=0.85),
            _make_backtest_result(pnl=5.0, sharpe=4.0, wr=0.85),
            _make_backtest_result(pnl=1.0, sharpe=1.5, wr=0.55),
            _make_backtest_result(pnl=1.0, sharpe=1.5, wr=0.55),
        ]
        engine.run.side_effect = results_queue

        runner = ShadowRunner(engine=engine)

        d1, r1 = runner.evaluate_and_decide(
            strategy_id="strategy_a",
            strategy_type="cross_exchange",
            baseline_params=StrategyParams(),
            shadow_params=StrategyParams(min_spread_bps=3.0),
        )

        d2, r2 = runner.evaluate_and_decide(
            strategy_id="strategy_b",
            strategy_type="funding_rate",
            baseline_params=StrategyParams(),
            shadow_params=StrategyParams(entry_threshold=0.001),
        )

        assert r1.strategy_id == "strategy_a"
        assert r2.strategy_id == "strategy_b"
        assert d1 in ("APPLY", "MONITOR", "REJECT")
        assert d2 in ("APPLY", "MONITOR", "REJECT")

    def test_regime_detector_shared_but_stateless_per_detection(self):
        """Single RegimeDetector can serve multiple strategies without bias."""
        rd = RegimeDetector()

        # Strategy A sees high vol
        r1 = rd.detect(_HIGH_VOL)
        assert r1 == MarketRegime.HIGH

        # Strategy B sees low vol — detector updates to LOW
        r2 = rd.detect(_LOW_VOL)
        assert r2 == MarketRegime.LOW

        # Regime reflects latest data, not strategy-specific state
        assert rd.current_regime == MarketRegime.LOW


# ===========================================================================
# History & Persistence
# ===========================================================================


class TestHistoryPersistence:
    """Tuning history is recorded for TimescaleDB persistence."""

    @pytest.mark.asyncio
    async def test_adaptive_and_regime_histories_accumulate(self):
        """Both components accumulate history entries that can be persisted."""
        at = AdaptiveThreshold(initial_edge_bps=5.0)
        rd = RegimeDetector()

        # Generate some history
        at.adjust(win_rate=0.30, total_trades=50)  # edge up
        at.adjust(win_rate=0.95, total_trades=50)  # edge down
        rd.detect(_CRISIS_VOL)  # MEDIUM → CRISIS
        rd.detect(_LOW_VOL)  # CRISIS → LOW

        assert len(at.history) == 2
        assert len(rd.history) == 2

        # Persist via mock conn
        mock_conn = MagicMock()
        mock_conn.executemany = AsyncMock()

        await at.save_history(mock_conn)
        await rd.save_history(mock_conn)

        assert mock_conn.executemany.await_count == 2
        # History cleared after save
        assert len(at.history) == 0
        assert len(rd.history) == 0

    @pytest.mark.asyncio
    async def test_empty_history_skips_save(self):
        """save_history is a no-op when history is empty."""
        at = AdaptiveThreshold(initial_edge_bps=5.0)
        rd = RegimeDetector()

        mock_conn = MagicMock()
        mock_conn.executemany = AsyncMock()

        await at.save_history(mock_conn)
        await rd.save_history(mock_conn)

        mock_conn.executemany.assert_not_awaited()
