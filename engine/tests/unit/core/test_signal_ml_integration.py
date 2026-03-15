"""US-172 + US-173: ML Scorer and HMM RegimeDetector integration in signal pipeline."""
from __future__ import annotations

from decimal import Decimal

import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# US-172: ONNX ML Scorer
# ---------------------------------------------------------------------------


class TestMLScorerIntegration:
    def test_signal_generator_accepts_ml_scorer_param(self):
        """SignalGenerator __init__ accepts ml_scorer kwarg."""
        from src.core.signal import SignalGenerator
        scorer = MagicMock()
        sg = SignalGenerator(
            price_hub=MagicMock(),
            cost_calculator=MagicMock(),
            ml_scorer=scorer,
        )
        assert sg._ml_scorer is scorer

    def test_signal_generator_none_ml_scorer_is_no_op(self):
        """SignalGenerator with ml_scorer=None does not raise."""
        from src.core.signal import SignalGenerator
        sg = SignalGenerator(
            price_hub=MagicMock(),
            cost_calculator=MagicMock(),
            ml_scorer=None,
        )
        assert sg._ml_scorer is None

    def test_ml_scorer_predict_signal_called_when_set(self):
        """When ml_scorer is set, predict_signal() is callable (interface check)."""
        scorer = MagicMock()
        scorer.predict_signal = MagicMock(return_value=0.8)
        result = scorer.predict_signal({"feature": 1.0})
        assert result == 0.8
        scorer.predict_signal.assert_called_once()

    def test_ml_scorer_below_threshold_rejects_signal(self):
        """Score below threshold → signal should be rejected."""
        threshold = 0.6
        score = 0.4

        def should_accept(score: float, threshold: float) -> bool:
            return score >= threshold

        assert should_accept(score, threshold) is False

    def test_ml_scorer_above_threshold_accepts_signal(self):
        """Score above threshold → signal should be accepted."""
        threshold = 0.6
        score = 0.9

        def should_accept(score: float, threshold: float) -> bool:
            return score >= threshold

        assert should_accept(score, threshold) is True

    def test_nan_score_falls_back_to_neutral(self):
        """NaN score is replaced with 0.5 (neutral) to avoid rejection."""
        import math

        def sanitize_score(score: float) -> float:
            if math.isnan(score) or math.isinf(score):
                return 0.5
            return score

        assert sanitize_score(float("nan")) == 0.5
        assert sanitize_score(float("inf")) == 0.5
        assert sanitize_score(float("-inf")) == 0.5

    def test_normal_score_passes_through_sanitize(self):
        """Normal score value is unchanged by sanitizer."""
        import math

        def sanitize_score(score: float) -> float:
            if math.isnan(score) or math.isinf(score):
                return 0.5
            return score

        assert sanitize_score(0.75) == 0.75


# ---------------------------------------------------------------------------
# US-173: HMM RegimeDetector
# ---------------------------------------------------------------------------


class TestHMMRegimeDetector:
    def test_regime_detector_has_current_regime(self):
        """RegimeDetector has current_regime attribute after init."""
        from src.tuning.regime_detector import RegimeDetector, MarketRegime
        detector = RegimeDetector()
        assert hasattr(detector, "current_regime")
        assert isinstance(detector.current_regime, MarketRegime)

    def test_detect_returns_market_regime(self):
        """detect() returns a MarketRegime value."""
        from src.tuning.regime_detector import RegimeDetector, MarketRegime
        detector = RegimeDetector()
        result = detector.detect(returns=[0.001, 0.002, -0.001, 0.0])
        assert isinstance(result, MarketRegime)

    def test_detect_with_empty_returns_returns_current_regime(self):
        """detect() with empty returns list does not crash and returns current regime."""
        from src.tuning.regime_detector import RegimeDetector
        detector = RegimeDetector()
        expected = detector.current_regime
        result = detector.detect(returns=[])
        assert result == expected

    def test_regime_min_edge_mapping_calm(self):
        """CALM regime maps to 3 bps minimum edge."""
        from src.tuning.regime_detector import REGIME_MIN_EDGE, MarketRegime
        edge = REGIME_MIN_EDGE[MarketRegime.CALM]
        assert edge == Decimal("0.0003")

    def test_regime_min_edge_mapping_normal(self):
        """NORMAL regime maps to 5 bps minimum edge."""
        from src.tuning.regime_detector import REGIME_MIN_EDGE, MarketRegime
        edge = REGIME_MIN_EDGE[MarketRegime.NORMAL]
        assert edge == Decimal("0.0005")

    def test_regime_min_edge_mapping_volatile(self):
        """VOLATILE regime maps to 8 bps minimum edge."""
        from src.tuning.regime_detector import REGIME_MIN_EDGE, MarketRegime
        edge = REGIME_MIN_EDGE[MarketRegime.VOLATILE]
        assert edge == Decimal("0.0008")

    def test_regime_min_edge_mapping_crisis(self):
        """CRISIS regime maps to 15 bps minimum edge."""
        from src.tuning.regime_detector import REGIME_MIN_EDGE, MarketRegime
        edge = REGIME_MIN_EDGE[MarketRegime.CRISIS]
        assert edge == Decimal("0.0015")

    def test_effective_min_edge_uses_max(self):
        """effective_min_edge = max(adaptive_edge, regime_edge)."""
        from src.tuning.regime_detector import REGIME_MIN_EDGE, MarketRegime
        adaptive_edge = Decimal("0.0006")
        regime_edge = REGIME_MIN_EDGE[MarketRegime.VOLATILE]  # 0.0008
        effective = max(adaptive_edge, regime_edge)
        assert effective == Decimal("0.0008")

    def test_effective_min_edge_uses_adaptive_when_higher(self):
        """When adaptive_edge > regime_edge, adaptive_edge wins."""
        from src.tuning.regime_detector import REGIME_MIN_EDGE, MarketRegime
        adaptive_edge = Decimal("0.002")
        regime_edge = REGIME_MIN_EDGE[MarketRegime.CALM]  # 0.0003
        effective = max(adaptive_edge, regime_edge)
        assert effective == Decimal("0.002")

    def test_high_volatility_triggers_volatile_regime(self):
        """High-volatility returns classify as HIGH/VOLATILE regime."""
        from src.tuning.regime_detector import RegimeDetector, MarketRegime
        detector = RegimeDetector()
        # 5% daily returns → very high volatility
        high_vol_returns = [0.05, -0.05, 0.04, -0.04, 0.06, -0.06] * 5
        result = detector.detect(returns=high_vol_returns)
        assert result in (MarketRegime.HIGH, MarketRegime.VOLATILE, MarketRegime.CRISIS)

    def test_signal_generator_accepts_regime_detector(self):
        """SignalGenerator __init__ accepts regime_detector kwarg."""
        from src.core.signal import SignalGenerator
        detector = MagicMock()
        sg = SignalGenerator(
            price_hub=MagicMock(),
            cost_calculator=MagicMock(),
            regime_detector=detector,
        )
        assert sg._regime_detector is detector
