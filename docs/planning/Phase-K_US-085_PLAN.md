# US-085: Walk-forward 레짐 검증

## 변경 대상
1. `engine/src/analysis/walk_forward.py` — RegimeWalkForwardAnalyzer 추가
2. `engine/tests/test_regime_ml.py` — 레짐-성과 상관분석 + walk-forward 검증 테스트

## 구현 상세

### RegimeWalkForwardAnalyzer
기존 WalkForwardAnalyzer 위에 레짐 상관분석 레이어:

```python
@dataclass
class RegimeWindowResult:
    regime: MarketRegime
    trade_count: int
    total_pnl: float
    win_rate: float
    avg_pnl_per_trade: float
    sharpe: float

@dataclass
class RegimeCorrelation:
    """레짐 전환 vs 성과 상관분석 결과."""
    regime_results: dict[str, RegimeWindowResult]  # regime별 성과
    regime_transition_count: int  # 레짐 전환 횟수
    pnl_improvement_pct: float  # regime-adaptive vs fixed min_edge 개선율
    correlation_score: float  # 레짐-성과 상관 (-1~1)
    walk_forward_pass: bool  # 검증 통과

class RegimeWalkForwardAnalyzer:
    """레짐 기반 Walk-forward 검증."""

    def __init__(self, regime_detector=None):
        self._detector = regime_detector

    def analyze_regime_correlation(
        self, trades: list[dict], regime_history: list[dict]
    ) -> RegimeCorrelation:
        """레짐 전환 시점 vs 성과 상관분석."""
        # 1. 각 거래를 해당 시점의 레짐으로 분류
        # 2. 레짐별 PnL/WR/Sharpe 계산
        # 3. regime-adaptive vs fixed 비교
        # 4. 상관계수 계산

    def simulate_regime_effect(
        self, trades: list[dict], regime_history: list[dict]
    ) -> dict:
        """백테스트에서 regime 효과 측정.
        fixed min_edge vs regime-adaptive min_edge 비교."""

    def validate_walk_forward(
        self, regime_results: dict
    ) -> bool:
        """Walk-forward PASS 조건:
        - 모든 레짐에서 win_rate > 50%
        - regime-adaptive PnL >= fixed PnL
        - VOLATILE 레짐에서 MDD 감소
        """
```

## 테스트
- `engine/tests/test_regime_ml.py`
  - 레짐별 성과 분류 (CALM/NORMAL/VOLATILE)
  - regime-adaptive vs fixed min_edge PnL 비교
  - walk-forward PASS 조건 검증
  - 상관계수 범위 (-1~1)
  - 레짐 전환 카운트
  - 빈 데이터 처리
