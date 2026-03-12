# US-092: XGBoost 학습 루프 — Implementation Plan

## Summary
Weekly batch training pipeline: TimescaleDB → MLFeaturePipeline extraction → XGBoost train with Optuna HPO.

## Dependencies
- US-091 (ML Feature Pipeline + Feature Store) ✅

## Files
- `engine/src/ml/xgb_trainer.py` (NEW) — XGBTrainer class
- `engine/pyproject.toml` — add xgboost>=2.0 to [ml] deps
- `engine/src/ml/__init__.py` — export XGBTrainer
- `engine/tests/unit/ml/test_xgb_trainer.py` (NEW)

## Design

### XGBTrainer Class (follows HMMTrainer pattern)
```python
class XGBTrainer:
    """XGBoost 시그널 스코어링 학습 파이프라인.

    TimescaleDB → MLFeaturePipeline → label generation → XGBoost train.
    Optuna HPO for hyperparameter optimization.
    """

    # Cache paths
    DEFAULT_CACHE_DIR = ".cache/xgb"
    MODEL_FILE = "xgb_model.json"  # XGBoost native JSON format
    META_FILE = "xgb_meta.json"

    def __init__(
        feature_pipeline: MLFeaturePipeline,
        cache_dir: str,
        retrain_interval_days: int = 7,
        n_hpo_trials: int = 50,
        target_metric: str = "auc",
    )

    # Core methods
    async fetch_training_data(conn, lookback_days=30) -> dict
    generate_labels(returns, threshold_bps=5.0) -> np.ndarray  # 1=profitable, 0=not
    train(X, y) -> xgb.Booster  # Direct train without HPO
    train_with_hpo(X, y, n_trials=None) -> xgb.Booster  # Optuna HPO
    save_model(path=None) -> str
    load_model(path=None) -> bool
    should_retrain() -> bool
    async scheduled_train(conn) -> bool
    predict(features: np.ndarray) -> np.ndarray  # probability scores
    predict_latency_ms(features) -> float
    feature_importance() -> dict[str, float]
```

### Label Generation
- Binary classification: 1 = forward return > threshold_bps, 0 = otherwise
- Forward window: 5 minutes (configurable)
- Threshold: 5 bps default (aligns with MIN_EDGE_BPS=5)

### Optuna HPO Search Space
- max_depth: [3, 10]
- learning_rate: [0.01, 0.3] log
- n_estimators: [50, 500]
- min_child_weight: [1, 10]
- subsample: [0.5, 1.0]
- colsample_bytree: [0.5, 1.0]
- gamma: [0, 5]
- reg_alpha: [1e-8, 10] log
- reg_lambda: [1e-8, 10] log

### Validation
- TimeSeriesSplit (5 folds) — no future leakage
- Metric: AUC-ROC (primary), also log accuracy

### Model Persistence
- XGBoost native JSON format (not pickle — portable, inspectable)
- Meta JSON: trained_at, samples, n_features, best_params, metric_score

## Acceptance Criteria Mapping
1. ✅ Weekly batch: fetch_training_data (TimescaleDB) → MLFeaturePipeline extract → train
2. ✅ Optuna HPO: train_with_hpo method with configurable n_trials
3. ✅ [ml] optional dep: xgboost>=2.0 in pyproject.toml
