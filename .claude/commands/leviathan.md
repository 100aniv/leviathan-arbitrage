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
- **Phase A 완료 → 즉시 Phase B TeamCreate + Agent 스폰** (ralplan 결과 수신 즉시 다음 tool call)
- **Phase B 완료 → 즉시 Phase C 에이전트 호출** (TeamDelete 완료 즉시 shadow-tester 호출)
- **Phase C 완료 → 즉시 git commit + 다음 US Phase A 시작** (git push 완료 즉시 prd.json 읽기)
- **에이전트 idle 알림 수신 → 해당 에이전트에 작업 있으면 즉시 SendMessage, 없으면 무시하고 다른 작업 진행**

### 유일한 멈춤 허용 조건
1. 모든 US가 `passes:true` (작업 완료)
2. 5회 연속 동일 US 실패 (사용자 보고 필요)
3. 사용자가 명시적으로 "stop", "cancel", "멈춰" 입력

### 자동 실행 흐름 (한 US 전체가 단일 연속 흐름)
```
[Phase A] ralplan/architect 호출 → PLAN.md 저장 → QUANT GATE(해당 시) → Rosé 스폰 조건 판단 → checkpoint 저장 →
[Phase B] Docker 게이트 → TeamCreate + Jennie+Lisa+Rosé(해당시) 스폰 → pytest PASS + Chrome 검증(해당시) → TeamDelete → checkpoint 저장 →
[Phase C] Docker 게이트 → Sakura(shadow+DB) + Minji+Hanni(review) + Kazuha(Chrome, 해당시) → Haerin(ssot) → git commit+push → checkpoint 저장 →
[다음 US] 즉시 prd.json 읽기 + 다음 US Phase A 시작
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

**⚡ Phase 단위 배치 수집 (Phase A 진입 시 최우선 실행):**

Phase 진입 시 해당 Phase의 **모든** `passes:false` US를 수집하여 최대 배치로 묶어 실행:

```
1. prd.json에서 현재 Phase의 모든 passes:false US 수집
2. 의존성 그래프 분석 → 독립 US는 배치, 의존 US는 순차 그룹으로 분류
3. 도메인 분석 → 동일 도메인(engine/dashboard) 기준 배치 그룹 형성
4. Phase 내 모든 배치 그룹 완료 = 1 워크플로우 사이클 종료 → 다음 Phase로
```

**배치 그룹 형성 규칙:**

| 조건 | 배치 가능 | 예시 |
|------|----------|------|
| 동일 Phase AND 동일 도메인 (engine/src/같은 디렉토리) | O | US-058~062 (모두 Phase SR, Shadow 현실성) |
| 동일 Phase BUT 다른 도메인 (engine vs dashboard) | X (별도 배치) | US-058(engine) + US-063(dashboard) |
| US 간 의존성 (B가 A 결과 필요) | X (순차 실행) | US-060(슬리피지 모델) → US-061(슬리피지 기반 시그널) |

**의존성 안전장치** (배치 전 반드시 확인):
1. `prd.json`의 `dependencies` 필드 확인 — 의존 US가 `passes:false`면 배치 불가
2. `files` 배열 교집합 확인 — 동일 파일 수정 US끼리는 순차 실행 (병렬 금지)
3. 배치 크기 상한: **최대 5 US** (초과 시 분할)

배치 판정 결과:
- **배치 가능** → 배치 내 US들을 하나의 PLAN.md에 통합 기획 (`docs/planning/Phase-X_PLAN.md`)
- **배치 불가** → 단일 US 모드로 진행 (기존 방식)
- Phase 내 모든 배치 그룹 완료 시 1사이클 종료

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

**Phase A → Phase B 전환 (구체적 실행 순서 — 반드시 이 순서대로):**
```
1. ralplan/architect 결과 수신 → docs/planning/US-XXX_PLAN.md 파일 존재 확인
2. QUANT GATE 해당 시 → quant-validator 호출 → PASS 확인 (FAIL 시 PLAN 수정 후 재호출, 2회 FAIL → L2 에스컬레이션: ralplan 재실행)
3. leviathan-progress.json 저장:
   Bash: echo '{"current_us":"US-XXX","next_phase":"B","plan_file":"docs/planning/US-XXX_PLAN.md","timestamp":"'$(date -Iseconds)'"}' > .omc/state/leviathan-progress.json
4. Rosé 스폰 조건 사전 판단: Phase D/H OR files에 "dashboard/","api/","shadow.py" OR criteria에 "dashboard","UI","프론트"
5. 즉시 Phase B 시작 → TeamCreate(team_name="leviathan-us-xxx") 호출
```
> ⚠️ **1~4 사이에 텍스트만 출력하고 tool call 없이 멈추는 행위 금지. 반드시 4번까지 연속 실행.**

---

### Phase B — 개발 (BLACKPINK팀)

**Docker 필수 게이트** (Phase B 진입 시 최우선 실행):
`docker compose up -d timescaledb redis && docker compose ps` — DB/Redis healthy 확인 (engine 컨테이너는 로컬 python과 port 8000 충돌 → 제외). 미실행 시 Phase B 진행 금지.
Docker 게이트 FAIL 시: `docker compose up -d timescaledb redis` 재실행 (최대 2회, 30초 대기). 2회 실패 → L2 에스컬레이션.
Phase D/H US는 추가로: `cd dashboard && npm run dev` (port 3000) + engine API 구동 확인.

**TeamCreate 기반 팀 구성** (단독 구현 절대 금지):

#### Step 1: 팀 생성 (Jisoo/Lead = main session)
`TeamCreate(team_name="leviathan-us-xxx")` 호출. US 번호로 팀 이름 지정.

#### Step 2: Teammate 스폰 (한 응답에서 동시에 2-3명)

**필수 (모든 US):**
- `Agent(subagent_type="oh-my-claudecode:executor", name="jennie", team_name="leviathan-us-xxx", prompt="[US-XXX] Backend: .omc/handoffs/US-XXX-handoff.md 읽고 engine/src/ 구현. 파일 경계: engine/src/**만 수정. dashboard/, tests/ 절대 수정 금지. acceptanceCriteria 전부 달성. 완료 시 SendMessage(recipient='lisa', content='구현 완료, 테스트 부탁')")`
- `Agent(subagent_type="oh-my-claudecode:test-engineer", name="lisa", team_name="leviathan-us-xxx", prompt="[US-XXX] QA+Infra: engine/tests/ 테스트 작성 + 인프라 검증. 파일 경계: tests/**만 수정. (1) Jennie 완료 메시지 받으면 cd engine && python -m pytest tests/ -x --tb=short 실행. 0 failures 필수. (2) docker compose ps로 전 컨테이너 healthy 확인. (3) Phase D/H US 시 npm run dev + engine API 구동 상태 확인. 결과를 팀 리드에게 SendMessage로 보고")`

**Rosé(Frontend) 스폰 조건 (확대 적용):**
- Phase D/H US → 항상 Rosé 추가
- 백엔드 US라도 아래 조건 중 하나 해당 시 Rosé 추가 스폰:
  - prd.json `files` 배열에 `"api/"` 포함 (대시보드 API 연동)
  - prd.json `files` 배열에 `"shadow.py"` 포함 (대시보드 데이터 소스)
  - prd.json `files` 배열에 `"dashboard/"` 경로 포함 (모든 대시보드 파일 변경)
  - `acceptanceCriteria`에 `"dashboard"`, `"UI"`, `"프론트"` 키워드 포함
- `Agent(subagent_type="oh-my-claudecode:designer", name="rose", team_name="leviathan-us-xxx", prompt="[US-XXX] Frontend: dashboard/ 구현. 파일 경계: dashboard/**만 수정. engine/ 절대 수정 금지. 구현 완료 후 Chrome DevTools MCP로 실제 브라우저 검증 필수: (1) navigate_page로 해당 페이지 이동, (2) take_screenshot으로 렌더링 증거 캡처, (3) list_network_requests로 API 200 응답 확인, (4) resize_page(375,812) + take_screenshot으로 모바일 확인. npm run build만으로 완료 선언 금지.")`

**D-verify US (US-063, US-064) — Jennie/Lisa/Rosé 없이 메인 세션이 직접 Chrome 검증:**

> **전제 조건**: Docker healthy + engine API 실행 + `npm run dev` (dashboard port 3000)

**Chrome DevTools MCP 도구 사용 (`chrome-devtools` MCP 서버):**
1. `navigate_page(url="http://localhost:3000/login")` → 로그인 페이지 확인
2. `fill` + `click` → JWT 로그인 수행 (또는 `evaluate_script`로 토큰 쿠키 설정)
3. 전 페이지 순회 (각 페이지별):
   - `navigate_page(url="http://localhost:3000/[page]")` → 페이지 이동
   - `wait_for` → 동적 콘텐츠 로딩 대기
   - `take_screenshot` → 렌더링 스크린샷 캡처 (증거)
   - `list_network_requests` → API 200 응답 확인
   - `evaluate_script` → WebSocket 상태 + 데이터 바인딩 확인
   - `list_console_messages` → JS 에러 0건 확인
4. `resize_page(width=375, height=812)` → 모바일 뷰포트 전환
5. `take_screenshot` → 모바일 렌더링 증거 캡처
6. **판정**: 모든 페이지 스크린샷 정상 + API 200 + WS 연결 + 에러 0 = PASS

> D-verify US는 TeamCreate 없이 메인 세션 직접 실행. 에이전트 스폰 금지.

#### Step 2.5: 내부 통합 검증 (백엔드/프론트 연동 + Chrome 실제 검증)

> 백엔드 US라도 대시보드 반영 필요 여부를 판단하여 프론트 연동까지 검증.
> **npm run build 성공만으로 프론트 검증 완료 선언 절대 금지.**

- **전제**: Docker healthy + engine API 구동 + `npm run dev` (dashboard port 3000)
- **판단 기준**: Jennie 구현 완료 → "해당 기능이 대시보드에 반영 필요한가?" 판단
  - API 엔드포인트 변경/추가 → 대시보드 연동 확인 필요
  - Shadow 데이터 소스 변경 → 대시보드 데이터 표시 확인 필요
- **필요 시**: Rosé가 미스폰 상태면 추가 스폰. 이미 스폰된 경우 Rosé에게 SendMessage로 검증 지시 (중복 스폰 금지)
- **Chrome DevTools MCP 검증** (Rosé 또는 Lead가 실행):
  1. `navigate_page(url="http://localhost:3000/[해당페이지]")` → 페이지 로드
  2. `take_screenshot` → 렌더링 스크린샷 캡처 (증거)
  3. `list_network_requests` → API 엔드포인트 200 응답 확인
  4. `evaluate_script` → 데이터 바인딩 확인 (null/empty 감지)
  5. `resize_page(width=375, height=812)` + `take_screenshot` → 모바일 확인
- **프론트 US라도**: 백엔드 API 정상 동작 확인 (Jennie 또는 Lead가 curl/pytest)
- **"기능 구현 완료" 재정의**: pytest 0 failures **AND** Chrome 스크린샷으로 실제 렌더링 확인 **AND** API 200 응답

#### Step 3: 통합 (Jisoo 조율)
- Jennie → SendMessage(Lisa): "구현 완료, 테스트 부탁"
- Lisa: pytest -x 실행 → 결과를 Jisoo에게 SendMessage
- Lisa가 5분 내 응답 없으면: Lead가 Lisa에게 재전송. 10분 내 무응답 → Lisa shutdown + 재스폰.
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
| Lisa (QA+Infra) | `tests/**/*.py`, `docker-compose.yml`, Docker/인프라 검증 | `engine/src/`, `dashboard/src/` |
| Jisoo (Lead) | SSOT.md, .env, .omc/ | 직접 구현 금지 |

통합 게이트: `cd engine && python -m pytest tests/ -x --tb=short` — 0 failures 필수
실패 시 fix 루프 반복 (최대 3회)

> ⚠️ **Phase B 완료(pytest 0 failures OR D-verify Chrome 검증 완료) 즉시 Phase C 시작. 멈추지 말 것.**

**배치 모드 분기** (Phase A에서 배치 판정된 경우):
- 배치 내 US들을 **순차적으로** Phase B 실행 (각 US별 TeamCreate → 구현 → pytest → TeamDelete)
- 단, 같은 도메인 US끼리는 **동일 팀에서 연속 처리** 가능 (팀 재생성 불필요)
- 배치 내 모든 US의 Phase B 완료 후 → Phase C를 **배치 단위로 일괄 실행**

**Phase B → Phase C 전환 (구체적 실행 순서 — 반드시 이 순서대로):**
```
1. pytest PASS 확인 (Lisa 보고 수신)
2. Phase D/H US → Chrome 검증 완료 확인 (Rosé 또는 Step 2.5 결과)
3. TeamDelete 완료 (Jennie+Lisa+Rosé 전원 shutdown)
4. orphan 팀 확인: ~/.claude/teams/leviathan-* 잔존 시 TeamDelete 먼저 실행
5. leviathan-progress.json 저장:
   Bash: echo '{"current_us":"US-XXX","next_phase":"C","plan_file":"docs/planning/US-XXX_PLAN.md","test_result":"PASS","timestamp":"'$(date -Iseconds)'"}' > .omc/state/leviathan-progress.json
6. 즉시 Phase C 시작 → Docker 게이트 + Sakura(shadow-tester) 호출
```
> ⚠️ **1~4 사이에 멈추지 말 것. TeamDelete 완료 즉시 Phase C 에이전트 호출.**

---

### Phase C — 검증 (LE SSERAFIM + IVE + NEWJEANS)

**Docker 필수 게이트** (Phase C 진입 시 최우선 실행):
`docker compose up -d timescaledb redis && docker compose ps` — DB/Redis healthy 확인 (engine 컨테이너는 local python과 port 충돌 → 제외). Docker 미실행 상태에서 검증 진행 절대 금지.
Docker 게이트 FAIL 시: `docker compose up -d timescaledb redis` 재실행 (최대 2회, 30초 대기). 2회 실패 → L2 에스컬레이션.

**순서대로 실행** (하나라도 실패하면 Phase B로 복귀):

#### C-1. Shadow 테스트 (LE SSERAFIM)
- Docker healthy 확인 완료 상태에서 실행 (Phase C 진입 시 이미 확인됨)
- `Agent(subagent_type="shadow-tester", name="sakura", prompt="Shadow 10분 + DB 모니터링: (1) docker compose up -d timescaledb redis && docker compose ps 확인, (2) cd engine && timeout 600 python -m src.main을 백그라운드 실행, (3) 3분/6분/9분 시점에 DB/Redis 체크: docker exec leviathan-timescaledb psql -U leviathan -d leviathan -c 'SELECT count(*) FROM execution_log;' + docker exec leviathan-redis redis-cli DBSIZE, (4) Shadow 완료 후 .omc/state/shadow-result-latest.json 기록 + 최종 DB 레코드 수 + PnL/WR/crash 보고")` **직접 호출**
- **필수 조건**: `PnL > 0`, `crash = 0`
- 실패 시 → Phase B fix 루프

#### C-2. 퀀트 검증 (IVE) — 전략/수식 변경 시에만
- `Agent(subagent_type="quant-validator", name="wonyoung", prompt="SSOT.md §4 수식 대비 코드 검증. 슬리피지/마찰력 수학 검증")` **직접 호출**
- 파라미터 민감도 분석

#### C-3. 코드 리뷰 (NEWJEANS)
- `Agent(subagent_type="oh-my-claudecode:code-reviewer", name="minji", model="opus", prompt="변경 코드 전체 리뷰: 보안/로직/성능. docs/review/US-XXX_REVIEW.md 작성")` **직접 호출**
- `Agent(subagent_type="oh-my-claudecode:critic", name="hanni", model="opus", prompt="설계 비판 + 개선안 제시")` **직접 호출**

#### C-3.5. Chrome 브라우저 검증 (Phase D/H US + 대시보드 변경 US 전용)
> Phase D/H US이거나 대시보드 변경이 포함된 US인 경우 **반드시 실행**.
> 코드 리뷰(텍스트 분석)만으로 대시보드 검증 완료 선언 금지.

- **전제**: Docker healthy + engine API 구동 + `npm run dev` (dashboard port 3000)
- `Agent(subagent_type="browser-verifier", name="kazuha", prompt="[US-XXX] Chrome 검증: 변경된 대시보드 페이지 전체 순회. (1) navigate_page로 각 페이지 방문, (2) take_screenshot으로 렌더링 증거, (3) list_network_requests로 API 200 확인, (4) evaluate_script로 데이터 바인딩 확인, (5) resize_page(375,812) 모바일 확인, (6) list_console_messages로 JS 에러 0건 확인. 결과: 페이지별 PASS/FAIL + 스크린샷")` **직접 호출**
- LE SSERAFIM 소속 (Sakura와 함께 검증팀)
- **FAIL 시**: Phase B fix 루프 복귀 (Rosé에게 수정 지시)

#### C-4. 완료 처리

**단일 US 모드:**
- `Agent(subagent_type="ssot-keeper", name="haerin", prompt="SSOT.md 해당 섹션 업데이트")` **직접 호출**
- prd.json 해당 US `passes: true` 마킹
- `docker compose ps` → 전 컨테이너 healthy 확인
- `git add` + `git commit` + `git push` (gh CLI)

**배치 US 모드** (Phase A에서 배치 판정된 경우):
- **Shadow**: 배치 단위 1회 실행 (마지막 US 코드 통합 후). 개별 US마다 Shadow 불필요.
- **Code Review**: 배치 단위 1회 실행 (변경 코드 전체 대상).
- **Chrome 검증**: 대시보드 변경 US가 배치에 포함된 경우 Kazuha 1회 호출.
- 배치 내 **모든 US**의 Phase B+C 완료 후 일괄 처리:
  1. `Agent(subagent_type="ssot-keeper", name="haerin", prompt="SSOT.md 배치 US 일괄 업데이트: US-XXX~US-YYY")` **직접 호출**
  2. prd.json 배치 내 전체 US `passes: true` 마킹
  3. `docker compose ps` → 전 컨테이너 healthy 확인
  4. `git add -A && git commit -m "Phase [phase]: US-XXX~US-YYY [배치 설명]"` — **배치 단위 단일 커밋**
  5. `git push` (gh CLI)
- ⚠️ 배치 커밋 메시지 형식: `Phase SR US-058~062: [공통 변경 요약]`

**Phase C → 다음 US 전환 (구체적 실행 순서 — 반드시 이 순서대로):**
```
1. Shadow PASS 확인 (Sakura 보고: PnL>0, crash=0, DB 레코드 존재)
2. Code Review 완료 확인 (Minji + Hanni 결과 수신)
3. Chrome 검증 완료 확인 (해당 시, Kazuha 결과 수신)
4. ssot-keeper(haerin) → SSOT.md 업데이트
5. prd.json 해당 US passes:true 마킹
6. docker compose ps → 전 컨테이너 healthy 최종 확인
7. git add + git commit + git push
8. leviathan-progress.json 저장:
   Bash: echo '{"current_us":"US-XXX","next_phase":"A","next_us":"US-YYY","timestamp":"'$(date -Iseconds)'"}' > .omc/state/leviathan-progress.json
9. 즉시 다음 US Phase A 시작 → prd.json에서 다음 passes:false US 찾기
```
> ⚠️ **1~6 사이에 멈추지 말 것. git push 완료 즉시 다음 US 진행.**

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

**Phase D/H 및 대시보드 변경 US 추가 완료 기준:**

| 항목 | 조건 | Chrome DevTools MCP 도구 |
|------|------|------|
| Chrome 브라우저 테스트 | 해당 페이지 실제 렌더링 확인 | Kazuha(browser-verifier) 또는 Rosé |
| API 연동 | 대시보드 ↔ 엔진 API 200 응답 | `list_network_requests` |
| WebSocket 피드 | 실시간 데이터 업데이트 렌더링 | `evaluate_script` (WS 상태 확인) |
| 모바일 반응형 | 375x812 뷰포트 레이아웃 정상 | `resize_page` + `take_screenshot` |
| 데이터 바인딩 | 컴포넌트에 실제 데이터 표시 (null/empty 아님) | `evaluate_script` (DOM 확인) |
| JS 에러 | 콘솔 에러 0건 | `list_console_messages` |
| Docker 서비스 | 전 컨테이너 healthy 상태에서 검증 | `docker compose ps` |

> **중요**: `npm run build` 성공만으로 Phase D/H 완료 선언 **절대 금지**.
> 반드시 Docker 실행 + Chrome DevTools MCP로 실제 브라우저 렌더링 검증 필수.
> Phase B(Rosé) + Phase C(Lead/C-3.5) 양쪽에서 Chrome 검증 실시.

## 4. 다음 US 전환 조건

위 완료 기준 **전부 충족** 후에만 다음 US 시작.
충족 안 되면 fix 루프 반복. 최대 5회 실패 시 사용자에게 보고.

> ⚠️ **완료 기준 전부 충족 즉시 → 사용자 확인 없이 자동으로 다음 `passes:false` US의 Phase A 시작.**
> **"계속 진행할까요?" / "다음으로 넘어갈까요?" 절대 금지. 멈추지 말 것.**

## 4.5 에스컬레이션 규칙 (문제 심각도별 대응)

> 기존 fix 루프(최대 3회)를 넘어서는 구조적 문제 발생 시 상위 단계로 에스컬레이션.

| Level | 조건 | 대응 |
|-------|------|------|
| **L0** (팀 내 즉시 해결) | 단순 버그, 테스트 실패, 타입 에러 | 해당 팀에서 바로 수정 |
| **L1** (개발↔검증 반복) | Phase B fix 루프 (최대 3회) | 기존 방식 유지 |
| **L2** (Phase A 재기획) | fix 루프 3회 초과 OR 구조적 문제 발견 | Phase B/C 중단 → Phase A 복귀 → 새 PLAN.md 작성 후 Phase B 재시작 |
| **L3** (PRD/SSOT 수정 필요) | SSOT↔PRD↔구현 간 모순 발견 | 즉시 작업 중단 → SSOT 수정 → PRD 수정 → Phase A 재기획 |
| **L4** (Phase 재편성 필요) | 현재 Phase 범위로 해결 불가능한 문제 | 새 US 생성 → prd.json 추가 → 다음 Phase에 배정 OR 현재 Phase 확장 |

**에스컬레이션 흐름:**
```
L0: Jennie/Lisa/Rosé가 팀 내부에서 해결
L1: Jisoo(Lead)가 fix 루프 3회 내 해결
L2: Lead가 Phase A architect에게 재기획 요청 → 새 PLAN.md
L3: Lead가 ssot-keeper(haerin)에게 SSOT 수정 요청 → prd.json 동기화
L4: Lead가 prd.json에 새 US 추가 → 다음 사이클에서 처리
```

> **자동화**: L0~L1은 기존 ralph 루프 내에서 자동 처리. L2~L4는 로그 출력 후 해당 단계로 자동 복귀.

---

## 5. 인프라 규칙

- **테스트**: `cd engine && python -m pytest tests/ -x --tb=short`
- **Shadow**: `docker compose up -d timescaledb redis && docker compose ps` 후 `cd engine && timeout 600 python -m src.main` (engine 컨테이너는 local python과 port 8000 충돌 — DB/Redis만 기동)
- **Docker**: **Phase B 진입 시 + Phase C 진입 시** 반드시 `docker compose up -d timescaledb redis && docker compose ps` — DB/Redis healthy 필수. Docker 미실행 상태에서 개발/검증 진행 금지.
- **Chrome DevTools MCP**: Phase D/H US + 대시보드 변경 US에서 필수. `chrome-devtools` MCP 서버가 `~/.claude/settings.json`에 설정 필요. 핵심 도구: `navigate_page`, `take_screenshot`, `list_network_requests`, `evaluate_script`, `resize_page`, `wait_for`, `list_console_messages`, `fill`, `click`.
- **Dashboard 서버**: 대시보드 검증 시 `cd dashboard && npm run dev` (port 3000) + engine API 구동 필수
- **GitHub**: `gh` CLI로 push, PR, issue 관리
- **거래소**: 8 native adapters (ccxt 미사용)
- **슬리피지**: PowerLaw `impact = k * size^gamma` (k=0.0, gamma=0.5) — k=0.0이므로 PowerLaw 비활성. CEXOrderbookSlippage가 유일한 슬리피지 소스
- **이중 슬리피지 금지**: SignalGenerator의 CEXOrderbookSlippage가 유일한 슬리피지 소스

## 6. 컨텍스트 관리

### 6.1 연속 실행 원칙

**Phase A→B→C는 하나의 연속 흐름으로 실행.** Phase 간 `/clear` 없음.
Phase 완료마다 `leviathan-progress.json`에 체크포인트 저장 (세션 복구용).

> **왜 Phase 간 `/clear` 안 하나?** Claude는 `/clear`를 프로그래밍적으로 실행할 수 없음 (사용자 전용 명령어).
> 자동화된 연속 실행과 `/clear`는 양립 불가. 멈추지 않는 것이 최우선.
> Claude Code의 내장 auto-compression이 컨텍스트 한계 도달 시 자동 처리.

| 시점 | 동작 |
|------|------|
| **Phase A 완료** | PLAN.md 저장 + checkpoint → **즉시 Phase B TeamCreate** |
| **Phase B 완료** | pytest PASS + TeamDelete + checkpoint → **즉시 Phase C shadow-tester** |
| **Phase C 완료** | SSOT/prd.json/git push + checkpoint → **즉시 다음 US Phase A** |

### 6.2 절대 금지

- **`/compact` 어떤 시점에서도 절대 금지** (버그: GitHub #3274, #19567, #18482)
- **Phase 진행 중 컨텍스트 조작 금지** (백그라운드 에이전트 결과 소실)
- **Phase 간 멈춤 금지** — 체크포인트 저장 후 즉시 다음 Phase tool call 실행
- **실패 사례 (US-060)**: Phase C 에이전트 실행 중 컨텍스트 압축 → Shadow 결과 미수집

### 6.3 체크포인트 파일 (leviathan-progress.json)

`.omc/state/leviathan-progress.json` — 각 Phase 완료 시 자동 저장:
```json
{"current_us": "US-066", "next_phase": "B", "plan_file": "docs/planning/US-066_PLAN.md", "timestamp": "2026-03-10T15:30:00+09:00"}
```

**용도**: 세션 복구 전용
- 세션 크래시, 사용자 수동 `/clear`, 또는 네트워크 오류 시 → `/leviathan` 재호출하면 이 파일로 재개
- 정상 실행 시에는 Phase 간 끊김 없이 연속 진행 (체크포인트는 보험)
- §8의 시작 로직이 이 파일을 1순위로 확인

### 6.4 사용자 수동 `/clear` 시 복구 흐름

```
사용자가 수동 /clear 실행
  → 컨텍스트 초기화됨
  → 사용자가 /leviathan 입력
  → §8 시작 로직: leviathan-progress.json 읽기 (1순위)
  → next_phase에 따라 해당 Phase부터 재개 (Phase A가 아니어도 됨)
  → plan_file이 있으면 PLAN.md 읽어 컨텍스트 복원
```
> **정상 흐름에서는 /clear 불필요.** ralph 루프가 Phase A→B→C→다음 US를 끊김 없이 순회.

> **orphan 팀 처리**: 세션 복구 시 `~/.claude/teams/leviathan-*` 디렉토리 존재 확인.
> 존재하면: TeamDelete 먼저 실행 후 해당 Phase 재시작.

## 7. Phase F 최종 검수 규칙

> **진입 조건**: Phase H, I, J, K, L, M의 모든 US가 `passes:true`여야 Phase F(LAST) 진입 가능.
> prd.json에서 H/I/J/K/L/M US 중 `passes:false`가 하나라도 있으면 Phase F 진입 금지.
> 순서: H → I → J → K → L → M → F(최종검수, LAST) (CLAUDE.md §현재상태 참조)

> Phase F는 **모든 기능이 구현·검증된 후** 진입하는 **마지막 관문**. 자동차 출고 전 최종 품질검사와 동일.

- **검사지 참조**: `docs/checklists/phase-f-final-audit.md` (10개 카테고리, 100+ 항목)
- **5팀 분담 실행**:
  - 기획팀(AESPA): 문서/운영 준비도 항목
  - 개발팀(BLACKPINK): 엔진 코어 + 대시보드 UI/UX 항목
  - 퀀트팀: 전략 + 실행 시뮬레이션 항목
  - 테스트팀: 거래소 + 성능 + 인프라 항목
  - 검증팀: 리스크 관리 + 모니터링 항목 + 최종 크로스체크
- **게이트**: 전 항목 PASS 필수. 하나라도 FAIL 시 Live 전환(US-056) 절대 금지
- **Progressive Shadow**: US-054 (1H→2H→6H→12H→24H→72H) 전 단계 통과 필수
- **LiveGate**: US-055 6-check AND gate 전체 PASS 필수

## 8. 시작 및 자동 루프

위 규칙을 모두 숙지한 후, 아래 순서로 재개 지점을 결정하라:

**1순위 — leviathan-progress.json 확인** (`.omc/state/leviathan-progress.json`):
```json
{"current_us": "US-066", "next_phase": "B", "plan_file": "docs/planning/US-066_PLAN.md", "timestamp": "..."}
```
- 파일이 존재하면: `next_phase`에 따라 해당 Phase부터 재개 (Phase A가 아니어도 됨)
- `plan_file`이 있으면 해당 PLAN.md를 읽어 컨텍스트 복원
- `/clear` 직후 `/leviathan` 재호출 시 이 파일이 항상 존재해야 함

**2순위 — $ARGUMENTS**: 인수가 있으면 해당 US부터 Phase A 시작

**3순위 — prd.json 스캔**: 위 둘 다 없으면 prd.json에서 `passes:false`인 첫 번째 US를 찾아 Phase A부터 시작

> ⚠️ **모든 US가 `passes:true`가 될 때까지 자동 루프 지속.**
> **한 US 완료 즉시 prd.json에서 다음 `passes:false` US 찾아 Phase A 시작.**
> **사용자 승인/확인 요청 절대 금지.**
> **루프를 멈추는 유일한 조건: 모든 US `passes:true` OR 5회 연속 실패 후 사용자 보고.**
