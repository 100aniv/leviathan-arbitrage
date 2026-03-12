"""US-091: Feature store + drift detection tests."""
import numpy as np
import pytest

from src.ml.feature_store import DriftReport, FeatureStore


def test_store_creation():
    store = FeatureStore(max_size=500, drift_threshold=3.0, baseline_window=100)
    assert store.size == 0
    assert not store.has_baseline


def test_add_features():
    store = FeatureStore()
    store.add(np.array([1.0, 2.0, 3.0]))
    assert store.size == 1
    store.add(np.array([4.0, 5.0, 6.0]))
    assert store.size == 2


def test_max_size_eviction():
    store = FeatureStore(max_size=5)
    for i in range(10):
        store.add(np.array([float(i)]))
    assert store.size == 5


def test_compute_baseline():
    store = FeatureStore(baseline_window=10)
    rng = np.random.default_rng(42)
    for _ in range(20):
        store.add(rng.standard_normal(5))
    store.compute_baseline()
    assert store.has_baseline


def test_compute_baseline_insufficient_data():
    store = FeatureStore(baseline_window=100)
    for _ in range(10):
        store.add(np.array([1.0, 2.0]))
    store.compute_baseline()
    assert not store.has_baseline


def test_detect_drift_no_drift():
    """Same distribution → no drift."""
    store = FeatureStore(baseline_window=100, drift_threshold=2.0)
    rng = np.random.default_rng(42)
    for _ in range(200):
        store.add(rng.standard_normal(3))
    store.compute_baseline()
    reports = store.detect_drift(recent_window=50)
    assert len(reports) == 3
    drifted = [r for r in reports if r.is_drifted]
    assert len(drifted) == 0


def test_detect_drift_with_drift():
    """Mean shift 5σ → drift detected."""
    store = FeatureStore(baseline_window=100, drift_threshold=2.0)
    rng = np.random.default_rng(42)
    # Baseline: N(0,1)
    for _ in range(100):
        store.add(rng.standard_normal(3))
    store.compute_baseline()
    # Drifted: N(5,1) — 5σ shift
    for _ in range(100):
        store.add(rng.standard_normal(3) + 5.0)
    reports = store.detect_drift(recent_window=50)
    drifted = [r for r in reports if r.is_drifted]
    assert len(drifted) > 0


def test_detect_drift_no_baseline():
    store = FeatureStore()
    reports = store.detect_drift()
    assert reports == []


def test_get_recent():
    store = FeatureStore()
    for i in range(5):
        store.add(np.array([float(i), float(i * 2)]))
    recent = store.get_recent(3)
    assert recent.shape == (3, 2)
    assert recent[-1, 0] == 4.0


def test_get_recent_empty():
    store = FeatureStore()
    recent = store.get_recent()
    assert len(recent) == 0


def test_set_feature_names():
    store = FeatureStore(baseline_window=5, drift_threshold=2.0)
    store.set_feature_names(["feat_a", "feat_b"])
    for _ in range(10):
        store.add(np.array([1.0, 2.0]))
    store.compute_baseline()
    reports = store.detect_drift(recent_window=5)
    assert len(reports) == 2
    assert reports[0].feature_name == "feat_a"
    assert reports[1].feature_name == "feat_b"
