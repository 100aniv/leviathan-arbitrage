"""HMM training pipeline — US-083."""
from __future__ import annotations

import json
import logging
import os
import pickle
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from src.ml.feature_pipeline import RegimeFeaturePipeline
from src.tuning.regime_detector import HMMRegimeDetector, MarketRegime

logger = logging.getLogger(__name__)


class HMMTrainer:
    """GaussianHMM 학습 파이프라인.

    TimescaleDB → RegimeFeaturePipeline → GaussianHMM fit → 전이행렬 캐시.
    """

    DEFAULT_CACHE_DIR = ".cache/hmm"
    MODEL_FILE = "hmm_model.pkl"
    META_FILE = "hmm_meta.json"

    def __init__(
        self,
        feature_pipeline: RegimeFeaturePipeline | None = None,
        hmm_detector: HMMRegimeDetector | None = None,
        cache_dir: str = DEFAULT_CACHE_DIR,
        retrain_interval_days: int = 7,
    ) -> None:
        self._pipeline = feature_pipeline or RegimeFeaturePipeline()
        self._detector = hmm_detector or HMMRegimeDetector()
        self._cache_dir = Path(cache_dir)
        self._retrain_interval_days = retrain_interval_days
        self._last_trained_at: datetime | None = None
        self._train_samples: int = 0

    @property
    def detector(self) -> HMMRegimeDetector:
        return self._detector

    @property
    def last_trained_at(self) -> datetime | None:
        return self._last_trained_at

    @property
    def retrain_interval_days(self) -> int:
        return self._retrain_interval_days

    async def fetch_training_data(
        self,
        conn: Any,
        lookback_days: int = 30,
    ) -> dict[str, np.ndarray]:
        """TimescaleDB에서 학습 데이터 조회.

        Parameters:
            conn: asyncpg connection
            lookback_days: 조회 기간 (일)
        Returns:
            {"returns": array, "spreads": array, "volumes": array}
        """
        query = """
            SELECT close_price, bid_ask_spread, volume
            FROM market_data_1m
            WHERE timestamp > NOW() - INTERVAL '%s days'
            ORDER BY timestamp ASC
        """
        try:
            rows = await conn.fetch(query % lookback_days)
        except Exception as exc:
            logger.error("hmm_trainer: fetch failed: %s", exc)
            return {"returns": np.array([]), "spreads": np.array([]), "volumes": np.array([])}

        if len(rows) < 2:
            return {"returns": np.array([]), "spreads": np.array([]), "volumes": np.array([])}

        prices = np.array([float(r["close_price"]) for r in rows])
        returns = np.diff(prices) / prices[:-1]
        spreads = np.array([float(r["bid_ask_spread"]) for r in rows[1:]])
        volumes = np.array([float(r["volume"]) for r in rows[1:]])

        return {"returns": returns, "spreads": spreads, "volumes": volumes}

    def train(
        self,
        returns: np.ndarray,
        spreads: np.ndarray,
        volumes: np.ndarray,
        bid_volumes: np.ndarray | None = None,
        ask_volumes: np.ndarray | None = None,
    ) -> HMMRegimeDetector:
        """피처 추출 → 정규화 → HMM fit → 전이행렬 저장.

        Parameters:
            returns: 수익률 배열
            spreads: bid-ask spread 배열
            volumes: 거래량 배열
        Returns:
            학습된 HMMRegimeDetector
        """
        if len(returns) < self._detector.N_STATES * 10:
            raise ValueError(
                f"Insufficient data: {len(returns)} samples, need >= {self._detector.N_STATES * 10}"
            )

        # 롤링 피처 추출 (슬라이딩 윈도우)
        window = self._pipeline._short_window
        n_samples = len(returns) - window + 1
        if n_samples < self._detector.N_STATES * 3:
            raise ValueError(f"Too few windows: {n_samples}")

        features_list = []
        for i in range(n_samples):
            end = i + window
            feat = self._pipeline.extract(
                returns[i:end],
                spreads[i:end] if len(spreads) >= end else spreads,
                volumes[i:end] if len(volumes) >= end else volumes,
                bid_volumes[i:end] if bid_volumes is not None and len(bid_volumes) >= end else None,
                ask_volumes[i:end] if ask_volumes is not None and len(ask_volumes) >= end else None,
            )
            features_list.append(feat.flatten())

        features = np.array(features_list)
        features = self._pipeline.normalize(features)

        # HMM fit
        self._detector.fit(features)
        self._last_trained_at = datetime.now(timezone.utc)
        self._train_samples = len(features)

        logger.info(
            "hmm_trainer: fitted %d samples, transition_matrix shape=%s",
            self._train_samples,
            self._detector.transition_matrix.shape if self._detector.transition_matrix is not None else "None",
        )
        return self._detector

    def save_model(self, path: str | None = None) -> str:
        """학습된 모델 + 메타데이터 저장.

        Returns:
            저장된 모델 파일 경로
        """
        cache_dir = Path(path) if path else self._cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)

        model_path = cache_dir / self.MODEL_FILE
        meta_path = cache_dir / self.META_FILE

        # 모델 pickle 저장
        model_data = {
            "model": self._detector._model,
            "transition_matrix": self._detector.transition_matrix,
            "fitted": self._detector.is_fitted,
            "current_regime": self._detector.current_regime.value,
        }
        with open(model_path, "wb") as f:
            pickle.dump(model_data, f)

        # 메타데이터 JSON 저장
        meta = {
            "trained_at": self._last_trained_at.isoformat() if self._last_trained_at else None,
            "samples": self._train_samples,
            "n_features": self._pipeline.N_FEATURES,
            "n_states": self._detector.N_STATES,
            "retrain_interval_days": self._retrain_interval_days,
        }
        if self._detector.transition_matrix is not None:
            meta["transition_matrix"] = self._detector.transition_matrix.tolist()

        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)

        logger.info("hmm_trainer: model saved to %s", model_path)
        return str(model_path)

    def load_model(self, path: str | None = None) -> bool:
        """캐시된 모델 로드. 유효기간 체크.

        Returns:
            True if loaded successfully and within validity period
        """
        cache_dir = Path(path) if path else self._cache_dir
        model_path = cache_dir / self.MODEL_FILE
        meta_path = cache_dir / self.META_FILE

        if not model_path.exists() or not meta_path.exists():
            logger.info("hmm_trainer: no cached model found at %s", cache_dir)
            return False

        try:
            with open(meta_path) as f:
                meta = json.load(f)

            trained_at_str = meta.get("trained_at")
            if trained_at_str:
                trained_at = datetime.fromisoformat(trained_at_str)
                age_days = (datetime.now(timezone.utc) - trained_at).days
                if age_days > self._retrain_interval_days:
                    logger.info("hmm_trainer: cached model expired (%d days old)", age_days)
                    return False
                self._last_trained_at = trained_at

            with open(model_path, "rb") as f:
                model_data = pickle.load(f)  # noqa: S301

            self._detector._model = model_data["model"]
            self._detector.transition_matrix = model_data.get("transition_matrix")
            self._detector._fitted = model_data.get("fitted", False)
            regime_val = model_data.get("current_regime", "NORMAL")
            self._detector.current_regime = MarketRegime(regime_val)
            self._train_samples = meta.get("samples", 0)

            logger.info("hmm_trainer: model loaded from %s (age=%s days)",
                       model_path, meta.get("trained_at", "unknown"))
            return True

        except Exception as exc:
            logger.error("hmm_trainer: load failed: %s", exc)
            return False

    def should_retrain(self) -> bool:
        """마지막 학습으로부터 retrain_interval_days 경과 여부."""
        if self._last_trained_at is None:
            return True
        age = datetime.now(timezone.utc) - self._last_trained_at
        return age.days >= self._retrain_interval_days

    async def scheduled_train(self, conn: Any) -> bool:
        """스케줄러용: should_retrain → fetch → train → save.

        Returns:
            True if training was performed
        """
        if not self.should_retrain():
            return False

        data = await self.fetch_training_data(conn)
        if len(data["returns"]) < self._detector.N_STATES * 10:
            logger.warning("hmm_trainer: insufficient data for training (%d samples)",
                          len(data["returns"]))
            return False

        try:
            self.train(data["returns"], data["spreads"], data["volumes"])
            self.save_model()
            return True
        except Exception as exc:
            logger.error("hmm_trainer: scheduled training failed: %s", exc)
            return False

    def predict_latency_ms(self, features: np.ndarray) -> float:
        """predict 호출 레이턴시 측정 (ms)."""
        start = time.perf_counter()
        self._detector.predict(features)
        return (time.perf_counter() - start) * 1000
