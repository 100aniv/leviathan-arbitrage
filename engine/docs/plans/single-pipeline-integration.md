# Single Pipeline Integration Plan (사장님 지시: "배관 공사 제대로 통합")

**Date**: 2026-04-26
**Status**: Phase 1 (Config 통합) 진행 중
**Priority**: HIGH (paper canary 의미 회복 — Day 0-15 모듈 0% 활성화 → 100%)

---

## 발견 (2026-04-26 병렬 audit)

### 1. 동일 배관 검증 — PARTIAL_SHARE (실질 SEPARATE)

**SHARED**: SignalGenerator, StrategyManager, 7 strategies, PriceHub, CostCalculator, exchange Adapter Protocol.

**SEPARATE (사장님 지적의 핵심)**:
- `shadow.py:1765` paper → `self._paper_executor.execute()` (per-leg sequential)
- `live.py:1445` live → `AtomicExecutor.execute_*` (multi-leg atomic)
- `executor.py:30-31` Day 6 Journal + Day 7 StateMachine = AtomicExecutor에만 wired
- `live.py:1383` PreTradeValidator = LiveMode only, **shadow.py는 PreTradeValidator 호출 0건**
- shadow.py 2,688 LOC 별개 monolith. live.py와 60-70% 코드 복제 (`_route_signal_to_strategies` 양쪽 존재).

### 2. Day 0-15 활성화 — 0/7 (0%)

`engine/.env` 파일 부재 (백업만 4/21자). 7 flag 모두 default `false`.
- `EXECUTION_JOURNAL_ENABLED` 미설정 — log 0건
- `EXECUTION_STATE_MACHINE_ENABLED` 미설정 — log 0건
- `EXECUTION_ROUTER_ENABLED` 미설정 — log 0건
- `CORE_REAL_ADV_ENABLED` 미설정 — log 0건
- `EXECUTION_PARALLEL_LEGS_ENABLED` 미설정 — `cross_exchange_v2 fast-reject`
- `EXECUTION_PRETRADE_VALIDATOR_ENABLED` 미설정 — paper bypass
- `SUPERVISOR_ACTIVE` 미설정 — `main.py:236` gate 차단

**결론**: 48h Gate canary는 Day 6-15 검증이 아니라 v1 경로 검증.

### 3. 산업 표준 (exa.ai 5개 비교)

| 시스템 | 배관 | 모드 전환 |
|--------|------|---------|
| Hummingbot | SHARED | ConnectorBase 단일 base, paper=별도 connector instance |
| NautilusTrader | SHARED | NautilusKernel 공통, ExecClient interface 교체 |
| LEAN | SHARED | IBrokerage interface, live-mode boolean |
| 3Commas | PARTIAL | 별도 Demo 경로 (커머셜) |
| Coinrule | PARTIAL | 별도 Demo 경로 (커머셜) |

**프로 등급 3개 = SHARED**. 사장님 지시 = 산업 표준 정확히 일치.

---

## Phase 1: Config 통합 (진행 중, 2026-04-26)

### 변경
- `engine/config/engine.json`에 `feature_flags` section 추가 (단일 진실 소스)
- `engine/src/core/config_loader.py:get_bool_flag()` engine.json fallback 통합
- 해결 우선순위: env > engine.json.feature_flags > default

### 활성 flag (5/7)
- `EXECUTION_JOURNAL_ENABLED=true` ✅
- `EXECUTION_STATE_MACHINE_ENABLED=true` ✅
- `EXECUTION_ROUTER_ENABLED=true` ✅
- `CORE_REAL_ADV_ENABLED=true` ✅
- `EXECUTION_PRETRADE_VALIDATOR_ENABLED=true` ✅

### 미활성 flag (2/7, 보수적)
- `EXECUTION_PARALLEL_LEGS_ENABLED=false` (HIGH risk, Day 11 별도 검증 후)
- `SUPERVISOR_ACTIVE=false` (Phase 3 통합 후)

### `engine/.env` 비도입
- 사장님 지시: 루트 `.env` 단일 시크릿 소스 (메모리 `feedback_env_no_fragmentation.md`)
- 비시크릿 = `engine.json` (이미 ConfigService Day-4 schema 있음)

---

## Phase 2: Executor 통합 (4-6시간, 별도 commit)

### 목표
- `PaperExecutor` (engine/src/execution/paper.py 223 LOC) → 폐기
- `AtomicExecutor` 단일 사용
- adapter Protocol로 paper/live 분기 (PaperExchangeAdapter는 이미 존재)

### 작업
1. PaperExchangeAdapter Protocol gap 검증 + 보강
2. AtomicExecutor가 PaperExchangeAdapter 받을 때도 정상 작동 검증
3. shadow.py:_execute_paper_trade_request 폐기 → live.py:_execute_trade_request 호출
4. paper.py 폐기

### 효과
- Day 6 Journal + Day 7 StateMachine + Day 8 Router 자동 paper 활성화
- PreTradeValidator paper 자동 활성화
- 코드 복제 5,938 LOC → 추정 3,500 LOC

---

## Phase 3: Mode loop 통합 (2-3일, US-386 합산)

### 목표
- shadow.py 2,688 LOC 폐기
- LiveMode가 mode flag 분기로 paper/live/backtest 모두 처리

### 작업
1. `BookWalkSlippage` (shadow.py 84-200) → `friction/slippage_model.py`로 이동
2. `PaperRateLimiter` (shadow.py 291-344) → `infra/rate_limiter.py`
3. `VirtualBalanceTracker` (shadow.py 205-258) → `core/balance_tracker.py`
4. `PaperStats` + `StrategyStats` → `core/stats.py`
5. shadow.py에 남는 `PaperMode` 클래스 본문 → live.py로 통합
6. shadow.py shim만 유지 (40 LOC: backward-compat alias)

### 위험 mitigation
- 40+ test imports `from src.modes.shadow import` → shadow.py shim에서 re-export
- live.py mode 분기점 3곳만 (executor adapter, data feed, risk gate)

---

## Phase 4 (Phase 2-3 완료 후): paper canary 재실행

- 5분 dry-run + Journal/StateMachine/Router/PreTradeValidator log 확인
- 30분 검증 (이전 사이클 대비 Day 6-15 모듈 활성화 증거)
- 24h paper PASS + 30분 live $10 카나리

---

## 추정 작업량 합계

| Phase | 작업 | 시간 |
|-------|------|------|
| 1 | Config 통합 + 5/7 flag ON | **1시간 (진행 중)** |
| 2 | Executor 통합 (PaperExecutor → AtomicExecutor) | 4-6시간 |
| 3 | Mode loop 통합 (shadow.py 모놀리스 폐기) | 2-3일 |
| 4 | paper canary 재실행 + Live 진입 | 24h paper + 30분 live |

**총 ~3.5일** — 산업 표준 (Nautilus/LEAN) 수준 달성.

---

## Acceptance Criteria

- AC-1: Day 6-15 모듈 7/7 active during paper canary (log 증거)
- AC-2: live.py + shadow.py 합 5,938 LOC → 3,500 LOC 이하
- AC-3: paper canary 30분 Journal/StateMachine 이벤트 logged
- AC-4: pytest 5,056+ pass / 0 regression
- AC-5: Binance API cross-check ±$1.00 (Live 재개 Gate 6)

---

## 메모리 추가

- `feedback_pipeline_must_be_unified.md` (2026-04-26): paper/live/backtest 동일 배관 필수. 다른 배관은 검증 무용.
- `feedback_config_no_fragmentation.md` (2026-04-26): 루트 .env + engine.json 단일 소스. engine/.env 금지.
