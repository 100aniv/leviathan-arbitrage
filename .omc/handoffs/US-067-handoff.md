# US-067 Handoff: 전략별 개별 Shadow 검증 (수익성 확인 후 활성화)

> **Plan**: `docs/planning/US-067_PLAN.md`
> **Phase**: G (전략 수익성 복원) | **작성일**: 2026-03-11 | **상태**: 구현 준비 완료

---

## Executive Summary

7개 전략 각각을 격리 실행하여 PnL > 0인 전략만 활성화하는 StrategyValidationOrchestrator 구현.
ShadowMode의 기존 `SHADOW_DISABLED_STRATEGIES` 메커니즘을 활용하여 동적으로 전략을 on/off하며,
WS 재연결 없이 단일 ShadowMode 인스턴스에서 순차 검증 수행.

---

## 수정 대상 파일 (3개 소스 + 2개 테스트)

### 1. `engine/src/modes/shadow.py` (기존 파일 수정)

**추가할 메서드 3개** (line 1704 이후):

- `reset_stats()` — `_stats`를 새 `ShadowStats`로 교체. 컬렉터/오더북 유지, 메트릭만 초기화
- `set_disabled_strategies(disabled: set[str])` — `_disabled_strategies`를 동적으로 교체
- `get_strategy_report() -> dict` — 현재 per-strategy 메트릭을 직렬화 가능한 dict로 반환

**핵심 주의사항**:
- `reset_stats()`는 `self._books`, `self._running`, `self._collector_manager` 등 인프라 상태를 건드리지 않음
- `_stats.by_strategy`도 빈 dict로 초기화됨 (전략 결과가 새로 쌓여야 하므로)

### 2. `engine/src/modes/strategy_validation.py` (새 파일)

**StrategyValidationOrchestrator 클래스**:
- `run()` — 전체 파이프라인 실행: start → hydrate → 개별 검증 → 분류 → 통합 검증 → stop → 결과 저장
- `_validate_single_strategy(strategy_id)` — 단일 전략 격리 실행
- `_validate_combined(profitable_ids)` — 수익 전략만 활성화하여 통합 실행
- `_write_activation_config()` — `config/strategy_activation.json` 기록
- `_send_telegram_report()` — Telegram 결과 알림

**데이터 클래스 2개**:
- `StrategyResult` — 단일 전략 검증 결과 (trades, pnl, win_rate, profitable, reason)
- `StrategyValidationReport` — 전체 보고서 (strategies, profitable/unprofitable 목록, combined_result)

**cross_exchange 특수 처리**:
- SignalGenerator가 `strategy_id=None`으로 신호를 발생 → ShadowMode에서 `shadow_arb_v1`로 매핑
- `cross_exchange_v1` 격리 시: `shadow_arb_v1`을 disabled에서 제외
- 다른 전략 격리 시: `shadow_arb_v1`도 disabled에 포함 (cross_exchange 신호 차단)

### 3. `engine/src/main.py` (기존 파일 수정)

**추가**:
- `_strategy_validation_loop()` 메서드 — ShadowMode + StrategyValidationOrchestrator 생성/실행
- `_start_background_tasks()` 내 분기 추가:
  - `STRATEGY_VALIDATION=true` → `_strategy_validation_loop()`
  - `SHADOW_PROGRESSIVE=true` → `_progressive_shadow_loop()` (기존)
  - else → `_shadow_mode_loop()` (기존)

**주의**: 기존 `_shadow_mode_loop()`과 `_progressive_shadow_loop()`는 수정하지 않음.

### 4. `engine/tests/unit/test_strategy_validation.py` (새 파일)

17개 테스트:
- 데이터 클래스 기본값 (2)
- 초기화 + env var (4)
- 전략 격리 (2): cross_exchange shadow_arb_v1 처리
- 수익성 분류 (3): profitable, unprofitable, insufficient_data
- 출력 파일 (2): 정상 기록, 전략 0개 profitable
- ShadowMode 새 메서드 (3): reset_stats, set_disabled, get_strategy_report

### 5. `engine/tests/integration/test_strategy_validation_integration.py` (새 파일)

1개 통합 테스트:
- 실제 ShadowMode (mock collector) → 오케스트레이터 lifecycle → 출력 파일 검증

---

## 환경 변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `STRATEGY_VALIDATION` | `false` | `true`로 설정 시 전략 검증 모드 활성 |
| `STRATEGY_VALIDATION_DURATION_S` | `600` | 전략당 실행 시간 (초) |
| `STRATEGY_VALIDATION_COMBINED_DURATION_S` | `600` | 통합 검증 실행 시간 |
| `STRATEGY_VALIDATION_MIN_TRADES` | `5` | 최소 거래 수 (미달 시 insufficient_data) |
| `STRATEGY_VALIDATION_HYDRATION_S` | `30` | 오더북 하이드레이션 대기 시간 |
| `STRATEGY_ACTIVATION_PATH` | `config/strategy_activation.json` | 결과 출력 경로 |

---

## 실행 방법

```bash
cd engine

# 전략 검증 실행 (10분/전략, 총 ~80분)
DATA_MODE=shadow STRATEGY_VALIDATION=true python -m src.main

# 빠른 검증 (2분/전략, 총 ~16분)
DATA_MODE=shadow STRATEGY_VALIDATION=true STRATEGY_VALIDATION_DURATION_S=120 python -m src.main

# 테스트 실행
python -m pytest tests/unit/test_strategy_validation.py -v
python -m pytest tests/integration/test_strategy_validation_integration.py -v
```

---

## 출력 예시: `config/strategy_activation.json`

```json
{
  "_meta": {
    "source": "US-067 StrategyValidationOrchestrator",
    "date": "2026-03-11",
    "duration_per_strategy_s": 600,
    "min_trades_threshold": 5
  },
  "active_strategies": ["cross_exchange_v1", "latency_arb_v1", "funding_rate_v1"],
  "disabled_strategies": ["spot_futures_v1", "futures_futures_v1", "triangular_v1", "statistical_arb_v1"],
  "shadow_disabled_env": "spot_futures_v1,futures_futures_v1,triangular_v1,statistical_arb_v1",
  "results": {
    "cross_exchange_v1": {
      "profitable": true,
      "trades": 86,
      "pnl": 14.21,
      "win_rate": 0.556,
      "reason": "profitable (PnL=$+14.2100, WR=55.6%)",
      "elapsed_s": 600.0
    },
    "spot_futures_v1": {
      "profitable": false,
      "trades": 32,
      "pnl": -8.5,
      "win_rate": 0.281,
      "reason": "unprofitable (PnL=$-8.5000, WR=28.1%)",
      "elapsed_s": 600.0
    }
  },
  "combined_validation": {
    "elapsed_s": 600.0,
    "total_trades": 130,
    "total_pnl": 28.68,
    "total_win_rate": 0.62,
    "max_drawdown": 2.1
  }
}
```

---

## 의존성 체크리스트

- [x] US-066 완료: StaleOrderbookDetector 4계층 방어 (stale_detector.py)
- [x] SHADOW_DISABLED_STRATEGIES env var 파싱 존재 (shadow.py:510)
- [x] ShadowStats.by_strategy per-strategy 추적 존재 (shadow.py:350)
- [x] 7개 전략 등록 패턴 존재 (main.py:582-589)
- [x] ProgressiveShadowOrchestrator 패턴 참조 가능 (progressive_shadow.py)

---

## 전략 ID 매핑 (참조)

| main.py 등록 ID | STRATEGY_TYPE | signal.strategy_id 매핑 |
|-----------------|--------------|------------------------|
| `cross_exchange_v1` | `cross_exchange_spot` | `shadow_arb_v1` (SignalGenerator 기본값) |
| `spot_futures_v1` | `spot_futures_basis` | `spot_futures_basis` |
| `futures_futures_v1` | `futures_futures` | `futures_futures` |
| `triangular_v1` | `triangular` | `triangular` |
| `funding_rate_v1` | `funding_rate_arb` | `funding_rate_arb` |
| `statistical_arb_v1` | `statistical_arb` | `statistical_arb` |
| `latency_arb_v1` | `latency_arb` | `latency_arb` |

---

## 리스크 및 주의사항

1. **WS 연결 안정성**: 80분 연속 실행 → 자동 재연결 로직 의존 (기존 collector에 구현됨)
2. **Stats 오염**: `reset_stats()` 호출 시 `by_strategy` dict 완전 초기화 → 이전 전략 결과 누적 없음
3. **cross_exchange shadow_arb_v1**: SignalGenerator가 strategy_id 없이 신호 발생 → `_execute_shadow_trade`에서 `shadow_arb_v1`로 기록. 격리 시 반드시 처리
4. **기존 테스트 무영향**: 새 파일 추가 + shadow.py에 메서드 3개 추가만. 기존 로직 경로 변경 없음
5. **Docker 필수**: Shadow 실행 전 `docker compose up -d` (TimescaleDB + Redis)
