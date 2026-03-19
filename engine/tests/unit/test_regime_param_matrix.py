"""Tests for US-263: Regime parameter matrix + get_regime_params()."""
from __future__ import annotations

import pytest

from src.tuning.regime_detector import (
    MarketRegime,
    REGIME_PARAM_MATRIX,
    RegimeDetector,
)


class TestRegimeParamMatrixStructure:
    """Verify REGIME_PARAM_MATRIX has correct structure."""

    REQUIRED_PARAMS = {
        "max_position_multiplier",
        "min_edge_bps",
        "cooldown_seconds",
        "volatility_multiplier",
    }

    EXPECTED_REGIMES = {"CALM", "NORMAL", "VOLATILE", "CRISIS", "LOW", "MEDIUM", "HIGH"}

    def test_all_regimes_present(self):
        for regime in self.EXPECTED_REGIMES:
            assert regime in REGIME_PARAM_MATRIX, f"Missing regime: {regime}"

    def test_all_params_per_regime(self):
        for regime, params in REGIME_PARAM_MATRIX.items():
            for p in self.REQUIRED_PARAMS:
                assert p in params, f"Missing param {p} in regime {regime}"

    def test_crisis_blocks_positions(self):
        assert REGIME_PARAM_MATRIX["CRISIS"]["max_position_multiplier"] == 0.0

    def test_calm_allows_larger_positions(self):
        assert REGIME_PARAM_MATRIX["CALM"]["max_position_multiplier"] > 1.0

    def test_volatility_multiplier_increases_with_regime(self):
        calm = REGIME_PARAM_MATRIX["CALM"]["volatility_multiplier"]
        normal = REGIME_PARAM_MATRIX["NORMAL"]["volatility_multiplier"]
        volatile = REGIME_PARAM_MATRIX["VOLATILE"]["volatility_multiplier"]
        crisis = REGIME_PARAM_MATRIX["CRISIS"]["volatility_multiplier"]
        assert calm < normal <= volatile < crisis


class TestGetRegimeParams:
    """Test RegimeDetector.get_regime_params() method."""

    def test_returns_value_for_current_regime(self):
        rd = RegimeDetector()
        rd.current_regime = MarketRegime.CALM
        val = rd.get_regime_params("max_position_multiplier")
        assert val == 1.2

    def test_crisis_min_edge(self):
        rd = RegimeDetector()
        rd.current_regime = MarketRegime.CRISIS
        val = rd.get_regime_params("min_edge_bps")
        assert val == 15.0

    def test_unknown_param_returns_default(self):
        rd = RegimeDetector()
        rd.current_regime = MarketRegime.NORMAL
        val = rd.get_regime_params("nonexistent_param", default=42.0)
        assert val == 42.0

    def test_volatile_cooldown(self):
        rd = RegimeDetector()
        rd.current_regime = MarketRegime.VOLATILE
        val = rd.get_regime_params("cooldown_seconds")
        assert val == 10.0

    def test_alias_regimes_match(self):
        """LOW/MEDIUM/HIGH should match CALM/NORMAL/VOLATILE values."""
        for alias, primary in [("LOW", "CALM"), ("MEDIUM", "NORMAL"), ("HIGH", "VOLATILE")]:
            for param in ["max_position_multiplier", "min_edge_bps", "cooldown_seconds", "volatility_multiplier"]:
                assert REGIME_PARAM_MATRIX[alias][param] == REGIME_PARAM_MATRIX[primary][param], \
                    f"Mismatch: {alias}.{param} != {primary}.{param}"
