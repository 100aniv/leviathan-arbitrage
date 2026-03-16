# Phase S10: Strategy Architecture Hardening — PLAN.md

> **Phase**: S10 | **US**: US-187 ~ US-202 (16개) | **작성일**: 2026-03-17
> **작성자**: Planner (Giselle) | **Entry Gate**: Karina APPROVED
> **SSOT 참조**: §7 Phase S10, §9 CRITICAL 3 + HIGH 3 + MEDIUM 1

---

## 1. 목표 및 회귀 사유

### 1.1 회귀 사유

TF Semi-Final Stage 2 (2H Shadow) **PnL -$78.82 FAIL** (2026-03-16).

| 지표 | 값 |
|------|-----|
| Stage 1 (1H) | PnL +$18.18 PASS |
| Stage 2 (2H) | PnL -$78.82 **FAIL** |
| 최대 손실 전략 | stat_arb: -$127 |
| stat_arb 제외 시 | +$48 (양수 전환) |

### 1.2 근본 원인 4가지

| # | 원인 | 심각도 | 해결 US |
|---|------|--------|---------|
| RC-1 | **전략 영역 겹침**: `_CROSS_EXCHANGE_CONSUMERS` frozenset이 stat_arb+latency_arb에 cross_exchange 신호 라우팅 → 중복 거래 | CRITICAL | US-187, US-194 |
| RC-2 | **stat_arb 구조적 결함**: 교차거래소 동일심볼 mean-reversion = cross_exchange와 동일 영역, WFE=-1.03 | CRITICAL | US-188 |
| RC-3 | **AdaptiveThreshold WR 피드백 루프**: WR 93.8%인데 손실 → WR>90%에서 edge 하향 = 손실 악화 | CRITICAL | US-201 |
| RC-4 | **Auto-tuner/ML 미작동**: ScheduledTuner 로그 미관찰, AdaptiveThreshold/RegimeDetector/ONNX 호출 미확인 | HIGH | US-190, US-191 |

### 1.3 목표

- 전략 간 신호 영역을 완전히 분리하여 중복 거래 제거
- stat_arb를 cross-asset pair 기반으로 재설계하여 독자 영역 확보
- latency_arb를 cross_exchange에 병합하여 전략 수 8→7개 정리
- AdaptiveThreshold를 PnL 기반 복합 지표로 전환
- Auto-tuner/ML 파이프라인 작동 확인 및 로깅
- **최종 검증**: 7개 전략 2H Shadow, 총합 PnL>$0, 개별 PnL>=-$5, overlap=0, crash=0

---

## 2. 16 US 전체 목록 + Acceptance Criteria

### CRITICAL (3건)

| US | 제목 | Acceptance Criteria |
|----|------|---------------------|
| **US-187** | `_CROSS_EXCHANGE_CONSUMERS` 제거 + 신호 흐름 검증 | `manager.py`에서 frozenset 삭제, `_should_deliver()` cross_exchange 브로드캐스트 로직 제거. stat_arb/latency_arb가 cross_exchange 신호를 수신하지 않음 (테스트 검증). 기존 테스트 전부 PASS. |
| **US-188** | stat_arb cross-asset pair 재설계 | BTC-ETH / ETH-SOL / BTC-BNB 고정 3쌍. `Signal.metadata["symbol2"]` 추가. `_is_cointegrated()` fail-closed (False 기본). Korean exchange 제외. cross_exchange와 영역 겹침 0%. 단위테스트 10+ 추가. |
| **US-201** | AdaptiveThreshold WR→복합 지표 전환 | `expected_edge_bps` + `Profit Factor` 기반 조정. WR은 보조 지표로만 사용 (threshold 조정에 미반영). WR>90%에서 edge 하향 버그 제거. 단위테스트 5+ 추가. |

### HIGH (3건)

| US | 제목 | Acceptance Criteria |
|----|------|---------------------|
| **US-190** | ScheduledTuner 작동 확인 | optuna/apscheduler import 확인. `--run-once` 수동 트리거 모드 추가. 실행 시 로그 `[ScheduledTuner] optimization completed` 출력. 단위테스트 3+ 추가. |
| **US-195** | 전략 간 포지션 충돌 방지 | `(symbol, exchange_pair)` 기준 10초 윈도우 중복 체크. `asyncio.Lock` 기반. 충돌 시 후발 주문 거부 + 로그. Prometheus counter `strategy_position_conflict_total`. 단위테스트 5+ 추가. |
| **US-196** | 전략별 자본 할당 | `trading.json`에 `capital_allocation_pct` (전략별 %). RiskGuardian check #11 추가. 합계 100% 검증. 초과 시 주문 거부. 단위테스트 5+ 추가. |

### MEDIUM (1건)

| US | 제목 | Acceptance Criteria |
|----|------|---------------------|
| **US-189** | cross_exchange min_spread_bps 5→10 복원 | 기본값 10bps. `latency_boost` 모드일 때만 5bps 허용. `CrossExchangeConfig.min_spread_bps` 기본값 변경. 단위테스트 2+ 추가. |

### 구조 개선 (5건)

| US | 제목 | Acceptance Criteria |
|----|------|---------------------|
| **US-194** | latency_arb → cross_exchange 병합 | `LatencyArbStrategy` 클래스 삭제. `CrossExchangeStrategy`에 `latency_boost` 모드 통합 (LatencyTracker 기반 lead-lag 감지 시 min_spread 완화). 전략 등록 8→7개. main.py 등록 코드 수정. 기존 latency_arb 테스트 cross_exchange로 이관/수정. |
| **US-197** | stat_arb ScheduledTuner EXCLUDED 제거 | `scheduled_tuner.py`의 `EXCLUDED = {"cex_dex", "statistical_arb"}`에서 `"statistical_arb"` 제거. US-188 완료 후 적용 (cross-asset 재설계 완료 전제). |
| **US-199** | 전략 overlap 감지 메트릭 | Prometheus counter `strategy_overlap_detected_total`. 10초 윈도우 내 동일 (symbol, exchange_pair) 신호 2건 이상 감지 시 카운트. Grafana 대시보드 연동 가능. 단위테스트 3+ 추가. |
| **US-192** | ExposureTracker Redis 연결 확인 | `ExposureTracker`가 Redis에 노출 데이터 저장/조회 확인. `exposure_tracker.py` Redis client 주입 검증. 연결 실패 시 graceful fallback (in-memory). 단위테스트 2+ 추가. |
| **US-193** | SSOT §9 RESOLVED 이슈 이관 | §9 RESOLVED 섹션 11개 항목 → `SSOT_COMPLETE.md` §9로 이동. SSOT.md에서 삭제. diff 검증. |

### 인프라/관찰성 (3건)

| US | 제목 | Acceptance Criteria |
|----|------|---------------------|
| **US-191** | ML/Tuning 컴포넌트 작동 로그 | AdaptiveThreshold: 매 조정 시 `[AdaptiveThreshold] edge_bps={} PF={} adjusted_to={}` 로그. RegimeDetector: 레짐 변경 시 `[RegimeDetector] regime={NORMAL/STRESS/CRISIS}` 로그. ONNX: 호출 수 Prometheus counter `onnx_scorer_calls_total`. |
| **US-198** | Korean exchange 필터 보강 | `latency_boost` 모드에서 Korean exchange (upbit/bithumb/coinone) 제외. stat_arb cross-asset에서 Korean 제외. 기존 `KOREAN_EXCHANGES` 상수 재활용. 단위테스트 3+ 추가. |
| **US-200** | 오토튜너 백테스트 리플레이 A/B 인프라 | event-level 데이터 TimescaleDB 저장 (timestamp, strategy, signal, fill). deterministic replay 함수. A/B 비교 리포트 (before/after Sharpe, PnL, MDD). 단위테스트 3+ 추가. |

### 통합 검증 (1건)

| US | 제목 | Acceptance Criteria |
|----|------|---------------------|
| **US-202** | 7개 전략 전체 Shadow 2H 재검증 | Docker 기동 → Shadow 2H 실행. 총합 PnL>$0. 개별 전략 PnL>=-$5. `strategy_overlap_detected_total`=0. crash=0. 전략별 메트릭 리포트 생성. |

---

## 3. 5-Wave 실행 순서 + 의존성 다이어그램

```
Wave 1 (병렬, 독립 기반 작업)
├── US-187: _CROSS_EXCHANGE_CONSUMERS 제거
├── US-189: min_spread_bps 5→10 복원
├── US-192: ExposureTracker Redis 확인
└── US-193: SSOT §9 RESOLVED 이관

Wave 2 (병렬, 핵심 아키텍처 변경 — Wave 1 의존)
├── US-194: latency_arb → cross_exchange 병합  ← US-187 (신호 라우팅 정리 후)
├── US-188: stat_arb cross-asset 재설계        ← US-187 (영역 분리 후)
├── US-201: AdaptiveThreshold 복합 지표        (독립)
├── US-195: 포지션 충돌 방지                    ← US-187 (중복 신호 제거 후)
└── US-199: overlap 감지 메트릭                 ← US-195 (충돌 방지와 연동)

Wave 3 (병렬, Wave 2 의존)
├── US-196: 전략별 자본 할당                    ← US-194 (7개 전략 확정 후)
├── US-200: A/B 리플레이 인프라                 (독립, 단 US-201 참조)
└── US-197: stat_arb EXCLUDED 제거              ← US-188 (cross-asset 완료 후)

Wave 4 (병렬, Wave 2~3 의존)
├── US-198: Korean exchange 필터 보강            ← US-194 + US-188 (병합/재설계 후)
├── US-190: ScheduledTuner 작동 확인             (독립, 단 US-197 후 효과적)
└── US-191: ML/Tuning 컴포넌트 작동 로그         ← US-201 + US-190 (컴포넌트 정상화 후)

Wave 5 (직렬, 통합 검증 — 전체 Wave 완료 후)
└── US-202: 7개 전략 Shadow 2H 재검증           ← ALL US 완료 필수
```

### 의존성 다이어그램 (화살표 = 선행 필수)

```
US-187 ──┬──→ US-194 ──→ US-196
         ├──→ US-188 ──→ US-197
         ├──→ US-195 ──→ US-199
         │
US-189 ──┤    (독립)
US-192 ──┤    (독립)
US-193 ──┘    (독립)

US-201 ──────→ US-191
US-194 + US-188 → US-198
US-190 ──────→ US-191
US-197 ←── US-188

ALL ──→ US-202 (통합 검증)
```

---

## 4. 파일 변경 목록 (US ↔ 파일 매핑)

### 엔진 핵심 (engine/src/)

| 파일 | 변경 US | 변경 내용 |
|------|---------|----------|
| `strategies/manager.py` | US-187, US-195, US-199 | `_CROSS_EXCHANGE_CONSUMERS` frozenset 삭제, `_should_deliver()` 단순화, 포지션 충돌 체크 추가, overlap 메트릭 |
| `strategies/cross_exchange.py` | US-189, US-194 | `min_spread_bps` 기본값 10, `latency_boost` 모드 통합 (LatencyTracker 기반) |
| `strategies/latency_arb.py` | US-194 | **삭제** (cross_exchange로 병합) |
| `strategies/statistical_arb.py` | US-188, US-198 | cross-asset pair 재설계 (BTC-ETH/ETH-SOL/BTC-BNB), `_is_cointegrated()` fail-closed, Korean 제외 |
| `core/real_signal_producer.py` | US-187, US-188, US-194 | latency_arb evaluator 제거, stat_arb evaluator cross-asset 전환, 신호 라우팅 정리 |
| `core/signal.py` | US-188 | `Signal.metadata["symbol2"]` 필드 지원 확인 |
| `main.py` | US-194, US-196 | LatencyArbStrategy 등록 제거 (8→7), capital_allocation 로딩, RiskGuardian check #11 주입 |
| `risk/guardian.py` | US-196 | check #11: per-strategy capital limit |
| `risk/exposure_tracker.py` | US-192 | Redis client 주입 검증, graceful fallback |

### 튜닝/ML (engine/src/tuning/)

| 파일 | 변경 US | 변경 내용 |
|------|---------|----------|
| `tuning/adaptive_threshold.py` | US-201, US-191 | WR→expected_edge_bps+PF 기반 전환, PnL 로그 추가 |
| `tuning/scheduled_tuner.py` | US-190, US-197 | `EXCLUDED`에서 `"statistical_arb"` 제거, `--run-once` 모드, 로그 강화 |
| `tuning/optimizer.py` | US-200 | event-level 데이터 저장, deterministic replay, A/B 비교 리포트 |

### ML/레짐 (engine/src/)

| 파일 | 변경 US | 변경 내용 |
|------|---------|----------|
| `ml/regime_detector.py` (또는 해당 경로) | US-191 | 레짐 변경 시 로그 추가 |
| `ml/onnx_scorer.py` (또는 해당 경로) | US-191 | Prometheus counter `onnx_scorer_calls_total` |

### 설정 (engine/config/)

| 파일 | 변경 US | 변경 내용 |
|------|---------|----------|
| `config/trading.json` (생성 필요 시) | US-196 | `capital_allocation_pct` 섹션 추가 |
| `config/strategy_activation.json` | US-194 | latency_arb 제거 (7개 전략) |

### 인프라/DB

| 파일 | 변경 US | 변경 내용 |
|------|---------|----------|
| `infra/db/market_recorder.py` | US-200 | event-level 데이터 저장 스키마 |

### 문서

| 파일 | 변경 US | 변경 내용 |
|------|---------|----------|
| `SSOT.md` | US-193 | §9 RESOLVED 11개 항목 삭제 |
| `SSOT_COMPLETE.md` | US-193 | §9로 RESOLVED 항목 이관 |

### 테스트 (engine/tests/)

| 파일 (신규/수정) | 변경 US | 내용 |
|------------------|---------|------|
| `tests/unit/test_manager.py` | US-187, US-195, US-199 | 신호 라우팅 변경 검증, 포지션 충돌 체크, overlap 메트릭 |
| `tests/unit/test_cross_exchange.py` | US-189, US-194 | min_spread 변경, latency_boost 모드 |
| `tests/unit/test_statistical_arb.py` | US-188, US-198 | cross-asset pair, Korean 제외 |
| `tests/unit/test_adaptive_threshold.py` | US-201 | 복합 지표 전환 검증 |
| `tests/unit/test_scheduled_tuner.py` | US-190, US-197 | run-once 모드, EXCLUDED 변경 |
| `tests/unit/test_guardian.py` | US-196 | check #11 자본 할당 |
| `tests/unit/test_exposure_tracker.py` | US-192 | Redis 연결/fallback |
| `tests/unit/test_overlap_metrics.py` (신규) | US-199 | overlap 감지 메트릭 |
| `tests/unit/test_position_conflict.py` (신규) | US-195 | 포지션 충돌 방지 |
| `tests/unit/test_replay.py` (신규) | US-200 | A/B 리플레이 |

---

## 5. 위험 요소 + 완화 전략

| # | 위험 | 영향 | 확률 | 완화 |
|---|------|------|------|------|
| R-1 | latency_arb 병합 시 기존 테스트 대량 실패 | HIGH | 높음 | Wave 2에서 집중 처리. 기존 테스트를 cross_exchange 테스트로 이관 후 삭제. 병합 전 테스트 목록 사전 파악. |
| R-2 | stat_arb cross-asset 재설계가 수익성 미달 | HIGH | 중간 | BTC-ETH/ETH-SOL/BTC-BNB 3쌍은 상관계수 0.85+ 검증된 쌍. `_is_cointegrated()` fail-closed로 안전 보장. US-202에서 2H Shadow 최종 검증. |
| R-3 | AdaptiveThreshold PnL 전환 후 과도한 edge 변동 | MEDIUM | 중간 | EMA smoothing (alpha=0.1) 적용. edge 변동 범위 [3bps, 50bps] 클램프. 로그로 모니터링. |
| R-4 | 포지션 충돌 방지 Lock이 성능 병목 | LOW | 낮음 | 10초 윈도우 + dict 기반 O(1) 조회. asyncio.Lock은 coroutine 전환 비용만 발생. 벤치마크 검증. |
| R-5 | ScheduledTuner가 cross-asset stat_arb 파라미터 최적화 실패 | MEDIUM | 중간 | `--run-once` 모드로 사전 테스트. EXCLUDED 제거는 US-188 완료 후에만 적용 (의존성 보장). |
| R-6 | 2H Shadow 검증 시간 부족 (시장 비활성) | LOW | 낮음 | UTC 기준 활성 시간대 선택 (08:00-20:00). 실패 시 재시도. |

---

## 6. 검증 방법

### 6.1 단위 테스트 (Wave별 즉시)

```bash
cd engine && python -m pytest tests/ -x --tb=short
```

- 각 Wave 완료 시 전체 pytest PASS 필수 (현재 4,588 기준)
- 신규 테스트 목표: **50+ tests** 추가 (US별 최소 2-10개)
- 커버리지 86% 유지 또는 향상

### 6.2 통합 검증 (Wave 5 — US-202)

```bash
# Docker 기동
docker compose up -d && docker compose ps

# Shadow 2H 실행
cd engine && timeout 7200 python -m src.main
```

**PASS 기준**:
| 항목 | 기준 |
|------|------|
| 총합 PnL | > $0 |
| 개별 전략 PnL | >= -$5 (각각) |
| strategy_overlap_detected_total | = 0 |
| crash | = 0 |
| 전략 수 | 7개 등록 |
| 거래소 수 | 10개 연결 |

### 6.3 회귀 검증

- 기존 4,588 테스트 전부 PASS (삭제된 latency_arb 테스트 제외, 이관된 테스트 포함)
- Docker 15 services healthy (promtail 제외)
- tsc 0 errors (대시보드 변경 없으므로 확인만)

---

## 7. 예상 작업량 (Wave별)

| Wave | US 수 | 예상 시간 | 복잡도 | 핵심 리스크 |
|------|-------|----------|--------|------------|
| **Wave 1** | 4개 (US-187, 189, 192, 193) | 1-2시간 | LOW | US-187 신호 흐름 깨짐 가능 |
| **Wave 2** | 5개 (US-194, 188, 201, 195, 199) | 4-6시간 | **HIGH** | latency_arb 병합 + stat_arb 재설계가 핵심 |
| **Wave 3** | 3개 (US-196, 200, 197) | 2-3시간 | MEDIUM | 자본 할당 RiskGuardian 연동 |
| **Wave 4** | 3개 (US-198, 190, 191) | 1-2시간 | LOW | Korean 필터 + 로깅 |
| **Wave 5** | 1개 (US-202) | 2-3시간 | MEDIUM | 2H Shadow 실행 + 결과 분석 |
| **합계** | **16개** | **10-16시간** | — | Wave 2가 전체의 40% |

---

## 8. Guardrails (Must Have / Must NOT Have)

### Must Have
- [ ] 전략 간 신호 영역 완전 분리 (cross_exchange 신호가 stat_arb에 전달되지 않음)
- [ ] stat_arb cross-asset pair만 사용 (동일 심볼 교차거래소 거래 금지)
- [ ] latency_arb 코드 완전 삭제 (cross_exchange에 기능 병합)
- [ ] AdaptiveThreshold가 PnL 기반으로 동작 (WR 기반 edge 하향 버그 제거)
- [ ] 2H Shadow PnL>$0 (US-202 PASS)
- [ ] 기존 4,588 테스트 회귀 PASS

### Must NOT Have
- [ ] cross_exchange 신호를 다른 전략에 라우팅하는 코드 (RC-1 재발)
- [ ] stat_arb에서 동일 심볼 교차거래소 거래 (RC-2 재발)
- [ ] WR 기반 threshold 조정 (RC-3 재발)
- [ ] PowerLaw slippage(k>0)를 PaperExecutor에 적용 (이중 슬리피지)
- [ ] latency_arb를 독립 전략으로 유지 (병합 미완료)
- [ ] `_CROSS_EXCHANGE_CONSUMERS` frozenset 잔존

---

## 9. 실행 워크플로우

```
Stage A (기획) ← 현재 완료 (이 문서)
  └── Entry Gate: Karina APPROVED
  └── PLAN.md: 이 문서
  └── Quant Gate: stat_arb cross-asset 3쌍 상관계수 검증 필요

Stage B (구현 + 검증)
  └── Phase 1: TeamCreate(IVE) → Wave 1~4 순차 구현
  └── 각 Wave 완료 시 pytest PASS 확인
  └── Phase 2: Wave 5 Shadow 2H 검증 (NewJeans)

Stage C (리뷰 + 릴리스)
  └── Step 1: Jennie(code-review) + Jisoo(security-review)
  └── Step 2: Karina Phase 완료 리뷰 + Go/No-Go
  └── Step 3: Sakura SSOT 업데이트 + git push → 텔레그램 → 사장님 승인

후속: TF QF 재실행 (단계 3.5 조립 검증 추가) → Phase S11 (UI/UX)
```

---

## 10. 성공 기준 (Phase S10 완료 판정)

| # | 기준 | 검증 방법 |
|---|------|----------|
| 1 | pytest 전체 PASS (4,588+ tests) | `python -m pytest tests/ -x` |
| 2 | 신규 테스트 50+ 추가 | test count diff |
| 3 | 전략 수 8→7 (latency_arb 삭제) | main.py 등록 코드 확인 |
| 4 | `_CROSS_EXCHANGE_CONSUMERS` 완전 제거 | grep 결과 0건 |
| 5 | stat_arb cross-asset 3쌍 동작 | 단위테스트 + Shadow 로그 |
| 6 | AdaptiveThreshold PnL 기반 | 단위테스트 + Shadow 로그 |
| 7 | Shadow 2H PnL>$0 | US-202 실행 결과 |
| 8 | overlap 메트릭 = 0 | Prometheus counter |
| 9 | crash = 0 | Shadow 로그 |
| 10 | SSOT.md §9 RESOLVED 이관 완료 | diff 검증 |
