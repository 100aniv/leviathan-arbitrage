# Phase 5 Checklist — 단계별 진행 체크리스트

**Date**: 2026-04-26
**Status**: 진행 전 (사장님 승인 대기)

---

## 매 commit 검증 사이클 (5단계 필수)

각 코드 변경 시 아래 5단계 모두 통과해야 commit 가능.

### Step 1 — pytest 회귀
- [ ] `cd engine && python -m pytest tests/unit/ --tb=line --no-cov -q`
- [ ] 5056+ pass / 14 skipped 유지 (회귀 0)
- [ ] 신규 fail 있으면 즉시 fix

### Step 2 — code-reviewer agent
- [ ] `Task(subagent_type="oh-my-claudecode:code-reviewer", ...)`
- [ ] CRITICAL: 0 / HIGH: ≤ 1 (즉시 fix) / MEDIUM: ≤ 3
- [ ] APPROVE 또는 APPROVE-with-fixes 받기

### Step 3 — omc ask codex (AI CLI)
- [ ] `omc ask codex "Review changes in <file>: <description>"`
- [ ] 산업 표준 부합 확인
- [ ] 보안/성능 문제 검토

### Step 4 — omc ask gemini (AI CLI)
- [ ] `omc ask gemini "Review changes in <file>: <description>"`
- [ ] alternative perspective 확인
- [ ] 디자인 일관성 검토

### Step 5 — paper canary 실 가동
- [ ] 엔진 paper 모드 5분 가동
- [ ] 엔진 alive (PID + RSS)
- [ ] universe_matrix entries=34
- [ ] 변경 모듈 활성화 log 확인 (Day 6/12 등)
- [ ] crash=0, ERROR ≤ 5 (telegram 등 routine 제외)

---

## Phase 5.0 Pre-audit Checklist

### 0.1 통신 플로우 trace
- [ ] paper canary 5분 log 분석
- [ ] Signal → Strategy → Executor → Result 호출 trace 그림 작성
- [ ] `engine/docs/architecture/communication-flow.md` 작성

### 0.2 Dependency graph
- [ ] runtime/* 7 모듈 import 관계 mapping
- [ ] `engine/docs/architecture/module-dependencies.md` 작성
- [ ] graphviz / mermaid 다이어그램

### 0.3 12 Listener 식별 (on_execution_result 분석)
- [ ] `risk_execution.on_execution_result` 본문 분석
- [ ] 12 책임 도출 (PnL, Position, Slippage, ...)
- [ ] 각 listener 의존성 매핑

### 0.4 EngineState mutable field 식별
- [ ] `engine.__init__` 본문 분석
- [ ] mutable state vs immutable settings 분리
- [ ] `EngineState` dataclass 설계

### 0.5 god-object attr access 카운트
- [ ] `grep -c "engine\\." engine/src/runtime/*.py`
- [ ] 가장 많이 access되는 attr top-10 식별
- [ ] Port 정의 우선순위 결정

---

## Phase 5.1 Port 인터페이스 Checklist

### 1.1 ExchangeAdapterPort
- [ ] design doc (engine/docs/architecture/ports/exchange-adapter-port.md)
- [ ] industry 비교 (Nautilus ExecClient vs LEAN IBrokerage vs Hummingbot ConnectorBase)
- [ ] `engine/src/ports/exchange_adapter_port.py` Protocol 작성
- [ ] tests/unit/ports/test_exchange_adapter_port.py
- [ ] **검증 5단계** 완료
- [ ] commit + push

### 1.2 ExecutorPort
- [ ] (반복)

### 1.3 RiskPort
- [ ] (반복)

### 1.4 DataFeedPort
- [ ] (반복)

### 1.5 JournalPort
- [ ] (반복)

### 1.6 LedgerPort
- [ ] (반복)

### 1.7 KillSwitchPort
- [ ] (반복)

---

## Phase 5.2 Engine god-object 해체 Checklist

### 2.1 EngineState 분리
- [ ] design doc
- [ ] `engine/src/core/engine_state.py` dataclass
- [ ] mutable field 이전 (_total_pnl, _position_sizes, _peak_equity, ...)
- [ ] **검증 5단계**
- [ ] commit + push

### 2.2 DI container 도입
- [ ] design doc (punq vs simple registry 결정)
- [ ] `engine/src/core/di_container.py`
- [ ] **검증 5단계**
- [ ] commit + push

### 2.3 runtime/ 26 함수 시그니처 변경
- [ ] 함수별 design doc (변경 전/후 signature)
- [ ] 한 모듈씩 점진적 마이그레이션 (ml_loops → bootstrap → exchange_init → ...)
- [ ] 각 모듈 마다 **검증 5단계**
- [ ] backward-compat thin wrapper 유지

### 2.4 on_execution_result 12 listener 분리
- [ ] listener별 design doc
- [ ] `engine/src/listeners/` 디렉토리
- [ ] 각 listener 마다 **검증 5단계**
- [ ] commit + push

---

## Phase 5.3 LifecycleManager Checklist

- [ ] design doc
- [ ] `engine/src/runtime/lifecycle_manager.py`
- [ ] register pattern + dependency graph
- [ ] graceful shutdown ordering 검증
- [ ] **검증 5단계**
- [ ] commit + push

---

## Phase 5.4 ModeRunner ABC Checklist

- [ ] design doc (ABC 시그니처)
- [ ] `ModeRunner` ABC 작성
- [ ] `BacktestRunner` 구현
- [ ] `PaperRunner` 구현
- [ ] `LiveRunner` 구현
- [ ] `Engine.run()` 단순화 (`create_mode_runner` factory)
- [ ] `if engine._engine_mode == X` 0개 확인
- [ ] 각 Runner 마다 **검증 5단계**
- [ ] commit + push

---

## Phase 5.5 pre-commit hook Checklist

- [ ] design doc (.pre-commit-config.yaml 변경)
- [ ] LOC budget script
- [ ] CI hook 동일 적용
- [ ] **검증 5단계**
- [ ] commit + push

---

## Acceptance Criteria 최종 검증

### 전체 Phase 5 종료 시 모두 통과 필수

- [ ] AC-1: `engine: "Engine"` 첫 인자 grep count = 0
- [ ] AC-2: `find engine/src/runtime -name "*.py" -exec wc -l {} \;`에서 모든 파일 < 400 LOC
- [ ] AC-3: `grep -r "if engine._engine_mode" engine/src/` count = 0
- [ ] AC-4: 검증 5단계 매 commit 완료 (commit message에 명시)
- [ ] AC-5: pytest 5056+ pass / 14 skipped
- [ ] AC-6: paper canary 30분 가동 + ExecutionJournal SENT/FILL/REJECT log 확인
- [ ] AC-7: live mode swap 시뮬레이션 (코드 변경 0)
- [ ] AC-8: 산업 표준 점수표 모두 8/10+ 도달

---

## 사장님 진행 승인 체크리스트

- [ ] Phase 5 plan 검토 완료
- [ ] 검증 사이클 5단계 (pytest + code-reviewer + codex + gemini + paper) 동의
- [ ] Phase 5.0 pre-audit 먼저 진행 동의
- [ ] paper canary v12 가동 중 진행 OR Phase 5 완료 후 재기동 결정
- [ ] **승인 후 Phase 5.0부터 시작**
