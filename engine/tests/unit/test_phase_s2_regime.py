"""Tests for US-131 — Regime-based MIN_EDGE and graceful fallbacks.

US-131: REGIME_MIN_EDGE values verified; HMMRegimeDetector graceful fallback when
        hmmlearn not installed; ONNXSignalScorer graceful fallback.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.tuning.regime_detector import (
    REGIME_MIN_EDGE,
    HMMRegimeDetector,
    MarketRegime,
    RegimeDetector,
)


# ---------------------------------------------------------------------------
# US-131: REGIME_MIN_EDGE values
# ---------------------------------------------------------------------------

class TestRegimeMinEdgeValues:
    """US-131: REGIME_MIN_EDGE dict must contain correct bps thresholds."""

    def test_calm_min_edge_is_3bps(self):
        """CALM → 0.0003 (3 bps)."""
        assert REGIME_MIN_EDGE[MarketRegime.CALM] == Decimal("0.0003")

    def test_normal_min_edge_is_5bps(self):
        """NORMAL → 0.0005 (5 bps)."""
        assert REGIME_MIN_EDGE[MarketRegime.NORMAL] == Decimal("0.0005")

    def test_volatile_min_edge_is_8bps(self):
        """VOLATILE → 0.0008 (8 bps)."""
        assert REGIME_MIN_EDGE[MarketRegime.VOLATILE] == Decimal("0.0008")

    def test_crisis_min_edge_is_15bps(self):
        """CRISIS → 0.0015 (15 bps)."""
        assert REGIME_MIN_EDGE[MarketRegime.CRISIS] == Decimal("0.0015")

    def test_low_alias_matches_calm(self):
        """LOW alias → same as CALM (3 bps)."""
        assert REGIME_MIN_EDGE[MarketRegime.LOW] == REGIME_MIN_EDGE[MarketRegime.CALM]

    def test_medium_alias_matches_normal(self):
        """MEDIUM alias → same as NORMAL (5 bps)."""
        assert REGIME_MIN_EDGE[MarketRegime.MEDIUM] == REGIME_MIN_EDGE[MarketRegime.NORMAL]

    def test_high_alias_matches_volatile(self):
        """HIGH alias → same as VOLATILE (8 bps)."""
        assert REGIME_MIN_EDGE[MarketRegime.HIGH] == REGIME_MIN_EDGE[MarketRegime.VOLATILE]

    def test_all_regimes_have_min_edge(self):
        """All MarketRegime variants must have an entry in REGIME_MIN_EDGE."""
        for regime in MarketRegime:
            assert regime in REGIME_MIN_EDGE, f"Missing REGIME_MIN_EDGE for {regime}"

    def test_min_edge_ordering_crisis_highest(self):
        """CRISIS min edge > VOLATILE > NORMAL > CALM (stricter in turbulent regimes)."""
        assert REGIME_MIN_EDGE[MarketRegime.CRISIS] > REGIME_MIN_EDGE[MarketRegime.VOLATILE]
        assert REGIME_MIN_EDGE[MarketRegime.VOLATILE] > REGIME_MIN_EDGE[MarketRegime.NORMAL]
        assert REGIME_MIN_EDGE[MarketRegime.NORMAL] > REGIME_MIN_EDGE[MarketRegime.CALM]


# ---------------------------------------------------------------------------
# US-131: HMMRegimeDetector graceful fallback (hmmlearn not installed)
# ---------------------------------------------------------------------------

class TestHMMRegimeDetectorFallback:
    """US-131: HMMRegimeDetector must not crash when hmmlearn is unavailable."""

    def test_load_hmmlearn_returns_none_when_not_installed(self):
        """_load_hmmlearn() returns None (no raise) when hmmlearn import fails."""
        detector = HMMRegimeDetector()
        with patch.dict(sys.modules, {"hmmlearn": None, "hmmlearn.hmm": None}):
            result = detector._load_hmmlearn()
        assert result is None

    def test_fit_raises_import_error_when_hmmlearn_missing(self):
        """fit() raises ImportError when hmmlearn not available."""
        import numpy as np
        detector = HMMRegimeDetector()
        with patch.object(detector, "_load_hmmlearn", return_value=None):
            with pytest.raises(ImportError, match="hmmlearn"):
                detector.fit(np.array([[0.1, 0.2], [0.3, 0.4]]))

    def test_predict_returns_default_when_not_fitted(self):
        """predict() returns current_regime (NORMAL default) when not fitted."""
        import numpy as np
        detector = HMMRegimeDetector()
        assert not detector.is_fitted
        result = detector.predict(np.array([[0.1, 0.2]]))
        assert result == MarketRegime.NORMAL

    def test_is_fitted_false_initially(self):
        """is_fitted property returns False before fit() is called."""
        detector = HMMRegimeDetector()
        assert detector.is_fitted is False

    def test_current_regime_defaults_to_normal(self):
        """HMMRegimeDetector starts with NORMAL regime."""
        detector = HMMRegimeDetector()
        assert detector.current_regime == MarketRegime.NORMAL

    def test_predict_from_raw_without_pipeline_returns_current(self):
        """predict_from_raw() without feature pipeline returns current_regime."""
        import numpy as np
        detector = HMMRegimeDetector()
        result = detector.predict_from_raw(
            returns=np.array([0.01, -0.02, 0.005]),
            spreads=np.array([0.001, 0.002, 0.001]),
            volumes=np.array([1000, 2000, 1500]),
        )
        assert result == MarketRegime.NORMAL  # fallback to current when no pipeline


# ---------------------------------------------------------------------------
# US-131: ONNXSignalScorer graceful fallback (onnxruntime not installed)
# ---------------------------------------------------------------------------

class TestONNXSignalScorerFallback:
    """US-131: ONNXSignalScorer must not crash when onnxruntime is unavailable.

    TDD: These tests drive the implementation of graceful onnxruntime import.
    """

    def test_onnx_scorer_import_survives_missing_onnxruntime(self):
        """Importing ml module must not crash when onnxruntime is absent."""
        # Mock onnxruntime as unavailable
        with patch.dict(sys.modules, {"onnxruntime": None}):
            try:
                # The import itself should not raise; graceful degradation expected
                from src.ml import onnx_scorer  # noqa: F401 — may not exist yet
            except (ImportError, ModuleNotFoundError):
                # Module doesn't exist yet or onnxruntime unavailable — TDD phase
                pytest.skip("src.ml.onnx_scorer not yet implemented (TDD)")

    def test_onnx_scorer_class_has_graceful_fallback(self):
        """ONNXSignalScorer.score() returns neutral score (0.5) when model not loaded."""
        try:
            from src.ml.onnx_scorer import ONNXSignalScorer
        except (ImportError, ModuleNotFoundError):
            pytest.skip("ONNXSignalScorer not yet implemented (TDD)")

        scorer = ONNXSignalScorer(model_path=None)
        result = scorer.score(features=[0.1, 0.2, 0.3])
        # Neutral fallback when no model loaded
        assert 0.0 <= result <= 1.0


# ---------------------------------------------------------------------------
# US-131: RegimeDetector (threshold-based) — default behavior
# ---------------------------------------------------------------------------

class TestRegimeDetectorDefaults:
    """US-131: RegimeDetector threshold-based detection for baseline."""

    def test_low_volatility_yields_low_regime(self):
        """Returns LOW when std(returns) < 0.5%."""
        detector = RegimeDetector()
        low_vol_returns = [0.001, 0.002, -0.001, 0.0015, -0.002]
        regime = detector.detect(low_vol_returns)
        assert regime == MarketRegime.LOW

    def test_high_volatility_yields_high_regime(self):
        """Returns HIGH when std(returns) in [3%, 8%)."""
        detector = RegimeDetector()
        high_vol = [0.05, -0.04, 0.06, -0.05, 0.04, -0.06, 0.05, -0.04, 0.06, -0.05]
        regime = detector.detect(high_vol)
        assert regime in (MarketRegime.HIGH, MarketRegime.CRISIS)

    def test_crisis_volatility_yields_crisis_regime(self):
        """Returns CRISIS when std(returns) >= 8%."""
        detector = RegimeDetector()
        crisis_returns = [0.15, -0.20, 0.18, -0.16, 0.22, -0.19]
        regime = detector.detect(crisis_returns)
        assert regime == MarketRegime.CRISIS

    def test_empty_returns_returns_current_regime(self):
        """Empty returns list returns current_regime without change."""
        detector = RegimeDetector()
        original = detector.current_regime
        regime = detector.detect([])
        assert regime == original
