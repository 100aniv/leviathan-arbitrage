# LEVIATHAN Execution Command

> ralph 루프 + Stage B TeamCreate 방식으로 prd.json US 자동 순회.
> 사용법: `/project:leviathan` 또는 `/project:leviathan US-010부터`

## 0. ZERO TOLERANCE (최우선 규칙)

**절대 금지**: 사용자 확인 요청, Stage 간 멈춤, 에이전트 대기 중 멈춤, US 간 멈춤, 상태 보고만 하고 멈춤, **run_in_background: true**
**강제**: 모든 응답에 tool call 포함. Stage A→B→C→다음Phase 끊김 없는 연속 흐름. **모든 Agent는 foreground 실행** (background 금지 — 메인 스레드 멈춤 버그).
**멈춤 허용**: (1) 전 US `passes:true` (2) 5회 연속 동일 US 실패 (3) 사용자 "stop/cancel/멈춰" (4) Stage C 완료 후 사장님 승인 대기

```
Stage A (Entry Gate → 기획 → QUANT GATE → checkpoint)
  → Stage B (TeamCreate → pytest PASS → TeamDelete → Shadow 10min+ → 모니터링 → checkpoint)
  → Stage C (코드리뷰+보안 → Phase 완료 리뷰+Go/No-Go → SSOT+git push → 텔레그램 → 사장님 승인)
  → 다음 Phase Stage A
```

> **용어 규칙**: "Stage" = 워크플로우 단계 (A~C), "Phase" = 로드맵/PRD 단계 (G/H/I/K/L/M/F). 혼용 금지.

---

## 1. 소스

- `SSOT.md` — 유일한 설계 문서. 작업 전 반드시 읽기.
- `.omc/prd.json` — 146 US 목록. `passes:false`인 첫 번째 US부터 시작.
- 팀 구조, 기술 스택, 커스텀 에이전트, 자주 틀리는 패턴 → **CLAUDE.md 참조** (여기서 중복 기술하지 않음)

## 2. 실행 모드

**시작 즉시 실행 (건너뛰기 금지):** `Skill("oh-my-claudecode:ralph")`

> ⚠️ `ralph` (올바름) vs `team ralph` (잘못됨 — OMC 내장 파이프라인과 충돌). 절대 변경 금지.
> LEVIATHAN 팀은 Stage B에서 `TeamCreate("leviathan-phase-X")`로 Phase별 직접 생성/삭제.

ralph 루프 안에서 **Phase(로드맵) 단위**로 **3-Stage Sequential** 수행:

> **핵심 원칙**: Stage 사이클은 Phase(로드맵) 단위로 돈다. US 단위가 아님.
> Phase 진입 시 해당 Phase의 모든 `passes:false` US를 배치 수집 → Stage A~C 1사이클로 처리.

---

### Stage A — 기획 (Plan)

**[Entry Gate → 기획] — 2-Step Sub-agent (4명):**

> ⚠️ **run_in_background 절대 금지**. 모든 Agent는 **foreground**(기본값)로 실행.
> background 스폰 시 메인 스레드가 결과 대기 중 멈추는 버그 발생. 이미 3회 재발.

**Step 1: Karina — Entry Gate + 코드 탐색 (순차, 먼저 실행)**
```
Agent(subagent_type="oh-my-claudecode:architect", name="karina", model="opus",
      prompt="Entry Gate + 코드 탐색: SSOT.md + CLAUDE.md + prd.json 3-way 정합성 검사.
              **필수 체크 항목**:
              1) prd.json passes:true/false 카운트 vs SSOT.md §2 '완료된 US' vs §7 헤더 숫자 — 3곳 일치
              2) prd.json passes:true/false 카운트 vs CLAUDE.md PRD 숫자 — 일치
              3) SSOT.md §2 테스트 수 vs CLAUDE.md 테스트 수 — 일치
              4) SSOT.md §2 '다음 작업' vs CLAUDE.md '다음 작업' — 일치
              5) Phase 순서/기술스택/팀구조 일치
              6) 대상 파일 현재 코드 구조 파악
              **불일치 발견 시**: 구체적 수정 지시 (파일:라인 + 현재값→정확값). 수정 완료까지 Stage B 진입 금지.")
```
> Entry Gate 결과 수신 즉시 → Step 2 스폰. 상태 요약/보고 금지.

**Step 2: NingNing + Winter + Giselle — 병렬 (Karina 결과 주입)**
```
# NingNing: 요구사항 분석
Agent(subagent_type="oh-my-claudecode:analyst", name="ningning",
      prompt="Phase X 요구사항 분석: [us_targets 목록] + acceptanceCriteria 검증.
              엣지케이스 도출, 수용기준 누락/모호 확인. Entry Gate 결과 반영: [Karina 결과 주입]")

# Winter: 기획 비판
Agent(subagent_type="oh-my-claudecode:critic", name="winter", model="opus",
      prompt="Phase X 기획 비판: PLAN.md 초안 대비 설계 결함, 누락 사항, 과도한 복잡성 지적.
              Entry Gate 결과 반영: [Karina 결과 주입]")

# Giselle: PLAN.md 작성
Agent(subagent_type="oh-my-claudecode:planner", name="giselle",
      prompt="Phase X PLAN.md 작성: [us_targets 목록] + acceptanceCriteria 기반.
              SSOT.md §4 참조. 산출물: docs/planning/Phase-X_PLAN.md
              **Entry Gate 결과 반영**: [Karina 결과 ~3K 토큰 주입]")
```
> 3명 **병렬** 스폰. 근거: 독립 작업에 병렬 = +81% (Google DeepMind/MIT 2025).
> 결과 수신 즉시 다음 블록 tool call 발행. 상태 요약/보고 금지.

- Entry Gate 불일치 발견 → 수정 후 재검사 (최대 2회). 통과 시 QUANT GATE 진행.

**[기획] Phase 단위 배치 수집:**
1. `leviathan-progress.json`의 `us_targets` 확인 → 있으면 그대로 사용 (prd.json 재파싱 생략)
2. `us_targets` 없을 때만: `Grep(pattern='"passes":\s*false', path='.omc/prd.json')`로 현재 Phase US 추출 → progress.json에 캐싱
3. **prd.json 전체 Read 절대 금지** (32K tokens 초과로 Read 도구 실패함)
4. 의존성 그래프 분석 → 독립 US 배치, 의존 US 순차
5. 도메인(engine/dashboard) 기준 배치 그룹 형성
6. Phase 내 모든 배치 그룹 완료 = 1사이클 종료

**배치 규칙:**
- 동일 Phase + 동일 도메인 → 배치 가능 (최대 5 US)
- 다른 도메인 → 별도 배치
- `dependencies` 미충족 OR `files` 교집합 → 순차 실행
- 배치 → 통합 `docs/planning/Phase-X_PLAN.md` 작성

**복잡도 판단:** Step 2의 planner(giselle)가 PLAN.md 작성 + critic(winter)이 비판.
- 복잡 US(files 3+개): planner가 상세 기획 + architect가 아키텍처 검증 (Step 1에서 수행)
- **ralplan 직접 호출 금지** (plan mode 활성화 → 파일 수정 차단 데드락 위험)
- 산출물: `docs/planning/Phase-X_PLAN.md`

**QUANT GATE — `files`에 전략/수식 키워드 포함 시에만:**
키워드: `slippage|signal|strategy|executor|funding|futures|triangular|statistical|friction|cost_calculator|regime|hmm|xgboost|onnx|dex|gas_oracle`
```
Agent(subagent_type="quant-validator", name="yeji", model="opus",
      prompt="Phase X 기획 검증: SSOT.md §4 대비 PLAN.md 정합성.
              1) 파라미터 범위 2) 이중계산 여부 3) PnL 영향 4) 수식 일치. PASS/FAIL+근거")
```
- PASS → Stage B. FAIL → PLAN 수정 후 재호출 (최대 2회)
> 결과 수신 즉시 A→B 전환 tool call 발행. 상태 요약/보고 금지.

**활성 팀**: AESPA(기획, 4명) + ITZY(퀀트, 해당 시)

**A→B 전환 (동일 메시지 병렬 필수):** PLAN.md 존재 + QUANT PASS 확인 즉시 → 아래 2개를 **반드시 동일 메시지에서 병렬 호출**:
1. `state_write(next_stage:"B")`
2. `TeamCreate("leviathan-phase-X")`
텍스트 출력 금지. tool call만 출력. state_write만 하고 TeamCreate 안 하면 = BUG.

---

### Stage B — 구현 + 검증 (Build + Verify)

**개발(TeamCreate) → 단위테스트 → Shadow 10min+ → QA/모니터링을 하나의 Stage로 통합.**

> Stage B의 핵심: "만들고 동작 확인". 코드 작성부터 런타임 검증까지.
> Shadow fix loop가 Stage B 내부에서 완결 = 0 stage 전환 (기존 5-Stage: 3 stage 전환).

#### B-Phase 1: 개발 + 단위테스트 (TeamCreate)

**Step 1: 팀 생성**
`TeamCreate(team_name="leviathan-phase-X")`

**Step 2: Teammate 스폰 (동시 2-6명)**

**필수:**
- **Yujin** (executor): `engine/src/` 백엔드/엔진 구현 #1. dashboard/tests/ 수정 금지. 완료 시 Wonyoung에게 SendMessage.
- **Wonyoung** (test-engineer): `tests/` 단위+통합 테스트 작성 + `pytest -x --tb=short`. 결과를 Lead에게 보고.

**Rei(designer) 스폰 조건:**
- Phase D/H US → 항상
- `files`에 `"api/"`, `"shadow.py"`, `"dashboard/"` 포함
- `acceptanceCriteria`에 `"dashboard"`, `"UI"`, `"프론트"` 키워드
- `dashboard/src/` 구현. engine/ 수정 금지.

**병렬 개발 스폰 (독립 모듈 동시 개발 시):**
- **Gaeul** (executor): 병렬 백엔드 #2
- **Leeseo** (executor): 병렬 백엔드 #3 (대규모 시)
- **Liz** (executor): 병렬 백엔드 #4 (최대 병렬 시)

**D-verify US (US-063, US-064) — 메인 세션 직접 Chrome 검증:**
1. `preview_start("dashboard")` → localhost:3000
2. 4페이지 순회: `preview_snapshot()` + `preview_network()` (API 200)
3. `preview_resize(preset="mobile")` → 모바일 뷰 확인
4. `preview_screenshot()` → 증거 캡처

**Step 2.5: 통합 검증**
- Yujin 완료 → 대시보드 반영 필요 판단 (API/Shadow 변경 시)
- 필요 시 Rei 추가 스폰 + Chrome 검증
- **완료 기준**: pytest 0 failures **AND** 관련 대시보드 데이터 정상 표시

**Step 3: 통합**
- Wonyoung pytest → PASS: Step 4. FAIL: Yujin 수정 (최대 3회)

**Step 4: 팀 해산**
- 전원 `shutdown_request` → `TeamDelete()`

**파일 소유권:** Yujin/Gaeul/Leeseo/Liz=`engine/src/`, Rei=`dashboard/src/`, Wonyoung=`tests/`+`docker-compose.yml`, Lead=SSOT/`.omc/`

**배치 모드:** 배치 내 US 순차 Stage B Phase 1 → 전부 완료 후 Phase 2 일괄 실행. 동일 도메인은 팀 재사용 가능.

**활성 팀**: IVE(개발) — TeamCreate 협업 (최대 6명)

#### B-Phase 2: Shadow 테스트 + 모니터링 (Sub-agents)

> TeamDelete 후, 독립 Sub-agents로 런타임 검증.
> shadow-tester는 `context: fork`로 fresh context 자동 보장.

**B-2-1. Docker 확인**
- `docker compose up -d && docker compose ps` (실패 시 최대 2회 재시도)
- 전 컨테이너 healthy 필수

**B-2-2. Shadow 실행 (10분 이상 무중단)**
```
Agent(subagent_type="shadow-tester", name="minji",
      prompt="Shadow 10분+: docker compose up -d 확인 후
              cd engine && timeout 600 python -m src.main.
              PnL/WR/crash/DD 보고. 10분 미만 실행 = 자동 FAIL")
```

**B-2-3. 데이터 모니터링 (Shadow 실행 중 병렬)**
```
Agent(subagent_type="oh-my-claudecode:scientist", name="danielle", model="haiku",
      prompt="Shadow 실행 중 PnL/WR/DD 통계 분석. 이상치 탐지. 결과 보고")
```

**B-2-4. QA 검증**
```
Agent(subagent_type="oh-my-claudecode:qa-tester", name="hanni", model="haiku",
      prompt="CLI/서비스 런타임 검증. 엣지케이스 테스트. API 응답 확인")
```

**B-2-5. 대시보드 검증 — Phase D/H US에서만**
```
Agent(subagent_type="browser-verifier", name="haerin",
      prompt="Chrome DevTools로 대시보드 검증: 렌더링, API 200, WebSocket, 모바일 반응형")
```

**B-2-6. 에스컬레이션 — crash 발생 시에만**
```
Agent(subagent_type="oh-my-claudecode:debugger", name="hyein",
      prompt="crash 루트코즈 분석. 회귀 격리. 수정 방안 제시")
```

**Shadow 필수 조건:**
- `PnL > 0`
- `crash = 0`
- `10분 이상 무중단 실행`

**활성 팀**: NewJeans(테스트, 최대 5명) + ITZY(퀀트 수식검증, 해당 시)

#### B-Phase 3: Shadow 실패 시 fix loop

```
Shadow FAIL → 원인 분석 → fix (executor sub-agent) → pytest 재확인 → Shadow 재실행
fix loop 최대 3회. 3회 초과 시 Stage A 재기획 (에스컬레이션 L2)
```

> **3-Stage 핵심 이점**: Shadow 실패 → Stage B 내부에서 fix + re-test = 0 stage 전환.
> (기존 5-Stage: Shadow 실패(D) → B복귀 → pytest(B) → 리뷰(C) → Shadow(D) = 3 stage 전환)

**B→C 전환 (동일 메시지 병렬 필수):** pytest PASS + Shadow PASS (PnL > 0, crash = 0, 10min+) 확인 즉시 → 아래 2개를 **반드시 동일 메시지에서 병렬 호출**:
1. `state_write(next_stage:"C")`
2. Stage C 리뷰 에이전트 2개 스폰 (jennie + jisoo)
텍스트 출력 금지. tool call만 출력. state_write만 하고 에이전트 안 스폰하면 = BUG.

---

### Stage C — 리뷰 + 릴리스 (Review + Release)

**코드리뷰 → Phase 완료 리뷰(Go/No-Go) → SSOT+git push → 텔레그램을 하나의 Stage로 통합.**

> Stage C의 핵심: "이 코드가 좋은가?" + "이 Phase가 완료되었는가?" + 릴리스.
> 리뷰어가 Shadow 결과를 참조 → 코드 품질 + 런타임 동작 교차 평가 가능.

#### C-Step 1: 코드리뷰 + 보안리뷰 (병렬 Sub-agents)

```
Agent(subagent_type="oh-my-claudecode:code-reviewer", name="jennie", model="opus",
      prompt="Phase X 변경 코드 종합 리뷰. API 계약, 하위호환, 아키텍처 준수, 설계 비판(PLAN.md 대비).
              **품질 체크리스트**: SOLID 원칙 위반, 안티패턴, 에러 핸들링 일관성, 성능 병목, 유지보수성.
              **Shadow 결과 참조**: PnL/WR/crash 수치와 코드 로직의 정합성 교차 평가.
              **Entry Gate 컨텍스트**: [Karina Entry Gate 결과 ~3K 토큰 주입]
              docs/review/Phase-X_REVIEW.md 작성")

Agent(subagent_type="oh-my-claudecode:security-reviewer", name="jisoo",
      prompt="Phase X 보안 리뷰. JWT/API키/거래실행 보안, OWASP Top 10, 시크릿 노출")
```

**조건부 추가 에이전트 (C-Step 1과 병렬):**

**퀀트 검증 — 전략/수식 변경 시에만:**
```
Agent(subagent_type="quant-validator", name="yeji", model="opus",
      prompt="SSOT.md §4 수식 대비 Phase X 코드 검증. 이중계산/파라미터 범위/PnL 영향")
```

**Chrome 검증 — Phase D/H US에서만:**
```
Agent(subagent_type="browser-verifier", name="haerin",
      prompt="Chrome DevTools MCP로 대시보드 검증: 페이지 렌더링, API 200, WebSocket, 모바일 뷰")
```

> 리뷰 결과 수집 → CRITICAL/HIGH 이슈 발견 시 Stage B Phase 1 fix 루프 복귀.
> 결과 수신 즉시 C-Step 2 tool call 발행. 상태 요약/보고 금지.

#### C-Step 2: Phase 완료 리뷰 — Karina (PM + Architect)

**이것이 Phase 최종 리뷰. 모든 산출물을 종합하여 Go/No-Go 결정.**

```
Agent(subagent_type="oh-my-claudecode:architect", name="karina", model="opus",
      prompt="Phase 완료 리뷰 (PM+Architect):
              1. 계획 이행: PLAN.md 대비 실제 구현 — 빠진 항목 없는가?
              2. 리뷰 해결: REVIEW.md CRITICAL/HIGH — 전부 해결?
              3. 런타임 검증: Shadow 결과 — PnL > 0, crash = 0, 10min+?
              4. 수용기준: prd.json acceptanceCriteria — 전부 충족?
              5. 3-way 정합성: SSOT.md/prd.json/CLAUDE.md 숫자 일치?
              6. Go/No-Go:
                 - PASS → Sakura에게 SSOT+git 지시
                 - FAIL → 복귀 대상 명시:
                   • 코드 결함 → Stage B Phase 1 (개발 fix)
                   • Shadow 미달 → Stage B Phase 2 (Shadow 재실행)
                   • 기획 결함 → Stage A (재기획)")
```

> **Agent Teams 불필요**: Karina가 PLAN.md + REVIEW.md + Shadow 결과 + prd.json을 직접 읽으면,
> Jennie/Minji를 소환해서 "회의"하는 것과 정보 품질이 동일. 비용만 3-7x 차이.
> 결과 수신 즉시 C-Step 3 tool call 발행. 상태 요약/보고 금지.

#### C-Step 3: SSOT + Git + 텔레그램 — Sakura (Go 시에만)

```
Agent(subagent_type="ssot-keeper", name="sakura", model="sonnet",
      prompt="Phase 완료 반영 — 아래 6가지 전부 수행:
              1) prd.json: 완료 US passes:true 마킹
              2) SSOT.md §2: Phase, 테스트 수, 완료 US 카운트, 다음 작업 업데이트
              3) SSOT.md §7 헤더: 'N개 User Stories, M개 완료, K개 미완' 숫자를 prd.json 실제 카운트와 동기화
              4) CLAUDE.md '현재 상태' 섹션: PRD 카운트, 테스트 수, 다음 작업을 SSOT.md §2와 동기화
              5) **검증**: Grep으로 prd.json passes:true/false 카운트 → 3곳(SSOT §2, §7, CLAUDE.md) 숫자 대조. 불일치 시 수정.
              6) **Git**: git add + git commit -m 'Phase X: [US 목록] 완료' + git push origin main. push 누락 = FAIL.")
```
> 결과 수신 즉시 다음 블록 tool call 발행. 상태 요약/보고 금지.

- **git commit만 하고 push 안 하는 것 = 미완료**. `git push origin main` 필수.

#### C-Step 4: 텔레그램 알림 + 사장님 승인 대기

- `WORKFLOW_TELEGRAM_BOT_TOKEN`으로 사장님에게 Phase 완료 알림 전송
- 알림 내용: Phase X 완료, 테스트 수, Shadow 결과 (PnL/WR/DD), 변경 파일 수
- **사장님 승인까지 대기** (자동 진행 금지)
- 승인 후 다음 Phase의 Stage A로 진행

**활성 팀**: BLACKPINK(코드리뷰, Step 1) + LE SSERAFIM(Phase 완료 리뷰+SSOT, Step 2-3) + ITZY(퀀트, 해당 시)

**산출물**: `docs/review/Phase-X_REVIEW.md`

**C→다음Phase 전환:** Karina Go + Sakura SSOT+git push + 텔레그램 알림 + 사장님 승인 수신 즉시 → `state_write(next_stage:"A", next_phase:"Phase-Y")` && 다음 Phase Stage A 시작. 중간 상태 보고 금지.

---

## 3. 팀 구조 (7팀 + TF)

> 팀은 **기능별로 정의**, Stage가 **필요한 팀을 호출**하는 구조.
> Stage B Phase 1만 TeamCreate 사용 (개발자 간 협업 필요), 나머지는 Agent() 서브에이전트 (독립 작업).

### ① 기획팀 [AESPA] — Stage A 활성화 (4명)

| 팀원 | 에이전트 (subagent_type) | 모델 | 역할 |
|------|------------------------|------|------|
| **Karina** | `oh-my-claudecode:architect` | opus | Entry Gate: SSOT/prd.json 정합성, 시스템 설계, 코드 탐색 |
| **NingNing** | `oh-my-claudecode:analyst` | sonnet | 요구사항 분석, acceptanceCriteria 검증, 엣지케이스 도출 |
| **Winter** | `oh-my-claudecode:critic` | opus | 기획 비판: 누락 엣지케이스, 과도한 복잡성, 설계 결함 지적 |
| **Giselle** | `oh-my-claudecode:planner` | sonnet | 태스크 분해, 실행 순서, PLAN.md 작성 |

사전 단계: `oh-my-claudecode:explore` (haiku) — 코드베이스 탐색 후 architect에게 컨텍스트 제공

### ② 개발팀 [IVE] — Stage B Phase 1 활성화 (최대 6명, TeamCreate)

| 팀원 | 에이전트 (subagent_type) | 모델 | 역할 |
|------|------------------------|------|------|
| **Yujin** | `oh-my-claudecode:executor` | sonnet | 백엔드/엔진 #1: `engine/src/` |
| **Wonyoung** | `oh-my-claudecode:test-engineer` | sonnet | 테스트: `tests/` + pytest |
| **Rei** | `oh-my-claudecode:designer` | sonnet | 프론트/UI: `dashboard/src/` (해당 시) |
| **Gaeul** | `oh-my-claudecode:executor` | sonnet | 병렬 백엔드 #2 (필요 시) |
| **Leeseo** | `oh-my-claudecode:executor` | sonnet | 병렬 백엔드 #3 (대규모 시) |
| **Liz** | `oh-my-claudecode:executor` | sonnet | 병렬 백엔드 #4 (최대 병렬 시) |

파일 소유권: Yujin/Gaeul/Leeseo/Liz=`engine/src/`, Rei=`dashboard/src/`, Wonyoung=`tests/`

### ③ 검증팀 [BLACKPINK] — Stage C Step 1 활성화 (2명)

| 팀원 | 에이전트 (subagent_type) | 모델 | 역할 |
|------|------------------------|------|------|
| **Jennie** | `oh-my-claudecode:code-reviewer` | opus | 종합 코드리뷰 + 설계 비판 + 품질 + Shadow 결과 교차 평가, REVIEW.md 작성 |
| **Jisoo** | `oh-my-claudecode:security-reviewer` | sonnet | 보안: JWT/API키/OWASP (금융 필수) |

### ④ 테스트팀 [NewJeans] — Stage B Phase 2 활성화 (최대 5명)

| 팀원 | 에이전트 (subagent_type) | 모델 | 역할 |
|------|------------------------|------|------|
| **Minji** | `shadow-tester` (커스텀) | sonnet | Shadow 10min+ 실행, PnL/crash 보고 |
| **Hanni** | `oh-my-claudecode:qa-tester` | haiku | QA: CLI/서비스 런타임, 엣지케이스 |
| **Danielle** | `oh-my-claudecode:scientist` | haiku | 데이터 모니터링: PnL/WR/DD 분석 |
| **Haerin** | `browser-verifier` (커스텀) | haiku | 대시보드: Chrome 렌더링/API/WS (해당 시) |
| **Hyein** | `oh-my-claudecode:debugger` | sonnet | 에스컬레이션: crash 루트코즈 (crash 시만) |

### ⑤ 릴리스팀 [LE SSERAFIM] — Stage C Step 2-3 활성화 (2명)

| 팀원 | 에이전트 (subagent_type) | 모델 | 역할 |
|------|------------------------|------|------|
| **Karina** | `oh-my-claudecode:architect` | opus | Phase 완료 리뷰 + Go/No-Go (PM+Architect 겸임, AESPA 겸임) |
| **Sakura** | `ssot-keeper` (커스텀) | sonnet | SSOT.md + prd.json + CLAUDE.md 업데이트 + 3-way 검증 + git push |

### ⑥ 퀀트팀 [ITZY] — Stage A(QUANT GATE) + Stage B(수식검증) 활성화 (5명)

| 팀원 | 에이전트 (subagent_type) | 모델 | 역할 |
|------|------------------------|------|------|
| **Yeji** | `quant-validator` (커스텀) | opus | 수학 검증: 슬리피지/마찰력/수익성, SSOT §4 |
| **Ryujin** | `oh-my-claudecode:scientist` | sonnet | 백테스트: 파라미터 민감도, 통계 유의성 |
| **Lia** | `ml-pipeline` (커스텀) | sonnet | ML: HMM 레짐, XGBoost, ONNX 추론 |
| **Chaeryeong** | `dex-specialist` (커스텀) | sonnet | DEX: 가스비, Uniswap V3, CEX-DEX |
| **Yuna** | `oh-my-claudecode:analyst` | sonnet | 파라미터 분석, 수익 시뮬레이션 |

### ⑦ Fix 루프 전용 — 에스컬레이션 L1+ 시 활성화

| 팀원 | 에이전트 (subagent_type) | 모델 | 역할 |
|------|------------------------|------|------|
| *Joy* | `oh-my-claudecode:debugger` | sonnet | 루트코즈 분석, 회귀 격리 |
| *Irene* | `oh-my-claudecode:build-fixer` | sonnet | 빌드/타입에러 최소 변경 수정 |
| *Wendy* | `oh-my-claudecode:code-simplifier` | opus | 리팩토링 필요 시 코드 단순화 |

**팀 규칙:**
- Karina는 AESPA(①) + LE SSERAFIM(⑤) 양쪽 — Entry Gate(A) + Phase 완료 리뷰(C) 연속성 보장
- ITZY(⑥)는 Stage A + Stage B 양쪽 활성화
- Stage B Phase 1만 TeamCreate (협업 필요), 나머지 Agent() (독립 작업)
- 병렬 필요 시 같은 역할 추가 스폰 가능 (IVE 최대 6명)
- 모델 라우팅: opus=아키텍처/심층분석, sonnet=구현/표준, haiku=탐색/단순

---

## 4. 완료 기준

| 항목 | 조건 | Stage |
|------|------|-------|
| Entry Gate | SSOT/prd.json/CLAUDE.md 정합성 PASS | A |
| PLAN.md | `docs/planning/Phase-X_PLAN.md` 존재 | A |
| QUANT GATE | 전략/수식 US 시 PASS | A |
| pytest | 0 failures | B (Phase 1) |
| Shadow 10min+ | PnL > 0, crash = 0, 10분 이상 | B (Phase 2) |
| Docker | 전 컨테이너 healthy | B (Phase 2) |
| 코드리뷰 | CRITICAL/HIGH 0건 | C (Step 1) |
| 보안리뷰 | CRITICAL 0건 | C (Step 1) |
| REVIEW.md | `docs/review/Phase-X_REVIEW.md` 존재 | C (Step 1) |
| Phase 완료 리뷰 | Karina Go/No-Go = PASS | C (Step 2) |
| SSOT.md | 해당 섹션 업데이트됨 | C (Step 3) |
| prd.json | `passes: true` | C (Step 3) |
| Git | `git add` + `git commit` + `git push origin main` 일괄 완료 | C (Step 3) |
| 텔레그램 | 사장님 알림 전송 | C (Step 4) |
| **Phase D/H 추가** | Chrome 렌더링 + API 200 + WebSocket + 모바일 반응형 | B+C |

> `npm run build` 성공만으로 Phase D/H 완료 선언 금지. Chrome 실제 렌더링 필수.
> Shadow 10분 미만 실행으로 완료 선언 금지. 실제 10분+ 무중단 필수.

## 4.5 에스컬레이션

| Level | 조건 | 대응 |
|-------|------|------|
| L0 | 단순 버그 | 팀 내 즉시 수정 |
| L1 | fix 루프 3회 | 기존 방식 |
| L2 | 3회 초과/구조적 문제 | Stage A 복귀 → 새 PLAN.md |
| L3 | SSOT↔PRD↔코드 모순 | SSOT→PRD 수정 → Stage A 재기획 |
| L4 | Phase 범위 초과 | 새 US → prd.json 추가 |
| **L5** | **동일 Phase 3회 이상 실패** | **텔레그램 알림 → 사장님 대기** |

L0~L1 자동 처리. L2~L4 로그 출력 후 자동 복귀. **L5 사장님 승인 필수.**

## 5. 인프라 규칙

- **테스트**: `cd engine && python -m pytest tests/ -x --tb=short`
- **Shadow**: `docker compose up -d && docker compose ps` 후 `cd engine && timeout 600 python -m src.main`
- **슬리피지**: CEXOrderbookSlippage가 유일한 소스. PowerLaw k=0.0 비활성. 이중 슬리피지 금지.

## 6. 컨텍스트 관리

**Stage A→B→C 연속 실행 (세션 초기화 없음, ralph 루프 유지).**
`/compact` 절대 금지 (GitHub #3274, #19567, #18482).

| 시점 | 동작 |
|------|------|
| Stage A 완료 | PLAN.md + checkpoint → 즉시 Stage B TeamCreate |
| Stage B Phase 1 완료 | pytest PASS + TeamDelete → 즉시 Phase 2 Shadow |
| Stage B Phase 2 완료 | Shadow PASS + checkpoint → 즉시 Stage C |
| Stage C Step 1 완료 | 코드리뷰 PASS → 즉시 Step 2 Karina |
| Stage C Step 2 완료 | Go → 즉시 Step 3 Sakura |
| Stage C Step 3 완료 | SSOT/git push + 텔레그램 → **사장님 승인 대기** |

**체크포인트**: `.omc/state/leviathan-progress.json` — 세션 복구 전용.
세션 크래시/수동 `/clear` 시 → `/leviathan` 재호출 → progress 파일로 재개.

**컨텍스트 60% 이상 시:**
1. WORKFLOW_TELEGRAM `send_context_warning()` → 60% 도달 알림 전송
2. 현재 진행 중인 Stage 완료까지 마무리
3. `/clear` 시도
4. 성공 시 → `send_context_clear_success()` 알림 → progress.json으로 자동 재개
5. 실패 시 → `send_context_alert()` 알림 → 사장님 수동 개입 필요
6. **`/compact` 절대 금지** — 결과 소실 위험

## 7. TF (Task Force) — Quarter-Final / Semi-Final / Final

> **진입 가드**: Phase G/H/I/J-EXT/K/L/M 전부 `passes:true` 필수. 하나라도 미완료 시 TF 소집 금지.
> TF는 기존 팀원을 TF 전용 역할로 재소집(리스폰). 개발 세션과 **완전 분리** (fresh context).

### TF 핵심 원칙: 회귀 구조

> **TF는 검사 프로세스이지, 새 Phase를 만드는 단계가 아니다.**
> TF FAIL 시 → **회귀 Phase 생성** (원본 Phase의 미비점 보완).
> 회귀 Phase는 SSOT.md에서 **TF 섹션 위, 원본 Phase 다음**에 위치한다.
> 각 회귀 US에는 `(← 원본 Phase US-XXX 사유)` 역추적 주석을 붙인다.

### TF 3-Round 체계 (XXX STUDIO 표준)

```
┌─────────────────────────────────────────────────────────────┐
│  TF Quarter-Final (QF) — Development Verification           │
│  "코드가 올바른가?"                                          │
│  정합성, 체크리스트, 교차검증, 코드 품질                      │
├─────────────────────────────────────────────────────────────┤
│  TF Semi-Final (SF) — System Validation                     │
│  "24시간 돈을 벌 수 있나?"                                   │
│  24H Shadow, 전략별 P&L, E2E 시나리오(병렬), UAT            │
├─────────────────────────────────────────────────────────────┤
│  TF Final (F) — Operations Readiness                        │
│  "문제 생기면 대응할 수 있나?"                                │
│  DR 훈련, Sandbox 실거래, 운영 매뉴얼, Canary 1~5%          │
└─────────────────────────────────────────────────────────────┘
```

```
SSOT.md §7 구조:
  Phase A~M (원본, 모두 ✅)
    ↓
  회귀 Phase S1~S7 (← 원본 Phase 보완)   ← TF 위에!
    ↓
  TF Quarter-Final (QF) — Development Verification
    ↓
  TF Semi-Final (SF) — System Validation
    ↓
  TF Final (F) — Operations Readiness → Live
```

> **통합 추적 문서**: 각 Round별 `docs/checklists/tf-{qf|sf|final}_YYYYMMDD.md`에 관리.
> SSOT.md의 TF 섹션은 판정 요약 + 문서 참조만 기록한다.

### TF 팀 [TWICE] (9명 + 차출)

| TF 역할 | 팀원 | 에이전트 (subagent_type) | 모델 | 역할 상세 |
|---------|------|------------------------|------|----------|
| TF 리더 | Nayeon | `oh-my-claudecode:architect` | opus | 전체 PASS/FAIL 최종 판정, 라운드별 Go/No-Go |
| 메인 아키텍트 | Karina | `oh-my-claudecode:architect` | opus | 체크리스트 초안, 정합성 대조, 합동 점검 주관 |
| 엔진 전문가 | Jeongyeon | `oh-my-claudecode:deep-executor` | opus | 엔진 무결성, 전략 로직, 24H 안정성 모니터링 |
| 인프라 전문가 | Momo | `oh-my-claudecode:qa-tester` | sonnet | Docker/DB/Redis/Nginx, DR 훈련 실행 |
| 데이터 전문가 | Sana | `oh-my-claudecode:scientist` | sonnet | PnL 통계, 전략별 Sharpe/WR/DD 분석 |
| UI/UX 전문가 | Mina | QF:`designer` SF:`browser-verifier` F:`writer` | sonnet | 라운드별 역할 전환 (기능→E2E→문서) |
| 퀀트 전문가 | Dahyun | `quant-validator` (커스텀) | opus | 수식/파라미터, 전략별 수익성 판정 |
| QA 감사관 #1 | Chaeyoung | `oh-my-claudecode:critic` | opus | 압박 면접, 스트레스 시나리오, DR 설계 |
| QA 감사관 #2 | Tzuyu | `oh-my-claudecode:verifier` | sonnet | 증거 수집, 각 Gate 통과 근거 문서화 |

**차출**: Jisoo(BLACKPINK, `security-reviewer`) — QF 보안 6항목 + Final Live 보안 점검.
**클론**: 병렬 검증 필요 시 Jeongyeon2, Momo2 등 추가 스폰 가능.

**Mina 라운드별 역할 전환**:
- QF: `designer` — 대시보드 기능 정상성 (렌더링, 로그인, API)
- SF: `browser-verifier` — E2E 시나리오 (로그인→모드선택→거래소→모니터링)
- Final: `writer` — 운영 매뉴얼, IRP, 일일점검 체크리스트 작성

### TF Quarter-Final (QF) — Development Verification

> **핵심 질문**: "코드가 올바르고, 빠진 것이 없는가?"

```
[단계 0] Smoke Test Gate
- 전체 pytest PASS
- Docker 전 컨테이너 healthy
- 통합 Shadow 10min (crash=0, 전략 신호 흐름 정상, PnL 기록 확인)
- 실패 시 TF 소집하지 않고 해당 Phase로 회귀

[단계 1] 정합성 확인
- Karina: SSOT.md + prd.json + CLAUDE.md 3-way 정합성 확인
- 누락 US/Phase 발견 시 새 Phase/US 생성

[단계 2] 체크리스트 수립 (The Blueprint)
- Karina + 도메인 전문가 협의 → '완성 기준' 수립
- 분야별 확인 체크리스트 문서 생성
- Nayeon(TF 리더)이 상용화 기준 부합 여부 최종 승인

[단계 3] 교차 검증 (The Deep Dive)
- 전문가별 체크리스트 기반 자기 분야 검증 (병렬):
  · Jeongyeon(엔진): 초기화 체인, 전략 등록, 어댑터, RiskGuardian, KillSwitch, Shutdown, dead wiring
  · Momo(인프라): Docker, DB 스키마, Redis 인증, Nginx, .env 동기화, 포트, 리소스 제한, 백업
  · Dahyun(퀀트): 슬리피지 모델, 수수료 정합, 마찰력 공식, Sharpe, MDD 단위, 기본값 위험
  · Sana(데이터): Shadow 완전성, PnL 기록, WS 흐름, KRW 환율, 피드 연결 상태
  · Mina(UI/UX): 대시보드 4페이지 렌더링, 로그인, API 응답, 모바일 반응형, 콘솔 에러 0건
  · Jisoo(보안): JWT 인증, API 키 노출, CSP 헤더, IP whitelist, Redis commands, .gitignore
- 타 분야 협업 필요 시 클론 전문가 스폰
- Karina 합동 점검: 실전적 질의응답

[단계 3.5] 조립 검증 — 통합 검증 (Assembly Verification)
> "부품이 아니라, 조립된 완성품이 제대로 동작하는가?"
- verify_assembly.py 자동화: main.py 10단계 초기화 체인 서브시스템 non-None 확인
- Signal Flow E2E: 7개 전략 각각 on_signal() 호출 횟수 > 0 (5분 대기)
- Config Flag Audit: ENABLE_INLINE_TUNER=true, SHADOW_DISABLED_STRATEGIES=[], ScheduledTuner.EXCLUDED 확인
- Dead Wiring Detection: 구현되었으나 미연결 코드 0건 (main.py 코드 경로 추적)
- PASS 기준: 4개 sub-check 전부 PASS
- FAIL 시: 미연결 기능 → 회귀 Phase 생성

[단계 4] 최종 확인 + 회귀 (The Feedback Loop)
- Karina → Nayeon 보고
- Chaeyoung/Tzuyu QA 감사단 압박 면접
- FAIL 시:
  1. 미비점 분석 → 원본 Phase별 회귀 US 생성
  2. 회귀 Phase를 SSOT.md에서 TF 섹션 위에 배치 (원본 Phase 다음)
  3. 각 회귀 US에 `(← 원본 Phase US-XXX 사유)` 역추적 주석
  4. 통합 추적 문서 생성: `docs/checklists/tf-qf-consolidated_YYYYMMDD.md`
  5. 회귀 Phase 개발 → 3-Stage(A~C) 사이클 → QF 재검증
- PASS 기준: CRITICAL 0, HIGH 0, MEDIUM ≤ 5 (자금 손실 경로 아님)
- PASS 시: SF 진출

산출물: docs/checklists/tf-quarter-final_YYYYMMDD.md
```

### TF Semi-Final (SF) — System Validation

> **핵심 질문**: "72시간 동안 실제로 돈을 벌 수 있는가?"

```
[전제]: QF 통과 상태에서만 진행

[단계 1-A] 경량 재확인 (Delta Check)
- QF 이후 코드 변경분만 대상 (git diff QF-PASS..HEAD)
- CRITICAL/HIGH 신규 발생 여부 확인
- 10분 Smoke Shadow (crash=0, 신호 흐름 정상)

[단계 1-B] 전략별 독립 검증 (Strategy Isolation)
- 각 활성 전략을 단독 실행하여 독립 수익성 확인
- 전략별 10분 Shadow: P&L, WR, Sharpe, MDD 개별 측정
- 손실 전략 식별 → disabled_strategies에 추가 여부 판단
- 전략 간 상관관계 분석 (CorrelationMonitor 데이터 활용)

[단계 1-C] 전략 상호작용 검증 (Strategy Interaction) ← 신규
- 7개 전략 동시 10min Shadow 실행
- 합산 PnL vs 개별 PnL 합계 비교
  · 합산 > 개별합 80% → PASS (약간의 간섭 허용)
  · 합산 < 개별합 50% → FAIL (심각한 전략 간섭)
- Strategy overlap 메트릭 = 0 확인 (Prometheus counter)
- FAIL 시: 전략 간 충돌 원인 분석 → 회귀 Phase

[단계 2] Progressive Shadow (24H+) — 순차 OFF→ON 오토튜너 비교
- Stage 1:  1H  (튜너 OFF) → crash=0, 신호 흐름, 거래소 10/10
- Stage 2:  2H  (튜너 OFF) → WR>60%, PnL>0, 전략별 분리 리포트
- Stage 3:  2H  (튜너 ON)  → Stage 2 대비 비교 리포트 (오토튜너 효과 검증)
  · 튜너 효과 판정:
    PROVEN:  튜너 ON PnL > 튜너 OFF PnL + 10% → 튜너 유지
    NEUTRAL: 차이 < 10% → 추가 조사
    HARMFUL: 튜너 ON PnL < 튜너 OFF PnL → 튜너 비활성화 or 튜닝
    BUG:     튜너 ON에서 crash/error → 즉시 수정
- Stage 4:  6H  (최적 설정) → 각 전략 WR>50%, 마찰력 오차<20%
- Stage 5: 12H  → 메모리 증가<100MB, CPU<80%, WS 재연결
- Stage 6: 24H  → LiveGate 6-check + 일일 성과 (최종)
  1. Sharpe ≥ 2.0
  2. MDD < 5%
  3. 총 신호 ≥ 100개
  4. KillSwitch 수동 테스트 PASS
  5. CircuitBreaker 동작 확인
  6. 거래소 건강도 ≥ 95%
- 각 Stage PASS 시 자동으로 다음 연장 (멈추지 않고 누적)
- 장기 안정성은 TF Final Canary 7일에서 실 자본으로 검증
- 실패 시 회귀 Phase 생성 → 3-Stage(A~C) → SF 재검증 (QF 스킵, 구조적 결함 시 QF부터)

[단계 3] 병렬 검증 (단계 2 실행 중 동시 수행)
- 단계 3-A,B,C는 단계 2 Stage 2 통과 후 병렬 수행 (순차 대기 불필요)
- 효과: 단계 3이 Shadow 시간에 흡수 → SF 전체 소요 = 24H + α(단계1)

[단계 3-A] E2E 사용자 시나리오 (UAT) ← Stage 2+ 통과 후 병렬
- Mina(browser-verifier) 에이전트 실행
- 시나리오 체크리스트:
  □ 로그인: dashboard/login → JWT 쿠키 발급 → 리다이렉트
  □ Overview: 실시간 PnL, 승률, 활성 거래 표시
  □ Strategies: 전략별 성과, 활성/비활성 상태
  □ Portfolio: 거래소별 잔고, 자산 분배
  □ Settings: 모드 선택, 거래소 토글
  □ 모바일 반응형: 375px, 768px 뷰포트
  □ 실시간 업데이트: WebSocket 1초 간격 갱신
  □ 알림 흐름: Kill Switch → Telegram 도달 (< 5초)
  □ API 응답: 전 엔드포인트 200 (인증 포함)
  □ 콘솔 에러: 0건

[단계 3-B] Master Inspection
- 전체 시스템의 "결" 맞춤
- 코드: TODO/FIXME, dead code, 하드코딩 상수
- 로그: 적절한 레벨, 민감 정보 미포함
- 설정: trading.json ↔ .env ↔ 코드 기본값 일관성

[단계 3-C] 알림 체계 종합 검증
- Telegram 거래 알림 수신 확인
- Telegram 워크플로우 알림 수신 확인
- Kill Switch → 알림 → 거래 중단 (< 5초)
- Alertmanager 규칙 → 라우팅 → 수신 확인

PASS 기준:
- 24H Shadow 5-Stage 전부 PASS
- 활성 전략 각각 WR>50%, Sharpe>1.0 (통합 Sharpe>2.0)
- E2E 시나리오 10개 전부 PASS
- 알림: Kill Switch → Telegram < 5초
- LiveGate 6-check 전부 PASS

산출물:
- docs/checklists/tf-semi-final_YYYYMMDD.md (전체 보고서)
- docs/checklists/tf-sf-shadow-report_YYYYMMDD.md (24H 상세)
- docs/checklists/tf-sf-strategy-pnl_YYYYMMDD.md (전략별 P&L)
- docs/checklists/tf-sf-e2e-scenarios_YYYYMMDD.md (E2E 결과)
```

### TF Final (F) — Operations Readiness

> **핵심 질문**: "문제가 생기면 대응할 수 있는가? 실제 돈을 안전하게 운용할 준비가 되었는가?"

```
[전제]: SF 통과 + 24H Shadow ALL PASS

[단계 1] Operations Readiness Review (ORR)
- Mina(writer): 운영 매뉴얼 작성
  □ 일일 점검 절차 (매일 09:00 체크리스트)
  □ 주간 리포트 양식 (P&L, 거래소별 성과, 리스크 지표)
  □ 장애 대응 절차 (IRP):
    - P1 (자본 손실): Kill Switch → 전원 알림 → 15분 내 대응
    - P2 (서비스 장애): 30분 내 대응, 거래 일시 중단
    - P3 (성능 저하): 4시간 내 대응, 모니터링 강화
  □ 에스컬레이션: 엔진 → TF 리더 → 사장님
  □ 당직 체계: 자동 알림 기반

[단계 2] Disaster Recovery (DR) 훈련
- Jeongyeon + Momo 주도, Chaeyoung 시나리오 설계
  □ DR-1: 엔진 crash → 재시작 → 포지션 복구 → DB 무결성
  □ DR-2: DB 장애 → WAL 복구 → PITR → 데이터 정합성
  □ DR-3: 거래소 API 장애 → CircuitBreaker → 자동 복구
  □ DR-4: Redis 장애 → 상태 복구 → Kill Switch 유지
  □ DR-5: 네트워크 단절 → WS 재연결 → 데이터 갭 처리
  □ DR-6: 코드 롤백 → git revert → 포지션 청산 → 안전 상태
  □ DR-7: 전략 카니발리제이션 → overlap 감지 → 해당 전략 자동 비활성화
  □ DR-8: 폭주 전략 (단일 전략 연속 손실) → per-strategy circuit breaker 발동
  □ DR-9: 오토튜너 잘못된 파라미터 적용 → strategy_params.json 즉시 롤백 + 튜너 비활성화

[단계 3] Sandbox 실거래 테스트
- Momo 주도
  □ Binance Testnet: 주문 생성 → 체결 → 잔고 → PnL
  □ 주문 취소 → 잔고 복원
  □ Rate limit + 응답 지연 처리
  □ Testnet 없는 거래소(Upbit, Bithumb, Coinone): API 조회만

[단계 4] 자본/리스크 한도 확정
- Dahyun 검증, Nayeon + 사장님 승인
  □ 거래소별 자본 한도 (alpha: $70, beta: $750)
  □ 전략별 포지션 한도
  □ max_daily_loss_usd, max_single_loss_usd
  □ DynamicSizer 파라미터 (Kelly fraction, min/max)
  □ Auto-discovery 필터 (min_volume_usd, min_exchanges)

[단계 5] Canary Deployment (1% 자본, 7일) — 오토튜너 최종 검증 포함
- Sana 리콘실리에이션, Dahyun 수익성 판단
  □ Alpha Phase: $70/exchange × 10 = $700
  □ 튜너 OFF 3일 → 튜너 ON 4일 → 순차 A/B 비교 (최종 실거래 검증)
  □ 일일 3-way 대조: 엔진 P&L vs 거래소 잔고 vs DB
  □ 슬리피지/수수료 실측 vs 예측 비교 (FillAnalyzer 데이터)
  □ 수수료/네트워크 비용 실측
  □ PASS: P&L>0, 리콘 오차<1%, 슬리피지 오차<50%, 튜너 효과 판정 완료

[단계 6] Live Kick-Off
- Nayeon(TF 리더) 최종 서명
- Jisoo(보안): API 키 Live 환경 최종 점검
- 사장님 승인
- Alpha → Beta 확대 또는 Full Live 전환
- 운영 모니터링 시작

PASS 기준:
- ORR: 운영 매뉴얼 + IRP + 에스컬레이션 완비
- DR: 9개 시나리오 전부 PASS (DR-1~6 인프라 + DR-7~9 전략)
- Sandbox: Binance Testnet 주문 흐름 정상
- 자본 한도: 사장님 승인 완료
- Canary 7일: P&L>0, 리콘 오차<1%

산출물:
- docs/operations/daily-checklist.md (일일 점검)
- docs/operations/incident-response.md (IRP)
- docs/operations/weekly-report-template.md (주간 리포트)
- docs/checklists/tf-final_YYYYMMDD.md (검증 보고서)
- docs/checklists/tf-final-dr-report_YYYYMMDD.md (DR 결과)
- docs/checklists/tf-final-canary-report_YYYYMMDD.md (Canary 리포트)
```

### 회귀 구조 (3-Round 공통)

```
QF FAIL → 회귀 Phase 생성 → 3-Stage(A→B→C) → QF 재검증
SF FAIL → 회귀 Phase 생성 → 3-Stage(A→B→C) → SF 재검증 (QF 스킵)
          ※ 구조적 결함 시 QF부터 재검증
Final FAIL → 항목별 수정 → Final 해당 단계 재검증
             ※ 코드 변경 시 SF 재검증부터
```

---

## 8. 텔레그램 워크플로우 알림

**환경변수** (기존 거래 알림과 완전 분리):
```
WORKFLOW_TELEGRAM_BOT_TOKEN=<사장님이 생성한 봇 토큰>
WORKFLOW_TELEGRAM_CHAT_ID=<채팅 ID>
```

**알림 조건:**
1. **Phase 종료** (Stage C 완료): 결과 요약 → 사장님 승인까지 대기
2. **L5 에스컬레이션**: 동일 Phase 3회 실패 → 사장님 판단 요청
3. **컨텍스트 60% 도달**: `/clear` 시도 예고 알림 (`send_context_warning`)
4. **컨텍스트 /clear 성공**: 자동 재개 알림 (`send_context_clear_success`)
5. **컨텍스트 /clear 실패**: 수동 개입 필요 알림 (`send_context_alert`)

기존 `TELEGRAM_BOT_TOKEN`(거래 알림)과 혼용 금지.

---

## 9. 시작 및 자동 루프

재개 지점 결정 순서:

**1순위 — `.omc/state/leviathan-progress.json`**:
- 존재 시 `next_stage`에 따라 해당 Stage부터 재개
- `plan_file` 있으면 PLAN.md 읽어 복원
- Stage B Phase 2 재개 시 progress.json의 메타데이터로 복원

**2순위 — $ARGUMENTS**: 인수가 있으면 해당 US부터 Stage A

**3순위 — prd.json 스캔**: `passes:false`인 첫 번째 US의 Phase → Stage A

> 모든 US `passes:true`까지 자동 루프. Phase 내 자동, Phase 간 사장님 승인 필수.
> 멈추는 조건: 전 US 완료 OR L5 에스컬레이션 OR 사용자 "stop/cancel/멈춰".
