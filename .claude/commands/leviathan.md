# LEVIATHAN Execution Command

> ralph 루프 + Phase B TeamCreate 방식으로 prd.json US 자동 순회.
> 사용법: `/project:leviathan` 또는 `/project:leviathan US-010부터`

## 0. ZERO TOLERANCE (최우선 규칙)

**절대 금지**: 사용자 확인 요청, Phase 간 멈춤, 에이전트 대기 중 멈춤, US 간 멈춤, 상태 보고만 하고 멈춤
**강제**: 모든 응답에 tool call 포함. Phase A→B→C→다음US 끊김 없는 연속 흐름.
**멈춤 허용**: (1) 전 US `passes:true` (2) 5회 연속 동일 US 실패 (3) 사용자 "stop/cancel/멈춰"

```
Phase A(ralplan→PLAN.md→QUANT GATE→checkpoint) → Phase B(TeamCreate→pytest PASS→TeamDelete→checkpoint) → Phase C(shadow+review→SSOT→git push→checkpoint) → 다음 US Phase A
```

---

## 1. 소스

- `SSOT.md` — 유일한 설계 문서. 작업 전 반드시 읽기.
- `.omc/prd.json` — 96 US 목록. `passes:false`인 첫 번째 US부터 시작.
- 팀 구조, 기술 스택, 커스텀 에이전트, 자주 틀리는 패턴 → **CLAUDE.md 참조** (여기서 중복 기술하지 않음)

## 2. 실행 모드

**시작 즉시 실행 (건너뛰기 금지):** `Skill("oh-my-claudecode:ralph")`

> ⚠️ `ralph` (올바름) vs `team ralph` (잘못됨 — OMC 내장 파이프라인과 충돌). 절대 변경 금지.
> LEVIATHAN 팀은 Phase B에서 `TeamCreate("leviathan-us-xxx")`로 US별 직접 생성/삭제.

ralph 루프 안에서 각 US마다 **3-Phase Sequential** 수행:

---

### Phase A — 기획

**Phase 단위 배치 수집 (Phase A 진입 시 최우선):**
1. prd.json에서 현재 Phase의 모든 `passes:false` US 수집
2. 의존성 그래프 분석 → 독립 US 배치, 의존 US 순차
3. 도메인(engine/dashboard) 기준 배치 그룹 형성
4. Phase 내 모든 배치 그룹 완료 = 1사이클 종료

**배치 규칙:**
- 동일 Phase + 동일 도메인 → 배치 가능 (최대 5 US)
- 다른 도메인 → 별도 배치
- `dependencies` 미충족 OR `files` 교집합 → 순차 실행
- 배치 → 통합 `docs/planning/Phase-X_PLAN.md` 작성

**복잡도 판단 (prd.json `files` 기준):**
- **복잡 US** (files 3+개 OR 아키텍처 변경): `Skill("oh-my-claudecode:ralplan")` → `--deliberate "US-XXX [제목]: [acceptanceCriteria]"`
- **단순 US** (files 1-2개): `Agent(subagent_type="oh-my-claudecode:architect", prompt="...")`
- 산출물: `docs/planning/US-XXX_PLAN.md` + `.omc/handoffs/US-XXX-handoff.md`

**QUANT GATE — `files`에 전략/수식 키워드 포함 시에만:**
키워드: `slippage|signal|strategy|executor|funding|futures|triangular|statistical|friction|cost_calculator|regime|hmm|xgboost|onnx|dex|gas_oracle`
```
Agent(subagent_type="quant-validator", name="winter",
      prompt="US-XXX 기획 검증: SSOT.md §4 대비 PLAN.md 정합성.
              1) 파라미터 범위 2) 이중계산 여부 3) PnL 영향 4) 수식 일치. PASS/FAIL+근거")
```
- PASS → Phase B. FAIL → PLAN 수정 후 재호출 (최대 2회)

**A→B 전환 (순서 엄수):**
1. PLAN.md 존재 확인
2. QUANT GATE 해당 시 PASS 확인
3. `leviathan-progress.json` 저장 (`next_phase:"B"`)
4. 즉시 `TeamCreate(team_name="leviathan-us-xxx")`

---

### Phase B — 개발

**TeamCreate 기반** (단독 구현 절대 금지):

#### Step 1: 팀 생성
`TeamCreate(team_name="leviathan-us-xxx")`

#### Step 2: Teammate 스폰 (동시 2-3명)

**필수:**
- **Jennie** (executor): `engine/src/` 구현. dashboard/tests/ 수정 금지. 완료 시 Lisa에게 SendMessage.
- **Lisa** (test-engineer): `tests/` 테스트 작성 + `pytest -x --tb=short`. 결과를 Lead에게 보고.

**Rosé(designer) 스폰 조건:**
- Phase D/H US → 항상
- `files`에 `"api/"`, `"shadow.py"`, `"dashboard/"` 포함
- `acceptanceCriteria`에 `"dashboard"`, `"UI"`, `"프론트"` 키워드
- `dashboard/` 구현. engine/ 수정 금지.

**D-verify US (US-063, US-064) — 메인 세션 직접 Chrome 검증:**
1. `preview_start("dashboard")` → localhost:3000
2. 4페이지 순회: `preview_snapshot()` + `preview_network()` (API 200)
3. `preview_resize(preset="mobile")` → 모바일 뷰 확인
4. `preview_screenshot()` → 증거 캡처

#### Step 2.5: 통합 검증
- Jennie 완료 → 대시보드 반영 필요 판단 (API/Shadow 변경 시)
- 필요 시 Rosé 추가 스폰 + Chrome 검증
- **완료 기준**: pytest 0 failures **AND** 관련 대시보드 데이터 정상 표시

#### Step 3: 통합
- Lisa pytest → PASS: Step 4. FAIL: Jennie 수정 (최대 3회)

#### Step 4: 팀 해산
- 전원 `shutdown_request` → `TeamDelete()`

**파일 소유권:** Jennie=`engine/src/`, Rosé=`dashboard/src/`, Lisa=`tests/`+`docker-compose.yml`, Lead=SSOT/`.omc/`

**배치 모드:** 배치 내 US 순차 Phase B → 전부 완료 후 Phase C 일괄 실행. 동일 도메인은 팀 재사용 가능.

**B→C 전환 (순서 엄수):**
1. pytest PASS
2. TeamDelete 완료
3. `leviathan-progress.json` 저장 (`next_phase:"C"`)
4. 즉시 shadow-tester(sakura) 호출

---

### Phase C — 검증

**순서대로** (실패 시 Phase B fix 루프):

#### C-1. Shadow 테스트
- **Docker 필수**: `docker compose up -d && docker compose ps` (실패 시 최대 2회 재시도)
- `Agent(subagent_type="shadow-tester", name="sakura", prompt="Shadow 10분: docker compose up -d 확인 후 cd engine && timeout 600 python -m src.main. PnL/WR/crash 보고")`
- 필수: `PnL > 0`, `crash = 0`

#### C-2. 퀀트 검증 — 전략/수식 변경 시에만
- `Agent(subagent_type="quant-validator", name="wonyoung", prompt="SSOT.md §4 수식 대비 코드 검증")`

#### C-3. 코드 리뷰
- `Agent(subagent_type="oh-my-claudecode:code-reviewer", name="minji", prompt="변경 코드 리뷰. docs/review/US-XXX_REVIEW.md 작성")`
- `Agent(subagent_type="oh-my-claudecode:critic", name="hanni", prompt="설계 비판 + 개선안")`

#### C-3.5. Chrome 검증 — Phase D/H US에서만
- `Agent(subagent_type="browser-verifier", name="kazuha", prompt="Chrome DevTools MCP로 대시보드 검증: 페이지 렌더링, API 200, WebSocket, 모바일 뷰")`

#### C-4. 완료 처리
- `Agent(subagent_type="ssot-keeper", name="haerin", prompt="SSOT.md 업데이트")`
- prd.json `passes: true` 마킹
- `docker compose ps` → healthy 확인
- `git add` + `git commit` + `git push`
- 배치 모드: 전체 US 일괄 처리, 배치 단위 단일 커밋

**C→다음US 전환 (순서 엄수):**
1. Shadow PASS + Review 완료
2. ssot-keeper SSOT.md 업데이트
3. prd.json passes:true
4. git commit + push
5. `leviathan-progress.json` 저장 (`next_phase:"A", next_us:"US-YYY"`)
6. 즉시 다음 US Phase A

---

## 3. 완료 기준

| 항목 | 조건 |
|------|------|
| pytest | 0 failures |
| Shadow 10min | PnL > 0, crash = 0 |
| SSOT.md | 해당 섹션 업데이트됨 |
| prd.json | `passes: true` |
| PLAN.md | `docs/planning/`에 존재 |
| REVIEW.md | `docs/review/`에 존재 |
| Docker | 전 컨테이너 healthy |
| Git | commit + push 완료 |
| **Phase D/H 추가** | Chrome 렌더링 + API 200 + WebSocket + 모바일 반응형 |

> `npm run build` 성공만으로 Phase D/H 완료 선언 금지. Chrome 실제 렌더링 필수.

## 4. 다음 US 전환

완료 기준 **전부 충족** 후 자동 전환. 미충족 시 fix 루프 (최대 5회 → 사용자 보고).

## 4.5 에스컬레이션

| Level | 조건 | 대응 |
|-------|------|------|
| L0 | 단순 버그 | 팀 내 즉시 수정 |
| L1 | fix 루프 3회 | 기존 방식 |
| L2 | 3회 초과/구조적 문제 | Phase A 복귀 → 새 PLAN.md |
| L3 | SSOT↔PRD↔코드 모순 | SSOT→PRD 수정 → Phase A 재기획 |
| L4 | Phase 범위 초과 | 새 US → prd.json 추가 |

L0~L1 자동 처리. L2~L4 로그 출력 후 자동 복귀.

## 5. 인프라 규칙

- **테스트**: `cd engine && python -m pytest tests/ -x --tb=short`
- **Shadow**: `docker compose up -d && docker compose ps` 후 `cd engine && timeout 600 python -m src.main`
- **슬리피지**: CEXOrderbookSlippage가 유일한 소스. PowerLaw k=0.0 비활성. 이중 슬리피지 금지.

## 6. 컨텍스트 관리

**Phase A→B→C 연속 실행.** Phase 간 `/clear` 없음. `/compact` 절대 금지 (GitHub #3274, #19567, #18482).

| 시점 | 동작 |
|------|------|
| Phase A 완료 | PLAN.md + checkpoint → 즉시 Phase B TeamCreate |
| Phase B 완료 | pytest PASS + TeamDelete + checkpoint → 즉시 Phase C |
| Phase C 완료 | SSOT/prd.json/git push + checkpoint → 즉시 다음 US |

**체크포인트**: `.omc/state/leviathan-progress.json` — 세션 복구 전용.
세션 크래시/수동 `/clear` 시 → `/leviathan` 재호출 → progress 파일로 재개.

## 7. Phase F 최종 검수

> **진입 가드**: Phase G/H/I/J/K/L/M 전부 `passes:true` 필수. 하나라도 미완료 시 Phase F 진입 금지.

- **검사지**: `docs/checklists/phase-f-final-audit.md` (12개 카테고리, 178항목)
- **5팀 분담**: 기획(문서/운영) + 개발(엔진/UI) + 퀀트(전략/시뮬레이션) + 테스트(거래소/성능/인프라) + 검증(리스크/모니터링)
- **게이트**: 전 항목 PASS 필수. 1건 FAIL → Live 전환(US-056) 금지
- **Progressive Shadow**: US-054 (1H→2H→6H→12H→24H→72H) 전 단계 통과
- **LiveGate**: US-055 6-check AND gate 전체 PASS

## 8. 시작 및 자동 루프

재개 지점 결정 순서:

**1순위 — `.omc/state/leviathan-progress.json`**:
- 존재 시 `next_phase`에 따라 해당 Phase부터 재개
- `plan_file` 있으면 PLAN.md 읽어 복원

**2순위 — $ARGUMENTS**: 인수가 있으면 해당 US부터 Phase A

**3순위 — prd.json 스캔**: `passes:false`인 첫 번째 US → Phase A

> 모든 US `passes:true`까지 자동 루프. 사용자 승인 요청 절대 금지.
> 멈추는 조건: 전 US 완료 OR 5회 연속 실패 후 사용자 보고.
