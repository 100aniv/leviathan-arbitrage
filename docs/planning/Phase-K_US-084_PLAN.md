# US-084: 레짐→시그널 통합

## 변경 대상
1. `engine/src/core/signal.py` — SignalGenerator에 regime 필터 추가
2. `engine/src/tuning/regime_detector.py` — REGIME_MIN_EDGE 상수 추가

## 구현 상세

### 1. regime_detector.py — 상수 추가
```python
REGIME_MIN_EDGE: dict[MarketRegime, Decimal] = {
    MarketRegime.CALM: Decimal("0.0003"),      # 3 bps
    MarketRegime.NORMAL: Decimal("0.0005"),     # 5 bps
    MarketRegime.VOLATILE: Decimal("0.0008"),   # 8 bps
    MarketRegime.LOW: Decimal("0.0003"),        # threshold alias
    MarketRegime.MEDIUM: Decimal("0.0005"),     # threshold alias
    MarketRegime.HIGH: Decimal("0.0008"),       # threshold alias
    MarketRegime.CRISIS: Decimal("0.0015"),     # 15 bps (매우 보수적)
}
```

### 2. signal.py — SignalGenerator 수정

#### 2a. __init__ 확장
```python
def __init__(self, ..., regime_detector=None):
    ...
    self._regime_detector = regime_detector  # US-084
```

#### 2b. min_edge gate 수정 (on_orderbook_update 내)
기존: `if net_edge < self._config.min_edge:`
변경:
```python
# US-084: regime-adaptive min_edge
effective_min_edge = self._config.min_edge
if self._regime_detector is not None:
    regime = self._regime_detector.current_regime
    from src.tuning.regime_detector import REGIME_MIN_EDGE
    effective_min_edge = REGIME_MIN_EDGE.get(regime, self._config.min_edge)
if net_edge < effective_min_edge:
    return None
```

## QUANT 검증 포인트
- CALM:3bps → 기존 1bps보다 보수적 (OK, 변동성 낮을 때 마진 작으므로 더 보수적)
- VOLATILE:8bps → 높은 변동성에서 더 큰 마진 요구 (OK, slippage 위험 대응)
- CRISIS:15bps → 극단 상황 추가 안전장치
- 기존 동작 보존: regime_detector=None이면 기존 min_edge 사용

## 테스트
- regime_detector=None → 기존 min_edge 사용 (하위호환)
- CALM 레짐 → 3bps 적용
- VOLATILE 레짐 → 8bps 적용
- 레짐 전환 시 시그널 필터 변경 확인
