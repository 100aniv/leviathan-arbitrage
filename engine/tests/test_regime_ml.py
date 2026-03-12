"""US-085: Regime Walk-Forward Analysis — TDD tests.

Tests for RegimeWindowResult, RegimeCorrelation, RegimeWalkForwardAnalyzer.

Run: cd engine && python -m pytest tests/test_regime_ml.py -x --tb=short
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src.analysis.walk_forward import (
    RegimeCorrelation,
    RegimeWalkForwardAnalyzer,
    RegimeWindowResult,
)
from src.tuning.regime_detector import MarketRegime, REGIME_MIN_EDGE


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_trades():
    """레짐별 거래 샘플: CALM에서 작은 이익, VOLATILE에서 큰 이익."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    trades = []
    # CALM 구간 (0-30분): 작은 이익, 높은 WR
    for i in range(20):
        trades.append({"ts": base + timedelta(minutes=i), "pnl": 0.5, "edge_bps": 4.0})
    # NORMAL 구간 (30-60분): 중간
    for i in range(20):
        trades.append({
            "ts": base + timedelta(minutes=30 + i),
            "pnl": 1.0 if i % 3 != 0 else -0.3,
            "edge_bps": 6.0,
        })
    # VOLATILE 구간 (60-90분): 큰 이익, 보수적 필터
    for i in range(20):
        trades.append({
            "ts": base + timedelta(minutes=60 + i),
            "pnl": 2.0 if i % 4 != 0 else -1.0,
            "edge_bps": 10.0,
        })
    return trades


@pytest.fixture
def regime_history():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        {"timestamp": base, "regime": "CALM"},
        {"timestamp": base + timedelta(minutes=30), "regime": "NORMAL"},
        {"timestamp": base + timedelta(minutes=60), "regime": "VOLATILE"},
    ]


# ---------------------------------------------------------------------------
# RegimeWindowResult
# ---------------------------------------------------------------------------


class TestRegimeWindowResult:
    def test_basic_creation_and_fields(self):
        """RegimeWindowResult 생성 후 regime 필드가 정상 설정된다."""
        result = RegimeWindowResult(regime="CALM")
        assert result.regime == "CALM"

    def test_default_values(self):
        """기본값: trade_count=0, total_pnl=0, win_rate=0."""
        result = RegimeWindowResult(regime="NORMAL")
        assert result.trade_count == 0
        assert result.total_pnl == 0
        assert result.win_rate == 0


# ---------------------------------------------------------------------------
# RegimeCorrelation
# ---------------------------------------------------------------------------


class TestRegimeCorrelation:
    def test_basic_creation(self):
        """RegimeCorrelation 생성 후 기본 속성이 존재한다."""
        corr = RegimeCorrelation()
        assert corr is not None

    def test_walk_forward_pass_defaults_to_false(self):
        """walk_forward_pass 기본값은 False이다."""
        corr = RegimeCorrelation()
        assert corr.walk_forward_pass is False


# ---------------------------------------------------------------------------
# RegimeWalkForwardAnalyzer
# ---------------------------------------------------------------------------


class TestRegimeWalkForwardAnalyzer:
    def test_creation_with_default_regime_detector(self):
        """기본 생성 시 regime_detector(=_detector)=None이다."""
        analyzer = RegimeWalkForwardAnalyzer()
        assert analyzer._detector is None

    def test_analyze_regime_correlation_empty_trades_returns_empty(self, regime_history):
        """빈 trades → regime_results가 비어 있다."""
        analyzer = RegimeWalkForwardAnalyzer()
        result = analyzer.analyze_regime_correlation([], regime_history)
        assert len(result.regime_results) == 0

    def test_analyze_regime_correlation_returns_three_regimes(
        self, sample_trades, regime_history
    ):
        """정상 데이터 → 3개 레짐 분류 결과를 반환한다."""
        analyzer = RegimeWalkForwardAnalyzer()
        result = analyzer.analyze_regime_correlation(sample_trades, regime_history)
        assert len(result.regime_results) == 3

    def test_each_regime_has_20_trades(self, sample_trades, regime_history):
        """CALM/NORMAL/VOLATILE 각 구간이 정확히 20개 거래를 갖는다."""
        analyzer = RegimeWalkForwardAnalyzer()
        result = analyzer.analyze_regime_correlation(sample_trades, regime_history)
        for window in result.regime_results.values():
            assert window.trade_count == 20

    def test_each_regime_win_rate_positive(self, sample_trades, regime_history):
        """레짐별 win_rate > 0이다."""
        analyzer = RegimeWalkForwardAnalyzer()
        result = analyzer.analyze_regime_correlation(sample_trades, regime_history)
        for window in result.regime_results.values():
            assert window.win_rate > 0

    def test_regime_transition_count_equals_two(self, sample_trades, regime_history):
        """3개 레짐 히스토리 → transition_count == 2."""
        analyzer = RegimeWalkForwardAnalyzer()
        result = analyzer.analyze_regime_correlation(sample_trades, regime_history)
        assert result.regime_transition_count == 2


# ---------------------------------------------------------------------------
# simulate_regime_effect
# ---------------------------------------------------------------------------


class TestSimulateRegimeEffect:
    def test_fixed_vs_adaptive_improvement_computed(
        self, sample_trades, regime_history
    ):
        """fixed vs adaptive PnL 비교 → improvement 값이 계산된다."""
        analyzer = RegimeWalkForwardAnalyzer()
        out = analyzer.simulate_regime_effect(sample_trades, regime_history)
        assert "fixed_pnl" in out
        assert "adaptive_pnl" in out
        assert "improvement_pct" in out
        assert isinstance(out["improvement_pct"], (int, float))

    def test_simulate_regime_effect_empty_trades_returns_zeros(self, regime_history):
        """빈 trades → fixed_pnl=0, adaptive_pnl=0, improvement_pct=0."""
        analyzer = RegimeWalkForwardAnalyzer()
        out = analyzer.simulate_regime_effect([], regime_history)
        assert out["fixed_pnl"] == 0
        assert out["adaptive_pnl"] == 0
        assert out["improvement_pct"] == 0


# ---------------------------------------------------------------------------
# validate_walk_forward
# ---------------------------------------------------------------------------


class TestValidateWalkForward:
    def test_good_data_passes(self, sample_trades, regime_history):
        """win_rate>50% + improvement>=0 → walk_forward_pass=True."""
        analyzer = RegimeWalkForwardAnalyzer()
        corr = analyzer.analyze_regime_correlation(sample_trades, regime_history)
        assert analyzer.validate_walk_forward(corr) is True

    def test_low_win_rate_fails(self, regime_history):
        """CALM 구간 전부 손실 → validate_walk_forward=False."""
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        bad_trades = (
            # CALM: 모두 손실 → win_rate=0
            [{"ts": base + timedelta(minutes=i), "pnl": -1.0, "edge_bps": 4.0}
             for i in range(20)]
            # NORMAL: 모두 이익
            + [{"ts": base + timedelta(minutes=30 + i), "pnl": 1.0, "edge_bps": 6.0}
               for i in range(20)]
            # VOLATILE: 모두 이익
            + [{"ts": base + timedelta(minutes=60 + i), "pnl": 2.0, "edge_bps": 10.0}
               for i in range(20)]
        )
        analyzer = RegimeWalkForwardAnalyzer()
        corr = analyzer.analyze_regime_correlation(bad_trades, regime_history)
        assert analyzer.validate_walk_forward(corr) is False

    def test_empty_regime_results_fails(self):
        """빈 RegimeCorrelation → False."""
        analyzer = RegimeWalkForwardAnalyzer()
        assert analyzer.validate_walk_forward(RegimeCorrelation()) is False


# ---------------------------------------------------------------------------
# correlation_score (RegimeCorrelation 필드)
# ---------------------------------------------------------------------------


class TestCorrelationScore:
    def test_score_in_valid_range(self, sample_trades, regime_history):
        """-1 <= correlation_score <= 1 범위 내에 있다."""
        analyzer = RegimeWalkForwardAnalyzer()
        result = analyzer.analyze_regime_correlation(sample_trades, regime_history)
        assert -1.0 <= result.correlation_score <= 1.0


# ---------------------------------------------------------------------------
# REGIME_MIN_EDGE 단조 증가
# ---------------------------------------------------------------------------


class TestRegimeMinEdgeMonotone:
    def test_calm_lt_normal_lt_volatile_lt_crisis(self):
        """CALM < NORMAL < VOLATILE < CRISIS bps 단조 증가 확인."""
        calm = float(REGIME_MIN_EDGE[MarketRegime.CALM])
        normal = float(REGIME_MIN_EDGE[MarketRegime.NORMAL])
        volatile = float(REGIME_MIN_EDGE[MarketRegime.VOLATILE])
        crisis = float(REGIME_MIN_EDGE[MarketRegime.CRISIS])

        assert calm < normal, f"CALM({calm}) must be < NORMAL({normal})"
        assert normal < volatile, f"NORMAL({normal}) must be < VOLATILE({volatile})"
        assert volatile < crisis, f"VOLATILE({volatile}) must be < CRISIS({crisis})"
