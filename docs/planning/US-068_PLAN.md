# US-068: Shadow 기반 전략 파라미터 재최적화

> **Phase**: G (전략 수익성 복원) | **선행**: US-066 (Stale 감지), US-067 (전략별 검증)
> **작성일**: 2026-03-11 | **작성자**: Architect Agent

---

## 1. 목표

US-066(Stale Orderbook 방어)과 US-067(전략별 Shadow 검증) 완료 후,
Optuna 100 trials로 활성 전략의 파라미터를 재최적화하고 strategy_params.json을 업데이트한다.

### Acceptance Criteria

1. Optuna 100 trials 실행 (stale 필터 적용 후 활성 전략 대상)
2. `engine/config/strategy_params.json` 업데이트 (WFE > 0 전략만 READY/MONITOR)
3. 1H Shadow PnL 개선 확인
4. `pytest` 전체 PASS

---

## 2. 현재 상태 분석

### 2.1 strategy_params.json (Phase 6 synthetic GBM 기반)

| 전략 | WFE | status | 비고 |
|------|-----|--------|------|
| spot_futures | 1.52 | READY | |
| funding_rate | 0.62 | READY | |
| triangular | 0.94 | MONITOR | |
| cex_dex | 1.19 | MONITOR | DEX stub → 실행 불가 |
| cross_exchange | 0.32 | MONITOR | 낮은 WFE |
| futures_futures | 0.28 | MONITOR | 낮은 WFE |
| statistical_arb | -1.03 | MONITOR | **WFE 음수** → 재설계 필요 |
| **latency_arb** | **N/A** | **없음** | **튜닝 파이프라인에 없음** |

### 2.2 발견된 GAP 6건

| # | GAP | 파일:라인 | 심각도 |
|---|-----|----------|--------|
| G1 | `latency_arb` — STRATEGY_TYPES 누락, 신호 생성기 없음, _build_strategy() 케이스 없음 | `strategy_backtest.py:57-65`, `:427-501`, `:508-516` | HIGH |
| G2 | Synthetic 전용 최적화 — TimescaleDB 실 데이터 미사용 | `scheduled_tuner.py:86` | MEDIUM |
| G3 | US-067 activation 결과 미활용 — 전략 필터링 없음 | `scheduled_tuner.py:36` | MEDIUM |
| G4 | ShadowRunner auto-apply 미연결 — 최적화 후 자동 적용 없음 | `scheduled_tuner.py:40-56` | MEDIUM |
| G5 | `strategy_params.json` 키 이름 불일치 — `max_position_size_usdt` vs `max_position_usdt` vs `max_position_size` | `param_bridge.py:34`, `main.py:555`, `strategy_params.json:13` | LOW |
| G6 | `statistical_arb`, `cex_dex` 제외 필요 — WFE 음수 / DEX stub | `strategy_params.json:62-70,35-43` | LOW |

---

## 3. 구현 계획

### Step 1: `latency_arb` 튜닝 파이프라인 추가 [GAP G1]

**파일**: `engine/src/tuning/strategy_backtest.py`

1. `STRATEGY_TYPES`에 `"latency_arb"` 추가
2. `_make_latency_arb_signals()` 함수 추가 (두 거래소 간 latency-driven 가격 차이 생성)
3. `_build_strategy()` 에 `latency_arb` 케이스 추가 (MockLatencyTracker 사용)
4. `_SIGNAL_GENERATORS`에 `"latency_arb"` 매핑 추가

**파일**: `engine/config/strategy_params.json`
- `latency_arb` 섹션 추가 (초기값: min_spread_bps=3, max_position_size_usdt=5000)

### Step 2: US-067 activation 결과 연동 [GAP G3, G6]

**파일**: `engine/src/tuning/scheduled_tuner.py`

1. `__init__()` 수정: `config/strategy_activation.json` 읽기
2. `active_strategies` 목록으로 최적화 대상 필터링
3. `cex_dex` 무조건 제외 (DEX stub)
4. `statistical_arb` 제외 (WFE -1.03)

### Step 3: TimescaleDB 데이터 소스 옵션 추가 [GAP G2]

**파일**: `engine/src/tuning/scheduled_tuner.py`

1. `data_source` 파라미터: `"synthetic"` (기본) 또는 `"timescaledb"`
2. TimescaleDB 연결 실패 시 synthetic fallback + 경고 로그
3. 환경 변수: `TUNER_DATA_SOURCE=synthetic|timescaledb`

### Step 4: ShadowRunner auto-apply 연결 [GAP G4]

**파일**: `engine/src/tuning/scheduled_tuner.py`

1. `run_optimization()` 완료 후 ShadowRunner.evaluate_and_decide() 호출
2. APPLY → strategy_params.json 업데이트
3. REJECT → 기존 파라미터 유지

### Step 5: `strategy_params.json` 키 이름 정규화 [GAP G5]

**파일**: `engine/src/tuning/param_bridge.py`
- `max_position_usdt` → `max_position_size_usdt` 통일

### Step 6: 테스트 추가

- `engine/tests/unit/tuning/test_scheduled_tuner.py` — 8개 테스트 추가
- `engine/tests/unit/tuning/test_latency_arb_backtest.py` — 4개 테스트 (새 파일)

---

## 4. 파일 변경 요약

| 파일 | 변경 유형 | 예상 라인 |
|------|----------|---------|
| `engine/src/tuning/strategy_backtest.py` | 수정: latency_arb 추가 | +80 |
| `engine/src/tuning/scheduled_tuner.py` | 수정: activation 필터 + DB 소스 + auto-apply | +60 |
| `engine/src/tuning/param_bridge.py` | 수정: 키 이름 정규화 | +5 |
| `engine/config/strategy_params.json` | 수정: latency_arb 추가 + _meta 갱신 | +10 |
| `engine/tests/unit/tuning/test_scheduled_tuner.py` | 수정: 테스트 8개 추가 | +80 |
| `engine/tests/unit/tuning/test_latency_arb_backtest.py` | 새 파일: 테스트 4개 | +60 |

---

## 5. 리스크

1. **이중 슬리피지 금지**: PowerLaw k=0.0 유지. CEXOrderbookSlippage가 유일
2. **latency_arb MockLatencyTracker**: 백테스트용 Mock 필요
3. **strategy_params.json 병행 수정 금지**: 순차 처리로 해결
4. **Docker 필수 (TimescaleDB)**: `docker compose up -d` 선행

---

## 6. 완료 기준

- [ ] `pytest tests/ -x --tb=short` — 전체 PASS
- [ ] `strategy_params.json` 업데이트 (latency_arb 포함, _meta 갱신)
- [ ] Optuna 100 trials 완료 로그
- [ ] 1H Shadow: PnL > 0, crash 0건
