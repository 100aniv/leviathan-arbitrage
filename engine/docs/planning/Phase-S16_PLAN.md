# Phase S16 PLAN.md — 동적 임계치 + 고급 기능

> **Phase**: S16
> **US 범위**: 이월 6 US (US-248, US-250, US-253, US-256, US-258-b, US-259-a) + 고유 6 US (US-260~265) = 12 US
> **목표**: S15 미구현 이월 US 완료 + 동적 임계치(롤링 백분위수/변동성 가중치/Funding Rate) + Regime 파라미터 매트릭스 + CorrelationMonitor 강제 적용
> **선행 조건**: Phase S15 VERIFIED (CRITICAL 6 + Math 3 수정 완료, 4,940 tests PASS)
> **작성일**: 2026-03-19
> **계획서 기반**: `.claude/plans/parallel-finding-sparrow.md` (7 Phase, 63 US)

---

## 0. Entry Gate 분류 (Karina 분석 결과 반영)

| 상태 | US | 판단 근거 |
|------|----|---------|
| **이월 — 수정 필요** | US-248, US-250, US-253, US-256, US-258-b | S15에서 "이미 완료"로 분류됐으나 런타임 증거 미확보. 외과적 수정 필요. |
| **이월 — 통합 검증** | US-259-a | 이월 US 전부 완료 후 Shadow 10min |
| **신규 — 구현** | US-260, US-261, US-262, US-263, US-264 | rolling percentile/vol weight/regime matrix/corr enforce |
| **신규 — 통합 검증** | US-265 | 신규 US 전부 완료 후 Shadow 10min |

### Entry Gate 판단 상세

| US | Entry Gate 판단 | 작업 방향 |
|----|----------------|---------|
| US-248 | ADV/sigma 동적 계산 코드 존재 확인 필요. `signal.py`에 `_compute_dynamic_adv()` 메서드 여부 미확인. | fallback 기본값(1000, 0.001) 대비 동적 계산 경로 추가 + 런타임 증거 |
| US-250 | `PositionRecovery.scan()` + `Reconciler` background task 등록 확인. main.py 호출 경로 검증. | startup 연결 확인 + 런타임 로그 확보 |
| US-253 | `MLFeaturePipeline` 10개 stub → 20개 실 피처 교체. `signal.py` ONNX 연결. | 10→20 피처 교체 + signal.py canary 연동 |
| US-256 | peak_equity JSON → TimescaleDB 전환. 현재 메모리 fallback 동작 중. | DB 저장/로드 경로 구현 |
| US-258-b | stat_arb warm-up(min_history=120) 무음 거절 → 명시적 로그 + Shadow 체크 제외 처리 | 로그 추가 + 복합지표 체크 11번 수정 |
| US-259-a | 이월 5개 US 완료 후 Shadow 10min 통합 검증 | 13항목 복합지표 전체 PASS |
| US-260 | `cross_exchange.py`, `futures_futures.py` 에 rolling percentile 미구현 | 신규 구현 — 50th 백분위수 기준 동적 edge_bps |
| US-261 | `spot_futures.py` basis rolling 미구현 | 신규 구현 — basis 히스토리 기반 동적 threshold |
| US-262 | `funding_rate.py` 동적 threshold 미구현 | 신규 구현 — funding rate 분포 백분위수 기반 |
| US-263 | `regime_detector.py` 4개 레짐 정의됨, min_edge만 매트릭스화. 전략별 full matrix 미구현 | 기존 edge_bps 매트릭스 → max_position/stop_loss/timeout 포함 확장 |
| US-264 | `CorrelationMonitor` 존재, `guardian.py`에 연결됨. 위반 시 log만 — enforce(차단) 미구현 | log → enforce 전환 (상관도 > threshold 시 신호 차단) |
| US-265 | 신규 US-260~264 전부 완료 후 Shadow 10min | 13항목 복합지표 전체 PASS |

---

## 1. 실행 배치 구성 (의존성 + Entry Gate 기반)

### 1.1 배치 요약

| 배치 | US | 테마 | 병렬 여부 | 담당 |
|------|----|------|---------|------|
| **Batch 1** | US-248, US-250, US-256, US-258-b | 이월 — 독립 수정 | 4개 병렬 | Yujin + Gaeul |
| **Batch 2** | US-253 | 이월 — ML 연결 (Batch 1 독립 진행) | Liz 순차 | Liz |
| **Batch 3** | US-259-a | 이월 통합 Shadow 10min | Batch 1+2 완료 후 | 전원 |
| **Batch 4** | US-260, US-261, US-262, US-263, US-264 | 신규 동적 임계치 + 고급 기능 | 5개 병렬 | Yujin + Gaeul + Leeseo |
| **Batch 5** | US-265 | 신규 통합 Shadow 10min | Batch 4 완료 후 | 전원 |

### 1.2 의존성 그래프

```
Batch 1 (병렬 — 이월 독립)
├── US-248: ADV/sigma 동적 계산 (signal.py)
├── US-250: PositionRecovery + Reconciler startup 검증
├── US-256: peak_equity JSON→TimescaleDB 전환
└── US-258-b: stat_arb warm-up 로그 + Shadow 체크 제외

Batch 2 (Batch 1 병렬 진행 — ML 독립)
└── US-253: Feature Pipeline 10→20 피처 + ONNX Scorer 연결

Batch 3 (Batch 1 + 2 완료 후)
└── US-259-a: 이월 통합 Shadow 10min (13항목)

Batch 4 (Batch 3 PASS 후 — 병렬)
├── US-260: rolling percentile + vol weight (cross_exchange, futures_futures)
├── US-261: rolling percentile + vol weight (spot_futures basis)
├── US-262: Funding Rate 동적 임계치
├── US-263: Regime 파라미터 매트릭스 확장 (min_edge → full matrix)
└── US-264: CorrelationMonitor log → enforce 전환

Batch 5 (Batch 4 완료 후)
└── US-265: 신규 통합 Shadow 10min (13항목)
```

---

## 2. TeamCreate 구성 (IVE 팀원 배정)

### 2.1 팀원별 소유 영역

| 팀원 | 역할 | 배치 | 담당 US | 소유 파일 |
|------|------|------|---------|---------|
| **Yujin** | executor | Batch 1 + 4 | US-256, US-258-b, US-260, US-262 | `shadow.py`, `statistical_arb.py`, `cross_exchange.py`, `funding_rate.py` |
| **Gaeul** | executor | Batch 1 + 4 | US-248, US-263, US-264 | `signal.py`, `regime_detector.py`, `correlation_monitor.py`, `guardian.py` |
| **Liz** | executor | Batch 2 + 4 | US-253, US-261 | `feature_pipeline.py`, `onnx_runtime.py`, `signal.py (ML section)`, `spot_futures.py` |
| **Leeseo** | executor | Batch 4 | US-250, US-265 (Shadow 조율) | `main.py`, `position_recovery.py`, `reconciler.py` |
| **Wonyoung** | test-engineer | 전 배치 | 각 US 테스트 파일 | `tests/test_*.py` |

### 2.2 실행 타임라인

```
Phase 1 (동시 시작):
  Yujin  → US-256 (peak_equity DB 전환) + US-258-b (warm-up 로그)
  Gaeul  → US-248 (ADV/sigma 동적 계산)
  Liz    → US-253 (20 피처 + ONNX 연결)
  Leeseo → US-250 (PositionRecovery startup 확인)
  Wonyoung → 각 US 테스트 파일 작성

Phase 2 (Batch 3 — 전원):
  US-259-a Shadow 10min → 13항목 체크리스트 PASS 확인

Phase 3 (Batch 4 — 동시 시작):
  Yujin  → US-260 (cross_exchange/futures_futures rolling percentile)
           + US-262 (funding rate 동적 threshold)
  Gaeul  → US-263 (regime matrix 확장)
           + US-264 (CorrelationMonitor enforce)
  Liz    → US-261 (spot_futures basis rolling)
  Leeseo → main.py 순차 머지 조율

Phase 4 (Batch 5 — 전원):
  US-265 Shadow 10min → 13항목 체크리스트 PASS 확인
```

---

## 3. 파일 소유권 매트릭스 (충돌 방지)

| 파일 | 소유자 | 충돌 위험 | 방지 전략 |
|------|--------|---------|---------|
| `src/main.py` | **Leeseo (조율)** | CRITICAL | 순차 머지 — Phase 3 완료 후 함수 단위 적용 |
| `src/core/signal.py` | Gaeul (ADV/sigma) + Liz (ML section) | HIGH | 섹션 분리 — Gaeul: `_compute_dynamic_*()` / Liz: `_ml_score()` |
| `src/modes/shadow.py` | Yujin | LOW | warm-up 체크 11번 수정 전담 |
| `src/ml/feature_pipeline.py` | Liz | LOW | — |
| `src/ml/onnx_runtime.py` | Liz | LOW | — |
| `src/strategies/cross_exchange.py` | Yujin | LOW | — |
| `src/strategies/futures_futures.py` | Yujin | LOW | — |
| `src/strategies/spot_futures.py` | Liz | LOW | — |
| `src/strategies/funding_rate.py` | Yujin | LOW | — |
| `src/strategies/statistical_arb.py` | Yujin | LOW | warm-up 로그 추가 |
| `src/tuning/regime_detector.py` | Gaeul | LOW | matrix dict 확장 |
| `src/risk/correlation_monitor.py` | Gaeul | LOW | enforce 메서드 추가 |
| `src/risk/guardian.py` | Gaeul | LOW | CorrelationMonitor 호출 경로 |
| `src/modes/progressive_shadow.py` | Leeseo | LOW | peak_equity DB 로드 연동 |
| `src/execution/position_recovery.py` | Leeseo | LOW | startup scan 확인 |

---

## 4. US별 상세 작업 + AC

### Batch 1 — 이월 독립 수정 (병렬)

#### US-248: ADV/sigma 동적 계산 [이월 → 수정]
- **파일**: `src/core/signal.py`
- **현재**: `default_adv=1000`, `default_sigma=0.001` 하드코딩. 동적 계산 메서드 미확인.
- **수정**: `_compute_dynamic_adv()` — 오더북 L1~L5 depth 합산. `_compute_dynamic_sigma()` — 최근 N분 mid-price 수익률 표준편차. 데이터 부족 시 fallback 유지.
- **Entry Gate 특화**: 런타임에서 실제 동적 값이 사용되는지 로그 증거 필수.
- **AC**:
  1. `_compute_dynamic_adv(orderbook)` — L1~L5 bid+ask depth 합산 → ADV 추정
  2. `_compute_dynamic_sigma(price_history)` — N분 수익률 표준편차 계산
  3. 데이터 부족(N < min_window) 시 기존 default(1000, 0.001) fallback
  4. `impact_fraction = sigma * k * sqrt(size / ADV)` 수식 적용 (SSOT §4.1)
  5. Shadow 로그: `[signal] dynamic adv=X sigma=Y` 1회 이상
  6. 테스트 4개: 동적 계산, fallback(데이터 부족), 수식 검증, signal.py 컴파일 오류 없음
- **WIRING AC**:
  - 생성: `_compute_dynamic_adv()`, `_compute_dynamic_sigma()` 메서드 존재
  - 주입: SignalGenerator.calculate() 내 동적 값 사용
  - 호출: orderbook depth 데이터 → 슬리피지 계산 경로 추적

#### US-250: PositionRecovery + Reconciler startup 검증 [이월 → 수정]
- **파일**: `src/main.py`, `src/execution/position_recovery.py`
- **현재**: 코드 존재 확인됨. startup 시 자동 호출 여부 미검증.
- **수정**: Engine 시작 시 `PositionRecovery.scan()` 1회 실행 + `Reconciler` background task 등록 확인. 런타임 로그 확보.
- **AC**:
  1. Engine.run() 시작 시 `PositionRecovery.scan()` 자동 1회 실행
  2. Reconciler background task 60초 주기 등록 확인
  3. 미정리 포지션 감지 시 Telegram 알림 발송
  4. Shadow 로그: `[position_recovery] scan completed` 확인
  5. 테스트 3개: startup scan, reconcile 주기, Telegram 알림
- **WIRING AC**:
  - 생성: `PositionRecovery` 인스턴스 Engine._setup()에서 생성
  - 주입: Engine.run() → scan() 직접 호출
  - 호출: background task 등록 경로 (`asyncio.create_task` 또는 `run_forever`)

#### US-256: peak_equity JSON → TimescaleDB 전환 [이월 → 수정]
- **파일**: `src/modes/shadow.py`, `src/modes/progressive_shadow.py`
- **현재**: peak_equity 메모리 또는 JSON 파일 저장. 재시작 시 유실.
- **수정**: TimescaleDB `shadow_metrics` 테이블에 peak_equity 저장/로드. DB 미연결 시 메모리 fallback.
- **AC**:
  1. Shadow 시작 시 DB에서 peak_equity 로드 (`SELECT MAX(peak_equity) FROM shadow_metrics`)
  2. 매 업데이트 시 DB INSERT/UPDATE
  3. DB 미연결 시 메모리 fallback (crash 없음)
  4. Shadow 로그: `[peak_equity] loaded from DB: $X` 또는 `no prior peak_equity`
  5. 테스트 3개: DB 저장, 복원, fallback
- **WIRING AC**:
  - 생성: DB 쿼리 함수 `_load_peak_equity_from_db()`, `_save_peak_equity_to_db()`
  - 주입: ShadowMode.__init__() 에서 DB 연결 참조
  - 호출: `_update_stats()` 내 peak_equity 갱신 경로

#### US-258-b: stat_arb warm-up 상태 추적 [이월 → 수정]
- **파일**: `src/strategies/statistical_arb.py`, `src/modes/shadow.py`
- **현재**: min_history(120) 미달 시 무음 거절. Shadow 13항목 체크 11번("전략별 trade >= 1")에서 위반처럼 보임.
- **수정**: warm-up 로그 추가 + Shadow 복합지표 체크 11번에서 warm-up 전략 명시적 제외.
- **AC**:
  1. warm-up 중 매 10 bar: `[stat_arb] warming up: N/120 bars` 로그
  2. Shadow 체크 11번: warm-up 전략 목록 별도 추적, PASS 판정에서 제외
  3. warm-up 완료 후 첫 신호: `[stat_arb] warm-up complete, first signal generated` 로그
  4. 테스트 2개: min_history 미달 시 스킵 + warm-up 로그

---

### Batch 2 — ML 연결 (Liz 전담, Batch 1 병렬 진행)

#### US-253: Feature Pipeline 10→20 피처 + ONNX Scorer 연결 [이월 → 수정]
- **파일**: `src/core/signal.py`, `src/ml/feature_pipeline.py`, `src/ml/canary.py`
- **현재**: signal.py에 ML feature stub 3개 (`net_edge, trade_size, sigma`). MLFeaturePipeline 10개 피처.
- **수정**: MLFeaturePipeline.extract() → 20개 피처 → ONNXSignalScorer.predict_signal() → confidence 보정.
- **AC**:
  1. MLFeaturePipeline.extract() 20개 피처 반환 (기존 10개 + 10개 추가)
  2. signal.py: ONNX 모델 존재 시 predict_signal() 호출 → edge_bps 보정
  3. ONNX 모델 미존재 시 기존 로직 그대로 (fallback, crash 없음)
  4. MLCanary 단계: DISABLED → SHADOW → PARTIAL → FULL 지원
  5. Shadow 로그: `[ml_scorer] canary=SHADOW score=0.XX`
  6. 테스트 4개: 20 피처 추출, ONNX 스코어링, canary 단계 전환, fallback
- **WIRING AC**:
  - 생성: MLFeaturePipeline.extract() 20개 피처 목록 문서화
  - 주입: SignalGenerator에서 feature_pipeline 참조 보유
  - 호출: ONNX 모델 로드 → predict_signal() 경로 추적

---

### Batch 3 — 이월 통합 Shadow (Batch 1+2 완료 후)

#### US-259-a: S15 이월 통합 Shadow 10min 검증
- **실행**: `docker compose up -d && cd engine && timeout 600 python -m src.main`
- **13항목 체크리스트**:

| # | 체크 | 임계값 | S16 이월 특화 |
|---|------|--------|-------------|
| 1 | crash | = 0 | — |
| 2 | 무중단 실행 | >= 10분 | — |
| 3 | PnL | >= $0 | — |
| 4 | MDD | < 5% | peak_equity DB 로드 확인 (US-256) |
| 5 | Profit Factor | > 1.0 | 금액 비율 수식 (S15-US-257 확인) |
| 6 | 신호 수 | >= 70/10min | — |
| 7 | Kill Switch | Not halted | — |
| 8 | Circuit Breaker | CLOSED | — |
| 9 | 거래소 Health | >= 95% | — |
| 10 | loss_capped | = 0 | — |
| 11 | 전략별 trade | 활성 전략 >= 1 | **warm-up 전략 제외** (US-258-b) |
| 12 | 방어 레이어 활성 | 로그 >= 1건 | — |
| 13 | 결과 파일 | `.omc/state/shadow-result-latest.json` | — |

- **추가 로그 검증**:
  ```
  [signal] dynamic adv=X sigma=Y                         # US-248
  [position_recovery] scan completed                      # US-250
  [peak_equity] loaded from DB: $X                        # US-256
  [stat_arb] warming up: N/120 bars                       # US-258-b
  [ml_scorer] canary=SHADOW score=0.XX                    # US-253
  ```

---

### Batch 4 — 신규 동적 임계치 + 고급 기능 (병렬, Batch 3 PASS 후)

#### US-260: 롤링 백분위수 + 변동성 가중치 (cross_exchange, futures_futures) [신규]
- **파일**: `src/strategies/cross_exchange.py`, `src/strategies/futures_futures.py`
- **수학**: `dynamic_edge = base_edge * (1 + vol_ratio)` 여기서 `vol_ratio = current_vol / historical_vol_50pct`
- **구현**: 최근 N분 spread 히스토리 → 50th 백분위수 기준 동적 min_edge_bps 조정
- **AC**:
  1. spread 히스토리 deque(maxlen=N) 유지 — N=60(기본), 환경변수 오버라이드 가능
  2. 50th 백분위수 초과 시 edge_bps 상향 (최대 2x), 미달 시 하향 (최소 0.5x)
  3. 히스토리 부족(< min_window) 시 기존 static edge_bps fallback
  4. Shadow 로그: `[cross_exchange] dynamic edge=Xbps (vol_ratio=Y)`
  5. 테스트 4개: 동적 조정, 2x 상한, 0.5x 하한, fallback
- **WIRING AC**:
  - 생성: `_rolling_percentile(data, p=50)` 유틸 함수
  - 주입: on_signal() 내 edge_bps 계산 시 동적 값 사용
  - 호출: 각 신호마다 spread 히스토리 업데이트 + 동적 edge 계산

#### US-261: 롤링 백분위수 + 변동성 가중치 (spot_futures basis) [신규]
- **파일**: `src/strategies/spot_futures.py`
- **수학**: basis = futures_price - spot_price. `dynamic_threshold = base_threshold * (1 + basis_vol_ratio)`
- **구현**: basis 히스토리 50th 백분위수 기반 동적 threshold 조정
- **AC**:
  1. basis 히스토리 deque(maxlen=60) 유지
  2. 현재 basis가 50th 백분위수 초과 시 threshold 상향 (최대 2x)
  3. 히스토리 부족 시 static threshold fallback
  4. Shadow 로그: `[spot_futures] dynamic threshold=Xbps (basis_ratio=Y)`
  5. 테스트 3개: 동적 조정, fallback, 경계값

#### US-262: Funding Rate 동적 임계치 [신규]
- **파일**: `src/strategies/funding_rate.py`
- **수학**: funding rate 분포에서 75th 백분위수 초과 시에만 신호 발생 (극단 funding만 차익 가능)
- **구현**: funding rate 히스토리 → 75th 백분위수 동적 threshold
- **AC**:
  1. funding rate 히스토리 deque(maxlen=288) — 8h * 36회 = 3일치
  2. 현재 funding rate가 75th 백분위수 미달 시 신호 차단
  3. 히스토리 부족(< 24) 시 static threshold fallback
  4. Shadow 로그: `[funding_rate] dynamic threshold=X% (75th pct=Y%)`
  5. 테스트 4개: 75th 초과 신호, 미달 차단, 히스토리 축적, fallback
- **WIRING AC**:
  - 생성: `_compute_funding_threshold()` 메서드
  - 주입: on_signal() 내 funding rate 비교 시 동적 threshold 사용
  - 호출: FundingRateCollector → 수신 시 히스토리 업데이트

#### US-263: Regime별 파라미터 매트릭스 확장 [신규]
- **파일**: `src/tuning/regime_detector.py`, `src/strategies/cross_exchange.py` (+ 5개 전략)
- **현재**: `min_edge_bps`만 regime별 정의됨 (4개 레짐 × 1개 파라미터)
- **수정**: `max_position_size`, `stop_loss_bps`, `signal_timeout_s` 추가 → full 매트릭스
- **AC**:
  1. regime_detector.py에 `REGIME_PARAMS` 딕셔너리:
     ```python
     {
       "LOW":    {min_edge:5, max_pos:1.0, stop_loss:50, timeout:60},
       "MEDIUM": {min_edge:8, max_pos:0.7, stop_loss:40, timeout:45},
       "HIGH":   {min_edge:12, max_pos:0.4, stop_loss:30, timeout:30},
       "CRISIS": {min_edge:15, max_pos:0.0, stop_loss:20, timeout:0},
     }
     ```
  2. 각 전략 on_signal()에서 `regime_detector.get_params(regime)` 조회 후 적용
  3. CRISIS 시 `max_pos=0.0` → 신호 차단
  4. Shadow 로그: `[cross_exchange] regime=HIGH params=min_edge:12 max_pos:0.4`
  5. 테스트 6개: 4개 레짐 파라미터 적용, CRISIS 차단, None fallback
- **WIRING AC**:
  - 생성: `RegimeDetector.get_params(regime)` → dict 반환 메서드
  - 주입: 6개 전략 on_signal() 내 파라미터 조회
  - 호출: 신호 생성 전 regime 파라미터 적용 경로 추적

#### US-264: CorrelationMonitor log → enforce 전환 [신규]
- **파일**: `src/risk/correlation_monitor.py`, `src/risk/guardian.py`, `src/main.py`
- **현재**: `guardian.py`에 CorrelationMonitor 연결됨. 위반 시 `logger.warning()` 만 발생.
- **수정**: 상관도 > threshold(0.7) 시 신호 차단 (GuardianRule.enforce). 기존 log 동작은 warn 레벨 유지.
- **AC**:
  1. CorrelationMonitor.check_and_enforce(signals) → 고상관 쌍 신호 필터링
  2. 상관도 > 0.7: 상관 쌍 중 수익 낮은 신호 DROP + 로그: `[correlation] enforced: dropped X (corr=0.XX)`
  3. 상관도 <= 0.7: 기존 동작 유지 (warning만)
  4. Guardian.apply_rules() 호출 체인에 CorrelationMonitor.enforce 포함
  5. Shadow 로그: `[correlation] enforced:` 1회 이상 (실 데이터에서 고상관 쌍 존재 시)
  6. 테스트 4개: 고상관 차단, 저상관 통과, drop 우선순위 (수익 낮은 쪽), Guardian 통합
- **WIRING AC**:
  - 생성: `CorrelationMonitor.enforce(signals) → filtered_signals` 메서드
  - 주입: `Guardian.apply_rules()` 내 CorrelationMonitor.enforce 호출
  - 호출: SignalRouter → Guardian → CorrelationMonitor 경로 추적

---

### Batch 5 — 신규 통합 Shadow (Batch 4 완료 후)

#### US-265: S16 통합 Shadow 10min 검증
- **실행**: `docker compose up -d && cd engine && timeout 600 python -m src.main`
- **13항목 체크리스트** (이월 Shadow와 동일 기준):

| # | 체크 | 임계값 | S16 신규 특화 |
|---|------|--------|-------------|
| 1 | crash | = 0 | — |
| 2 | 무중단 실행 | >= 10분 | — |
| 3 | PnL | >= $0 | — |
| 4 | MDD | < 5% | — |
| 5 | Profit Factor | > 1.0 | — |
| 6 | 신호 수 | >= 70/10min | 동적 threshold 적용 후 신호 감소 허용 범위 확인 |
| 7 | Kill Switch | Not halted | — |
| 8 | Circuit Breaker | CLOSED | — |
| 9 | 거래소 Health | >= 95% | — |
| 10 | loss_capped | = 0 | — |
| 11 | 전략별 trade | 활성 전략 >= 1 | warm-up 제외. 동적 threshold로 신호 0인 전략 → MEDIUM 레짐 확인 |
| 12 | 방어 레이어 활성 | 로그 >= 1건 | CorrelationMonitor enforce 로그 확인 (US-264) |
| 13 | 결과 파일 | `.omc/state/shadow-result-latest.json` | — |

- **추가 로그 검증 (신규 US)**:
  ```
  [cross_exchange] dynamic edge=Xbps (vol_ratio=Y)        # US-260
  [spot_futures] dynamic threshold=Xbps (basis_ratio=Y)   # US-261
  [funding_rate] dynamic threshold=X% (75th pct=Y%)       # US-262
  [cross_exchange] regime=HIGH params=min_edge:12 max_pos:0.4  # US-263
  [correlation] enforced: dropped X (corr=0.XX)           # US-264
  ```

---

## 5. pytest 전략 (배치별 테스트 범위)

### Batch 1 테스트

| US | 테스트 파일 | 케이스 수 | 핵심 검증 |
|-----|-----------|---------|----------|
| US-248 | `tests/test_dynamic_adv_sigma.py` | 4 | 동적 계산, fallback, 수식 검증 |
| US-250 | `tests/test_position_recovery_startup.py` | 3 | startup scan, reconcile 주기, Telegram |
| US-256 | `tests/test_peak_equity_persist.py` | 3 | DB 저장, 복원, fallback |
| US-258-b | `tests/test_stat_arb_warmup.py` | 2 | warm-up 스킵, Shadow 제외 |

### Batch 2 테스트

| US | 테스트 파일 | 케이스 수 | 핵심 검증 |
|-----|-----------|---------|----------|
| US-253 | `tests/test_ml_feature_scoring.py` | 4 | 20 피처, ONNX 스코어링, canary 단계 |

### Batch 4 테스트

| US | 테스트 파일 | 케이스 수 | 핵심 검증 |
|-----|-----------|---------|----------|
| US-260 | `tests/test_rolling_percentile_cross.py` | 4 | 동적 조정, 2x/0.5x 한계, fallback |
| US-261 | `tests/test_rolling_percentile_spot.py` | 3 | basis 동적, fallback, 경계값 |
| US-262 | `tests/test_funding_dynamic_threshold.py` | 4 | 75th 초과/미달, 히스토리, fallback |
| US-263 | `tests/test_regime_param_matrix.py` | 6 | 4 레짐 파라미터, CRISIS 차단, None fallback |
| US-264 | `tests/test_correlation_enforce.py` | 4 | 고상관 차단, 저상관 통과, drop 우선순위 |

### 회귀 실행 명령

```bash
# 전체 회귀 (각 배치 완료 시마다)
cd engine && python -m pytest tests/ -x --tb=short

# 빠른 검증 (이월 US 완료 시)
cd engine && python -m pytest tests/test_dynamic_adv_sigma.py tests/test_peak_equity_persist.py tests/test_stat_arb_warmup.py -v

# 신규 US 검증 (Batch 4 완료 시)
cd engine && python -m pytest tests/test_rolling_percentile_cross.py tests/test_regime_param_matrix.py tests/test_correlation_enforce.py -v
```

**목표**: 기존 4,940 PASS 유지 + 신규 ~37개 추가 → 4,977+ tests

---

## 6. Assembly Gate 체크리스트 (C-Step 1)

> Phase 완료 후 코드리뷰 전 필수 검증 (Assembly Verifier)

- [ ] **init chain**: 신규/수정 컴포넌트가 Engine.run() 경로에서 올바르게 초기화
  - `_compute_dynamic_adv/sigma()` → SignalGenerator 내 호출
  - peak_equity DB 로드 → ShadowMode.__init__()
  - rolling percentile 히스토리 → 각 전략 __init__() deque 초기화
  - RegimeDetector.get_params() → 6개 전략 on_signal() 호출
  - CorrelationMonitor.enforce() → Guardian.apply_rules() 체인
- [ ] **signal flow**: 신호 생성 → 동적 threshold 적용 → CorrelationMonitor 필터 → Guardian → Executor
- [ ] **dead wiring**: 미사용 import, 미호출 메서드 없음
  - 기존 static edge_bps 변수가 동적 계산으로 완전히 대체됐는지 확인
- [ ] **config audit**: 새 env var(`ROLLING_WINDOW_MINUTES`, `CORR_THRESHOLD` 등) `.env.example` + SSOT.md 동기화

---

## 7. 리스크 목록

| # | 리스크 | 영향 | 완화 |
|---|--------|------|------|
| R1 | 동적 threshold로 신호 과다 억제 | Shadow 13항목 체크 6번(신호 수 >= 70) 위반 | 조정 범위 0.5x~2x 한계 설정, fallback static 보장 |
| R2 | CRISIS regime max_pos=0.0 → 전 전략 신호 0 | 체크 11번(전략별 trade >= 1) 위반처럼 보임 | CRISIS 레짐은 warm-up과 동일하게 체크 11번 제외 처리 |
| R3 | signal.py 동시 수정 (Gaeul + Liz) | 병합 충돌 | 섹션 분리 — `_compute_dynamic_*` vs `_ml_score` 함수명 사전 합의 |
| R4 | CorrelationMonitor enforce 후 신호 수 급감 | Shadow 성능 저하 | threshold 0.7 조정 가능하도록 env var화 (`CORR_THRESHOLD`) |
| R5 | Funding Rate 75th threshold로 신호 거의 없음 | spot_futures와 유사 신호 감소 | min_history=24 미달 시 static fallback 보장 |
| R6 | TimescaleDB 미연결 시 US-256 peak_equity 소실 | MDD 계산 오류 | 메모리 fallback 명시 + 테스트로 보장 |
| R7 | ML feature 10→20 교체 후 ONNX 모델 미존재 | predict_signal() 호출 불가 | 모델 미존재 시 fallback 경로 단위 테스트 필수 |

---

## 8. 성공 기준

1. **전체 테스트**: 4,977+ passed, 0 failed
2. **이월 Shadow (US-259-a)**: 13항목 전항목 PASS (warm-up 전략 제외 처리 포함)
3. **신규 Shadow (US-265)**: 13항목 전항목 PASS (동적 threshold 신호 감소 허용 범위 내)
4. **동적 임계치 3종**: rolling percentile(cross/futures_futures/spot_futures) + funding 75th + regime matrix 전부 Shadow 로그 증거
5. **CorrelationMonitor enforce**: Shadow 로그에서 enforce 1회 이상 확인 (실 데이터 고상관 쌍 존재 시)
6. **이월 US 완료**: US-248/250/253/256/258-b 런타임 로그 증거 확보
7. **회귀 없음**: 기존 4,940 테스트 전부 PASS

---

## 9. 핵심 아키텍처 주의사항 (SSOT §4 기반)

1. **이중 슬리피지 절대 금지**: US-248 ADV/sigma 동적 계산에 slippage 추가 금지. `CEXOrderbookSlippage`는 `SignalGenerator` 전용.
2. **동적 threshold fallback 보장**: 히스토리 부족 시 static 값 유지 — crash 없이 graceful degradation.
3. **CRISIS regime 체크 11번 예외**: max_pos=0.0으로 trade=0인 경우 warm-up과 동일하게 Shadow 체크 제외.
4. **ENGINE_ENV**: `dev|staging|prod|test`만 허용.
5. **signal.py 섹션 분리 합의**: Gaeul(`_compute_dynamic_*`) / Liz(`_ml_score*`) — 함수명 사전 합의 후 병렬 작업.
6. **CorrelationMonitor threshold**: 기본값 0.7, `CORR_THRESHOLD` env var 오버라이드 가능.
7. **Regime 파라미터 딕셔너리 위치**: `regime_detector.py` 내 `REGIME_PARAMS` 상수 — 전략 파일에 하드코딩 금지.
