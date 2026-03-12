"""TDD tests for HMMTrainer — US-083.

Tests match the actual HMMTrainer implementation interface.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from src.ml.hmm_trainer import HMMTrainer
from src.ml.feature_pipeline import RegimeFeaturePipeline
from src.tuning.regime_detector import HMMRegimeDetector, MarketRegime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_data(n: int = 200, seed: int = 42) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns (returns, spreads, volumes) arrays of length n."""
    rng = np.random.default_rng(seed)
    returns = rng.normal(0, 0.01, n)
    spreads = rng.uniform(0.0001, 0.005, n)
    volumes = rng.uniform(1_000, 100_000, n)
    return returns, spreads, volumes


@pytest.fixture
def mock_hmm_class():
    """Patch HMMRegimeDetector._load_hmmlearn to avoid real hmmlearn dependency."""
    with patch("src.tuning.regime_detector.HMMRegimeDetector._load_hmmlearn") as m:
        mock_model = MagicMock()
        mock_model.fit = MagicMock()
        mock_model.predict = MagicMock(return_value=np.array([0, 1, 2, 1, 0]))
        mock_model.transmat_ = np.array([
            [0.7, 0.2, 0.1],
            [0.1, 0.7, 0.2],
            [0.2, 0.1, 0.7],
        ])
        mock_cls = MagicMock(return_value=mock_model)
        m.return_value = mock_cls
        yield mock_cls, mock_model


# ---------------------------------------------------------------------------
# 1. Construction + defaults
# ---------------------------------------------------------------------------


class TestHMMTrainerConstruction:
    def test_default_construction_creates_pipeline_and_detector(self):
        """HMMTrainer() auto-creates _pipeline and _detector instances."""
        trainer = HMMTrainer()
        assert trainer._pipeline is not None
        assert trainer._detector is not None

    def test_custom_cache_dir_stored(self, tmp_path):
        """HMMTrainer(cache_dir=tmp_path) stores the cache dir as Path."""
        trainer = HMMTrainer(cache_dir=str(tmp_path))
        assert trainer._cache_dir == tmp_path

    def test_custom_retrain_interval_days_stored(self):
        """HMMTrainer(retrain_interval_days=14) stores the interval via property."""
        trainer = HMMTrainer(retrain_interval_days=14)
        assert trainer.retrain_interval_days == 14

    def test_detector_property_returns_hmm_regime_detector_instance(self):
        """trainer.detector is an HMMRegimeDetector instance."""
        trainer = HMMTrainer()
        assert isinstance(trainer.detector, HMMRegimeDetector)


# ---------------------------------------------------------------------------
# 2. should_retrain
# ---------------------------------------------------------------------------


class TestShouldRetrain:
    def test_returns_true_when_never_trained(self):
        """should_retrain() returns True when last_trained_at is None."""
        trainer = HMMTrainer(retrain_interval_days=7)
        trainer._last_trained_at = None
        assert trainer.should_retrain() is True

    def test_returns_false_when_trained_recently(self):
        """should_retrain() returns False when trained 1 day ago (interval=7)."""
        trainer = HMMTrainer(retrain_interval_days=7)
        trainer._last_trained_at = datetime.now(timezone.utc) - timedelta(days=1)
        assert trainer.should_retrain() is False

    def test_returns_true_when_trained_too_long_ago(self):
        """should_retrain() returns True when trained 8 days ago (interval=7)."""
        trainer = HMMTrainer(retrain_interval_days=7)
        trainer._last_trained_at = datetime.now(timezone.utc) - timedelta(days=8)
        assert trainer.should_retrain() is True


# ---------------------------------------------------------------------------
# 3. train
# ---------------------------------------------------------------------------


class TestTrain:
    def test_normal_training_calls_fit_on_model(self, mock_hmm_class):
        """train(returns, spreads, volumes) calls GaussianHMM.fit."""
        _, mock_model = mock_hmm_class
        trainer = HMMTrainer()
        returns, spreads, volumes = _make_data(n=200)
        trainer.train(returns, spreads, volumes)
        mock_model.fit.assert_called_once()

    def test_insufficient_data_raises_value_error(self):
        """train() with fewer than N_STATES*10 (=30) samples raises ValueError."""
        trainer = HMMTrainer()
        returns, spreads, volumes = _make_data(n=5)
        with pytest.raises(ValueError, match=r"(?i)insufficient|too few|not enough|minimum"):
            trainer.train(returns, spreads, volumes)

    def test_training_sets_last_trained_at(self, mock_hmm_class):
        """After train(), last_trained_at is set to a recent datetime."""
        trainer = HMMTrainer()
        assert trainer.last_trained_at is None
        returns, spreads, volumes = _make_data(n=200)
        trainer.train(returns, spreads, volumes)
        assert trainer.last_trained_at is not None
        age = datetime.now(timezone.utc) - trainer.last_trained_at
        assert age.total_seconds() < 5.0

    def test_training_sets_detector_is_fitted(self, mock_hmm_class):
        """After train(), detector.is_fitted is True."""
        trainer = HMMTrainer()
        returns, spreads, volumes = _make_data(n=200)
        trainer.train(returns, spreads, volumes)
        assert trainer.detector.is_fitted is True


# ---------------------------------------------------------------------------
# 4. save_model / load_model
# ---------------------------------------------------------------------------


class TestSaveLoadModel:
    def test_save_model_creates_pkl_and_json_files(self, tmp_path, mock_hmm_class):
        """save_model() writes hmm_model.pkl and hmm_meta.json."""
        trainer = HMMTrainer(cache_dir=str(tmp_path))
        returns, spreads, volumes = _make_data(n=200)
        trainer.train(returns, spreads, volumes)
        # Replace mock (unpicklable) with a picklable sentinel before saving
        trainer._detector._model = {"__sentinel__": True}
        trainer.save_model(str(tmp_path))

        assert (tmp_path / HMMTrainer.MODEL_FILE).exists(), "hmm_model.pkl must exist"
        assert (tmp_path / HMMTrainer.META_FILE).exists(), "hmm_meta.json must exist"

    def test_load_model_after_save_returns_true(self, tmp_path, mock_hmm_class):
        """load_model() returns True when a valid saved model exists."""
        trainer = HMMTrainer(cache_dir=str(tmp_path))
        returns, spreads, volumes = _make_data(n=200)
        trainer.train(returns, spreads, volumes)
        # Replace mock (unpicklable) with a picklable sentinel before saving
        trainer._detector._model = {"__sentinel__": True}
        trainer.save_model(str(tmp_path))

        trainer2 = HMMTrainer(cache_dir=str(tmp_path))
        result = trainer2.load_model(str(tmp_path))
        assert result is True

    def test_load_model_returns_false_when_no_files(self, tmp_path):
        """load_model() returns False when no saved model files exist."""
        trainer = HMMTrainer(cache_dir=str(tmp_path))
        result = trainer.load_model(str(tmp_path))
        assert result is False

    def test_load_model_returns_false_when_model_too_old(self, tmp_path, mock_hmm_class):
        """load_model() returns False when trained_at in metadata is >retrain_interval_days ago."""
        trainer = HMMTrainer(cache_dir=str(tmp_path), retrain_interval_days=7)
        returns, spreads, volumes = _make_data(n=200)
        trainer.train(returns, spreads, volumes)
        trainer._detector._model = {"__sentinel__": True}
        trainer.save_model(str(tmp_path))

        meta_path = tmp_path / HMMTrainer.META_FILE
        meta = json.loads(meta_path.read_text())
        meta["trained_at"] = (
            datetime.now(timezone.utc) - timedelta(days=30)
        ).isoformat()
        meta_path.write_text(json.dumps(meta))

        trainer2 = HMMTrainer(cache_dir=str(tmp_path), retrain_interval_days=7)
        result = trainer2.load_model(str(tmp_path))
        assert result is False


# ---------------------------------------------------------------------------
# 5. predict latency
# ---------------------------------------------------------------------------


class TestPredictLatency:
    def test_predict_latency_ms_under_2ms(self, mock_hmm_class):
        """predict_latency_ms() returns <2ms with a mock fitted model."""
        _, mock_model = mock_hmm_class
        trainer = HMMTrainer()
        # Wire a fitted mock detector directly
        trainer._detector._model = mock_model
        trainer._detector._fitted = True

        returns, spreads, volumes = _make_data(n=200)
        features = trainer._pipeline.extract(returns, spreads, volumes)

        latency_ms = trainer.predict_latency_ms(features)
        assert latency_ms < 2.0, f"predict latency {latency_ms:.3f}ms exceeds 2ms budget"


# ---------------------------------------------------------------------------
# 6. scheduled_train
# ---------------------------------------------------------------------------


class TestScheduledTrain:
    @pytest.mark.asyncio
    async def test_scheduled_train_calls_train_when_should_retrain(self, mock_hmm_class):
        """scheduled_train() fetches data and calls train when should_retrain is True."""
        trainer = HMMTrainer()
        trainer._last_trained_at = None  # force retrain

        returns, spreads, volumes = _make_data(n=200)
        mock_data = {"returns": returns, "spreads": spreads, "volumes": volumes}

        mock_conn = AsyncMock()
        with patch.object(
            trainer, "fetch_training_data", new=AsyncMock(return_value=mock_data)
        ) as mock_fetch, patch.object(trainer, "train") as mock_train, patch.object(
            trainer, "save_model"
        ):
            result = await trainer.scheduled_train(mock_conn)
            mock_fetch.assert_called_once_with(mock_conn)
            mock_train.assert_called_once_with(
                mock_data["returns"], mock_data["spreads"], mock_data["volumes"]
            )

    @pytest.mark.asyncio
    async def test_scheduled_train_returns_false_when_no_retrain_needed(self):
        """scheduled_train() returns False when should_retrain is False."""
        trainer = HMMTrainer(retrain_interval_days=7)
        trainer._last_trained_at = datetime.now(timezone.utc) - timedelta(days=1)

        mock_conn = AsyncMock()
        result = await trainer.scheduled_train(mock_conn)
        assert result is False


# ---------------------------------------------------------------------------
# 7. fetch_training_data
# ---------------------------------------------------------------------------


class TestFetchTrainingData:
    @pytest.mark.asyncio
    async def test_fetch_returns_returns_spreads_volumes_arrays(self):
        """fetch_training_data() returns dict with returns/spreads/volumes ndarrays."""
        trainer = HMMTrainer()

        mock_rows = [
            {"close_price": 50000.0 + i * 10.0, "bid_ask_spread": 0.001, "volume": 1000.0 + i}
            for i in range(100)
        ]
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=mock_rows)

        result = await trainer.fetch_training_data(mock_conn)

        assert "returns" in result
        assert "spreads" in result
        assert "volumes" in result
        assert isinstance(result["returns"], np.ndarray)
        assert isinstance(result["spreads"], np.ndarray)
        assert isinstance(result["volumes"], np.ndarray)
        assert len(result["returns"]) > 0

    @pytest.mark.asyncio
    async def test_fetch_returns_empty_arrays_on_db_error(self):
        """fetch_training_data() returns empty arrays when DB raises an exception."""
        trainer = HMMTrainer()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(side_effect=RuntimeError("connection refused"))

        result = await trainer.fetch_training_data(mock_conn)

        assert len(result["returns"]) == 0
        assert len(result["spreads"]) == 0
        assert len(result["volumes"]) == 0
