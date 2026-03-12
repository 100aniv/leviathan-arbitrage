"""Feature store with drift detection — US-091."""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class DriftReport:
    """피처 drift 감지 결과."""

    feature_name: str
    baseline_mean: float
    current_mean: float
    drift_score: float  # |current - baseline| / baseline_std
    is_drifted: bool  # drift_score > threshold


class FeatureStore:
    """인메모리 피처 저장소 + drift 감지.

    최근 N개 피처 벡터를 저장하고, 기준선 대비 drift를 감지.
    """

    def __init__(
        self,
        max_size: int = 10000,
        drift_threshold: float = 2.0,
        baseline_window: int = 1000,
    ) -> None:
        self._max_size = max_size
        self._drift_threshold = drift_threshold
        self._baseline_window = baseline_window
        self._store: deque[np.ndarray] = deque(maxlen=max_size)
        self._timestamps: deque[float] = deque(maxlen=max_size)
        self._feature_names: list[str] = []
        self._baseline_stats: dict[str, tuple[float, float]] | None = None

    def add(self, features: np.ndarray, timestamp: float | None = None) -> None:
        """피처 벡터 추가. shape (1, n) 또는 (n,)."""
        flat = features.flatten()
        self._store.append(flat)
        self._timestamps.append(timestamp or time.time())

    def set_feature_names(self, names: list[str]) -> None:
        self._feature_names = names

    def compute_baseline(self) -> None:
        """최초 baseline_window 샘플로 기준선(mean, std) 계산."""
        if len(self._store) < self._baseline_window:
            logger.warning(
                "feature_store: not enough data for baseline (%d/%d)",
                len(self._store),
                self._baseline_window,
            )
            return
        data = np.array(list(self._store)[: self._baseline_window])
        means = np.mean(data, axis=0)
        stds = np.std(data, axis=0)
        stds[stds < 1e-12] = 1.0
        self._baseline_stats = {}
        for i in range(data.shape[1]):
            name = self._feature_names[i] if i < len(self._feature_names) else f"f{i}"
            self._baseline_stats[name] = (float(means[i]), float(stds[i]))
        logger.info("feature_store: baseline computed from %d samples", self._baseline_window)

    def detect_drift(self, recent_window: int = 100) -> list[DriftReport]:
        """최근 window vs baseline 비교 → drift 감지."""
        if self._baseline_stats is None:
            return []
        if len(self._store) < recent_window:
            return []

        recent = np.array(list(self._store)[-recent_window:])
        recent_means = np.mean(recent, axis=0)

        reports: list[DriftReport] = []
        for i, (name, (bl_mean, bl_std)) in enumerate(self._baseline_stats.items()):
            if i >= recent_means.shape[0]:
                break
            drift_score = abs(float(recent_means[i]) - bl_mean) / bl_std
            reports.append(
                DriftReport(
                    feature_name=name,
                    baseline_mean=bl_mean,
                    current_mean=float(recent_means[i]),
                    drift_score=drift_score,
                    is_drifted=drift_score > self._drift_threshold,
                )
            )
        return reports

    @property
    def size(self) -> int:
        return len(self._store)

    @property
    def has_baseline(self) -> bool:
        return self._baseline_stats is not None

    def get_recent(self, n: int = 100) -> np.ndarray:
        """최근 n개 피처 벡터 반환. shape (n, features)."""
        data = list(self._store)[-n:]
        if not data:
            return np.array([])
        return np.array(data)
