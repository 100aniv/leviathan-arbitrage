"""Tests for RegimeFeaturePipeline — US-082."""
from __future__ import annotations

import numpy as np
import pytest

from src.ml.feature_pipeline import RegimeFeaturePipeline

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SHORT = 20
LONG = 100


def _make_pipeline(**kwargs) -> RegimeFeaturePipeline:
    return RegimeFeaturePipeline(**kwargs)


def _make_data(n: int = 200, seed: int = 42):
    """Returns (returns, spreads, volumes) arrays of length n."""
    rng = np.random.default_rng(seed)
    returns = rng.normal(0, 0.01, n)
    spreads = rng.uniform(0.0001, 0.005, n)
    volumes = rng.uniform(1_000, 100_000, n)
    return returns, spreads, volumes


# ---------------------------------------------------------------------------
# Feature extraction — shape
# ---------------------------------------------------------------------------


class TestExtractShape:
    def test_returns_shape_1_by_N_FEATURES(self):
        """extract() returns shape (1, N_FEATURES)."""
        pipe = _make_pipeline()
        returns, spreads, volumes = _make_data()
        out = pipe.extract(returns, spreads, volumes)
        assert out.shape == (1, RegimeFeaturePipeline.N_FEATURES)

    def test_N_FEATURES_is_10(self):
        """N_FEATURES class constant equals 10."""
        assert RegimeFeaturePipeline.N_FEATURES == 10


# ---------------------------------------------------------------------------
# Feature values
# ---------------------------------------------------------------------------


class TestFeatureValues:
    def setup_method(self):
        self.pipe = _make_pipeline()
        self.returns, self.spreads, self.volumes = _make_data()
        self.out = self.pipe.extract(self.returns, self.spreads, self.volumes)
        self.names = self.pipe.feature_names  # instance property

    def _get(self, name: str) -> float:
        idx = self.names.index(name)
        return float(self.out[0, idx])

    def test_realized_vol_matches_np_std_short_window(self):
        """realized_vol == np.std(returns[-20:])."""
        expected = float(np.std(self.returns[-SHORT:]))
        assert pytest.approx(self._get("realized_vol"), rel=1e-6) == expected

    def test_historical_vol_matches_np_std_long_window(self):
        """historical_vol == np.std(returns[-100:])."""
        expected = float(np.std(self.returns[-LONG:]))
        assert pytest.approx(self._get("historical_vol"), rel=1e-6) == expected

    def test_vol_ratio_is_realized_over_historical(self):
        """vol_ratio == realized_vol / historical_vol."""
        r = float(np.std(self.returns[-SHORT:]))
        h = float(np.std(self.returns[-LONG:]))
        expected = r / h if h > 1e-12 else 1.0
        assert pytest.approx(self._get("vol_ratio"), rel=1e-6) == expected

    def test_spread_mean_matches_np_mean_short_window(self):
        """bid_ask_spread_mean == np.mean(spreads[-20:])."""
        expected = float(np.mean(self.spreads[-SHORT:]))
        assert pytest.approx(self._get("bid_ask_spread_mean"), rel=1e-6) == expected

    def test_spread_std_matches_np_std_short_window(self):
        """spread_std == np.std(spreads[-20:])."""
        expected = float(np.std(self.spreads[-SHORT:]))
        assert pytest.approx(self._get("spread_std"), rel=1e-6) == expected

    def test_volume_zscore_formula(self):
        """volume_zscore == (current - mean(long)) / std(long)."""
        mu = float(np.mean(self.volumes[-LONG:]))
        sigma = float(np.std(self.volumes[-LONG:]))
        current = float(self.volumes[-1])
        expected = (current - mu) / sigma if sigma > 1e-12 else 0.0
        assert pytest.approx(self._get("volume_zscore"), rel=1e-5) == expected

    def test_volume_ratio_is_current_over_short_ma(self):
        """volume_ratio == current_volume / mean(volumes[-short:])."""
        ma = float(np.mean(self.volumes[-SHORT:]))
        current = float(self.volumes[-1])
        expected = current / ma if ma > 1e-12 else 1.0
        assert pytest.approx(self._get("volume_ratio"), rel=1e-6) == expected

    def test_rolling_return_matches_np_sum_short_window(self):
        """rolling_return == np.sum(returns[-20:])."""
        expected = float(np.sum(self.returns[-SHORT:]))
        assert pytest.approx(self._get("rolling_return"), rel=1e-6) == expected

    def test_order_imbalance_with_bid_ask_arrays(self):
        """order_imbalance == (sum_bid - sum_ask) / (sum_bid + sum_ask)."""
        bid_volumes = np.full(SHORT, 1500.0)
        ask_volumes = np.full(SHORT, 500.0)
        out = self.pipe.extract(
            self.returns, self.spreads, self.volumes,
            bid_volumes=bid_volumes, ask_volumes=ask_volumes,
        )
        idx = self.pipe.feature_names.index("order_imbalance")
        bid_sum = float(np.sum(bid_volumes[-SHORT:]))
        ask_sum = float(np.sum(ask_volumes[-SHORT:]))
        expected = (bid_sum - ask_sum) / (bid_sum + ask_sum)
        assert pytest.approx(float(out[0, idx]), rel=1e-6) == expected


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def setup_method(self):
        self.pipe = _make_pipeline()

    def test_empty_arrays_return_all_zeros(self):
        """Empty arrays → all features are 0."""
        out = self.pipe.extract(np.array([]), np.array([]), np.array([]))
        assert out.shape == (1, RegimeFeaturePipeline.N_FEATURES)
        assert np.all(out == 0)

    def test_single_element_vol_is_zero(self):
        """Single-element arrays → realized_vol == 0 (std of one value)."""
        out = self.pipe.extract(np.array([0.01]), np.array([0.001]), np.array([1000.0]))
        idx = self.pipe.feature_names.index("realized_vol")
        assert float(out[0, idx]) == pytest.approx(0.0, abs=1e-9)

    def test_nan_input_replaced_with_zero(self):
        """NaN values in input → fill_missing replaces with 0, no NaN in output."""
        returns = np.array([np.nan, 0.01, np.nan, -0.01] * 10)
        spreads = np.array([np.nan] * 40)
        volumes = np.array([np.nan] * 40)
        out = self.pipe.extract(returns, spreads, volumes)
        assert not np.any(np.isnan(out)), "Output must not contain NaN"

    def test_inf_input_replaced_with_zero(self):
        """Inf values in input → fill_missing replaces with 0, no Inf in output."""
        returns = np.array([np.inf, 0.01, -np.inf, -0.01] * 10)
        spreads = np.ones(40) * np.inf
        volumes = np.ones(40) * np.inf
        out = self.pipe.extract(returns, spreads, volumes)
        assert not np.any(np.isinf(out)), "Output must not contain Inf"


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


class TestNormalize:
    def setup_method(self):
        self.pipe = _make_pipeline()

    def _build_batch(self, n: int = 100) -> np.ndarray:
        rng = np.random.default_rng(0)
        all_returns = [rng.normal(0, 0.01, 200) for _ in range(n)]
        all_spreads = [rng.uniform(0.0001, 0.005, 200) for _ in range(n)]
        all_volumes = [rng.uniform(1_000, 100_000, 200) for _ in range(n)]
        return self.pipe.extract_batch(all_returns, all_spreads, all_volumes)

    def test_normalize_100_samples_mean_near_zero(self):
        """After normalization of 100 samples, column mean ≈ 0 (atol=0.01)."""
        features = self._build_batch(100)
        normalized = self.pipe.normalize(features)
        assert normalized.shape == features.shape
        assert np.allclose(normalized.mean(axis=0), 0.0, atol=0.01)

    def test_normalize_100_samples_std_near_one(self):
        """After normalization, columns with variance > 0 have std ≈ 1 (atol=0.01).

        Zero-variance columns (e.g. order_imbalance when no bid/ask data) are
        excluded — normalizing a constant column to std=1 is not possible.
        """
        features = self._build_batch(100)
        nonzero_cols = features.std(axis=0) > 1e-12
        normalized = self.pipe.normalize(features)
        assert np.allclose(normalized[:, nonzero_cols].std(axis=0), 1.0, atol=0.01)

    def test_normalize_single_sample_returns_unchanged(self):
        """Normalizing a single sample returns it as-is (no NaN from zero std)."""
        returns, spreads, volumes = _make_data()
        features = self.pipe.extract(returns, spreads, volumes)
        result = self.pipe.normalize(features)
        assert result.shape == (1, RegimeFeaturePipeline.N_FEATURES)
        assert not np.any(np.isnan(result))


# ---------------------------------------------------------------------------
# Class / instance attributes
# ---------------------------------------------------------------------------


class TestFeatureNames:
    def test_FEATURE_NAMES_class_attr_length_equals_N_FEATURES(self):
        """len(FEATURE_NAMES) == N_FEATURES == 10."""
        assert len(RegimeFeaturePipeline.FEATURE_NAMES) == RegimeFeaturePipeline.N_FEATURES

    def test_feature_names_property_length_equals_N_FEATURES(self):
        """Instance feature_names property has length N_FEATURES."""
        pipe = _make_pipeline()
        assert len(pipe.feature_names) == RegimeFeaturePipeline.N_FEATURES

    def test_feature_names_contains_core_names(self):
        """feature_names includes all expected feature name strings."""
        pipe = _make_pipeline()
        expected = {
            "realized_vol",
            "historical_vol",
            "vol_ratio",
            "bid_ask_spread_mean",
            "spread_std",
            "volume_zscore",
            "volume_ratio",
            "rolling_return",
            "order_imbalance",
        }
        names_set = set(pipe.feature_names)
        missing = expected - names_set
        assert not missing, f"Missing feature names: {missing}"


# ---------------------------------------------------------------------------
# Batch extraction
# ---------------------------------------------------------------------------


class TestExtractBatch:
    def test_extract_batch_5_samples_shape(self):
        """extract_batch with 5 time series → shape (5, N_FEATURES)."""
        pipe = _make_pipeline()
        all_returns = [_make_data(seed=i)[0] for i in range(5)]
        all_spreads = [_make_data(seed=i)[1] for i in range(5)]
        all_volumes = [_make_data(seed=i)[2] for i in range(5)]
        out = pipe.extract_batch(all_returns, all_spreads, all_volumes)
        assert out.shape == (5, RegimeFeaturePipeline.N_FEATURES)


# ---------------------------------------------------------------------------
# Custom window integration
# ---------------------------------------------------------------------------


class TestCustomWindows:
    def test_custom_short_long_windows_extract_succeeds(self):
        """Pipeline with short=5, long=50 extracts without error or NaN."""
        pipe = RegimeFeaturePipeline(short_window=5, long_window=50)
        returns, spreads, volumes = _make_data(n=100)
        out = pipe.extract(returns, spreads, volumes)
        assert out.shape == (1, RegimeFeaturePipeline.N_FEATURES)
        assert not np.any(np.isnan(out))
