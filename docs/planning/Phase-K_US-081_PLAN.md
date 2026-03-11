# US-081: ML 의존성 + HMM 3-regime 설계

## 변경 대상
1. `engine/pyproject.toml` — `[ml]` optional dependency group 추가
2. `engine/src/tuning/regime_detector.py` — MarketRegime 확장 + HMMRegimeDetector 설계

## 상세 구현

### 1. pyproject.toml
```toml
[project.optional-dependencies]
ml = [
    "hmmlearn>=0.3",
    "scikit-learn>=1.4",
]
```

### 2. MarketRegime enum 확장
기존 LOW/MEDIUM/HIGH/CRISIS 유지 (threshold-based 호환).
HMM 3-state용 CALM/NORMAL/VOLATILE 추가:
```python
class MarketRegime(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRISIS = "CRISIS"
    # HMM 3-state regimes (US-081)
    CALM = "CALM"         # HMM state 0: 낮은 변동성
    NORMAL = "NORMAL"     # HMM state 1: 정상 변동성
    VOLATILE = "VOLATILE" # HMM state 2: 높은 변동성
```

매핑: CALM↔LOW, NORMAL↔MEDIUM, VOLATILE↔HIGH (CRISIS는 threshold kill-switch 전용)

### 3. HMMRegimeDetector 클래스
```python
class HMMRegimeDetector:
    """GaussianHMM 3-state 레짐 분류기 (US-081 설계, US-083에서 학습 구현)."""
    - n_states = 3 (CALM/NORMAL/VOLATILE)
    - covariance_type = "full"
    - n_iter = 100
    - model: GaussianHMM | None (lazy import)
    - predict(features: np.ndarray) → MarketRegime
    - fit(features: np.ndarray) → self (US-083에서 구현)
    - transition_matrix property
    - HMM_REGIME_MAP: dict[int, MarketRegime]
```

## 테스트
- `engine/tests/unit/tuning/test_hmm_regime.py`
  - enum 확장 검증 (CALM/NORMAL/VOLATILE 존재)
  - HMMRegimeDetector 생성 (hmmlearn 없이도 graceful)
  - 매핑 검증 (state 0→CALM, 1→NORMAL, 2→VOLATILE)

## 의존성: 없음 (첫 번째 US)
## 후속: US-082 (피처 엔지니어링) → US-083 (학습) → US-084 (시그널 통합) → US-085 (검증)
