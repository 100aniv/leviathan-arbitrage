# Phase S15 PLAN.md — CRITICAL 버그 수정 + ML 파이프라인 연결

> **Phase**: S15 (회귀 — TF SF 9H 중단 후)
> **US 범위**: US-245 ~ US-259-a (17 US)
> **목표**: CRITICAL 6건 + 수학 오류 3건 해결, ML 3모듈 background loop 연결
> **선행 조건**: Entry Gate CONDITIONAL PASS (Karina 분석 완료 — 2026-03-19)
> **작성일**: 2026-03-19
> **회귀 사유**: profit_factor 계산 버그 / LiveGate 차단 미동작 / ML 미연결 / 전략 평가 기준 위반

---

## 0. Entry Gate 분류 (Karina 분석 결과)

Entry Gate는 각 US의 현재 구현 상태를 3단계로 분류했습니다.
이 분류가 Batch 배정의 기준이 됩니다.

| 상태 | US | 작업 방향 |
|------|----|---------|
| **이미 완료** (검증만 필요, 7개) | US-245, US-246, US-249, US-250-a, US-254, US-256, US-257 | Shadow 10min 중 런타임 동작으로 검증. 코드 수정 최소화. |
| **부분 완료** (수정 필요, 5개) | US-248, US-250, US-251, US-252, US-253, US-258-b | 핵심 로직 존재하나 연결/수정 누락. 외과적 수정. |
| **미완료** (신규 구현, 3개) | US-247, US-255, US-258-a | 신규 구현 필요. |
| **통합 검증** (1개) | US-259-a | 모든 Batch 완료 후 Shadow 10min. |

> **이미 완료 US의 검증 전략**: Shadow 10min 실행 중 로그에서 각 항목 동작 확인.
> - US-245: `stat_arb` 로그에 `regime_detector=<RegimeDetector object>` 확인
> - US-246: `EXECUTION_MODE=live` 시 LiveGate enforce 로그 확인
> - US-249: triangular 첫 신호에서 `leg1_size`, `leg2_size`, `leg3_size` 각자 다름 확인
> - US-250-a: 시작 로그에 `ComplianceChecker audit PASS` 확인
> - US-254: 6개 전략 로그에 `regime_detector connected` 확인
> - US-256: 시작 로그에 `peak_equity loaded from DB` 또는 `no prior peak_equity` 확인
> - US-257: Shadow 종료 시 `profit_factor=X.XX` (금액 비율) 확인

---

## 1. 실행 배치 구성 (의존성 + Entry Gate 기반)

### 1.1 배치 요약

| 배치 | US 수 | 테마 | 병렬 여부 | Entry Gate 상태 | 담당 |
|------|-------|------|----------|----------------|------|
| **Batch 1** | 6 | CRITICAL 버그 + 미완료 신규 | 6개 병렬 | 미완료(3) + 부분완료(3) | Yujin + Gaeul |
| **Batch 2** | 2 | ML 루프 sleep-first 수정 | 2개 순차 | 부분완료(2) | Liz |
| **Batch 3** | 1 | Feature→ONNX Scorer 연결 | Batch 2 완료 후 | 부분완료(1) | Liz |
| **Batch 4** | 7 | 이미 완료 US 검증 | 7개 병렬 검증 | 이미 완료(7) | Wonyoung |
| **Batch 5** | 1 | 통합 Shadow 10min | 전체 의존 | — | 전원 |

### 1.2 의존성 그래프

```
Batch 1 (병렬)
├── US-247: estimate_cost→calculate 통합          [미완료 → 신규]
├── US-248: ADV/sigma 동적 계산                   [부분완료 → 수정]
├── US-255: AdaptiveThreshold 전략별 분리          [미완료 → 신규]
├── US-257: profit_factor 금액 비율 수정           [이미완료 → 검증+수정]  ← CRITICAL
├── US-258-a: ShadowMiniTuner 활성화/제거          [미완료 → 신규]
└── US-258-b: warm-up 추적 (stat_arb)             [부분완료 → 수정]

Batch 2 (순차, Batch 1 독립)
├── US-251: HMM Trainer loop sleep-first 수정     [부분완료]
└── US-252: XGBoost+ONNX loop sleep-first 수정    [부분완료, US-251 후]

Batch 3 (Batch 2 완료 후)
└── US-253: Feature Pipeline→ONNX Scorer 연결     [부분완료, US-252 완료 필요]

Batch 4 (Batch 1 병렬 진행 — 검증 전용)
├── US-245: stat_arb regime_detector 주입 검증     [이미완료]
├── US-246: LiveGate 실행 경로 강제 검증           [이미완료]
├── US-249: 삼각 leg sizing 보정 검증              [이미완료]
├── US-250: 포지션 리커버리 + 리콘실러 검증         [이미완료 — startup 연결만 확인]
├── US-250-a: ComplianceChecker startup 검증       [이미완료]
├── US-254: RegimeDetector 전 전략 연결 검증        [이미완료]
└── US-256: peak_equity DB 영속화 검증             [이미완료]

Batch 5 (전체 완료 후)
└── US-259-a: 통합 Shadow 10min (13항목 복합지표)
```

---

## 2. TeamCreate 구성 (IVE 팀원 배정)

### 2.1 팀원별 소유 영역

| 팀원 | 역할 | 배치 | 담당 US | 소유 파일 |
|------|------|------|---------|---------|
| **Yujin** | executor | Batch 1 | US-257, US-258-a, US-258-b | `shadow.py`, `live_gate.py`, `scheduled_tuner.py` |
| **Gaeul** | executor | Batch 1 | US-247, US-248, US-255 | `cost_calculator.py`, `signal.py`, `adaptive_threshold.py` |
| **Liz** | executor | Batch 2→3 | US-251→US-252→US-253 | `ml/hmm_trainer.py`, `ml/xgb_trainer.py`, `ml/onnx_runtime.py`, `ml/feature_pipeline.py` |
| **Leeseo** | executor | (대기) | main.py 순차 머지 담당 | `main.py` (충돌 방지 역할) |
| **Wonyoung** | test-engineer | Batch 4 | US-245, 246, 249, 250, 250-a, 254, 256 검증 | 각 테스트 파일 신규 작성 |

### 2.2 실행 타임라인

```
Phase 1 (동시 시작):
  Yujin  → US-257, US-258-a, US-258-b (shadow.py 전담)
  Gaeul  → US-247, US-248, US-255     (cost + signal + threshold)
  Liz    → US-251 (HMM loop, sleep-first)
  Wonyoung → 이미완료 7개 US 테스트 작성 (Batch 4)

Phase 2 (Liz 순차):
  Liz    → US-252 (XGBoost+ONNX, US-251 완료 후)

Phase 3 (Liz 순차):
  Liz    → US-253 (Feature→Scorer, US-252 완료 후)

Phase 4 (Leeseo — main.py 순차 머지):
  Leeseo → Yujin 변경분 머지 → Gaeul 변경분 머지 → Liz 변경분 머지
  순서: LiveGate(Yujin) → 전략등록(Gaeul/Wonyoung) → ML(Liz) → 인프라 확인

Phase 5 (전원):
  US-259-a Shadow 10min 실행 + 13항목 체크리스트 검증
```

---

## 3. 파일 소유권 매트릭스 (충돌 방지)

| 파일 | 소유자 | 충돌 위험 | 방지 전략 |
|------|--------|---------|---------|
| `src/main.py` | **Leeseo (조율)** | CRITICAL (4명 접근) | 순차 머지 — Phase 4에서 함수 단위 순서대로 적용 |
| `src/modes/shadow.py` | **Yujin 전담** | HIGH (US-257, 258-a, 258-b 모두 해당) | Yujin이 3개 US 묶어서 단일 PR |
| `src/modes/live_gate.py` | Yujin | LOW | — |
| `src/friction/cost_calculator.py` | Gaeul | LOW | — |
| `src/core/signal.py` | Gaeul | MEDIUM (US-248, US-255 교차) | Gaeul이 단일 브랜치에서 처리 |
| `src/tuning/adaptive_threshold.py` | Gaeul | LOW | — |
| `src/strategies/triangular.py` | (검증만) | LOW | Wonyoung 테스트만 |
| `src/strategies/statistical_arb.py` | (검증만) | LOW | Wonyoung 테스트만 |
| `src/strategies/*.py` (5개 나머지) | (검증만) | LOW | Wonyoung 테스트만 |
| `src/ml/hmm_trainer.py` | Liz | LOW | — |
| `src/ml/xgb_trainer.py` | Liz | LOW | — |
| `src/ml/onnx_runtime.py` | Liz | LOW | — |
| `src/ml/feature_pipeline.py` | Liz | LOW | — |
| `src/tuning/scheduled_tuner.py` | Yujin | LOW | — |
| `src/execution/position_recovery.py` | (검증만) | LOW | — |
| `src/infra/compliance.py` | (검증만) | LOW | — |

---

## 4. US별 상세 작업 + AC

### Batch 1 — CRITICAL 버그 + 신규 구현 (병렬)

#### US-257: profit_factor 금액 비율 수정 [이미완료 → 수정 확인]
- **파일**: `src/modes/shadow.py`
- **버그**: `profit_factor = trades_won / trades_lost` (건수 비율) → 의미없는 지표
- **수정**: `profit_factor = total_profit / max(0.01, abs(total_loss))` (금액 비율)
- **Entry Gate 상태**: "이미 완료"로 분류됐으나 수식 확인 필요 — 실제 코드가 금액 비율인지 재확인
- **AC**:
  1. profit_factor가 `total_profit / |total_loss|` (금액 기반) 수식 사용
  2. total_loss=0 시 ZeroDivisionError 없음 (`max(0.01, ...)` 가드)
  3. AdaptiveThreshold.adjust()에 올바른 profit_factor 전달
  4. 테스트 2개: 금액 비율 검증 + 경계값 (loss=0)
- **WIRING AC**:
  - 생성: `_stats.total_profit`, `_stats.total_loss` 필드 존재 확인
  - 주입: `_shadow_adaptive_threshold_loop()` 내 profit_factor 계산식
  - 호출: AdaptiveThreshold.adjust(profit_factor=...) 호출 경로

#### US-258-a: ShadowMiniTuner 활성화 또는 제거 [미완료 → 신규]
- **파일**: `src/modes/shadow.py:685-692`, `src/tuning/scheduled_tuner.py:345-440`
- **현재**: `run_in_thread()` 호출하지만 `shadow_elapsed_seconds` 미전달 → dead call
- **판단 기준**: TF SF Stage 3 결과 기반 (PROVEN → 활성화 / HARMFUL → 제거)
- **AC**:
  1. 활성화 시: `shadow_elapsed_seconds` 올바르게 전달 + 결과 로그 확인
  2. 제거 시: dead code 정리 + 관련 테스트 제거 (회귀 없음 확인)
  3. 어느 경로든 shadow.py 컴파일 오류 없음

#### US-258-b: warm-up 추적 (stat_arb) [부분완료 → 수정]
- **파일**: `src/strategies/statistical_arb.py`, `src/modes/shadow.py`
- **현재**: min_history(120) 미달 시 무음 거절 — Shadow 13항목에서 "trade >= 1" 위반처럼 보임
- **수정**: warm-up 상태 명시적 로그 + Shadow 복합지표에서 warm-up 전략 제외 처리
- **AC**:
  1. warm-up 중 `[stat_arb] warming up: N/120 bars` 로그
  2. Shadow 13항목 체크 11번("전략별 trade >= 1")에서 warm-up 전략 제외
  3. 테스트 2개: min_history 미달 시 스킵 + warm-up 로그

#### US-247: estimate_cost → calculate 통합 [미완료 → 신규]
- **파일**: `src/friction/cost_calculator.py`, `src/strategies/triangular.py`, `src/strategies/spot_futures.py`, `src/strategies/funding_rate.py`
- **버그**: `estimate_cost()`는 taker_fee + network_cost만 — rollback cost, opportunity cost 누락
- **주의**: 슬리피지는 SignalGenerator의 CEXOrderbookSlippage가 유일한 소스 — estimate_cost()에 slippage 추가 금지
- **수정**: estimate_cost()에 `rollback_expected` 포함 (fee + network + rollback, slippage 제외)
- **AC**:
  1. estimate_cost()가 `fee + network + rollback_expected` 반환
  2. slippage는 포함하지 않음 (이중 계산 방지 — PowerLaw k=0.0 유지)
  3. 전략 3개에서 estimate_cost() 호출 경로 확인
  4. 테스트 5개: 각 비용 항목, 이중 슬리피지 방지
- **WIRING AC**:
  - 생성: estimate_cost()에 `rollback_expected` 파라미터 추가
  - 주입: 전략 on_signal() → estimate_cost() 호출
  - 호출: `triangular.py`, `spot_futures.py`, `funding_rate.py` 사용 경로 검증

#### US-248: ADV/sigma 동적 계산 [부분완료 → 수정]
- **파일**: `src/core/signal.py`
- **버그**: `default_adv=1000`, `default_sigma=0.001` 하드코딩
- **수정**: 실 오더북 depth + 가격 변동률에서 ADV/sigma 동적 계산
- **수학**: `impact_fraction = sigma * k * sqrt(size / ADV)` (SSOT §4.1)
- **AC**:
  1. ADV: 최근 N분 오더북 L1~L5 depth 합산 추정
  2. sigma: 최근 N분 mid-price 수익률 표준편차
  3. fallback: 데이터 부족 시 기존 default (1000, 0.001) 유지
  4. ML feature stub 3개 → 실 피처 교체 (signal.py 284-287)
  5. 테스트 4개: 동적 계산, fallback, ML feature 교체
- **WIRING AC**:
  - 생성: `_compute_dynamic_adv()`, `_compute_dynamic_sigma()` 메서드
  - 주입: SignalGenerator에서 PriceHub 참조 보유
  - 호출: calculate() 내 동적 값 전달

#### US-255: AdaptiveThreshold 전략별 분리 [미완료 → 신규]
- **파일**: `src/tuning/adaptive_threshold.py`, `src/core/signal.py`
- **버그**: 글로벌 단일 AdaptiveThreshold 인스턴스 — 전략별 특성 미반영
- **수정**: `Dict[strategy_id, AdaptiveThreshold]` — 전략별 독립 인스턴스
- **AC**:
  1. 전략별 독립 edge_bps 조정 (cross_exchange vs triangular 등)
  2. 기존 글로벌 인스턴스 하위 호환 유지 (default fallback)
  3. Shadow 로그에서 전략별 edge_bps 확인 가능
  4. 테스트 3개: 두 전략 독립 조정, fallback
- **WIRING AC**:
  - 생성: AdaptiveThresholdRegistry 또는 dict 구조
  - 주입: SignalGenerator에서 strategy_id별 threshold 조회
  - 호출: adjust(strategy_id=..., profit_factor=...) 경로

---

### Batch 2 — ML 루프 sleep-first 수정 (순차)

> **핵심 수정 사유**: 현재 HMM/XGBoost trainer가 루프 시작 시 N시간 sleep 후 학습.
> 이로 인해 엔진 시작 직후에는 ML 모델이 없음 → "시작 시 즉시 학습" 패턴으로 수정.

#### US-251: HMM Trainer 루프 [부분완료 → sleep-first 수정]
- **파일**: `src/main.py`, `src/ml/hmm_trainer.py`
- **현재**: background task 등록은 됐으나 루프가 `await asyncio.sleep(86400)` 먼저 실행
- **수정**: `train() → sleep() → train() → ...` 패턴으로 변경 (시작 시 즉시 학습)
- **AC**:
  1. 엔진 시작 직후 HMMTrainer.train() 즉시 1회 실행
  2. 이후 24h 주기 반복
  3. 학습 완료 시 RegimeDetector 모델 hot-swap
  4. 실패 시 기존 모델 유지 (graceful fallback)
  5. hmmlearn 미설치 시 ImportError catch → 건너뜀
  6. 테스트 3개: 즉시 실행, hot-swap, ImportError

#### US-252: XGBoost + ONNX 학습 루프 [부분완료 → sleep-first 수정]
- **파일**: `src/main.py`, `src/ml/xgb_trainer.py`, `src/ml/onnx_runtime.py`
- **현재**: US-251과 동일 패턴 — sleep 먼저
- **수정**: `train() → export_onnx() → sleep() → ...` 패턴 (시작 시 즉시)
- **AC**:
  1. 엔진 시작 직후 XGBTrainer.train() 즉시 1회 실행
  2. ONNX export → ONNXSignalScorer.reload()
  3. xgboost/onnxruntime 미설치 시 건너뜀
  4. 테스트 3개: train→export→load, graceful fallback

---

### Batch 3 — Feature Pipeline 연결 (Batch 2 완료 후)

#### US-253: Feature → ONNX Scorer 연결 [부분완료 → Batch 2 의존]
- **파일**: `src/core/signal.py`, `src/ml/feature_pipeline.py`, `src/ml/canary.py`
- **현재**: signal.py에서 ML feature stub 3개만 (`net_edge, trade_size, sigma`)
- **수정**: MLFeaturePipeline.extract() → 20개 피처 → ONNXSignalScorer.predict_signal()
- **AC**:
  1. signal.py에서 MLFeaturePipeline 20개 피처 추출
  2. ONNX 모델 존재 시 predict_signal() 호출 → confidence 보정
  3. 모델 미존재 시 기존 로직 그대로 (fallback)
  4. MLCanary 단계: DISABLED → SHADOW → PARTIAL → FULL 전환 지원
  5. 테스트 4개: 20개 피처, ONNX 스코어링, canary 단계 전환
- **WIRING AC**:
  - 생성: MLFeaturePipeline.extract() 20개 피처
  - 주입: SignalGenerator에서 feature_pipeline 참조
  - 호출: ONNX 모델 로드 후 predict_signal() 호출 경로

---

### Batch 4 — 이미 완료 US 검증 (Wonyoung 전담)

> Shadow 10min 실행 없이도 단위 테스트로 "존재+연결" 검증 가능한 항목들.
> 테스트 작성 목적: 미래 회귀 방지 + Entry Gate "이미 완료" 주장의 증거 확보.

#### US-245: stat_arb regime_detector 주입 검증
- **검증 방법**: `StatisticalArbStrategy` 생성자 파라미터 확인 + main.py 호출 경로 확인
- **테스트**: regime=CRISIS 시 on_signal() 스킵, None fallback
- **AC**: 테스트 3개 PASS

#### US-246: LiveGate 실행 경로 강제 검증
- **검증 방법**: `EXECUTION_MODE=live` mock → Engine이 LiveGate.evaluate() 호출하는지 확인
- **테스트**: LiveGate FAIL 시 live 차단 + shadow fallback
- **AC**: 테스트 3개 PASS

#### US-249: 삼각 leg sizing 보정 검증
- **검증 방법**: triangular.py에서 각 leg size 단위 확인 (BTC 단위, ETH 단위, ETH 단위)
- **테스트**: 3-leg 순환 후 USDT 잔액 변화 = expected_profit (오차 < 0.1%)
- **AC**: 테스트 4개 PASS

#### US-250: 포지션 리커버리 + 리콘실러 startup 검증
- **검증 방법**: Engine 시작 시 PositionRecovery.scan() 호출 확인 + reconciler background task 등록 확인
- **테스트**: 미정리 포지션 시 recovery 동작, 60초 주기 reconcile
- **AC**: 테스트 3개 PASS

#### US-250-a: ComplianceChecker startup 검증
- **검증 방법**: Engine 시작 시 ComplianceChecker.audit() 자동 실행 확인
- **테스트**: CRITICAL 위반 시 경고 동작
- **AC**: 테스트 2개 PASS

#### US-254: RegimeDetector 전 전략 연결 검증
- **검증 방법**: 6개 전략 생성자에 regime_detector 파라미터 존재 + main.py 전달 확인
- **테스트**: 6개 전략 모두 CRISIS 시 on_signal() 차단, None fallback
- **AC**: 테스트 6개 PASS

#### US-256: peak_equity DB 영속화 검증
- **검증 방법**: Engine 시작 시 DB에서 peak_equity 로드 로직 확인
- **테스트**: DB 저장/복원, DB 미연결 시 메모리 fallback
- **AC**: 테스트 3개 PASS

---

## 5. pytest 전략 (배치별 테스트 범위)

### Batch 1 테스트

| US | 테스트 파일 | 케이스 수 | 핵심 검증 |
|-----|-----------|---------|----------|
| US-257 | `tests/test_shadow_profit_factor.py` | 4 | 금액 비율, 경계값(loss=0), AdaptiveThreshold 연동 |
| US-258-a | `tests/test_shadow_mini_tuner.py` | 2 | 활성화(elapsed 전달) 또는 제거(dead code 없음) |
| US-258-b | `tests/test_stat_arb_warmup.py` | 2 | warm-up 스킵, Shadow 복합지표 제외 |
| US-247 | `tests/test_cost_calculator_v2.py` | 5 | rollback 포함, 이중 슬리피지 방지, 전략별 호출 |
| US-248 | `tests/test_dynamic_adv_sigma.py` | 4 | 동적 계산, fallback, ML feature 교체 |
| US-255 | `tests/test_adaptive_per_strategy.py` | 3 | 전략별 독립 조정, fallback |

### Batch 2~3 테스트

| US | 테스트 파일 | 케이스 수 | 핵심 검증 |
|-----|-----------|---------|----------|
| US-251 | `tests/test_hmm_trainer_loop.py` | 3 | 즉시 실행, hot-swap, ImportError |
| US-252 | `tests/test_xgb_onnx_loop.py` | 3 | train→export→load, graceful fallback |
| US-253 | `tests/test_ml_feature_scoring.py` | 4 | 20 피처, ONNX 스코어링, canary 단계 |

### Batch 4 테스트 (이미완료 검증)

| US | 테스트 파일 | 케이스 수 | 핵심 검증 |
|-----|-----------|---------|----------|
| US-245 | `tests/test_stat_arb_regime.py` | 3 | regime 주입, CRISIS 차단, None fallback |
| US-246 | `tests/test_live_gate_enforce.py` | 3 | 게이트 차단, fallback shadow |
| US-249 | `tests/test_triangular_sizing.py` | 4 | leg별 통화 변환, 순환 잔액 검증 |
| US-250 | `tests/test_position_recovery_startup.py` | 3 | scan, reconcile 주기, Telegram |
| US-250-a | `tests/test_compliance_startup.py` | 2 | audit 실행, CRITICAL 차단 |
| US-254 | `tests/test_regime_all_strategies.py` | 6 | 6개 전략 regime 수신, CRISIS 차단 |
| US-256 | `tests/test_peak_equity_persist.py` | 3 | DB 저장, 복원, fallback |

### 회귀 실행 명령

```bash
# 전체 회귀 (각 배치 완료 시마다)
cd engine && python -m pytest tests/ -x --tb=short

# 빠른 검증 (개별 US 완료 시)
cd engine && python -m pytest tests/test_shadow_profit_factor.py tests/test_live_gate_enforce.py -v

# ML 관련 (hmmlearn/xgboost 없으면 skip)
cd engine && python -m pytest tests/test_hmm_trainer_loop.py tests/test_xgb_onnx_loop.py -v
```

**목표**: 기존 4,920 PASS 유지 + 신규 ~46개 추가 → 4,966+ tests

---

## 6. Shadow 검증 계획 (US-259-a)

### 6.1 실행 명령

```bash
docker compose up -d          # DB 없으면 데이터 미저장
cd engine && timeout 600 python -m src.main
```

### 6.2 Shadow 13항목 복합지표 체크리스트

| # | 체크 | 임계값 | Phase S15 특화 검증 |
|---|------|--------|-------------------|
| 1 | crash | = 0 | 프로세스 exit code 0 |
| 2 | 무중단 실행 | >= 10분 | elapsed_seconds >= 600 |
| 3 | PnL | >= $0 | shadow stats total_pnl |
| 4 | Max Drawdown | < 5% | peak_equity DB 로드 확인 (US-256) |
| 5 | Profit Factor | > 1.0 | **금액 비율 수식 확인** (US-257) |
| 6 | 신호 수 | >= 70 (10min 외삽) | 신호 흐름 정상 |
| 7 | Kill Switch | Not halted | kill_switch.is_halted == False |
| 8 | Circuit Breaker | CLOSED | circuit_breaker.state == CLOSED |
| 9 | 거래소 Health | >= 95% | min(exchange_health_scores) >= 0.95 |
| 10 | loss_capped | = 0 | TRADE_LOSS_CAPPED counter |
| 11 | 전략별 trade | 활성 전략 trade >= 1 | **warm-up 전략 제외** (US-258-b) |
| 12 | 방어 레이어 활성 | CB/StaleDetector/OutlierFilter 로그 >= 1건 | structlog 확인 |
| 13 | 결과 파일 | `.omc/state/shadow-result-latest.json` 존재 | 검증 증거 |

### 6.3 Phase S15 추가 로그 검증

"이미 완료" US의 Shadow 런타임 동작 확인:

```
[stat_arb] regime_detector connected                    # US-245
[live_gate] enforce_live_gate called (live mode)        # US-246
[triangular] leg1=X BTC, leg2=Y ETH, leg3=Z ETH       # US-249 (다른 단위 확인)
[compliance] ComplianceChecker audit PASS               # US-250-a
[cross_exchange] regime_detector connected              # US-254
[peak_equity] loaded from DB: $X                        # US-256
[shadow] profit_factor=X.XX (amount ratio)             # US-257
[hmm_trainer] training started immediately             # US-251
[xgb_trainer] training started immediately             # US-252
[ml_scorer] ONNX model loaded, canary=SHADOW           # US-253
```

---

## 7. 리스크 목록

| # | 리스크 | 영향 | 완화 |
|---|--------|------|------|
| R1 | main.py 동시 수정 충돌 | 4명이 main.py 접근 | Leeseo가 Phase 4 순차 머지 전담 |
| R2 | "이미 완료" US가 실제로 미연결 | Batch 4 테스트 실패 | 테스트 실패 즉시 해당 US를 Batch 1로 격상 |
| R3 | ML 학습 데이터 부재 | HMM/XGBoost 학습 실패 | mock 데이터 단위 테스트 + Shadow 중 실 데이터 축적 |
| R4 | 이중 슬리피지 재발 | US-247 cost 통합 시 | estimate_cost()에 slippage 불포함 명시 + 테스트 |
| R5 | hmmlearn/xgboost 미설치 | ImportError | try/except + graceful skip, 핵심 기능 영향 없음 |
| R6 | warm-up 전략이 Shadow 13항목 위반처럼 보임 | false alarm | US-258-b로 명시적 제외 처리 |
| R7 | profit_factor가 이미 금액 비율이면 불필요한 수정 | — | Yujin이 코드 먼저 확인 후 수정 여부 결정 |

---

## 8. 성공 기준

1. **전체 테스트**: 4,966+ passed, 0 failed
2. **Shadow 13항목**: 전항목 PASS (profit_factor 금액 비율 포함)
3. **CRITICAL 0건**: 6개 CRITICAL 전부 해소
4. **수학 오류 0건**: 3개 수학 버그 전부 해소
5. **ML 연결**: HMM/XGBoost/ONNX background loop 등록 (ImportError 시 graceful skip)
6. **LiveGate 차단**: live mode 진입 시 6-check 강제 + 미통과 차단 확인
7. **회귀 없음**: 기존 4,920 테스트 전부 PASS

---

## 9. Assembly Gate 체크리스트 (C-Step 6)

> Phase 완료 후 코드리뷰(BLACKPINK) 전 필수 검증

- [ ] **init chain**: 신규/수정 컴포넌트가 Engine.run() 경로에서 올바르게 초기화
  - HMM/XGBoost background task 등록 경로
  - AdaptiveThreshold registry 초기화
  - MLFeaturePipeline 인스턴스 생성
- [ ] **signal flow**: RegimeDetector → 6개 전략, ML Scorer → SignalGenerator 연결 확인
- [ ] **dead wiring**: 미사용 import, 미호출 메서드 없음
  - US-258-a 제거 선택 시 dead code 완전 제거 확인
- [ ] **config audit**: 새 env var, 새 config 키 `.env.example` + SSOT.md 동기화

---

## 10. 핵심 아키텍처 주의사항 (SSOT §4 기반)

1. **이중 슬리피지 절대 금지**: `CEXOrderbookSlippage`는 `SignalGenerator` 전용. `PaperExecutor`에 PowerLaw(k=0) 적용 유지.
2. **ENGINE_ENV**: `dev|staging|prod|test`만 허용 (`development` 사용 금지).
3. **KRW 거래소**: upbit, bithumb, coinone은 KRW 페어 자동 매핑. `min_exchanges=3` 필수.
4. **friction prefix**: cost_calculator가 `paper_`/`sandbox_` prefix 자동 strip.
5. **ML import 방어**: `try: import hmmlearn` / `try: import xgboost` — 미설치 시 graceful skip.
6. **main.py 머지 순서**: LiveGate(Yujin) → 전략(Gaeul/Liz) → ML loop(Liz) → 인프라(Leeseo) — 역순 금지.
