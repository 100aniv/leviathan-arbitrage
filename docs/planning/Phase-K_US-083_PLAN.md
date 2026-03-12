# US-083: HMM 학습 파이프라인

## 변경 대상
1. `engine/src/ml/hmm_trainer.py` (NEW) — HMM 학습/캐시/스케줄러
2. `engine/src/ml/feature_pipeline.py` (MODIFY) — TimescaleDB 데이터 로더 추가
3. `engine/src/ml/__init__.py` (MODIFY) — HMMTrainer export

## 핵심 클래스

### HMMTrainer
```python
class HMMTrainer:
    """GaussianHMM 학습 파이프라인.

    TimescaleDB → RegimeFeaturePipeline → GaussianHMM fit → 전이행렬 캐시
    """

    def __init__(
        self,
        feature_pipeline: RegimeFeaturePipeline,
        hmm_detector: HMMRegimeDetector,
        cache_dir: str = ".cache/hmm",
        retrain_interval_days: int = 7,  # 주간 배치
    ): ...

    async def fetch_training_data(self, conn, lookback_days: int = 30) -> dict[str, np.ndarray]:
        """TimescaleDB에서 학습 데이터 조회."""

    def train(self, returns, spreads, volumes, bid_vols=None, ask_vols=None) -> HMMRegimeDetector:
        """피처 추출 → HMM fit → 전이행렬 캐시 저장."""

    def save_model(self, path: str | None = None) -> str:
        """학습된 모델 + 전이행렬 pickle 저장."""

    def load_model(self, path: str | None = None) -> bool:
        """캐시된 모델 로드. 유효기간 체크."""

    def should_retrain(self) -> bool:
        """마지막 학습으로부터 retrain_interval_days 경과 여부."""

    async def scheduled_train(self, conn) -> bool:
        """스케줄러용: should_retrain → fetch → train → save."""
```

### 전이행렬 캐시
- pickle로 저장: `{cache_dir}/hmm_model.pkl`
- 메타데이터: `{cache_dir}/hmm_meta.json` (trained_at, samples, features, transition_matrix)
- 유효기간: retrain_interval_days (기본 7일)

### predict 레이턴시
- HMM predict는 numpy 연산으로 <1ms (10-feature vector)
- 테스트에서 `time.perf_counter()` 검증 (<2ms)

## 테스트
- `engine/tests/unit/ml/test_hmm_trainer.py`
  - HMMTrainer 생성 + 기본값
  - train() with mock data (no actual hmmlearn needed — mock)
  - save/load model cycle
  - should_retrain logic
  - predict latency <2ms (mock fitted model)
  - scheduled_train flow (mock DB conn)
