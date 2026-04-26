# Phase 5 Architecture Refactor — 추상화/SOLID/DI/Hexagonal Architecture

**Date**: 2026-04-26
**Status**: PLAN (사장님 승인 대기)
**Trigger**: Phase 4 mechanical 추출 완료 (-3514 LOC) → 산업 표준 (Nautilus/LEAN/Hummingbot) deep audit 결과 god-object 결합도 1/10 발견 → 추상화 작업 추가 필요

---

## 1. 배경

### 1.1 Phase 4 결과 (이미 완료)

| 모듈 | LOC | 책임 |
|------|-----|------|
| main.py | 689 | Engine + lifecycle + thin wrappers |
| runtime/ml_loops.py | 282 | HMM/XGB/Regime/Adaptive |
| runtime/bootstrap.py | 395 | Config + DB + Telegram + Rust + Tuner |
| runtime/exchange_init.py | 136 | Paper/Sandbox/Live/Native 어댑터 |
| runtime/risk_execution.py | 878 | Risk + Executor + 결과 콜백 |
| runtime/pipeline_init.py | 543 | SignalGenerator + Strategies + DEX |
| runtime/background_loops.py | 930 | Lifecycle + Health/Heartbeat |
| runtime/mode_loops.py | 826 | paper/live/backtest/feed |
| **합계** | 4,679 | (Phase 4: 7 commits, 5056 pytest pass) |

### 1.2 산업 표준 비교 점수표

| 차원 | 점수 | 근거 |
|------|------|------|
| Module size discipline | 5/10 | risk_execution(878)/background_loops(930)/mode_loops(826) 과대 |
| Dependency injection | **1/10** | 모든 함수 `engine: "Engine"` god-object 첫 인자 |
| Lifecycle hook abstraction | 3/10 | LifecycleManager 없음, asyncio.create_task 직접 호출 |
| Mode dispatching | 4/10 | if-elif 분기, ABC dispatch 없음 |
| Testing seams | 2/10 | Port interface 없음, mock injection 어려움 |

### 1.3 산업 표준 패턴 (Nautilus/LEAN/Hummingbot)

| 시스템 | Port 패턴 | DI 패턴 | Mode dispatch |
|--------|----------|--------|--------------|
| **NautilusTrader** | `ExecClient` + `DataClient` interface | NautilusKernel 공통 코어 | environment context (BACKTEST/SANDBOX/LIVE) |
| **Hummingbot** | `ConnectorBase` 단일 base | StrategyV2Base + Controller + Executor | 별도 connector instance |
| **LEAN** | `IBrokerage` + `IDataFeed` + `ISetupHandler` | IAlgorithm vs Engine 엄격 분리 | live-mode boolean + IBrokerage 다형성 |

---

## 2. Phase 5 목표

### 2.1 산업 표준 도달 점수표 (목표)

| 차원 | 현재 | 목표 |
|------|------|------|
| Module size discipline | 5/10 | **9/10** (모든 파일 < 400 LOC) |
| Dependency injection | 1/10 | **9/10** (Port DI, no god-object) |
| Lifecycle hook abstraction | 3/10 | **8/10** (LifecycleManager + register pattern) |
| Mode dispatching | 4/10 | **9/10** (ModeRunner ABC + 다형성) |
| Testing seams | 2/10 | **9/10** (Port mock injection) |

### 2.2 Acceptance Criteria

- AC-1: `engine: "Engine"` god-object 첫 인자 0개 (specific Port 의존성으로 대체)
- AC-2: 모든 runtime/ 파일 < 400 LOC
- AC-3: `if engine._engine_mode == X` 분기 0개 (ModeRunner 다형성)
- AC-4: pre-commit hook: 새 파일 god-object/LOC budget violation reject
- AC-5: pytest 5056 → 5056+ pass (회귀 0)
- AC-6: paper canary: ExecutionJournal SENT/FILL/REJECT 이벤트 정상 emit
- AC-7: live mode 전환 시 코드 변경 0 (Port 구현체만 swap)

---

## 3. Phase 5 단계 (5 sub-phases)

### Phase 5.1 — Port 인터페이스 정의 (LOW risk, 2-3d)

**목표**: typing.Protocol 기반 Port 7개 정의. 기존 코드 변경 0.

**산출물** (`engine/src/ports/`):
1. `exchange_adapter_port.py` — `place_order`, `cancel_order`, `get_balance`, `subscribe_orderbook`, `supports_symbol`, `get_min_notional`, `_market_type`
2. `executor_port.py` — `execute_trade_request`, callback registration
3. `risk_port.py` — `check_proposal`, `record_loss`, `record_win`, `is_halted`
4. `data_feed_port.py` — `subscribe`, `on_orderbook`, `on_trade`
5. `journal_port.py` — `append`, `replay`, `flush`
6. `ledger_port.py` — `record_pnl`, `get_total`, `get_per_strategy`
7. `kill_switch_port.py` — `halt`, `clear`, `is_active`

**검증 사이클** (각 Port):
- [ ] design doc 작성 (interface 시그니처 + docstring)
- [ ] industry 비교 (Nautilus ExecClient vs LEAN IBrokerage)
- [ ] Protocol 클래스 작성
- [ ] pytest tests/unit/ports/ 신규 (interface 충족 검증)
- [ ] code-reviewer agent 리뷰
- [ ] omc ask codex 리뷰 (CLI)
- [ ] omc ask gemini 리뷰 (CLI)
- [ ] 엔진 import smoke test
- [ ] 풀 회귀 5056 pass
- [ ] commit + push

### Phase 5.2 — Engine god-object 해체 (HIGH risk, 5-7d)

**목표**: `engine: "Engine"` 첫 인자 → specific Port DI

**작업**:
1. `EngineState` dataclass 분리 (mutable state — `_total_pnl`, `_position_sizes`, `_peak_equity`)
2. DI container (`punq` 또는 simple registry) 도입
3. runtime/ 26개 함수 시그니처 변경:
   - 변경 전: `def init_risk(engine: "Engine") -> None`
   - 변경 후: `def init_risk(*, kill_switch: KillSwitchPort, settings: Settings, db_pool: DatabasePool, telegram: TelegramPort | None) -> RiskComponents`
4. `risk_execution.on_execution_result` 360 LOC → 12개 listener 분리:
   - `PnLListener`, `PositionListener`, `SlippageListener`, `MarketRecorderListener`,
   - `ExposureListener`, `CorrelationListener`, `TCAListener`, `TradeHistoryListener`,
   - `CircuitBreakerListener`, `RollbackListener`, `TelegramListener`, `PositionSizeLeakListener`
5. backward-compat: `Engine` 클래스는 listener registration + DI container 마운트만

**위험**: 기존 401+ test가 `engine._signal_generator` 등 직접 attr 접근 → mock 변경 광범위.

**Mitigation**:
- worktree 격리 (`/freeze runtime/`)
- 단계적 진행 (runtime 모듈 1개씩)
- 기존 thin wrapper 유지 (옵션)

**검증**: 각 함수 시그니처 변경 후 pytest + AI CLI review + paper canary.

### Phase 5.3 — LifecycleManager (MED risk, 2-3d)

**목표**: `start_background_tasks` 270 LOC if-elif → register pattern

**작업**:
1. `engine/src/runtime/lifecycle_manager.py` 신규
2. `LifecycleManager.register(name, factory, depends_on=[...], priority=N)`
3. `start_background_tasks` → `LifecycleManager.start_all()`
4. graceful shutdown ordering (depends_on 역순)

**검증**: paper canary 시작/종료 시 task 순서 log 확인.

### Phase 5.4 — ModeRunner ABC (MED risk, 3-4d)

**목표**: `mode_loops.py` 826 LOC → ABC + 3 구현체

**작업**:
1. `engine/src/runtime/mode_runner.py` `ModeRunner` ABC
2. `BacktestRunner(ModeRunner)`, `PaperRunner(ModeRunner)`, `LiveRunner(ModeRunner)`
3. 공통: `start()`, `stop()`, `tick()`, `on_signal()`, `on_fill()`
4. `Engine.run()` → `mode_runner = create_mode_runner(mode, deps); await mode_runner.start()`
5. `if engine._engine_mode == X` 분기 모두 제거

**검증**: 모드 3가지 모두 paper canary 가동 + ExecutionJournal 이벤트.

### Phase 5.5 — Module size budget pre-commit hook (LOW risk, 1d)

**목표**: 새 파일 LOC > 400 reject

**작업**:
1. `.pre-commit-config.yaml`에 size-check hook 추가
2. CI에 동일 check 추가
3. 현재 위반 파일 (risk_execution 878, background_loops 930, mode_loops 826) → Phase 5.2~5.4에서 자연스럽게 해소

---

## 4. 통신 플로우 검증 (5.0 Pre-work, 2026-04-26)

**Phase 5 시작 전 통신 플로우 정합성 검증 필수**.

### 4.1 Signal → Strategy → Executor → Result 플로우

```
WS data → CoreOrderBook → SignalGenerator.on_orderbook_update()
  → emit Signal
  → StrategyManager.route_signal()
  → BaseStrategy.on_signal()
  → emit TradeRequest
  → PreTradeValidator.validate()    [Phase 5.1: RiskPort]
  → AtomicExecutor.execute_trade_request()  [Phase 5.1: ExecutorPort]
  → ExchangeAdapter.place_order()    [Phase 5.1: ExchangeAdapterPort]
  → ExecutionResult callback
  → on_execution_result listeners (12개)   [Phase 5.2: 분리]
  → ExecutionJournal.append() + PnLLedger.record() + ...
```

### 4.2 Mode-specific 분기점

| 분기점 | 현재 | Phase 5.4 후 |
|-------|------|-----------|
| Mode dispatch | `Engine.run()` if-elif | `create_mode_runner(mode)` |
| Executor | `_paper_executor` vs `_executor` (AtomicExecutor) | 단일 ExecutorPort, adapter 다형성 |
| DataFeed | `_real_data_feed_loop` vs synthetic GBM | DataFeedPort + adapter |
| Risk gate | live: PreTradeValidator, paper: PaperMode 직접 | 단일 RiskPort |

### 4.3 Pre-Phase-5 audit 체크리스트

- [ ] 현재 SignalGenerator → Strategy → Executor 호출 trace (paper canary log 분석)
- [ ] 12 listener 식별 (on_execution_result 본문 분석)
- [ ] EngineState mutable field 식별
- [ ] god-object attr access 카운트 (`engine._X` 패턴)
- [ ] 기존 mock pattern 분석 (test files)

---

## 5. 검증 사이클 (각 commit마다)

### 5.1 매 commit 필수 단계 (체크리스트)

- [ ] design doc 업데이트
- [ ] 산업 표준 비교 (Nautilus/LEAN)
- [ ] 코드 변경
- [ ] `python -c "from src.main import Engine"` import smoke
- [ ] `pytest tests/unit/` 회귀 (5056 pass 유지)
- [ ] `code-reviewer` agent 리뷰
- [ ] `omc ask codex` review (CLI)
- [ ] `omc ask gemini` review (CLI)
- [ ] **paper canary 가동** (5분 minimum, fills + journal events 확인)
- [ ] commit (Co-Authored-By + 14-doc sync)
- [ ] push origin main

### 5.2 사이클 실패 시 처리

- pytest 실패 → 즉시 fix
- code-reviewer CRITICAL → 즉시 fix
- AI CLI conflict (codex vs gemini 의견 차이) → 사장님 결정 대기
- paper canary crash → 롤백 + retro

---

## 6. Phase 5 Timeline (총 13-18일)

| Phase | 작업 | 기간 | 위험 |
|-------|------|------|------|
| 5.0 | Pre-audit (통신 플로우 + dependency graph) | 1-2d | LOW |
| 5.1 | Port 인터페이스 7개 | 2-3d | LOW |
| 5.2 | Engine god-object 해체 | 5-7d | **HIGH** |
| 5.3 | LifecycleManager | 2-3d | MED |
| 5.4 | ModeRunner ABC | 3-4d | MED |
| 5.5 | Module size pre-commit hook | 1d | LOW |
| **총계** | | **13-18d** | |

---

## 7. Acceptance Criteria (전체 Phase 5)

- AC-1: `engine: "Engine"` 첫 인자 0개 (현재 26개)
- AC-2: runtime/ 모든 파일 < 400 LOC (현재 3개 위반)
- AC-3: `if engine._engine_mode == X` 0개 (현재 다수)
- AC-4: 모든 변경 마다 (pytest + code-reviewer + codex + gemini + paper canary) 5단계 완료
- AC-5: 5056+ pytest pass 유지
- AC-6: paper canary 안정 (Day 6/12 모듈 active log 확인)
- AC-7: SOLID DIP 준수 (specific Port 의존성)

---

## 8. 사장님 결정 대기

1. **Phase 5 진행 승인**?
2. **Phase 5.0 pre-audit 먼저** (통신 플로우 trace + dependency graph 그림 작성)?
3. **검증 사이클 5단계 (pytest + code-reviewer + codex + gemini + paper)** 매 commit 강제?
4. **paper canary v12 가동 중 Phase 5 진행** OR **Phase 5 완료 후 paper 재기동**?

---

## 9. 참조

- 산업 표준 audit: `/Users/100aniv/Development/arbitrage_OMC/engine/docs/plans/single-pipeline-integration.md`
- Phase 4 결과 commit: `0debd51` (mode_loops 추출, main.py 4203 → 689)
- exa.ai research: NautilusKernel + LEAN IBrokerage + Hummingbot ConnectorBase
- 메모리 참조: `feedback_pipeline_must_be_unified.md`, `feedback_config_no_fragmentation.md`
