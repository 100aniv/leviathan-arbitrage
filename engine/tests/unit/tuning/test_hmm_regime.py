"""TDD tests for HMM-based regime detector (US-081).

Behavioral contracts:
  - MarketRegime enum extended with CALM, NORMAL, VOLATILE values
  - Existing LOW, MEDIUM, HIGH, CRISIS values remain (backward compat)
  - HMM_REGIME_MAP: {0: CALM, 1: NORMAL, 2: VOLATILE}
  - THRESHOLD_TO_HMM: {LOW→CALM, MEDIUM→NORMAL, HIGH→VOLATILE}
  - HMMRegimeDetector: default current_regime=NORMAL, is_fitted=False
  - predict when not fitted: returns current_regime without error
  - _load_hmmlearn: returns None gracefully when hmmlearn not installed
  - fit without hmmlearn: raises ImportError
  - Constants: N_STATES=3, COVARIANCE_TYPE="full", N_ITER=100
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from src.tuning.regime_detector import (
    HMM_REGIME_MAP,
    THRESHOLD_TO_HMM,
    HMMRegimeDetector,
    MarketRegime,
    RegimeDetector,
)


# ---------------------------------------------------------------------------
# MarketRegime enum — extended values
# ---------------------------------------------------------------------------


class TestMarketRegimeExtendedValues:
    def test_calm_value_exists_in_enum(self):
        """MarketRegime.CALM exists for HMM low-volatility state."""
        assert hasattr(MarketRegime, "CALM")
        assert MarketRegime.CALM is not None

    def test_normal_value_exists_in_enum(self):
        """MarketRegime.NORMAL exists for HMM mid-volatility state."""
        assert hasattr(MarketRegime, "NORMAL")
        assert MarketRegime.NORMAL is not None

    def test_volatile_value_exists_in_enum(self):
        """MarketRegime.VOLATILE exists for HMM high-volatility state."""
        assert hasattr(MarketRegime, "VOLATILE")
        assert MarketRegime.VOLATILE is not None

    def test_calm_string_value_is_calm(self):
        """MarketRegime.CALM.value is 'CALM'."""
        assert MarketRegime.CALM.value == "CALM"

    def test_normal_string_value_is_normal(self):
        """MarketRegime.NORMAL.value is 'NORMAL'."""
        assert MarketRegime.NORMAL.value == "NORMAL"

    def test_volatile_string_value_is_volatile(self):
        """MarketRegime.VOLATILE.value is 'VOLATILE'."""
        assert MarketRegime.VOLATILE.value == "VOLATILE"


# ---------------------------------------------------------------------------
# MarketRegime enum — backward compatibility
# ---------------------------------------------------------------------------


class TestMarketRegimeBackwardCompat:
    def test_low_still_exists(self):
        """MarketRegime.LOW preserved for threshold-based detector."""
        assert MarketRegime.LOW == MarketRegime("LOW")

    def test_medium_still_exists(self):
        """MarketRegime.MEDIUM preserved for threshold-based detector."""
        assert MarketRegime.MEDIUM == MarketRegime("MEDIUM")

    def test_high_still_exists(self):
        """MarketRegime.HIGH preserved for threshold-based detector."""
        assert MarketRegime.HIGH == MarketRegime("HIGH")

    def test_crisis_still_exists(self):
        """MarketRegime.CRISIS preserved for kill-switch logic."""
        assert MarketRegime.CRISIS == MarketRegime("CRISIS")


# ---------------------------------------------------------------------------
# HMM_REGIME_MAP constant
# ---------------------------------------------------------------------------


class TestHMMRegimeMap:
    def test_state_0_maps_to_calm(self):
        """HMM state 0 corresponds to CALM (low-volatility) regime."""
        assert HMM_REGIME_MAP[0] == MarketRegime.CALM

    def test_state_1_maps_to_normal(self):
        """HMM state 1 corresponds to NORMAL (mid-volatility) regime."""
        assert HMM_REGIME_MAP[1] == MarketRegime.NORMAL

    def test_state_2_maps_to_volatile(self):
        """HMM state 2 corresponds to VOLATILE (high-volatility) regime."""
        assert HMM_REGIME_MAP[2] == MarketRegime.VOLATILE

    def test_map_has_exactly_three_states(self):
        """HMM_REGIME_MAP covers exactly 3 states (0, 1, 2)."""
        assert len(HMM_REGIME_MAP) == 3
        assert set(HMM_REGIME_MAP.keys()) == {0, 1, 2}


# ---------------------------------------------------------------------------
# THRESHOLD_TO_HMM mapping
# ---------------------------------------------------------------------------


class TestThresholdToHMM:
    def test_low_maps_to_calm(self):
        """LOW threshold regime maps to CALM HMM regime."""
        assert THRESHOLD_TO_HMM[MarketRegime.LOW] == MarketRegime.CALM

    def test_medium_maps_to_normal(self):
        """MEDIUM threshold regime maps to NORMAL HMM regime."""
        assert THRESHOLD_TO_HMM[MarketRegime.MEDIUM] == MarketRegime.NORMAL

    def test_high_maps_to_volatile(self):
        """HIGH threshold regime maps to VOLATILE HMM regime."""
        assert THRESHOLD_TO_HMM[MarketRegime.HIGH] == MarketRegime.VOLATILE


# ---------------------------------------------------------------------------
# HMMRegimeDetector — initialization
# ---------------------------------------------------------------------------


class TestHMMRegimeDetectorInit:
    def test_default_current_regime_is_normal(self):
        """HMMRegimeDetector starts with NORMAL as the default regime."""
        detector = HMMRegimeDetector()
        assert detector.current_regime == MarketRegime.NORMAL

    def test_default_is_fitted_is_false(self):
        """HMMRegimeDetector is not fitted before training."""
        detector = HMMRegimeDetector()
        assert detector.is_fitted is False

    def test_n_states_constant_is_three(self):
        """HMMRegimeDetector.N_STATES == 3 for CALM/NORMAL/VOLATILE."""
        assert HMMRegimeDetector.N_STATES == 3

    def test_covariance_type_constant_is_full(self):
        """HMMRegimeDetector.COVARIANCE_TYPE == 'full' for full covariance matrix."""
        assert HMMRegimeDetector.COVARIANCE_TYPE == "full"

    def test_n_iter_constant_is_100(self):
        """HMMRegimeDetector.N_ITER == 100 for EM algorithm iterations."""
        assert HMMRegimeDetector.N_ITER == 100


# ---------------------------------------------------------------------------
# HMMRegimeDetector — predict behavior without training
# ---------------------------------------------------------------------------


class TestHMMRegimeDetectorPredictUnfitted:
    def test_predict_returns_current_regime_when_not_fitted(self):
        """predict returns current_regime without raising when HMM is not fitted."""
        detector = HMMRegimeDetector()
        detector.current_regime = MarketRegime.CALM
        result = detector.predict([0.01, -0.01, 0.02, -0.02])
        assert result == MarketRegime.CALM

    def test_predict_does_not_raise_when_not_fitted(self):
        """predict is safe to call before fit() — no exception raised."""
        detector = HMMRegimeDetector()
        try:
            detector.predict([0.01, -0.01])
        except Exception as exc:
            pytest.fail(f"predict raised unexpectedly: {exc}")

    def test_predict_returns_normal_by_default_when_not_fitted(self):
        """predict returns NORMAL (default regime) when not fitted and regime unchanged."""
        detector = HMMRegimeDetector()
        result = detector.predict([0.01, -0.01, 0.02])
        assert result == MarketRegime.NORMAL


# ---------------------------------------------------------------------------
# HMMRegimeDetector — _load_hmmlearn graceful fallback
# ---------------------------------------------------------------------------


class TestLoadHmmlearnGraceful:
    def test_returns_none_when_hmmlearn_not_available(self):
        """_load_hmmlearn returns None instead of raising ImportError."""
        detector = HMMRegimeDetector()
        with patch.dict(sys.modules, {"hmmlearn": None, "hmmlearn.hmm": None}):
            result = detector._load_hmmlearn()
        assert result is None

    def test_does_not_raise_import_error_when_hmmlearn_missing(self):
        """_load_hmmlearn swallows ImportError gracefully."""
        detector = HMMRegimeDetector()
        with patch.dict(sys.modules, {"hmmlearn": None, "hmmlearn.hmm": None}):
            try:
                detector._load_hmmlearn()
            except ImportError:
                pytest.fail("_load_hmmlearn should not raise ImportError")


# ---------------------------------------------------------------------------
# HMMRegimeDetector — fit raises ImportError without hmmlearn
# ---------------------------------------------------------------------------


class TestHMMRegimeDetectorFitWithoutHmmlearn:
    def test_fit_raises_import_error_when_hmmlearn_not_installed(self):
        """fit() raises ImportError with informative message when hmmlearn missing."""
        detector = HMMRegimeDetector()
        with patch.object(detector, "_load_hmmlearn", return_value=None):
            with pytest.raises(ImportError):
                detector.fit([[0.01, -0.01, 0.02, -0.02]])

    def test_fit_error_message_mentions_hmmlearn(self):
        """fit() ImportError message guides user to install hmmlearn."""
        detector = HMMRegimeDetector()
        with patch.object(detector, "_load_hmmlearn", return_value=None):
            with pytest.raises(ImportError, match="hmmlearn"):
                detector.fit([[0.01, -0.01, 0.02, -0.02]])
