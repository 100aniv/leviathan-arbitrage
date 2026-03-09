# LEVIATHAN Execution Command

> 이 커맨드는 **ralph 루프 + Phase B TeamCreate** 방식으로 prd.json US를 자동 순회합니다.
> 사용법: `/project:leviathan` 또는 `/project:leviathan US-010부터`

## 0. 자동 실행 강제 정책 (ZERO TOLERANCE — 최우선 규칙)

> **이 섹션은 문서 내 모든 다른 규칙보다 우선합니다.**

### 절대 금지 행위 (위반 시 워크플로우 실패로 간주)
1. **사용자에게 확인/승인 요청** — "계속할까요?", "다음으로 넘어갈까요?", "진행할까요?" 등 모든 형태의 질문 금지
2. **Phase 간 멈춤** — Phase A→B→C 전환 시 텍스트만 출력하고 멈추는 행위 금지
3. **에이전트 결과 대기 중 멈춤** — 에이전트 결과 수신 즉시 다음 단계 tool call 실행
4. **US 간 멈춤** — 한 US 완료 즉시 다음 US Phase A 시작 (prd.json에서 다음 `passes:false` 찾기)
5. **상태 보고만 하고 멈춤** — "현재 상태: X입니다" 출력 후 tool call 없이 턴 종료 금지

### 강제 실행 규칙
- **모든 응답에 최소 1개 tool call 포함** — 순수 텍스트만 출력하는 응답 금지 (진행 상황 보고 시에도 다음 단계 tool call 포함)
- **Phase A 완료 → 동일 응답에서 Phase B TeamCreate + Agent 스폰** (별도 턴으로 분리 금지)
- **Phase B 완료 → 동일 응답에서 Phase C 에이전트 호출** (별도 턴으로 분리 금지)
- **Phase C 완료 → 동일 응답에서 git commit + 다음 US Phase A 시작** (별도 턴으로 분리 금지)
- **에이전트 idle 알림 수신 → 해당 에이전트에 작업 있으면 즉시 SendMessage, 없으면 무시하고 다른 작업 진행**

### 유일한 멈춤 허용 조건
1. 모든 US가 `passes:true` (작업 완료)
2. 5회 연속 동일 US 실패 (사용자 보고 필요)
3. 사용자가 명시적으로 "stop", "cancel", "멈춰" 입력

### 자동 실행 흐름 (한 US 전체가 단일 연속 흐름)
```
[Phase A] architect/ralplan 호출 → QUANT GATE(해당 시) →
[Phase B] 같은 응답에서 TeamCreate + Jennie+Lisa 스폰 → 에이전트 완료 대기 → pytest 결과 수신 →
[Phase C] 같은 응답에서 shadow-tester + code-reviewer 호출 → 결과 수신 → ssot-keeper → git commit+push →
[다음 US] 같은 응답에서 prd.json 읽기 + 다음 US Phase A 시작
```

> ⚠️ **위 흐름에서 사용자 입력을 기다리는 지점은 0개입니다.**
> ⚠️ **텍스트 출력은 진행 상황 1줄 요약만. 장황한 설명 금지.**

---

## 1. 소스 (반드시 읽을 것)

- `SSOT.md` — 유일한 설계 문서. 작업 전 반드시 읽기.
- `.omc/prd.json` — 64 US 목록. `passes:false`인 첫 번째 US부터 시작.
- `.claude/plans/jazzy-wishing-avalanche.md` — 25-Part 마스터 플랜.
- GAP 의존성 순서: `GAP9 → GAP10 → (GAP5,GAP6,GAP7 병렬) → GAP3 → (GAP1,GAP2) → GAP4`

## 2. 실행 모드

**시작 즉시 아래 명령어 실행 (건너뛰기 금지):**

```
Skill("oh-my-claudecode:ralph")
```

> ⚠️ **[설계 원칙 — 절대 변경 금지]** `ralph` vs OMC `team ralph` 스킬 차이:
> - `Skill("oh-my-claudecode:ralph")` = **올바름** — 메인 세션 self-referential outer loop (지속성만 담당)
> - `Skill("oh-my-claudecode:team", args="ralph")` = **잘못됨** — OMC 내장 파이프라인(team-plan→team-prd→team-exec→team-verify→team-fix) 강제 활성화 → LEVIATHAN 커스텀 3-Phase Sequential과 충돌
> - LEVIATHAN의 팀은 Phase B에서 **`TeamCreate("leviathan-us-xxx")` API로 US별 직접 생성/삭제** (OMC team 스킬 불필요, 별도 레이어)
> - 미래 Claude가 `team ralph`로 변경 시도 → 이 주석이 이유. 절대 변경 금지.

ralph 루프 안에서 각 US마다 **3-Phase Sequential** 필수 수행:

---

### Phase A — 기획 (AESPA팀)

**⚡ 배치 판단 (Phase A 진입 시 최우선 실행):**

현재 US와 prd.json의 다음 연속 US들을 비교하여 배치 가능 여부 판단:

| 조건 | 배치 가능 | 예시 |
|------|----------|------|
| 동일 Phase (SR, D, F 등) AND 동일 도메인 (engine/src/같은 디렉토리) | O | US-058~062 (모두 Phase SR, Shadow 현실성) |
| 동일 Phase BUT 다른 도메인 (engine vs dashboard) | X | US-058(engine) + US-063(dashboard) |
| US 간 의존성 (B가 A 결과 필요) | X | US-060(슬리피지 모델) → US-061(슬리피지 기반 시그널) |

**의존성 안전장치** (배치 전 반드시 확인):
1. `prd.json`의 `dependencies` 필드 확인 — 의존 US가 `passes:false`면 배치 불가
2. `files` 배열 교집합 확인 — 동일 파일 수정 US끼리는 순차 실행 (병렬 금지)
3. 배치 크기 상한: **최대 5 US** (초과 시 분할)

배치 판정 결과:
- **배치 가능** → 배치 내 US들을 하나의 PLAN.md에 통합 기획 (`docs/planning/US-XXX~YYY_PLAN.md`)
- **배치 불가** → 단일 US 모드로 진행 (기존 방식)

**복잡도 판단 (prd.json의 `files` 배열 기준):**

**복잡 US** (files 3개 이상 OR 아키텍처 변경):
→ `Skill` 도구로 `oh-my-claudecode:ralplan` 스킬을 **반드시 직접 호출** (단순 thinking으로 대체 불가):
  - 인수: `--deliberate "US-XXX [제목] 구현 계획: [acceptanceCriteria 목록]"`
  - architect(opus) + planner(opus) + analyst(opus) + critic(opus) 합의까지 반복
  - 산출물: `docs/planning/US-XXX_PLAN.md` 저장
  - 핸드오프: `.omc/handoffs/US-XXX-handoff.md`
  - ralplan 완료 즉시 → **Phase B 진행 (멈추지 말 것)**

**단순 US** (files 1-2개 AND 명확한 스펙):
→ `oh-my-claudecode:architect` 에이전트 단일 위임:
  - `Task(subagent_type="oh-my-claudecode:architect", model="opus", prompt="...")`
  - 산출물 동일 (docs/planning/US-XXX_PLAN.md + .omc/handoffs/US-XXX-handoff.md)
  - architect 완료 즉시 → **Phase B 진행 (멈추지 말 것)**

**⚡ QUANT GATE — 전략/수식 관련 US에서만 (인프라/Docker/대시보드 US는 생략):**

`prd.json`의 `files` 배열에 다음 키워드가 하나라도 포함된 경우:
`slippage`, `signal`, `strategy`, `executor`, `funding`, `futures`, `triangular`, `statistical`, `friction`, `cost_calculator`

→ ralplan/architect 완료 **직후** 반드시 호출:
```
Agent(subagent_type="quant-validator", name="winter", model="sonnet",
      prompt="US-XXX 기획 검증: SSOT.md §4(수식 모델) 대비
              docs/planning/US-XXX_PLAN.md 정합성 확인.
              1) 파라미터 범위 합리성 (k, gamma, threshold, bps 등)
              2) 이중 계산 여부 (slippage, fee 중복 경로)
              3) PnL 영향 예측 (변경 전/후 net profit 비교)
              4) SSOT.md §4 수식과 코드 구현 계획의 일치 여부
              판정: PASS/FAIL + 근거")
```
- **PASS** → 즉시 Phase B 진행
- **FAIL** → Phase B 진입 금지. PLAN.md 수정 후 winter 재호출 (최대 2회)

> 상용 퀀트 회사 동일 패턴: 수식 Sign-off 없이 개발 착수 불가.
> LEVIATHAN SR 스프린트에서 실제로 발생한 패턴 (PowerLaw k=5.0 이중계산, partial_fill=0.0).

> ⚠️ **Phase A 완료 = 즉시 Phase B 시작. 사용자 입력 기다리지 말 것. 절대 멈추지 말 것.**

---

### Phase B — 개발 (BLACKPINK팀)

**TeamCreate 기반 팀 구성** (단독 구현 절대 금지):

#### Step 1: 팀 생성 (Jisoo/Lead = main session)
`TeamCreate(team_name="leviathan-us-xxx")` 호출. US 번호로 팀 이름 지정.

#### Step 2: Teammate 스폰 (한 응답에서 동시에 2-3명)

**필수 (모든 US):**
- `Agent(subagent_type="oh-my-claudecode:executor", name="jennie", team_name="leviathan-us-xxx", prompt="[US-XXX] Backend: .omc/handoffs/US-XXX-handoff.md 읽고 engine/src/ 구현. 파일 경계: engine/src/**만 수정. dashboard/, tests/ 절대 수정 금지. acceptanceCriteria 전부 달성. 완료 시 SendMessage(recipient='lisa', content='구현 완료, 테스트 부탁')")`
- `Agent(subagent_type="oh-my-claudecode:test-engineer", name="lisa", team_name="leviathan-us-xxx", prompt="[US-XXX] QA: engine/tests/ 테스트 작성. 파일 경계: tests/**만 수정. Jennie 완료 메시지 받으면 cd engine && python -m pytest tests/ -x --tb=short 실행. 0 failures 필수. 결과를 팀 리드에게 SendMessage로 보고")`

**Phase D(구현) US만 추가:**
- `Agent(subagent_type="oh-my-claudecode:designer", name="rose", team_name="leviathan-us-xxx", prompt="[US-XXX] Frontend: dashboard/ 구현. 파일 경계: dashboard/**만 수정. engine/ 절대 수정 금지")`

**D-verify US (US-063, US-064) — Jennie/Lisa/Rosé 없이 메인 세션이 직접 Chrome 검증:**
1. `tabs_context_mcp()` → 탭 확인 (새 탭 필요 시 `tabs_create_mcp()`)
2. `preview_start("dashboard")` → npm run dev (port 3000) 시작
3. `navigate(url="http://localhost:3000/login")` → JWT 로그인 검증
4. 4페이지 순회 (각 페이지별):
   - `preview_snapshot()` → 렌더링 확인
   - `preview_network()` → API 200 응답 확인
   - WebSocket 실시간 업데이트 확인
5. `preview_resize(preset="mobile")` → 375x812 모바일 뷰 확인
6. `preview_screenshot()` → 증거 캡처 (완료 기준)
> D-verify US는 TeamCreate 없이 메인 세션 직접 실행. 에이전트 스폰 금지.

#### Step 3: 통합 (Jisoo 조율)
- Jennie → SendMessage(Lisa): "구현 완료, 테스트 부탁"
- Lisa: pytest -x 실행 → 결과를 Jisoo에게 SendMessage
- FAIL → Jisoo가 Jennie에게 SendMessage("수정 필요" + 로그) → Step 2 복귀 (최대 3회)
- PASS → Step 4 진행

#### Step 4: 팀 해산
- `SendMessage(type="shutdown_request", recipient="jennie")`
- `SendMessage(type="shutdown_request", recipient="lisa")`
- (Rosé 존재 시) `SendMessage(type="shutdown_request", recipient="rose")`
- 전원 승인 후 `TeamDelete()`

**파일 소유권 규칙 (충돌 방지):**

| 에이전트 | 소유 영역 | 금지 영역 |
|---------|----------|----------|
| Jennie (Backend) | `engine/src/**/*.py` | `dashboard/`, `tests/` |
| Rosé (Frontend) | `dashboard/src/**/*` | `engine/` |
| Lisa (QA+Infra) | `tests/**/*.py`, `docker-compose.yml` | `engine/src/`, `dashboard/src/` |
| Jisoo (Lead) | SSOT.md, .env, .omc/ | 직접 구현 금지 |

통합 게이트: `cd engine && python -m pytest tests/ -x --tb=short` — 0 failures 필수
실패 시 fix 루프 반복 (최대 3회)

> ⚠️ **Phase B 완료(pytest 0 failures OR D-verify Chrome 검증 완료) 즉시 Phase C 시작. 멈추지 말 것.**

**배치 모드 분기** (Phase A에서 배치 판정된 경우):
- 배치 내 US들을 **순차적으로** Phase B 실행 (각 US별 TeamCreate → 구현 → pytest → TeamDelete)
- 단, 같은 도메인 US끼리는 **동일 팀에서 연속 처리** 가능 (팀 재생성 불필요)
- 배치 내 모든 US의 Phase B 완료 후 → Phase C를 **배치 단위로 일괄 실행**

> ⚠️ **Phase B 완료 직후 반드시 `/compact` 실행. TeamCreate 에이전트 메시지가 컨텍스트의 40-60%를 차지. 미실행 시 Phase C 진입 불가.**

---

### Phase C — 검증 (LE SSERAFIM + IVE + NEWJEANS)

**순서대로 실행** (하나라도 실패하면 Phase B로 복귀):

#### C-1. Shadow 테스트 (LE SSERAFIM)
- **Docker 필수**: Shadow 전 `docker compose up -d && docker compose ps` 실행하여 Redis/TimescaleDB/Prometheus healthy 확인
- `Agent(subagent_type="shadow-tester", name="sakura", prompt="Shadow 10분: docker compose up -d 확인 후 cd engine && timeout 600 python -m src.main. PnL/WR/crash 보고")` **직접 호출**
- **필수 조건**: `PnL > 0`, `crash = 0`
- 실패 시 → Phase B fix 루프

#### C-2. 퀀트 검증 (IVE) — 전략/수식 변경 시에만
- `Agent(subagent_type="quant-validator", name="wonyoung", prompt="SSOT.md §4 수식 대비 코드 검증. 슬리피지/마찰력 수학 검증")` **직접 호출**
- 파라미터 민감도 분석

#### C-3. 코드 리뷰 (NEWJEANS)
- `Agent(subagent_type="oh-my-claudecode:code-reviewer", name="minji", model="opus", prompt="변경 코드 전체 리뷰: 보안/로직/성능. docs/review/US-XXX_REVIEW.md 작성")` **직접 호출**
- `Agent(subagent_type="oh-my-claudecode:critic", name="hanni", model="opus", prompt="설계 비판 + 개선안 제시")` **직접 호출**

#### C-4. 완료 처리

**단일 US 모드:**
- `Agent(subagent_type="ssot-keeper", name="haerin", prompt="SSOT.md 해당 섹션 업데이트")` **직접 호출**
- prd.json 해당 US `passes: true` 마킹
- `docker compose ps` → 전 컨테이너 healthy 확인
- `git add` + `git commit` + `git push` (gh CLI)

**배치 US 모드** (Phase A에서 배치 판정된 경우):
- 배치 내 **모든 US**의 Phase B+C 완료 후 일괄 처리:
  1. `Agent(subagent_type="ssot-keeper", name="haerin", prompt="SSOT.md 배치 US 일괄 업데이트: US-XXX~US-YYY")` **직접 호출**
  2. prd.json 배치 내 전체 US `passes: true` 마킹
  3. `docker compose ps` → 전 컨테이너 healthy 확인
  4. `git add -A && git commit -m "Phase [phase]: US-XXX~US-YYY [배치 설명]"` — **배치 단위 단일 커밋**
  5. `git push` (gh CLI)
- ⚠️ 배치 커밋 메시지 형식: `Phase SR US-058~062: [공통 변경 요약]`

---

## 3. 완료 기준 (이것 없이 절대 완료 선언 금지)

| 항목 | 조건 |
|------|------|
| pytest | 0 failures |
| Shadow 10min | PnL > 0, crash = 0 |
| SSOT.md | 해당 섹션 업데이트됨 |
| prd.json | 해당 US `passes: true` |
| US-XXX_PLAN.md | `docs/planning/`에 존재 |
| US-XXX_REVIEW.md | `docs/review/`에 존재 |
| Docker | 전 컨테이너 healthy |
| Git | commit + push 완료 |

**Phase D / D-verify US 추가 완료 기준:**

| 항목 | 조건 |
|------|------|
| Chrome 브라우저 테스트 | `npm run dev` 후 Chrome에서 해당 페이지 렌더링 확인 (스크린샷 or preview_screenshot) |
| API 연동 | 대시보드 ↔ 엔진 API 엔드포인트 200 응답 확인 |
| WebSocket 피드 | 실시간 데이터 업데이트 렌더링 확인 |
| 모바일 반응형 | Chrome DevTools 모바일 뷰포트(375x812)에서 레이아웃 정상 |

> **중요**: `npm run build` 성공만으로 Phase D 완료 선언 금지. 반드시 Chrome 브라우저에서 실제 렌더링 검증 필수.

## 4. 다음 US 전환 조건

위 완료 기준 **전부 충족** 후에만 다음 US 시작.
충족 안 되면 fix 루프 반복. 최대 5회 실패 시 사용자에게 보고.

> ⚠️ **완료 기준 전부 충족 즉시 → 사용자 확인 없이 자동으로 다음 `passes:false` US의 Phase A 시작.**
> **"계속 진행할까요?" / "다음으로 넘어갈까요?" 절대 금지. 멈추지 말 것.**

## 5. 인프라 규칙

- **테스트**: `cd engine && python -m pytest tests/ -x --tb=short`
- **Shadow**: `docker compose up -d && docker compose ps` 후 `cd engine && timeout 600 python -m src.main`
- **Docker**: Shadow 실행 전 반드시 `docker compose up -d` — Redis/TimescaleDB/Prometheus 필수
- **GitHub**: `gh` CLI로 push, PR, issue 관리
- **거래소**: 8 native adapters (ccxt 미사용)
- **슬리피지**: PowerLaw `impact = k * size^gamma` (k=1.0, gamma=0.5)
- **이중 슬리피지 금지**: SignalGenerator의 CEXOrderbookSlippage가 유일한 슬리피지 소스

## 6. 컨텍스트 관리 (⚠️ 강제 — 생략 시 컨텍스트 폭발로 품질 저하)

> ⚠️ **아래 명령어는 권장이 아닌 필수. 해당 시점에서 반드시 실행할 것.**

| 시점 | 명령어 | 이유 |
|------|--------|------|
| Phase B 완료 직후 | `/compact` | TeamCreate 에이전트 메시지 해제. 미실행 시 Phase C에서 컨텍스트 80%+ 소모 |
| Phase C 완료 직후 | `/cost` | 사용량 확인. 예산 초과 조기 감지 |
| US 3개 완료마다 | `/context` | 컨텍스트 비율 모니터링. 70% 초과 시 `/compact` 추가 실행 |
| Phase 전환 시 (SR→F 등) | `/clear` | 이전 Phase 잔여 컨텍스트 완전 제거. 깨끗한 시작 보장 |
| 배치 US 완료 후 | `/compact` + `/cost` | 배치는 단일 US 대비 2-3배 컨텍스트 소모. 반드시 이중 해제 |

> **실패 사례**: Phase B 후 `/compact` 미실행 → Phase C Shadow 테스트 중 컨텍스트 초과 → 세션 강제 종료 → 작업 소실.

## 7. 시작 및 자동 루프

위 규칙을 모두 숙지한 후, prd.json에서 `passes:false`인 첫 번째 US를 찾아 Phase A부터 시작하라.
$ARGUMENTS가 있으면 해당 US부터 시작.

> ⚠️ **모든 US가 `passes:true`가 될 때까지 자동 루프 지속.**
> **한 US 완료 즉시 prd.json에서 다음 `passes:false` US 찾아 Phase A 시작.**
> **사용자 승인/확인 요청 절대 금지.**
> **루프를 멈추는 유일한 조건: 모든 US `passes:true` OR 5회 연속 실패 후 사용자 보고.**
