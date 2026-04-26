# Phase 6 — risk_execution → Dispatcher 위임 (HIGH risk)

**Date**: 2026-04-26
**Status**: PLAN (paper canary 정지 후 진행)
**Priority**: HIGH — Phase 5 god-object 해체의 마지막 단계

---

## 1. 배경

Phase 5 완료 (5.0~5.5):
- 14 ExecutionResultListener 분리 ✅
- ExecutionResultDispatcher (sequential + 예외 격리) ✅
- ListenerFactory (Engine → 14 listeners builder) ✅
- LifecycleManager + ModeRunner ABC + LOC budget ✅

**잔존 위험**: `engine/src/runtime/risk_execution.py:on_execution_result` 360 LOC god-function이 production paper canary에서 active. Phase 6 에서 dispatcher 위임으로 완전 단축 (~20 LOC).

---

## 2. 작업 범위

### 2.1 변경 대상

| 파일 | 변경 전 LOC | 변경 후 LOC |
|------|------------|------------|
| `engine/src/runtime/risk_execution.py` | 878 | ~250 (-628) |
| `engine/src/main.py` | 696 | ~700 (Dispatcher init +4) |

### 2.2 변경 내용

#### 2.2.1 main.py Engine.__init__ wiring

```python
from src.listeners.factory import build_dispatcher_from_engine

# Engine.__init__ 후반부 (모든 의존성 초기화 후)
async def _init_listeners(self) -> None:
    """Phase 6: 14 listeners + dispatcher 빌드 (모든 init 완료 후 호출)."""
    self._listener_dispatcher = build_dispatcher_from_engine(self)
    logger.info("ListenerFactory built %d listeners", self._listener_dispatcher.listener_count)
```

#### 2.2.2 risk_execution.py:on_execution_result 단축

```python
def on_execution_result(engine, trade_request, execution_result) -> None:
    """Phase 6: dispatcher 위임으로 단축 (360 LOC → 20 LOC)."""
    if engine._listener_dispatcher is None:
        # Phase 5.2.6 fallback: factory 미빌드 시 legacy logic
        return _legacy_on_execution_result(engine, trade_request, execution_result)
    engine._listener_dispatcher.dispatch_sync(trade_request, execution_result)
```

#### 2.2.3 cross_gross_exposure sync (Phase 5.2.6 leftover)

CrossHedgeListener는 list[Decimal] holder를 mutate. Engine._cross_gross_exposure를 매 dispatch 후 sync 필요:

```python
# main.py post-dispatch sync (또는 EngineState.cross_gross_exposure 직접 mutate)
self._cross_gross_exposure = self._listener_dispatcher._listeners[3]._gross_holder[0]
```

또는 Phase 5.2.1 EngineState에 cross_gross_exposure 필드 활용 → CrossHedgeListener가 직접 mutate.

---

## 3. 위험 평가

### 3.1 HIGH 위험

1. **Paper canary 회귀**: dispatch_sync()가 listener 14개 sequential 실행 — 한 listener slow 시 전체 latency 증가.
   - **Mitigation**: 각 listener perf budget 측정 (post-Phase 6 첫 commit).
2. **Listener wiring 누락**: ListenerFactory 호출 시점 — 모든 의존성 초기화 후여야 함.
   - **Mitigation**: `_init_listeners()`를 `_start_background_tasks()` 직전에 배치.
3. **idempotency 위반**: 4 listeners (PositionSizeLeak/CrossHedge/PnLPeak/PositionManager) NOT idempotent.
   - **Mitigation**: Phase 7+ fill_id dedup 추가 (별도 작업).

### 3.2 MED 위험

4. **dispatch_sync() vs dispatch()** 선택: paper hot path는 sync (asyncio.ensure_future 잔여 task 추적 어려움).
5. **Backward-compat**: 기존 `on_execution_result`를 호출하는 모든 caller 검증 (테스트 401+ 영향 없는지).

---

## 4. 단계별 진행

### 4.1 Step 1: ListenerFactory 통합 (LOW risk, 1d)

- main.py Engine에 `_init_listeners()` 추가
- `_start_background_tasks()` 직전에 호출
- `self._listener_dispatcher` attr 추가 (None default)
- Engine 인스턴스 생성 + Engine.run() 통과 검증
- pytest 5119+ pass 회귀

### 4.2 Step 2: risk_execution.on_execution_result 단축 (HIGH risk, 1-2d)

- legacy 함수 _legacy_on_execution_result()로 rename + private 마커
- 새 on_execution_result는 dispatcher 위임 + fallback to legacy
- 단계적 cutover (env flag `EXECUTION_DISPATCHER_ENABLED` default false)
- paper canary 30분 검증 (Day 6/12 모듈 + 14 listeners 모두 active log)
- env flag enable 후 60분 검증

### 4.3 Step 3: legacy 함수 삭제 (1d)

- 모든 paper canary 사이클 PASS 후
- `_legacy_on_execution_result` 삭제
- risk_execution.py 878 → 250 LOC

### 4.4 Step 4: 14-doc sync + commit (1d)

- SSOT.md, CHANGELOG.md, REFACTOR_PLAN.md, MODULE_DESIGN.md
- LOC budget update (risk_execution 1000 → 300)
- ExecutionResultDispatcher integration test 강화

---

## 5. Acceptance Criteria

- AC-1: pytest 5119+ pass / 14 skip ZERO regression
- AC-2: paper canary 60분 가동 + 14 listeners 활성화 log + ExecutionJournal 정상
- AC-3: risk_execution.py < 300 LOC (LOC budget)
- AC-4: dispatcher latency p95 < 50ms per dispatch (perf budget)
- AC-5: idempotency follow-up plan (Phase 7) 문서화

---

## 6. 진행 조건

- 사장님 paper canary 정지 명령 OR 별도 worktree
- Phase 5 모든 commit push 완료 (현재 ✅)
- v12 paper canary 안정 검증 완료 (현재 2h+ stable ✅)
