# US-386 — shadow.py → paper.py 모놀리스 이전 Plan

**Date**: 2026-04-22
**Status**: DRAFT (Phase L L-1 / US-386 미완 작업)
**Risk**: HIGH (2,679 LOC 모놀리스 이전, downstream import 30+)
**Trigger**: SSOT.md 정정에서 "전면 리네임" 선언이 부분 완료임을 확인 (`f304355`). 클래스명만 PaperMode, 본체는 shadow.py에 잔존.

---

## 1. 현재 상태

```
engine/src/modes/shadow.py  (2,679 LOC) — 실 구현
  - PowerLawSlippage (line 77)
  - BookWalkSlippage (line 118)
  - VirtualBalanceTracker (line 198)
  - StrategyStats (line 258)
  - PaperRateLimiter (line 272)
  - PaperStats (line 338)
  - PaperMode (line 370)         ← 메인 클래스
  - ShadowMode = PaperMode      (line 2673, alias)
  - _execute_shadow_trade*       (line 2678-2679, alias)

engine/src/modes/paper.py  (43 LOC) — re-export shim
  - from .shadow import PaperMode
  - from .shadow import ShadowMode  # backward compat

engine/src/tuning/shadow_runner.py  (~?? LOC)
  - class ShadowRunner — 클래스명 잔존
```

## 2. 위험 인벤토리

`from src.modes.shadow import` 또는 `from src.modes.paper import` 사용처를 모두 찾아 영향 평가:

```bash
grep -rE "from src\.modes\.(shadow|paper) import" engine/src engine/tests
```

예상 30+ 호출처:
- `src/main.py` (1+)
- `src/api/*` (다수, dashboard backend)
- `src/cli/paper_runner.py`
- `src/tuning/shadow_runner.py`
- `tests/unit/modes/*` (다수)

## 3. 단계별 이전 Plan

### Step 1 — paper.py를 메인으로 (1일)
1. `engine/src/modes/shadow.py` → `engine/src/modes/paper.py`로 파일 이름 변경 (git mv)
2. 새 `engine/src/modes/shadow.py` 생성 = paper.py re-export shim (backward compat)
3. `from src.modes.paper import` 에 PaperMode/ShadowMode/PaperStats 등 모두 expose
4. 5,053 regression 유지 검증

### Step 2 — 호출처 마이그레이션 (1-2일, 격리 수정)
1. `src/main.py` import path 갱신 — `from src.modes.paper import PaperMode` 명시
2. `src/api/*`, `src/cli/paper_runner.py` 동일
3. `tests/unit/modes/*` 일괄 import 갱신
4. 각 단계 후 regression 5,053 유지

### Step 3 — ShadowRunner → PaperRunner 리네임 (0.5일)
1. `engine/src/tuning/shadow_runner.py` → `paper_runner.py` (rename)
2. `class ShadowRunner` → `class PaperRunner` + `ShadowRunner = PaperRunner` alias
3. tuning import 호출처 갱신

### Step 4 — DATA_MODE=shadow deprecation (0.5일)
1. `engine.json` mode validator에 `data_mode=shadow` 받으면 warning + auto-translate `data_mode=paper`로 전환
2. `engine/.env.example` 주석에 `data_mode=shadow` 폐기 명시
3. CLI `paper_runner` warning 추가

### Step 5 — 완료 후 검증
1. 5분 dry-run + Pre-canary check 4 항목 PASS
2. SSOT.md US-386 passes:true 복구 + "전면 리네임 완료" 선언
3. PRD US-386 passes:true
4. Architect APPROVE

## 4. Acceptance Criteria

- AC-1: `engine/src/modes/paper.py` 가 메인 (2,679 LOC), `shadow.py`는 ≤ 50 LOC re-export
- AC-2: `class PaperRunner` (`tuning/paper_runner.py`)가 메인, `ShadowRunner` alias만
- AC-3: 모든 5,053 regression 유지
- AC-4: `engine.json` `data_mode=shadow` deprecation warning 동작
- AC-5: 5분 Pre-canary check (universe_matrix > 0, fill ≥ 1, crash 0, PnL > 0) PASS
- AC-6: 14-doc sync 갱신 (SSOT, README, engine/README, RUNBOOK)

## 5. Rollback Criteria

- 5,053 pass 깨지면 git revert 후 Stage A 복귀
- Pre-canary check 실패 시 즉시 rollback (universe_matrix 영향)

## 6. 시간 견적

총 3-4일. HIGH risk (다수 import). worktree + /careful 권장. Day 16 retrograde 교훈 적용 — TDD RED 먼저, 각 단계 후 regression 측정.

## 7. 우선순위

**현재 우선순위 LOW**: 이름만 부분 리네임이라도 운영 정상 (PaperMode 클래스 정상 동작, ShadowMode alias 호환). Path-B v2 Gate 통과 → Live 거래 → 수익 확인 후 정리하는 게 옳음.

**시작 조건**: Phase L Live $10 카나리 통과 + 운영 안정화 후 정리 작업.
