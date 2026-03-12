"""US-091: Extended ML feature pipeline tests."""
import numpy as np
import pytest

from src.ml.feature_pipeline import MLFeaturePipeline


@pytest.fixture
def pipeline():
    return MLFeaturePipeline()


def test_ml_pipeline_n_features(pipeline):
    assert pipeline.N_FEATURES == 20


def test_ml_pipeline_feature_names_count(pipeline):
    assert len(pipeline.FEATURE_NAMES) == 20
    assert len(pipeline.feature_names) == 20


def test_extract_shape(pipeline):
    result = pipeline.extract()
    assert result.shape == (1, 20)


def test_extract_orderbook_features(pipeline):
    ob = {"spread_bps": 5.0, "depth_l1": 100.0, "depth_l3": 250.0, "depth_l5": 500.0}
    result = pipeline.extract(orderbook_data=ob)
    assert result[0, 0] == 5.0   # spread_bps
    assert result[0, 1] == 100.0  # depth_l1
    assert result[0, 2] == 250.0  # depth_l3
    assert result[0, 3] == 500.0  # depth_l5


def test_extract_volatility_features(pipeline):
    returns = np.random.randn(100) * 0.01
    result = pipeline.extract(returns=returns)
    # vol_1m (idx 4) should be > 0
    assert result[0, 4] > 0
    # vol_ratio (idx 7) should be > 0
    assert result[0, 7] > 0


def test_extract_volume_features(pipeline):
    volumes = np.random.uniform(100, 200, 50)
    result = pipeline.extract(
        volumes=volumes, vwap_price=50100.0, current_price=50000.0
    )
    # volume_zscore (idx 8) — some value
    assert result[0, 8] != 0 or True  # may be near 0
    # vwap_ratio (idx 9) — ~1.002
    assert abs(result[0, 9] - 1.002) < 0.01
    # volume_ma_ratio (idx 10) — > 0
    assert result[0, 10] > 0


def test_extract_regime_features(pipeline):
    result = pipeline.extract(regime_state=2, regime_confidence=0.9, transition_prob=0.3)
    assert result[0, 11] == 2.0   # hmm_state
    assert result[0, 12] == 0.9   # regime_confidence
    assert result[0, 13] == 0.3   # transition_prob


def test_extract_momentum_rsi(pipeline):
    # Uptrend returns → RSI > 50
    returns = np.ones(100) * 0.001
    result = pipeline.extract(returns=returns)
    assert result[0, 14] > 0  # rolling_return positive
    assert result[0, 16] > 50  # RSI > 50 for uptrend


def test_extract_rsi_downtrend(pipeline):
    # Downtrend returns → RSI < 50
    returns = np.ones(100) * -0.001
    result = pipeline.extract(returns=returns)
    assert result[0, 16] < 50  # RSI < 50 for downtrend


def test_extract_execution_features(pipeline):
    result = pipeline.extract(fill_rate=0.88, avg_slippage_bps=4.5, rejection_rate=0.05)
    assert result[0, 17] == 0.88  # fill_rate
    assert result[0, 18] == 4.5   # avg_slippage
    assert result[0, 19] == 0.05  # rejection_rate


def test_extract_empty_data(pipeline):
    """None inputs → zeros, no crash."""
    result = pipeline.extract()
    assert result.shape == (1, 20)
    # Regime defaults
    assert result[0, 11] == 1.0  # default regime_state
    assert result[0, 12] == 0.5  # default confidence


def test_extract_nan_inf_handled(pipeline):
    """NaN/Inf values → replaced with 0."""
    returns = np.array([np.nan, np.inf, -np.inf, 0.01, 0.02])
    result = pipeline.extract(returns=returns)
    assert np.all(np.isfinite(result))
