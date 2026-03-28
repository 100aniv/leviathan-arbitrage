"""ONNX Runtime inference with global session cache — US-094."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from threading import Lock
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Lazy import
_ort = None


def _get_ort():
    global _ort
    if _ort is None:
        try:
            import onnxruntime as ort
            _ort = ort
        except ImportError:
            raise ImportError("onnxruntime required: pip install onnxruntime")
    return _ort


class ONNXSignalScorer:
    """ONNX Runtime 기반 시그널 스코어러.

    글로벌 InferenceSession 캐시 + predict_signal() <1ms 보장.
    SignalGenerator에 optional ML 스코어 통합.
    """

    _session_cache: dict[str, Any] = {}
    _cache_lock = Lock()

    def __init__(
        self,
        model_path: str | None = None,
        models_dir: str = "models",
        score_threshold: float = 0.5,
        n_features: int = 20,
        enabled: bool = True,
    ) -> None:
        self._models_dir = Path(models_dir)
        self._score_threshold = score_threshold
        self._n_features = n_features
        self._enabled = enabled
        self._session = None
        self._input_name: str = ""
        self._model_path: str = ""
        self._latency_ema_ms: float = 0.0  # exponential moving average
        self._call_count: int = 0

        if model_path:
            self._model_path = model_path
        else:
            default = self._models_dir / "latest" / "model.onnx"
            self._model_path = str(default)

        if enabled and Path(self._model_path).exists():
            self._load_session()

    @property
    def enabled(self) -> bool:
        return self._enabled and self._session is not None

    @property
    def score_threshold(self) -> float:
        return self._score_threshold

    @property
    def latency_ema_ms(self) -> float:
        return self._latency_ema_ms

    @property
    def call_count(self) -> int:
        return self._call_count

    def _load_session(self) -> bool:
        """InferenceSession 로드 (글로벌 캐시)."""
        ort = _get_ort()

        with self._cache_lock:
            if self._model_path in self._session_cache:
                self._session = self._session_cache[self._model_path]
                self._input_name = self._session.get_inputs()[0].name
                logger.info("onnx_scorer: reused cached session for %s", self._model_path)
                return True

            if not Path(self._model_path).exists():
                logger.warning("onnx_scorer: model not found at %s", self._model_path)
                return False

            try:
                opts = ort.SessionOptions()
                opts.inter_op_num_threads = 1
                opts.intra_op_num_threads = 1
                opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

                self._session = ort.InferenceSession(
                    self._model_path,
                    sess_options=opts,
                    providers=["CPUExecutionProvider"],
                )
                self._input_name = self._session.get_inputs()[0].name
                self._session_cache[self._model_path] = self._session

                logger.info("onnx_scorer: loaded model from %s", self._model_path)
                return True

            except Exception as exc:
                logger.error("onnx_scorer: load failed: %s", exc)
                self._session = None
                return False

    def predict_signal(self, features: np.ndarray) -> float:
        """단일 시그널 스코어 예측. <1ms 보장.

        Parameters:
            features: shape (1, n_features) or (n_features,) — float32
        Returns:
            probability score in [0, 1]. Returns 0.5 (neutral) if disabled.
        """
        if not self.enabled:
            return 0.5

        if features.ndim == 1:
            features = features.reshape(1, -1)
        features = features.astype(np.float32)

        start = time.perf_counter()
        try:
            # Validate feature count matches model expectation
            expected_dim = self._session.get_inputs()[0].shape[1] if self._session else None
            if expected_dim and features.shape[1] != expected_dim:
                logger.debug("onnx_scorer: dim mismatch got=%d expected=%d", features.shape[1], expected_dim)
                return 0.5
            outputs = self._session.run(None, {self._input_name: features})
            # XGBoost classifier outputs: [labels, probabilities]
            # probabilities shape: [{0: prob0, 1: prob1}, ...]
            if len(outputs) >= 2 and isinstance(outputs[1], list):
                score = float(outputs[1][0].get(1, 0.5))
            elif isinstance(outputs[0], np.ndarray):
                score = float(outputs[0][0])
            else:
                score = 0.5
        except Exception as exc:
            logger.warning("onnx_scorer: predict failed: %s shape=%s", exc, features.shape)
            score = 0.5

        elapsed_ms = (time.perf_counter() - start) * 1000
        self._call_count += 1
        alpha = 0.1
        self._latency_ema_ms = alpha * elapsed_ms + (1 - alpha) * self._latency_ema_ms

        # US-191: periodic INFO log every 100 calls
        if self._call_count % 100 == 0:
            logger.info(
                "onnx_scorer: %d predictions, avg_latency=%.2fms, last_score=%.4f",
                self._call_count, self._latency_ema_ms, score,
            )

        return score

    def predict_batch(self, features: np.ndarray) -> np.ndarray:
        """배치 예측. shape (n, n_features) → (n,) scores."""
        if not self.enabled:
            return np.full(len(features), 0.5)

        features = features.astype(np.float32)

        try:
            outputs = self._session.run(None, {self._input_name: features})
            if len(outputs) >= 2 and isinstance(outputs[1], list):
                return np.array([d.get(1, 0.5) for d in outputs[1]])
            elif isinstance(outputs[0], np.ndarray):
                return outputs[0].flatten()
            return np.full(len(features), 0.5)
        except Exception as exc:
            logger.warning("onnx_scorer: batch predict failed: %s", exc)
            return np.full(len(features), 0.5)

    def should_trade(self, features: np.ndarray) -> bool:
        """ML 스코어가 threshold 이상인지 판단."""
        score = self.predict_signal(features)
        return score >= self._score_threshold

    def reload_model(self, model_path: str | None = None) -> bool:
        """모델 핫 리로드 (세션 캐시 무효화)."""
        if model_path:
            self._model_path = model_path

        with self._cache_lock:
            if self._model_path in self._session_cache:
                del self._session_cache[self._model_path]

        self._session = None
        return self._load_session()

    def stats(self) -> dict[str, Any]:
        """런타임 통계."""
        return {
            "enabled": self.enabled,
            "model_path": self._model_path,
            "call_count": self._call_count,
            "latency_ema_ms": round(self._latency_ema_ms, 4),
            "score_threshold": self._score_threshold,
            "n_features": self._n_features,
        }

    @classmethod
    def clear_cache(cls) -> None:
        """세션 캐시 전체 초기화 (테스트용)."""
        with cls._cache_lock:
            cls._session_cache.clear()
