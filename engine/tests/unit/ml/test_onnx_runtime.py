"""US-094: ONNX Runtime inference integration tests."""
import tempfile
from pathlib import Path

import numpy as np
import pytest

xgb = pytest.importorskip("xgboost", reason="xgboost not installed")
onnxmltools = pytest.importorskip("onnxmltools", reason="onnxmltools not installed")
ort = pytest.importorskip("onnxruntime", reason="onnxruntime not installed")

from src.ml.onnx_exporter import ONNXExporter
from src.ml.onnx_runtime import ONNXSignalScorer
from src.ml.xgb_trainer import XGBTrainer


@pytest.fixture
def model_dir():
    """Create a temp dir with a trained+exported ONNX model."""
    tmpdir = tempfile.mkdtemp()

    rng = np.random.default_rng(42)
    n, n_feat = 300, 5
    X = rng.standard_normal((n, n_feat))
    y = (X[:, 0] + X[:, 1] > 0).astype(float)

    trainer = XGBTrainer(cache_dir=tmpdir)
    trainer.train(X, y, feature_names=[f"f{i}" for i in range(n_feat)])

    exporter = ONNXExporter(models_dir=tmpdir)
    exporter.export(trainer.model, n_features=n_feat)

    return tmpdir, n_feat


@pytest.fixture
def scorer(model_dir):
    tmpdir, n_feat = model_dir
    ONNXSignalScorer.clear_cache()
    return ONNXSignalScorer(
        models_dir=tmpdir,
        n_features=n_feat,
        score_threshold=0.5,
    )


def test_scorer_creation(scorer):
    assert scorer.enabled
    assert scorer.call_count == 0
    assert scorer.latency_ema_ms == 0.0


def test_scorer_disabled():
    scorer = ONNXSignalScorer(enabled=False)
    assert not scorer.enabled
    score = scorer.predict_signal(np.zeros(5))
    assert score == 0.5  # neutral fallback


def test_scorer_no_model():
    ONNXSignalScorer.clear_cache()
    scorer = ONNXSignalScorer(model_path="/tmp/nonexistent.onnx")
    assert not scorer.enabled
    assert scorer.predict_signal(np.zeros(5)) == 0.5


def test_predict_signal(scorer, model_dir):
    _, n_feat = model_dir
    features = np.random.default_rng(42).standard_normal(n_feat)
    score = scorer.predict_signal(features)
    assert 0.0 <= score <= 1.0
    assert scorer.call_count == 1


def test_predict_signal_2d(scorer, model_dir):
    _, n_feat = model_dir
    features = np.random.default_rng(42).standard_normal((1, n_feat))
    score = scorer.predict_signal(features)
    assert 0.0 <= score <= 1.0


def test_predict_latency_under_1ms(scorer, model_dir):
    """Critical: predict_signal must be <1ms."""
    _, n_feat = model_dir
    features = np.random.default_rng(42).standard_normal(n_feat).astype(np.float32)

    # Warmup
    scorer.predict_signal(features)

    # Measure
    import time
    latencies = []
    for _ in range(100):
        start = time.perf_counter()
        scorer.predict_signal(features)
        latencies.append((time.perf_counter() - start) * 1000)

    median_ms = sorted(latencies)[50]
    assert median_ms < 1.0, f"Median latency {median_ms:.3f}ms exceeds 1ms"


def test_predict_batch(scorer, model_dir):
    _, n_feat = model_dir
    features = np.random.default_rng(42).standard_normal((10, n_feat))
    scores = scorer.predict_batch(features)
    assert len(scores) == 10
    assert np.all((scores >= 0) & (scores <= 1))


def test_predict_batch_disabled():
    scorer = ONNXSignalScorer(enabled=False)
    scores = scorer.predict_batch(np.zeros((5, 3)))
    assert len(scores) == 5
    assert np.all(scores == 0.5)


def test_should_trade(scorer, model_dir):
    _, n_feat = model_dir
    features = np.random.default_rng(42).standard_normal(n_feat)
    result = scorer.should_trade(features)
    assert isinstance(result, bool)


def test_stats(scorer):
    stats = scorer.stats()
    assert "enabled" in stats
    assert "call_count" in stats
    assert "latency_ema_ms" in stats
    assert stats["enabled"] is True
    assert stats["call_count"] == 0


def test_session_cache(model_dir):
    """Session cache reuses same session for same model path."""
    tmpdir, n_feat = model_dir
    ONNXSignalScorer.clear_cache()

    scorer1 = ONNXSignalScorer(models_dir=tmpdir, n_features=n_feat)
    scorer2 = ONNXSignalScorer(models_dir=tmpdir, n_features=n_feat)
    assert scorer1._session is scorer2._session


def test_reload_model(scorer, model_dir):
    tmpdir, n_feat = model_dir
    features = np.random.default_rng(42).standard_normal(n_feat)

    score_before = scorer.predict_signal(features)
    assert scorer.reload_model()
    score_after = scorer.predict_signal(features)
    # Same model → same scores
    assert abs(score_before - score_after) < 0.01


def test_latency_ema_tracking(scorer, model_dir):
    _, n_feat = model_dir
    features = np.random.default_rng(42).standard_normal(n_feat)
    for _ in range(10):
        scorer.predict_signal(features)
    assert scorer.latency_ema_ms > 0
    assert scorer.call_count == 10


def test_clear_cache():
    ONNXSignalScorer.clear_cache()
    assert len(ONNXSignalScorer._session_cache) == 0
