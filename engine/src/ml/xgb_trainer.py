"""XGBoost signal scoring training pipeline — US-092."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Lazy imports for optional deps
_xgb = None
_optuna = None


def _get_xgb():
    global _xgb
    if _xgb is None:
        try:
            import xgboost as xgb
            _xgb = xgb
        except ImportError:
            raise ImportError("xgboost>=2.0 required: pip install leviathan-engine[ml]")
    return _xgb


def _get_optuna():
    global _optuna
    if _optuna is None:
        try:
            import optuna
            _optuna = optuna
        except ImportError:
            raise ImportError("optuna>=3.6 required (already in base deps)")
    return _optuna


class XGBTrainer:
    """XGBoost 시그널 스코어링 학습 파이프라인.

    TimescaleDB → MLFeaturePipeline → label generation → XGBoost train.
    Optuna HPO for hyperparameter optimization.
    """

    DEFAULT_CACHE_DIR = ".cache/xgb"
    MODEL_FILE = "xgb_model.json"
    META_FILE = "xgb_meta.json"

    def __init__(
        self,
        cache_dir: str = DEFAULT_CACHE_DIR,
        retrain_interval_days: int = 7,
        n_hpo_trials: int = 50,
        target_metric: str = "auc",
        label_threshold_bps: float = 5.0,
        forward_window: int = 5,
    ) -> None:
        self._cache_dir = Path(cache_dir)
        self._retrain_interval_days = retrain_interval_days
        self._n_hpo_trials = n_hpo_trials
        self._target_metric = target_metric
        self._label_threshold_bps = label_threshold_bps
        self._forward_window = forward_window
        self._model = None  # xgb.Booster
        self._best_params: dict[str, Any] = {}
        self._last_trained_at: datetime | None = None
        self._train_samples: int = 0
        self._best_score: float = 0.0
        self._feature_names: list[str] = []

    @property
    def model(self):
        return self._model

    @property
    def last_trained_at(self) -> datetime | None:
        return self._last_trained_at

    @property
    def best_params(self) -> dict[str, Any]:
        return dict(self._best_params)

    @property
    def best_score(self) -> float:
        return self._best_score

    @property
    def is_trained(self) -> bool:
        return self._model is not None

    async def fetch_training_data(
        self,
        conn: Any,
        lookback_days: int = 30,
    ) -> dict[str, np.ndarray]:
        """TimescaleDB에서 학습 데이터 조회.

        Returns:
            {"prices": array, "spreads": array, "volumes": array}
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
            logger.error("xgb_trainer: fetch failed: %s", exc)
            return {"prices": np.array([]), "spreads": np.array([]), "volumes": np.array([])}

        if len(rows) < 2:
            return {"prices": np.array([]), "spreads": np.array([]), "volumes": np.array([])}

        prices = np.array([float(r["close_price"]) for r in rows])
        spreads = np.array([float(r["bid_ask_spread"]) for r in rows])
        volumes = np.array([float(r["volume"]) for r in rows])

        return {"prices": prices, "spreads": spreads, "volumes": volumes}

    def generate_labels(
        self,
        prices: np.ndarray,
        threshold_bps: float | None = None,
        forward_window: int | None = None,
    ) -> np.ndarray:
        """Forward return 기반 이진 레이블 생성.

        Label = 1 if forward_return > threshold_bps, else 0.
        마지막 forward_window 개는 NaN → 제거 필요.
        """
        th = threshold_bps if threshold_bps is not None else self._label_threshold_bps
        fw = forward_window if forward_window is not None else self._forward_window

        n = len(prices)
        labels = np.zeros(n, dtype=np.float64)

        for i in range(n - fw):
            fwd_return_bps = (prices[i + fw] - prices[i]) / prices[i] * 10000
            labels[i] = 1.0 if fwd_return_bps > th else 0.0

        # Mark trailing as NaN (to be excluded)
        labels[-fw:] = np.nan
        return labels

    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
        params: dict[str, Any] | None = None,
        feature_names: list[str] | None = None,
    ):
        """XGBoost train (without HPO).

        Parameters:
            X: features (n_samples, n_features)
            y: binary labels (n_samples,)
            params: XGBoost params (defaults if None)
        Returns:
            trained xgb.Booster
        """
        xgb = _get_xgb()

        if feature_names:
            self._feature_names = feature_names

        default_params = {
            "objective": "binary:logistic",
            "eval_metric": "auc",
            "max_depth": 6,
            "learning_rate": 0.1,
            "min_child_weight": 3,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "gamma": 0.1,
            "reg_alpha": 0.01,
            "reg_lambda": 1.0,
            "tree_method": "hist",
            "verbosity": 0,
        }
        if params:
            default_params.update(params)

        n_estimators = default_params.pop("n_estimators", 200)
        dtrain = xgb.DMatrix(X, label=y, feature_names=self._feature_names or None)
        self._model = xgb.train(default_params, dtrain, num_boost_round=n_estimators)
        self._best_params = default_params
        self._last_trained_at = datetime.now(timezone.utc)
        self._train_samples = len(y)

        logger.info("xgb_trainer: trained on %d samples", self._train_samples)
        return self._model

    def train_with_hpo(
        self,
        X: np.ndarray,
        y: np.ndarray,
        n_trials: int | None = None,
        feature_names: list[str] | None = None,
    ):
        """Optuna HPO → best params → final train.

        TimeSeriesSplit (5-fold) to prevent future leakage.
        """
        xgb = _get_xgb()
        optuna = _get_optuna()
        optuna.logging.set_verbosity(optuna.logging.WARNING)

        if feature_names:
            self._feature_names = feature_names

        trials = n_trials or self._n_hpo_trials
        n_splits = min(5, max(2, len(y) // 100))

        def objective(trial):
            params = {
                "objective": "binary:logistic",
                "eval_metric": "auc",
                "tree_method": "hist",
                "verbosity": 0,
                "max_depth": trial.suggest_int("max_depth", 3, 10),
                "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
                "min_child_weight": trial.suggest_int("min_child_weight", 1, 10),
                "subsample": trial.suggest_float("subsample", 0.5, 1.0),
                "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
                "gamma": trial.suggest_float("gamma", 0.0, 5.0),
                "reg_alpha": trial.suggest_float("reg_alpha", 1e-8, 10.0, log=True),
                "reg_lambda": trial.suggest_float("reg_lambda", 1e-8, 10.0, log=True),
            }
            n_estimators = trial.suggest_int("n_estimators", 50, 500)

            # TimeSeriesSplit
            fold_size = len(X) // (n_splits + 1)
            scores = []
            for fold in range(n_splits):
                train_end = fold_size * (fold + 2)
                val_start = train_end
                val_end = min(val_start + fold_size, len(X))
                if val_end <= val_start:
                    continue

                X_tr, y_tr = X[:train_end], y[:train_end]
                X_val, y_val = X[val_start:val_end], y[val_start:val_end]

                # Skip fold if single class (AUC undefined)
                if len(np.unique(y_val)) < 2 or len(np.unique(y_tr)) < 2:
                    continue

                dtrain = xgb.DMatrix(X_tr, label=y_tr)
                dval = xgb.DMatrix(X_val, label=y_val)

                model = xgb.train(
                    params, dtrain, num_boost_round=n_estimators,
                    evals=[(dval, "val")], verbose_eval=False,
                    early_stopping_rounds=20,
                )
                score = model.best_score
                if np.isfinite(score):
                    scores.append(score)

            if not scores:
                return 0.5  # fallback: random baseline AUC
            return float(np.mean(scores))

        study = optuna.create_study(direction="maximize")
        study.optimize(objective, n_trials=trials)

        # Fallback if all trials failed
        if len(study.trials) == 0 or all(
            t.state != optuna.trial.TrialState.COMPLETE for t in study.trials
        ):
            logger.warning("xgb_trainer: all HPO trials failed, using defaults")
            return self.train(X, y, feature_names=feature_names)

        self._best_params = study.best_params.copy()
        self._best_score = study.best_value

        # Final train with best params on all data
        final_params = {
            "objective": "binary:logistic",
            "eval_metric": "auc",
            "tree_method": "hist",
            "verbosity": 0,
        }
        n_estimators = self._best_params.pop("n_estimators", 200)
        final_params.update(self._best_params)

        dtrain = xgb.DMatrix(X, label=y, feature_names=self._feature_names or None)
        self._model = xgb.train(final_params, dtrain, num_boost_round=n_estimators)
        self._last_trained_at = datetime.now(timezone.utc)
        self._train_samples = len(y)

        logger.info(
            "xgb_trainer: HPO complete — best_score=%.4f, trials=%d, samples=%d",
            self._best_score, trials, self._train_samples,
        )
        return self._model

    def predict(self, features: np.ndarray) -> np.ndarray:
        """확률 스코어 예측. shape (n,) — each in [0, 1]."""
        if self._model is None:
            raise RuntimeError("Model not trained. Call train() or load_model() first.")
        xgb = _get_xgb()
        dtest = xgb.DMatrix(features, feature_names=self._feature_names or None)
        return self._model.predict(dtest)

    def predict_latency_ms(self, features: np.ndarray) -> float:
        """predict 호출 레이턴시 측정 (ms)."""
        start = time.perf_counter()
        self.predict(features)
        return (time.perf_counter() - start) * 1000

    def feature_importance(self) -> dict[str, float]:
        """피처 중요도 (gain 기준)."""
        if self._model is None:
            return {}
        scores = self._model.get_score(importance_type="gain")
        total = sum(scores.values()) or 1.0
        return {k: v / total for k, v in sorted(scores.items(), key=lambda x: -x[1])}

    def save_model(self, path: str | None = None) -> str:
        """모델 + 메타데이터 저장 (XGBoost native JSON)."""
        if self._model is None:
            raise RuntimeError("No model to save.")

        cache_dir = Path(path) if path else self._cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)

        model_path = cache_dir / self.MODEL_FILE
        meta_path = cache_dir / self.META_FILE

        self._model.save_model(str(model_path))

        meta = {
            "trained_at": self._last_trained_at.isoformat() if self._last_trained_at else None,
            "samples": self._train_samples,
            "best_params": self._best_params,
            "best_score": self._best_score,
            "target_metric": self._target_metric,
            "label_threshold_bps": self._label_threshold_bps,
            "forward_window": self._forward_window,
            "retrain_interval_days": self._retrain_interval_days,
            "feature_names": self._feature_names,
        }
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)

        logger.info("xgb_trainer: model saved to %s", model_path)
        return str(model_path)

    def load_model(self, path: str | None = None) -> bool:
        """캐시된 모델 로드. 유효기간 체크."""
        xgb = _get_xgb()
        cache_dir = Path(path) if path else self._cache_dir
        model_path = cache_dir / self.MODEL_FILE
        meta_path = cache_dir / self.META_FILE

        if not model_path.exists() or not meta_path.exists():
            logger.info("xgb_trainer: no cached model at %s", cache_dir)
            return False

        try:
            with open(meta_path) as f:
                meta = json.load(f)

            trained_at_str = meta.get("trained_at")
            if trained_at_str:
                trained_at = datetime.fromisoformat(trained_at_str)
                age_days = (datetime.now(timezone.utc) - trained_at).days
                if age_days > self._retrain_interval_days:
                    logger.info("xgb_trainer: cached model expired (%d days)", age_days)
                    return False
                self._last_trained_at = trained_at

            self._model = xgb.Booster()
            self._model.load_model(str(model_path))
            self._best_params = meta.get("best_params", {})
            self._best_score = meta.get("best_score", 0.0)
            self._train_samples = meta.get("samples", 0)
            self._feature_names = meta.get("feature_names", [])

            logger.info("xgb_trainer: model loaded from %s", model_path)
            return True

        except Exception as exc:
            logger.error("xgb_trainer: load failed: %s", exc)
            return False

    def should_retrain(self) -> bool:
        """retrain_interval_days 경과 여부."""
        if self._last_trained_at is None:
            return True
        age = datetime.now(timezone.utc) - self._last_trained_at
        return age.days >= self._retrain_interval_days

    async def scheduled_train(
        self,
        conn: Any,
        feature_pipeline=None,
    ) -> bool:
        """스케줄러용: should_retrain → fetch → extract → train_with_hpo → save.

        Returns:
            True if training was performed
        """
        if not self.should_retrain():
            return False

        data = await self.fetch_training_data(conn)
        prices = data["prices"]
        if len(prices) < 100:
            logger.warning("xgb_trainer: insufficient data (%d samples)", len(prices))
            return False

        try:
            # Generate features using MLFeaturePipeline if provided
            if feature_pipeline is not None:
                returns = np.diff(prices) / prices[:-1]
                X = np.vstack([
                    feature_pipeline.extract(returns=returns[:i+1]).flatten()
                    for i in range(len(returns))
                ])
                labels = self.generate_labels(prices[1:])  # align with returns
            else:
                # Fallback: use raw price features
                returns = np.diff(prices) / prices[:-1]
                X = returns.reshape(-1, 1)
                labels = self.generate_labels(prices[1:])

            # Remove NaN labels
            valid = ~np.isnan(labels)
            X = X[valid]
            y = labels[valid]

            if len(y) < 50:
                logger.warning("xgb_trainer: too few valid samples (%d)", len(y))
                return False

            self.train_with_hpo(X, y)
            self.save_model()
            return True

        except Exception as exc:
            logger.error("xgb_trainer: scheduled training failed: %s", exc)
            return False
