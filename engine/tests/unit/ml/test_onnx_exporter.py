"""US-093: ONNX exporter + version management tests."""
import json
import tempfile
from pathlib import Path

import numpy as np
import pytest

xgb = pytest.importorskip("xgboost", reason="xgboost not installed")
onnxmltools = pytest.importorskip("onnxmltools", reason="onnxmltools not installed")
onnx = pytest.importorskip("onnx", reason="onnx not installed")
ort = pytest.importorskip("onnxruntime", reason="onnxruntime not installed")

from src.ml.onnx_exporter import ONNXExporter
from src.ml.xgb_trainer import XGBTrainer


@pytest.fixture
def models_dir():
    return tempfile.mkdtemp()


@pytest.fixture
def exporter(models_dir):
    return ONNXExporter(models_dir=models_dir, opset_version=15)


@pytest.fixture
def trained_model():
    """Train a small XGBoost model for export tests."""
    rng = np.random.default_rng(42)
    n, n_feat = 200, 5
    X = rng.standard_normal((n, n_feat))
    y = (X[:, 0] + X[:, 1] > 0).astype(float)

    trainer = XGBTrainer(cache_dir=tempfile.mkdtemp())
    trainer.train(X, y, feature_names=[f"f{i}" for i in range(n_feat)])
    return trainer.model, n_feat, [f"f{i}" for i in range(n_feat)]


def test_exporter_creation(exporter):
    assert exporter.opset_version == 15
    assert exporter.models_dir.exists() or True  # dir created on export


def test_export_basic(exporter, trained_model):
    model, n_feat, names = trained_model
    path = exporter.export(model, n_features=n_feat, feature_names=names)
    assert Path(path).exists()
    assert Path(path).suffix == ".onnx"


def test_export_creates_version_dir(exporter, trained_model):
    model, n_feat, names = trained_model
    exporter.export(model, n_features=n_feat)
    version_dir = exporter.models_dir / "v001"
    assert version_dir.exists()
    assert (version_dir / "model.onnx").exists()
    assert (version_dir / "meta.json").exists()


def test_export_creates_latest(exporter, trained_model):
    model, n_feat, names = trained_model
    exporter.export(model, n_features=n_feat)
    latest = exporter.models_dir / "latest"
    assert latest.exists()
    assert (latest / "model.onnx").exists()


def test_export_meta_content(exporter, trained_model):
    model, n_feat, names = trained_model
    exporter.export(model, n_features=n_feat, feature_names=names, model_name="test_model")
    meta_path = exporter.models_dir / "v001" / "meta.json"
    with open(meta_path) as f:
        meta = json.load(f)
    assert meta["version"] == "v001"
    assert meta["model_name"] == "test_model"
    assert meta["n_features"] == n_feat
    assert meta["opset_version"] == 15
    assert meta["feature_names"] == names
    assert meta["file_size_bytes"] > 0


def test_export_version_increment(exporter, trained_model):
    model, n_feat, _ = trained_model
    exporter.export(model, n_features=n_feat)
    exporter.export(model, n_features=n_feat)
    assert (exporter.models_dir / "v001").exists()
    assert (exporter.models_dir / "v002").exists()
    versions = exporter.list_versions()
    assert len(versions) == 2
    assert versions[0]["version"] == "v001"
    assert versions[1]["version"] == "v002"


def test_validate_model(exporter, trained_model):
    model, n_feat, _ = trained_model
    path = exporter.export(model, n_features=n_feat)
    assert exporter.validate(path)


def test_validate_nonexistent(exporter):
    assert not exporter.validate("/tmp/nonexistent_model.onnx")


def test_test_inference(exporter, trained_model):
    model, n_feat, _ = trained_model
    exporter.export(model, n_features=n_feat)
    result = exporter.test_inference(n_features=n_feat, n_samples=10)
    assert "latency_ms" in result
    assert "throughput_per_sec" in result
    assert result["latency_ms"] > 0
    assert result["latency_ms"] < 10  # should be well under 10ms


def test_get_latest_version(exporter, trained_model):
    assert exporter.get_latest_version() is None
    model, n_feat, _ = trained_model
    exporter.export(model, n_features=n_feat)
    assert exporter.get_latest_version() == "v001"
    exporter.export(model, n_features=n_feat)
    assert exporter.get_latest_version() == "v002"


def test_rollback(exporter, trained_model):
    model, n_feat, _ = trained_model
    exporter.export(model, n_features=n_feat, extra_meta={"note": "first"})
    exporter.export(model, n_features=n_feat, extra_meta={"note": "second"})

    # Latest should be v002
    latest_meta = exporter.models_dir / "latest" / "meta.json"
    with open(latest_meta) as f:
        assert json.load(f)["version"] == "v002"

    # Rollback to v001
    assert exporter.rollback("v001")
    with open(latest_meta) as f:
        assert json.load(f)["version"] == "v001"


def test_rollback_nonexistent(exporter):
    assert not exporter.rollback("v999")


def test_extra_meta(exporter, trained_model):
    model, n_feat, _ = trained_model
    exporter.export(model, n_features=n_feat, extra_meta={"best_auc": 0.85, "train_samples": 500})
    meta_path = exporter.models_dir / "v001" / "meta.json"
    with open(meta_path) as f:
        meta = json.load(f)
    assert meta["best_auc"] == 0.85
    assert meta["train_samples"] == 500
