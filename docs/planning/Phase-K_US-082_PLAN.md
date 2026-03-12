# US-082: 레짐 피처 엔지니어링

## 변경 대상
1. `engine/src/ml/__init__.py` (NEW) — 패키지 초기화
2. `engine/src/ml/feature_pipeline.py` (NEW) — 피처 추출 파이프라인
3. `engine/src/tuning/regime_detector.py` (MODIFY) — HMMRegimeDetector에 feature_pipeline 연결 인터페이스

## 피처 목록 (5개 카테고리)

### 1. Volatility (변동성)
- `realized_vol`: 최근 N개 수익률의 표준편차 (rolling std)
- `historical_vol`: 더 긴 윈도우의 표준편차 (장기 변동성)
- `vol_ratio`: realized / historical (단기/장기 변동성 비율)

### 2. Spread (스프레드)
- `bid_ask_spread`: (ask - bid) / mid 평균
- `spread_std`: 스프레드 변동성

### 3. Volume (거래량)
- `volume_zscore`: (현재 볼륨 - 평균) / 표준편차
- `volume_ratio`: 현재 / 이동평균 볼륨

### 4. Momentum (모멘텀)
- `rolling_return`: N기간 수익률
- `momentum_ma`: 단기/장기 이동평균 차이

### 5. Order Flow (주문 흐름)
- `order_imbalance`: (bid_volume - ask_volume) / (bid_volume + ask_volume)

## 클래스 설계

```python
class RegimeFeaturePipeline:
    """레짐 분류를 위한 피처 추출 파이프라인."""

    def __init__(self, short_window=20, long_window=100):
        self._short_window = short_window
        self._long_window = long_window

    def extract(self, returns, spreads, volumes, bid_volumes, ask_volumes) -> np.ndarray:
        """Raw 시계열 → 정규화된 피처 벡터 (1, n_features)"""

    def extract_batch(self, ...) -> np.ndarray:
        """배치 피처 추출 (n_samples, n_features)"""

    @staticmethod
    def normalize(features: np.ndarray) -> np.ndarray:
        """Z-score 정규화"""

    @staticmethod
    def fill_missing(features: np.ndarray) -> np.ndarray:
        """결측값 처리 (0으로 대체 + 경고 로깅)"""

    @property
    def feature_names(self) -> list[str]:
        """피처 이름 목록"""

    N_FEATURES = 10  # 총 피처 수
```

## 테스트
- `engine/tests/unit/ml/test_feature_pipeline.py`
  - 각 피처 추출 정확성 (수동 계산 대비)
  - 정규화 (mean≈0, std≈1)
  - 결측값 처리 (NaN → 0)
  - 빈 입력 처리
  - feature_names 길이 == N_FEATURES
