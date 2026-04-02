# Phase J PLAN.md v2 — Backtest 검증

> 작성일: 2026-04-01 | 작성자: Giselle (Planner) → v2 Winter CRITICAL 반영
> 승인 플랜: `/Users/100aniv/.claude/plans/drifting-leaping-lantern.md`
> **v2 변경사항**: SQL 스키마 버그 수정 선행, 데이터 축적 전략 구체화, Tiered Sharpe 기준, BacktestResult 3중 정의 통합

---

## ⚠️ Phase J-0: 선행 필수 수정 (Stage B 시작 전 차단 조건)

Winter(Critic) REJECT 판정 3 CRITICAL — 이 수정 없이 Stage B 진입 금지.

### CRITICAL #1: SQL 스키마 불일치 (backtest.py)

**위치**: `engine/src/modes/backtest.py:184-209`

**버그**:
```python
# 현재 (잘못됨)
SELECT exchange, symbol, bids, asks,
       EXTRACT(EPOCH FROM timestamp) as timestamp
FROM orderbook_snapshots
...
AND timestamp >= $N::timestamptz
ORDER BY timestamp ASC
```

**수정**:
```python
# 수정 후 (올바름) — market_recorder.py 실제 스키마 기준
SELECT exchange, symbol, bids_json, asks_json,
       EXTRACT(EPOCH FROM ts) as timestamp
FROM orderbook_snapshots
...
AND ts >= $N::timestamptz
ORDER BY ts ASC
```

**수정 파일**: `engine/src/modes/backtest.py`
- L184: `bids` → `bids_json`, `asks` → `asks_json`, `timestamp` → `ts`
- L185: `timestamp` → `ts`
- L193, L197: `timestamp` → `ts`
- L209: `timestamp` → `ts`
- L219-220: `row["bids"]` → `row["bids_json"]`, `row["asks"]` → `row["asks_json"]`

### CRITICAL #2: BacktestMode main.py 미배선

**위치**: `engine/src/main.py:1784-1792`

**현황**: BACKTEST 분기가 `_orderbook_feed_loop` + `_paper_signal_simulator_loop` (synthetic) 호출 중.
`BacktestMode` 클래스는 전혀 import/호출 안 됨.

**수정**: US-351에서 처리 (BacktestMode 배선)

### CRITICAL #3: orderbook_snapshots 데이터 전무

**현황**: 7-day retention policy + 마지막 Shadow 실행 March 8 = 24일 전 → 데이터 0건.

**해결 전략** (Stage B US-352 선행):
1. Shadow 모드 1시간+ 실행 → `orderbook_snapshots` 누적
2. retention policy를 `30 days`로 확장 (migration)
3. BacktestMode는 데이터 부족 시 graceful fallback (crash 없음)

---

## 1. 목표

BacktestMode (`src/modes/backtest.py`) 를 main.py BACKTEST 경로에 배선하고,
TimescaleDB `orderbook_snapshots` 리플레이 기반 WFA(Walk-Forward Analysis)를 통해
전 6전략의 과거 수익성을 검증한다. ML A/B 비교로 ML 기여도를 정량화.

### 완료 기준 (Tiered — Winter 권고)

| 기준 | FAIL | CONDITIONAL | PASS |
|------|------|-------------|------|
| 수익 전략 Sharpe | < 1.0 | 1.0~1.5 | > 1.5 |
| MDD | > 10% | 5~10% | < 5% |
| crash | ≥ 1건 | — | 0건 |
| ML 기여도 | sharpe_delta < 0 | delta = 0 | delta > 0 |
| cross_exchange | — | trades=0 수치 기록 | — |

> CONDITIONAL: Phase K 진입 가능하나 해당 전략 제한적 운용
> FAIL: Phase K 진입 차단. 해당 전략 비활성 후 재검증

> **대시보드 /backtest** — US-355를 Phase K로 defer (Winter 권고 반영).
> Phase J에서 API 엔드포인트 + JSON 저장까지, 대시보드 페이지는 Phase K에서 처리.

---

## 2. 현황 분석

### 2.1 BacktestMode 구현 상태

- **파일**: `engine/src/modes/backtest.py` (365줄) — 구현 완료
- **`BacktestMode.run()`**: TimescaleDB `orderbook_snapshots` 쿼리 → 리플레이 → 메트릭 계산
- **`BacktestResult`**: sharpe_ratio, max_drawdown_pct, by_strategy, pnl_curve 포함
- **미배선**: `main.py:1784-1792` BACKTEST 분기가 `_orderbook_feed_loop` + `_paper_signal_simulator_loop` (synthetic) 호출 중
  → `BacktestMode(signal_generator, strategy_manager, db_pool=...).run()` 미호출
- **SQL 스키마 버그**: L184-209 `timestamp`/`bids`/`asks` → 실제 스키마 `ts`/`bids_json`/`asks_json`

### 2.2 BacktestResult 3중 정의 (NingNing 분석)

- `modes/backtest.py:38` — 운영 BacktestResult (sharpe_ratio, max_drawdown_pct, by_strategy)
- `analysis/ml_backtest.py:19` — ML BacktestResult (pnl, sharpe_ratio, max_drawdown)
- `tuning/backtest.py:23` — 튜닝 BacktestResult (win_rate, profit_factor, sharpe_ratio)

**결정**: 이름 충돌 해소. ml_backtest.py는 `MLBacktestResult`로 rename. tuning/backtest.py는 `TuningBacktestResult`로 rename.

### 2.3 WFA 인프라 상태

- `src/analysis/walk_forward.py`: `WalkForwardAnalyzer` — `execution_log` 기반 롤링 윈도우 분석
- `src/analysis/ml_backtest.py`: `MLSignalBacktester.walk_forward()` — n_folds A/B 비교
- `live_gate_continuous.py:94`: `result.walk_forward` 참조 — WFA 결과 소비 경로 존재

### 2.4 데이터 현황

- `orderbook_snapshots`: **현재 0건** (7-day retention + 마지막 Shadow March 8)
- **해결**: US-352에서 retention 30일 확장 + Phase J Shadow 재실행
- **Fallback**: 데이터 없어도 `BacktestResult(error="insufficient_data")` 반환 (crash 없음)

---

## 3. US 목록 (US-351 ~ US-357)

### US-351: BacktestMode main.py 배선 + SQL 스키마 버그 수정

**파일**: `engine/src/main.py`, `engine/src/modes/backtest.py`

**설명**:
1. `backtest.py:184-220` SQL 스키마 수정: `timestamp`→`ts`, `bids`→`bids_json`, `asks`→`asks_json`
2. `main.py:1784-1792` BACKTEST 분기를 `BacktestMode` 직접 실행으로 교체
3. 백테스트 완료 후 결과를 API 엔드포인트에서 조회 가능하도록 저장

**수락 기준**:
1. `EngineMode.BACKTEST` 진입 시 `BacktestMode.run()` 호출됨 (로그: `backtest.starting`)
2. `db_pool` 주입 경로 존재 — `Engine.__init__` → `BacktestMode.__init__(db_pool=...)`
3. `BacktestResult` 반환값이 `engine_state` 또는 API 캐시에 저장되어 `/api/backtest/result` 조회 가능
4. ⚡ WIRING 생성: `BacktestMode(signal_generator, strategy_manager, db_pool=...)` 인스턴스 생성
5. ⚡ WIRING 주입: `Engine._db_pool` → `BacktestMode.__init__(db_pool=self._db_pool)` 전달
6. ⚡ WIRING 호출: `main.py` BACKTEST 분기에서 `await backtest_mode.run()` 실제 호출
7. SQL: `SELECT ... bids_json, asks_json ... WHERE ts >= ... ORDER BY ts ASC` 스키마 일치 (ORDER BY 포함)
8. **[MUST FIX #1]** `BacktestMode._execute_paper_trade()` (또는 동등 메서드)가 체결 시 `execution_log`에 `mode='backtest'`로 INSERT — WFA 입력 데이터 확보
9. `python -m pytest tests/ -x --tb=short` 통과

**의존성**: 없음 (선행 작업)

---

### US-352: orderbook_snapshots 데이터 축적 + retention 확장

**파일**: `engine/src/modes/backtest.py`, `engine/src/infra/db/migrations/`

**설명**:
1. `005_extend_retention.sql` migration: retention 30d 확장 (기존 004_shadow_peak_equity.sql 이후)
2. BacktestMode 시작 시 스냅샷 수 + 시간 범위 로그 출력
3. 데이터 부족 시 graceful degradation (crash 없음)
4. Phase J Shadow 1시간+ 실행 → 데이터 축적

**수락 기준**:
1. `005_extend_retention.sql`: `add_retention_policy(INTERVAL '30 days')` 적용 (번호 004 다음)
2. `BacktestMode._load_snapshots()` 호출 후 스냅샷 수 로그 (`backtest.data_check: count=X, span=Y min`)
3. 스냅샷 0건 시 `BacktestResult(snapshots_replayed=0, error="insufficient_data")` 반환, 예외 없음
4. `start_time=None, end_time=None` 시 DB 전체 범위 자동 사용 (`SELECT MIN/MAX ts`)
5. **[MUST FIX #7]** `LIMIT` 값이 환경변수 `BACKTEST_MAX_ROWS`로 설정 가능 (기본 1,000,000) — 30일 데이터 잘림 방지
6. `python -m pytest tests/ -x --tb=short` 통과

**의존성**: US-351

---

### US-353: 전 6전략 WFA 실행 엔진 + 결과 JSON 저장

**파일**: `engine/src/analysis/walk_forward.py`, `engine/src/modes/backtest.py`, `engine/src/api/routes/`

**설명**:
`BacktestMode.run()` 완료 후 `WalkForwardAnalyzer`를 사용해 전략별 WFA 실행.
6전략 각각에 대해 rolling window 분석 + 결과 JSON 저장.

**수락 기준**:
1. `BacktestMode.run()` 완료 후 `WalkForwardAnalyzer.analyze()` 호출 (`wfa.starting strategy=X`)
2. **[MUST FIX #2]** `WalkForwardAnalyzer.analyze(strategy_id=X)` 가 6전략 각각에 대해 루프 호출됨 — 로그: `wfa.completed strategy=cross_exchange`, `wfa.completed strategy=funding_rate` 등 6건 확인
3. 전략별 `WalkForwardResult` JSON 직렬화 (sharpe, mdd, trades)
4. 결과 파일 `.omc/state/backtest_results.json` 저장
5. GET `/api/backtest/wfa` → 전략별 WFA 결과 반환 (200 OK)
6. cross_exchange 포함 — trades=0이어도 완료 (오류 없음)
7. `python -m pytest tests/ -x --tb=short` 통과

**의존성**: US-351, US-352

---

### US-354: ML A/B 비교 프레임워크

**파일**: `engine/src/analysis/ml_backtest.py`

**설명**:
`MLSignalBacktester.walk_forward()` (n_folds=5) ML 활성/비활성 A/B 비교.
ML 모델 없을 경우 (`ONNX .pkl 0개`) graceful fallback — baseline 결과만 반환.

**수락 기준**:
1. `MLSignalBacktester.walk_forward(n_folds=5)` 완료 (`ml_backtest.ab_result fold=5/5`)
2. `ABTestResult.ml_improves` 값이 `backtest_results.json`에 포함
3. **[MUST FIX #3]** `ml_scorer=None` 시 `ABTestResult.comparison_valid=False` 명시 반환 — `ml_improves=False`와 구분 불가한 상태 방지
4. GET `/api/backtest/ab-test` → ABTestResult 반환 (200 OK)
5. `python -m pytest tests/ -x --tb=short` 통과

**의존성**: US-353

---

### US-355: BacktestResult 3중 정의 통합 + API 완성

> ⚠️ v2 변경: 대시보드 UI를 Phase K로 defer. Phase J에서 API + BacktestResult 타입 통합만 처리.

**파일**: `engine/src/analysis/ml_backtest.py`, `engine/src/tuning/backtest.py`

**설명**:
BacktestResult 이름 충돌 해소:
- `analysis/ml_backtest.py:19`: `BacktestResult` → `MLBacktestResult` rename
- `tuning/backtest.py:23`: `BacktestResult` → `TuningBacktestResult` rename
- 모든 import 참조 업데이트

**수락 기준**:
1. `analysis/ml_backtest.py`에 `MLBacktestResult` 정의 (구 `BacktestResult`)
2. `tuning/backtest.py`에 `TuningBacktestResult` 정의 (구 `BacktestResult`)
3. **[MUST FIX #5]** rename 연쇄 영향 파일 전부 업데이트:
   - `tuning/evaluator.py`, `tuning/shadow_runner.py`, `tuning/optimizer.py`
   - `tuning/strategy_backtest.py`, `tuning/__init__.py`
   - `engine/src/api/` 내 backtest 관련 import
   - grep 확인: `grep -r "from.*backtest import BacktestResult" engine/src/` → 0건
4. `from src.analysis.ml_backtest import MLBacktestResult` 임포트 정상 동작
5. `python -m pytest tests/ -x --tb=short` 통과

**의존성**: US-354

---

### US-356: Phase J Shadow 검증 + LiveGate WFA 연동

**파일**: `engine/src/core/live_gate_continuous.py`, `engine/src/modes/backtest.py`

**설명**:
BacktestMode 실행 + LiveGate WFA 연동 확인. Shadow 1시간+ 실행으로 데이터 축적 후 백테스트 실행.

**수락 기준**:
1. `ENGINE_ENV=dev ENGINE_MODE=backtest timeout 600 python -m src.main` → crash 0건
2. 로그: `backtest.completed` + `wfa.starting` + `wfa.completed` 순서
3. `live_gate_continuous.py` `result.walk_forward` 타입 호환
4. `BacktestResult.by_strategy` 6전략 키 모두 존재 (trades=0 포함)
5. `.omc/state/backtest_results.json` 파일 생성
6. **[MUST FIX #6]** Sharpe annualization 통일 — SSOT §4.5 기준 `sqrt(8760)` (hourly):
   - `modes/backtest.py:349`: `sqrt(525600)` → `sqrt(8760)` (현재 7.75x 과장)
   - `tuning/backtest.py:202`: `sqrt(252)` → `sqrt(8760)` (현재 0.17x 과소)
   - `tuning/strategy_backtest.py:708`: `sqrt(252)` → `sqrt(8760)` (현재 0.17x 과소)
   - `analysis/ml_backtest.py:198`: annualization 없음 → `sqrt(8760)` 추가
   - 준수 파일 (변경 불필요): `walk_forward.py:226` (8760 ✓), `metrics_collector.py:296` (8760 ✓)
7. `python -m pytest tests/ -x --tb=short` 통과

**의존성**: US-351, US-352, US-353, US-354, US-355

---

### US-357: Shadow 1시간+ 실행 → orderbook_snapshots 데이터 축적

> ⚠️ BLOCKER: US-353 WFA 실행 전 필수. 실제 backtest 데이터 확보.

**파일**: `engine/.env`, `docker-compose.yml`

**설명**:
Shadow 모드로 1시간 이상 실행하여 `orderbook_snapshots` 최소 1,000건 축적.
retention 정책 30일 확장 후 실행.

**수락 기준**:
1. `docker compose up -d timescaledb redis` healthy
2. `cd engine && timeout 3700 python -m src.main` → 1시간+ 무중단 실행
3. DB 확인: `SELECT COUNT(*) FROM orderbook_snapshots` ≥ 1,000건
4. **[MUST FIX #4]** DB 확인: `SELECT COUNT(*) FROM execution_log WHERE mode='paper'` ≥ 10건 (WFA 입력용 체결 기록 필수)
5. Shadow PnL > 0, crash = 0 (Shadow 13항목 PASS)
6. retention: `SELECT * FROM timescaledb_information.jobs` 30-day policy 확인

**의존성**: US-352 (retention migration 선행)

---

## 4. 구현 순서 (의존성 기반 — v2)

```
US-351 (SQL 수정 + 배선)
  └── US-352 (retention 확장 + fallback)
        └── US-357 (Shadow 1H+ 실행 → 데이터 축적)
              └── US-353 (WFA 실행)
                    ├── US-354 (ML A/B)
                    │     └── US-355 (BacktestResult 통합)
                    │           └── US-356 (Shadow 검증 + LiveGate)
                    └── (US-355는 US-354 완료 후)
```

**병렬 가능**:
- US-353 + US-355 (WFA 실행 중 BacktestResult 통합 병렬 가능)
- US-357은 US-352 완료 즉시 Shadow 실행 시작 (비동기)

---

## 5. 배선 다이어그램 (BacktestMode v2)

```
main.py
  Engine._run_mode()
    if EngineMode.BACKTEST:
      backtest = BacktestMode(
          signal_generator=self._signal_generator,
          strategy_manager=self._strategy_manager,
          db_pool=self._db_pool,          ← 주입
          start_time=settings.backtest_start,
          end_time=settings.backtest_end,
          symbols=settings.symbols,
      )
      result = await backtest.run()       ← 호출 (SQL 수정됨)

      # SQL (수정됨)
      # SELECT exchange, symbol, bids_json, asks_json,
      #        EXTRACT(EPOCH FROM ts) as timestamp
      # FROM orderbook_snapshots WHERE ts >= $N

      # WFA
      wfa = WalkForwardAnalyzer(self._db_pool)
      wfa_result = await wfa.analyze()    ← 전략별 WFA

      # ML A/B
      ml_bt = MLSignalBacktester(ml_scorer=self._ml_scorer)
      ab_result = ml_bt.walk_forward(signals, prices)

      # 결과 저장
      save_backtest_results(result, wfa_result, ab_result)

TimescaleDB
  orderbook_snapshots ←── MarketRecorder (Shadow 1H+ 실행으로 축적)
    retention: 30d (↑ 7d에서 확장)
  execution_log        ←── WalkForwardAnalyzer 소스
```

---

## 6. 리스크 (v2 업데이트)

| 리스크 | 가능성 | 대응 |
|--------|--------|------|
| SQL 스키마 버그 → DB 쿼리 실패 | 확정 | US-351 첫 번째 수정 항목 |
| orderbook_snapshots 0건 | 확정 | US-357 Shadow 1H+ 실행 선행 |
| BacktestResult 타입 충돌 | 확정 | US-355 rename 처리 |
| ML 모델 미학습 상태 | 높음 | `ml_scorer=None` graceful fallback |
| WFA Sharpe < 1.5 (cross_exchange) | 높음 | CONDITIONAL — 수치 기록, 차단 아님 |
| LiveGate WFA 타입 호환 불일치 | 중간 | US-356 명시적 타입 검증 |

---

## 7. Phase J 완료 기준 체크리스트 (v2)

- [ ] US-351: SQL 스키마 수정 (`ts`/`bids_json`/`asks_json`) + `backtest.starting` 로그
- [ ] US-352: retention 30d migration + `backtest.data_check` 로그
- [ ] US-357: `SELECT COUNT(*) FROM orderbook_snapshots` ≥ 1,000건
- [ ] US-353: `wfa.completed` + `.omc/state/backtest_results.json` 존재
- [ ] US-354: `ml_backtest.ab_result fold=5/5` (또는 `ml_scorer=None` fallback)
- [ ] US-355: `MLBacktestResult` + `TuningBacktestResult` rename 완료
- [ ] US-356: crash 0건 + `backtest.completed` + `wfa.completed` 로그
- [ ] `python -m pytest tests/ -x --tb=short` 전체 통과
- [ ] Tiered Sharpe: 수익 전략 ≥ CONDITIONAL (1.0+) 또는 PASS (1.5+)
- [ ] MDD < 10%
- [ ] prd.json US-351~357 passes:true (런타임 호출 증거 필수)
