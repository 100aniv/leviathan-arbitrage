# LEVIATHAN Execution Command

> ralph 루프 + Stage B TeamCreate 방식으로 prd.json US 자동 순회.
> 사용법: `/project:leviathan` 또는 `/project:leviathan US-010부터`

## 0. ZERO TOLERANCE + ANTI-STALL

**절대 금지**: 사용자 확인 요청, Stage 간 멈춤, 에이전트 대기 중 멈춤, US 간 멈춤, 상태 보고만 하고 멈춤, **run_in_background: true**
**강제**: 모든 응답에 텍스트 1줄 + tool call 병행. Stage A→B→C→다음Phase 끊김 없는 연속 흐름. **모든 Agent foreground 실행.**
**외부 CLI**: codex/gemini/qwen은 **동일 메시지 foreground 병렬** + `timeout 300`. 실패 시 3회 재시도 → 전원 실패 시 L5 (스킵 절대 금지).
**멈춤 허용**: (1) TF Final PASS (Live 준비) (2) 5회 연속 동일 US/TF 실패 (L5) (3) 사용자 "stop/cancel/멈춰"
**에이전트 반환**: **파일 기반**. 결과를 `.omc/artifacts/{agent}-{phase}.md`에 기록. 반환은 `PASS/FAIL + 경로 + 핵심 요약`. 상세는 `.omc/artifacts/`에 기록. 컨텍스트 팽창 원천 차단.
**CLI 전용**: tmux/터미널에서 실행. VS Code에서 실행 금지 (freeze 위험 #26778).
**Watchdog**: Dev봇이 watchdog. `python -m src.infra.telegram_dev_bot` (또는 `bash scripts/watchdog.sh`). tmux 멈춤 감지 → 알림 → 자동 재개. `/go`로 텔레그램 수동 재개.
**ANTI-STALL (#30625/#33043/#34238)**: 텍스트 없는 응답 = stop hook 루프 종료. 에이전트 결과 수신 → 즉시 다음 tool call. "다음 세션에서" 금지. TeamCreate 30초 무응답 → TeamDelete → Agent() fallback.

```
Stage A (Entry Gate → 기획 → QUANT GATE → checkpoint)
  → Stage B (TeamCreate → pytest → TeamDelete → Shadow 10min+ → checkpoint)
  → Stage C (Assembly → 멀티모델 감사+Go/No-Go → 코드리뷰 → SSOT+git)
  → 다음 Phase Stage A
```

> **용어**: "Stage" = 워크플로우 (A~C), "Phase" = 로드맵/PRD (S15~S21 등). 혼용 금지.

---

## 1. 소스

- `SSOT.md` — 유일한 설계 문서. 작업 전 필수 읽기.
- `.omc/prd.json` — US 목록. `passes:false`인 첫 번째 US부터 시작.
- 팀 구조, 기술 스택, 커스텀 에이전트, 자주 틀리는 패턴, 에스컬레이션, 인프라, 텔레그램 → **CLAUDE.md 참조**

## 2. 실행 모드

**시작 즉시:** `Skill("oh-my-claudecode:ralph")`

> `ralph` (올바름) vs `team ralph` (잘못됨 — OMC 파이프라인 충돌). 절대 변경 금지.
> LEVIATHAN 팀은 Stage B에서 `TeamCreate("leviathan-phase-X")`로 Phase별 직접 생성/삭제.

ralph 루프 안에서 **Phase 단위**로 **3-Stage Sequential** 수행:
- Phase 진입 시 모든 `passes:false` US 배치 수집 → Stage A~C 1사이클로 처리.

---

### Stage A — 기획 (Plan)

#### 자동 일관성 검사
`cd engine && python -m src.workflow.cli --root $(git rev-parse --show-toplevel) check_all`
→ OK면 Karina에게 "PASS" 주입. DRIFT/ERROR면 Entry Gate 전 수정.

#### Step 1: Karina — Entry Gate (순차)
Karina(architect/opus): Entry Gate — SSOT+CLAUDE.md+prd.json 3-way 정합성. passes 카운트, 테스트 수, Phase 순서, 대상 파일 코드 구조. 불일치 → 수정 완료까지 B 진입 금지. **반환 1K 토큰 이내.**

> 결과 수신 즉시 Step 2 스폰.

#### Step 2: NingNing + Winter + Giselle (병렬, Karina 결과 주입)
- NingNing(analyst): 요구사항 분석 + AC 검증 + 엣지케이스. **1K 토큰.**
- Winter(critic/opus): 기획 비판 — 설계 결함, 누락, 과복잡성. **1K 토큰.**
- Giselle(planner): PLAN.md 작성 → `docs/planning/Phase-X_PLAN.md`. **1K 토큰. 상세는 PLAN.md에.**

> Entry Gate 불일치 시 수정 후 재검사 (최대 2회).

#### 배치 수집
1. progress.json `us_targets` 확인 → 있으면 재사용
2. 없으면: `Grep('"passes":\s*false', '.omc/prd.json')` → progress.json 캐싱
3. **prd.json 전체 Read 금지** (32K tokens 초과)
4. 의존성 분석 → 독립 배치(최대 5 US), 의존 순차. 도메인별 그룹.

#### PLAN REVIEW GATE — 멀티모델 감사 (PLAN.md 완성 후)

3개 외부 모델 병렬 + Claude 독립 리뷰 → quorum 합의 (2+ 지적 = MUST FIX):
- codex-plan-reviewer: `timeout 300 codex exec -s read-only '$REVIEW_PROMPT'`
- gemini-plan-reviewer: `timeout 300 gemini -p '$REVIEW_PROMPT' --approval-mode plan`
- qwen-plan-reviewer: `timeout 300 qwen --approval-mode plan -p '$REVIEW_PROMPT'`

REVIEW_PROMPT: "docs/planning/Phase-X_PLAN.md 읽고 설계 결함/누락/과복잡성/엣지케이스 지적. CRITICAL/HIGH/MEDIUM."
결과 → `.omc/artifacts/consensus-plan-{phase}.md`. MUST FIX → PLAN 수정 (최대 1회).

#### QUANT GATE — 전략/수식 키워드 포함 시에만

키워드: `slippage|signal|strategy|executor|funding|futures|triangular|statistical|friction|cost_calculator|regime|hmm|xgboost|onnx|dex|gas_oracle`
- Yeji(quant-validator/opus): 파라미터 범위, 이중계산, PnL 영향, 수식 일치. PASS/FAIL. **1K 토큰.**

PASS → Stage B. FAIL → 수정 후 재호출 (최대 2회).

**A→B 전환 (동일 메시지 병렬 필수):**
'Stage B 진입.' 텍스트 1줄 + `state_write(next_stage:"B")` + `TeamCreate("leviathan-phase-X")` 동일 메시지 병렬.

---

### Stage B — 구현 + 검증 (Build + Verify)

#### B-Step 1: 개발 + 단위테스트 (TeamCreate)

`TeamCreate(team_name="leviathan-phase-X")`

**필수 Teammate:**
- **Yujin** (executor): `engine/src/` 구현. dashboard/tests/ 금지. 완료 시 Wonyoung에게 SendMessage.
- **Wonyoung** (test-engineer): `tests/` 작성 + `pytest -x --tb=short`. 결과 Lead 보고.

**조건부:**
- **Rei** (designer): `files`에 `dashboard/` OR AC에 `UI/프론트` → `dashboard/src/` 구현.
- **Gaeul/Leeseo/Liz** (executor): 독립 모듈 병렬 시 추가 (최대 4명).

**D-verify (대시보드/API US 시 필수) — Chrome 검증:**
1. `preview_start("dashboard")` → localhost:3000
2. 4페이지 순회: `preview_snapshot()` + `preview_network()` (API 200)
3. `preview_resize(preset="mobile")` → 모바일 확인
4. `preview_screenshot()` → 증거 캡처

**완료:** Wonyoung pytest PASS → 전원 `shutdown_request` → `TeamDelete()`
**파일 소유권:** Yujin/Gaeul/Leeseo/Liz=`engine/src/`, Rei=`dashboard/src/`, Wonyoung=`tests/`

#### B-Step 2: Shadow + 모니터링 (Sub-agents)

**B-2-1. Docker 인프라:**
```
docker compose up -d timescaledb redis && docker compose ps
docker compose stop engine auto-tuner monitoring 2>/dev/null
```

**B-2-2. Shadow 10min+:**
Minji(shadow-tester): `cd engine && timeout 600 python -m src.main`. 결과 → `.omc/state/shadow-result-latest.json`. **1K 토큰.**

**B-2-3~6 (조건부 병렬):**
- Danielle(scientist/haiku): PnL/WR/DD 분석 — Shadow 중 병렬
- Hanni(qa-tester/haiku): CLI/런타임 검증
- Haerin(browser-verifier): 대시보드/API US 시 Chrome 검증 (API 200 + WS 연결 확인)
- Hyein(debugger): crash 시만

**Shadow 13항목**: CLAUDE.md 참조 (crash=0, >=10min, PnL>=0, MDD<5%, PF>1.0, 신호>=100/day, KillSwitch not halted, CB CLOSED, Health>=95%, loss_capped=0, 전략별 trade>=1, 방어로그>=1, 결과파일 존재)

#### B-Step 3: Shadow 실패 시 fix loop

Type W(Wiring: trade=0) → 즉시 L2. Type P(PnL<0, 전략 활성) → 3회. Type B(crash) → 3회. 분류는 Hyein(debugger).

**체크포인트:** `cd engine && python -m src.workflow.cli --root $(git rev-parse --show-toplevel) checkpoint save --trigger "stage_B_complete"`

**B→C 전환:** pytest PASS + Shadow 13항목 PASS → cleanup → `python -m src.workflow.cli transition shadow_pass` + `state_write(next_stage:"C")` + C-Step 1 스폰. 텍스트 1줄 + tool call 병행.

---

### Stage C — 리뷰 + 릴리스 (Review + Release)

#### C-Step 1: Assembly Verification (코드리뷰 전 필수)

> 조건부: `git diff --name-only`에 `class ` 또는 `__init__` 변경 시에만. 아니면 스킵.
> Assembly FAIL → B-Step 1 복귀. PASS → C-Step 2.

Assembly Verifier(verifier/sonnet): 4개 서브체크 — Init Chain, Signal Flow E2E, Dead Wiring, Config Flag. **1K 토큰.**

**체크포인트:** Assembly PASS 후 `checkpoint save --trigger "assembly_gate_pass"`

#### C-Step 2: 코드리뷰 + 보안 + 멀티모델 감사 (병렬, Assembly PASS 후)

> Claude 미포함 = 확증 편향 제거. 코드리뷰/보안/멀티모델 감사를 1회 병렬로 통합.

AUDIT_PROMPT: "Phase X 변경 파일 감사. git diff main...HEAD.
Part 1: 로직 오류, 엣지케이스, 수학, wiring. CRITICAL/HIGH/MEDIUM.
Part 2: Go/No-Go 판정 — PLAN 이행, Shadow 13항목, CRITICAL/HIGH 해결, wiring 완전성. Go/No-Go + 근거."

병렬 실행:
- Jennie(code-reviewer/opus): 종합 리뷰 — API 계약, 하위호환, SOLID, 통합 추적, Shadow trade=0 집중. 산출물: `docs/review/Phase-X_REVIEW.md`. **1K 토큰.**
- Jisoo(security-reviewer): JWT/API키/OWASP Top 10/시크릿 노출. **1K 토큰.**
- codex-auditor: `timeout 300 codex exec -s read-only '$AUDIT_PROMPT'`
- gemini-auditor: `timeout 300 gemini -p '$AUDIT_PROMPT' --approval-mode plan`
- qwen-auditor: `timeout 300 qwen --approval-mode plan -p '$AUDIT_PROMPT'`

Quorum: 2+ CRITICAL/HIGH = MUST FIX → B-Step 1 복귀. 과반수 Go = Go.
조건부: Yeji(퀀트, 수식 변경 시), Haerin(Chrome, 대시보드/API US 시)
결과 → `.omc/artifacts/consensus-audit-{phase}.md`.

#### C-Step 3: Karina Go/No-Go (C-Step 2 결과 주입)

Karina(architect/opus): Phase 완료 리뷰 7항목 — PLAN 이행, REVIEW CRITICAL/HIGH 해결, Shadow 13항목, Assembly 5-check, AC 충족(⚡ WIRING), 3-way 정합성, 외부 모델 Go/No-Go 참조. FAIL → 유형별 복귀. **1K 토큰.**

#### C-Step 4: SSOT + Git (Go 시, 팀 종료 후 단독)

Sakura(ssot-keeper/sonnet): passes:true 전 런타임 증거 확인. prd.json 업데이트, SSOT §2 업데이트, 이월 항목, §7 헤더 동기화, CLAUDE.md 동기화, 3곳 대조, git add+commit+push. **1K 토큰.**

**일관성 재검증 + 체크포인트 + FSM sync:**
```
cd engine && python -m src.workflow.cli --root $(git rev-parse --show-toplevel) check_all
cd engine && python -m src.workflow.cli --root $(git rev-parse --show-toplevel) checkpoint save --trigger "phase_complete"
cd engine && python -m src.workflow.cli --root $(git rev-parse --show-toplevel) sync --phase X --tests Y --prd-pass Z --prd-total W
```

**C→다음Phase:** Karina Go + Sakura push → cleanup → 텔레그램 알림(정보 전달, 대기 없음) → `state_write(next_stage:"A", next_phase:"Phase-Y")` → 즉시 다음 Phase.

---

## 6. 컨텍스트 관리 (Stage별 자동 분할)

**`/compact` 절대 금지. Stage 전환 시 자동 `/clear` + checkpoint 재개로 fresh context 보장.**

| 시점 | 동작 |
|------|------|
| A 완료 | `transition plan_approved` → checkpoint save → `/clear` → progress.json에서 B 재개 |
| B-1 완료 | pytest PASS + TeamDelete → 즉시 B-2 Shadow |
| B-2 완료 | `transition shadow_pass` → checkpoint save → `/clear` → progress.json에서 C 재개 |
| C-1~3 | 끊김 없이 체인 실행 |
| C-4 완료 | SSOT+push + `sync --phase X --tests Y --prd-pass Z --prd-total W` + 텔레그램 → checkpoint save → `/clear` → 다음 Phase A 재개 |

> `transition` 명령: `cd engine && python -m src.workflow.cli --root $(git rev-parse --show-toplevel) transition <event>`
> `sync` 명령: `cd engine && python -m src.workflow.cli --root $(git rev-parse --show-toplevel) sync --phase X --tests Y --prd-pass Z --prd-total W`

**체크포인트**: `.omc/state/leviathan-progress.json` (Phase Loop) + `.omc/state/leviathan-tf-status.json` (TF 추적).
**에이전트 결과**: `.omc/artifacts/{agent}-{phase}.md` 파일에 기록. 메인 컨텍스트에는 PASS/FAIL + 경로만.
**Watchdog**: Dev봇 독립 프로세스 = watchdog (`python -m src.infra.telegram_dev_bot`). tmux 멈춤 감지 + 알림 + 자동 재개 + `/go` 수동 재개.

**60% 컨텍스트:** 즉시 현 Stage 마무리 → checkpoint → `/clear` → progress.json 재개.

---

## 7. TF (Task Force) — Phase Loop 완료 후 자동 진입

> 전 US `passes:true` 감지 시 자동 진입. 별도 Skill 호출 불필요.
> TF 상세 절차 → **`.claude/commands/leviathan-tf.md`** 참조.

### 진입 조건
prd.json 전수 확인 → `passes:false` = 0건 → TF 진입.
- `leviathan-progress.json` 업데이트: `{"status": "tf-entry", "current_phase": "TF-QF"}`
- `leviathan-tf-status.json` 읽기 → 현재 라운드/상태 확인 후 해당 지점부터 재개

### 상태 파일 (2개 분리)
- **Phase Loop**: `.omc/state/leviathan-progress.json` — `current_phase/current_stage/current_step/status`
- **TF 추적**: `.omc/state/leviathan-tf-status.json` — 라운드별 상세 (QF/SF/PF/Final status, history, assembly)

### TF 실행 (leviathan-tf.md 절차 인라인 실행)
1. **TF QF**: leviathan-tf.md §QF 절차 실행
   - PASS → `tf-status.json` qf.status:"PASS" → SF 진입
   - FAIL → 회귀 처리 (아래)
2. **TF SF**: leviathan-tf.md §SF 절차 실행
   - PASS → `tf-status.json` sf.status:"PASS" → PF 진입
   - FAIL → 회귀 처리
3. **TF PF**: leviathan-tf.md §PF 절차 실행
   - PASS → `tf-status.json` pf.status:"PASS" → Final 진입
   - FAIL → rollback 후 PF 재시도 (최대 2회, 다른 리팩토링 전략). 2회 실패 → PF 스킵 → Final 직행
   - ※ PF는 prd.json 비관여 (git rollback 기반 회귀)
4. **TF Final**: leviathan-tf.md §Final 절차 실행
   - PASS → Live 준비 완료 → 텔레그램 알림 → **루프 정상 종료**
   - FAIL → 항목별 수정 → Final 재검증 (최대 3회, 코드 변경 시 SF부터, 구조 변경 시 PF부터)

### TF 회귀 자동화
1. FAIL 항목에서 회귀 US 도출 (예: `US-S22-001: TF QF에서 발견된 X 수정`)
2. prd.json에 추가 (`passes:false`, `phase: "S{N}-regression"`)
3. `tf-status.json`에 `regression_phase` 기록, `progress.json` status:"regression"
4. → 루프가 `passes:false` 감지 → Phase Loop 자연 복귀 (Stage A→B→C)
5. 회귀 완료 → 전 US `passes:true` → TF 재진입
   - QF 회귀: QF부터 재실행
   - SF 회귀: QF 스킵 (구조적 결함 시 QF부터)
   - PF 회귀: git rollback (prd.json 비관여) → PF 재시도 (최대 2회) → 스킵 시 Final 직행
   - Final 회귀: 코드 변경 시 SF부터, 구조 변경 시 PF부터

### 에스컬레이션
- 동일 TF 라운드 3회 연속 FAIL → 접근법 변경 (다른 수정 전략)
- 5회 연속 FAIL → L5 텔레그램 알림 + 루프 일시정지 + `/approve` 대기

---

## 9. 시작 및 자동 루프

재개 순서:
1. `.omc/state/leviathan-progress.json` → `next_stage`로 재개
2. `$ARGUMENTS` → 해당 US부터 Stage A
3. prd.json 스캔 → `passes:false` 첫 US Phase → Stage A

모든 US `passes:true`까지 Phase Loop 자동 진행. 전 US 완료 = TF 진입 트리거 (멈춤 아님).
**멈춤: TF Final PASS (Live 준비) OR L5 에스컬레이션 OR 사용자 stop.**

---

## 99. CONTINUATION ANCHOR

> §0 참조. 압축 후에도 유지: 결과 수신 → 즉시 tool call. 텍스트만 = BUG. A→B→C→다음A. 모든 Agent foreground. 텍스트 1줄 + tool call.
