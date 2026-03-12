"""US-092: XGBoost training pipeline tests."""
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

xgb = pytest.importorskip("xgboost", reason="xgboost not installed")

from src.ml.xgb_trainer import XGBTrainer


@pytest.fixture
def trainer():
    return XGBTrainer(
        cache_dir=tempfile.mkdtemp(),
        retrain_interval_days=7,
        n_hpo_trials=3,
        label_threshold_bps=5.0,
        forward_window=5,
    )


@pytest.fixture
def sample_data():
    """Synthetic price data with trend for label generation."""
    rng = np.random.default_rng(42)
    n = 500
    # Uptrend with noise
    prices = 50000 + np.cumsum(rng.standard_normal(n) * 10 + 0.5)
    returns = np.diff(prices) / prices[:-1]
    X = np.column_stack([
        returns,
        rng.standard_normal(n - 1),
        rng.standard_normal(n - 1),
    ])
    return prices, X


def test_trainer_creation(trainer):
    assert not trainer.is_trained
    assert trainer.last_trained_at is None
    assert trainer.best_score == 0.0
    assert trainer.best_params == {}


def test_generate_labels(trainer):
    prices = np.array([100.0, 100.1, 100.2, 100.3, 100.4, 100.5, 100.6, 100.7, 100.8, 100.9])
    labels = trainer.generate_labels(prices)
    assert len(labels) == len(prices)
    # Last forward_window entries should be NaN
    assert np.all(np.isnan(labels[-trainer._forward_window:]))
    # Valid entries should be 0 or 1
    valid = labels[~np.isnan(labels)]
    assert np.all((valid == 0) | (valid == 1))


def test_generate_labels_custom_threshold(trainer):
    prices = np.array([100.0, 100.0, 100.0, 100.0, 100.0, 101.0])
    labels = trainer.generate_labels(prices, threshold_bps=50.0, forward_window=1)
    # 100→101 = 100bps > 50bps threshold → label=1
    assert labels[4] == 1.0
    # Last 1 should be NaN
    assert np.isnan(labels[-1])


def test_train_basic(trainer, sample_data):
    prices, X = sample_data
    labels = trainer.generate_labels(prices[1:])
    valid = ~np.isnan(labels)
    X_valid = X[valid]
    y_valid = labels[valid]

    model = trainer.train(X_valid, y_valid)
    assert model is not None
    assert trainer.is_trained
    assert trainer.last_trained_at is not None
    assert trainer._train_samples == len(y_valid)


def test_predict(trainer, sample_data):
    prices, X = sample_data
    labels = trainer.generate_labels(prices[1:])
    valid = ~np.isnan(labels)
    X_valid = X[valid]
    y_valid = labels[valid]

    trainer.train(X_valid, y_valid)
    preds = trainer.predict(X_valid[:10])
    assert len(preds) == 10
    assert np.all((preds >= 0) & (preds <= 1))


def test_predict_not_trained(trainer):
    with pytest.raises(RuntimeError, match="not trained"):
        trainer.predict(np.array([[1.0, 2.0, 3.0]]))


def test_predict_latency(trainer, sample_data):
    prices, X = sample_data
    labels = trainer.generate_labels(prices[1:])
    valid = ~np.isnan(labels)
    trainer.train(X[valid], labels[valid])

    latency = trainer.predict_latency_ms(X[valid][:10])
    assert latency > 0
    assert latency < 100  # should be fast


def test_feature_importance(trainer, sample_data):
    prices, X = sample_data
    labels = trainer.generate_labels(prices[1:])
    valid = ~np.isnan(labels)
    trainer.train(X[valid], labels[valid], feature_names=["ret", "f1", "f2"])

    importance = trainer.feature_importance()
    assert isinstance(importance, dict)
    assert len(importance) > 0
    # Values should sum to ~1.0
    total = sum(importance.values())
    assert abs(total - 1.0) < 0.01


def test_train_with_hpo(trainer, sample_data):
    """HPO with 3 trials — should complete and find best params."""
    prices, X = sample_data
    labels = trainer.generate_labels(prices[1:])
    valid = ~np.isnan(labels)
    X_valid = X[valid]
    y_valid = labels[valid]

    model = trainer.train_with_hpo(X_valid, y_valid, n_trials=3)
    assert model is not None
    assert trainer.is_trained
    assert trainer.best_score > 0
    assert len(trainer.best_params) > 0
    assert "max_depth" in trainer.best_params


def test_save_and_load(trainer, sample_data):
    prices, X = sample_data
    labels = trainer.generate_labels(prices[1:])
    valid = ~np.isnan(labels)
    trainer.train(X[valid], labels[valid])

    # Save
    path = trainer.save_model()
    assert Path(path).exists()
    meta_path = Path(path).parent / "xgb_meta.json"
    assert meta_path.exists()

    with open(meta_path) as f:
        meta = json.load(f)
    assert meta["samples"] == trainer._train_samples
    assert meta["trained_at"] is not None

    # Load into new trainer
    trainer2 = XGBTrainer(cache_dir=str(Path(path).parent))
    assert trainer2.load_model()
    assert trainer2.is_trained

    # Predictions should match
    test_X = X[valid][:5]
    preds1 = trainer.predict(test_X)
    preds2 = trainer2.predict(test_X)
    np.testing.assert_array_almost_equal(preds1, preds2)


def test_load_no_model(trainer):
    assert not trainer.load_model("/tmp/nonexistent_xgb_model_path")


def test_save_not_trained(trainer):
    with pytest.raises(RuntimeError, match="No model"):
        trainer.save_model()


def test_should_retrain(trainer):
    assert trainer.should_retrain()
    # After training, should not need retrain
    prices = 50000 + np.cumsum(np.random.default_rng(42).standard_normal(100))
    X = np.random.default_rng(42).standard_normal((94, 3))
    labels = trainer.generate_labels(prices[1:])
    valid = ~np.isnan(labels)
    trainer.train(X[:sum(valid)], labels[valid])
    assert not trainer.should_retrain()


def test_train_with_feature_names(trainer, sample_data):
    prices, X = sample_data
    labels = trainer.generate_labels(prices[1:])
    valid = ~np.isnan(labels)

    trainer.train(X[valid], labels[valid], feature_names=["feat_a", "feat_b", "feat_c"])
    assert trainer._feature_names == ["feat_a", "feat_b", "feat_c"]


@pytest.mark.asyncio
async def test_fetch_training_data_empty(trainer):
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    result = await trainer.fetch_training_data(conn)
    assert len(result["prices"]) == 0


@pytest.mark.asyncio
async def test_fetch_training_data_error(trainer):
    conn = AsyncMock()
    conn.fetch = AsyncMock(side_effect=Exception("DB down"))
    result = await trainer.fetch_training_data(conn)
    assert len(result["prices"]) == 0


@pytest.mark.asyncio
async def test_scheduled_train_insufficient_data(trainer):
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[
        {"close_price": 100.0, "bid_ask_spread": 0.01, "volume": 1000.0}
    ])
    result = await trainer.scheduled_train(conn)
    assert result is False
