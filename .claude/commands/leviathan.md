# LEVIATHAN Execution Command

> ralph 루프 + Stage B TeamCreate 방식으로 prd.json US 자동 순회.
> 사용법: `/project:leviathan` 또는 `/project:leviathan US-010부터`

## 0. ZERO TOLERANCE + ANTI-STALL

**절대 금지**: 사용자 확인 요청, Stage 간 멈춤, 에이전트 대기 중 멈춤, US 간 멈춤, 상태 보고만 하고 멈춤, **run_in_background: true**
**강제**: 모든 응답에 텍스트 1줄 + tool call 병행. Stage A→B→C→다음Phase 끊김 없는 연속 흐름. **모든 Agent foreground 실행.**
**외부 CLI**: codex/gemini/qwen은 **동일 메시지 foreground 병렬** + `timeout 300`. 실패 시 3회 재시도 → 전원 실패 시 L5 (스킵 절대 금지).
**멈춤 허용**: (1) TF Final PASS (Live 준비) (2) 5회 연속 동일 US/TF 실패 (L5) (3) 사용자 "stop/cancel/멈춰"
**에이전트 반환**: **파일 기반**. 결과를 `.omc/artifacts/{agent}-{phase}.md`에 기록. 반환은 `PASS/FAIL + 경로 + 핵심 요약`. 컨텍스트 팽창 원천 차단.
**CLI 전용**: tmux/터미널에서 실행. VS Code에서 실행 금지 (freeze 위험 #26778).
**Watchdog**: Dev봇이 watchdog. `python -m src.infra.telegram_dev_bot` (또는 `bash scripts/watchdog.sh`). tmux 멈춤 감지 → 알림 → 자동 재개. `/go`로 텔레그램 수동 재개.
**ANTI-STALL (#30625/#33043/#34238)**:
- **모든 응답에 텍스트 1줄 + tool call 병행** (텍스트 없으면 stop hook이 "완료"로 판단 → 루프 종료 버그 #30625).
- 에이전트 결과 수신 → 즉시 다음 tool call. "다음 세션에서" 금지 (#34238).
- **TeamCreate 30초 무응답 → TeamDelete → Agent() fallback** (#33043).
- **컨텍스트 60% 감지 시**: checkpoint → `/clear` → progress.json 재개.
- **Stage C 전체가 1개 메시지 체인**: C-Step 1~7 끊기 없이 연속.
**압축 후에도 §0과 §99 유지.**

```
Stage A (Entry Gate → 기획 → QUANT GATE → checkpoint)
  → Stage B (TeamCreate → pytest PASS → TeamDelete → Shadow 10min+ → 모니터링 → checkpoint)
  → Stage C (코드리뷰+보안 → Phase 완료 리뷰+Go/No-Go → SSOT+git push → 텔레그램)
  → 다음 Phase Stage A (자동 진행)
```

> **용어 규칙**: "Stage" = 워크플로우 단계 (A~C), "Phase" = 로드맵/PRD 단계 (G/H/I/K/L/M/F). 혼용 금지.

---

## 1. 소스

- `SSOT.md` — 유일한 설계 문서. 작업 전 반드시 읽기.
- `.omc/prd.json` — US 목록 (335개). `passes:false`인 첫 번째 US부터 시작.
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

### 자동 일관성 검사 (워크플로우 자동화 레이어)
- 실행: `cd engine && python -m src.workflow.cli --root $(git rev-parse --show-toplevel) check_all`
- 결과가 OK → Karina에게 "자동 검사 PASS" 컨텍스트 주입
- 결과가 DRIFT → Karina에게 드리프트 상세 전달, 수동 확인 요청
- 결과가 ERROR → 에러 내용 전달, Entry Gate 진행 전 수정 필요
- **참고**: 이 검사는 보조 역할. Karina의 최종 판단을 대체하지 않음.

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
              **불일치 발견 시**: 구체적 수정 지시 (파일:라인 + 현재값→정확값). 수정 완료까지 Stage B 진입 금지.
              **반환 3K 토큰 이내 요약. 상세 내용은 파일에 기록.**")
```
> Entry Gate 결과 수신 즉시 → Step 2 스폰. 상태 요약/보고 금지.

**Step 2: NingNing + Winter + Giselle — 병렬 (Karina 결과 주입)**
```
# NingNing: 요구사항 분석
Agent(subagent_type="oh-my-claudecode:analyst", name="ningning",
      prompt="Phase X 요구사항 분석: [us_targets 목록] + acceptanceCriteria 검증.
              엣지케이스 도출, 수용기준 누락/모호 확인. Entry Gate 결과 반영: [Karina 결과 주입]
              **반환 3K 토큰 이내 요약.**")

# Winter: 기획 비판
Agent(subagent_type="oh-my-claudecode:critic", name="winter", model="opus",
      prompt="Phase X 기획 비판: PLAN.md 초안 대비 설계 결함, 누락 사항, 과도한 복잡성 지적.
              Entry Gate 결과 반영: [Karina 결과 주입]
              **반환 3K 토큰 이내 요약.**")

# Giselle: PLAN.md 작성
Agent(subagent_type="oh-my-claudecode:planner", name="giselle",
      prompt="Phase X PLAN.md 작성: [us_targets 목록] + acceptanceCriteria 기반.
              SSOT.md §4 참조. 산출물: docs/planning/Phase-X_PLAN.md
              **Entry Gate 결과 반영**: [Karina 결과 ~3K 토큰 주입]
              **반환 3K 토큰 이내 요약. 상세는 docs/planning/Phase-X_PLAN.md에 기록.**")
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

**PLAN REVIEW GATE — 멀티모델 플랜 감사 (PLAN.md 완성 후, QUANT GATE 전):**
> 3개 외부 모델(Codex+Gemini+Qwen)이 독립적으로 PLAN.md를 검증. Claude 편향 제거.
> **인라인 실행** (AskUserQuestion 없음, TeamCreate 없음 — leviathan 자동 흐름 보장)

**실행 절차:**
1. 프롬프트 작성: `REVIEW_PROMPT="docs/planning/Phase-X_PLAN.md를 읽고, 설계 결함, 누락된 요구사항, 과도한 복잡성, 엣지케이스 미고려를 지적하라. 이슈별로 CRITICAL/HIGH/MEDIUM 심각도를 부여하라."`
2. 3개 Agent() **병렬** spawn (각각 CLI를 직접 실행):
```
Agent(name="codex-plan-reviewer",
      prompt="Bash로 timeout 300 codex exec -s read-only '$REVIEW_PROMPT' 실행. 실패 시 최대 3회 재시도. 전원 실패 시 L5 에스컬레이션.")
Agent(name="gemini-plan-reviewer",
      prompt="Bash로 timeout 300 gemini -p '$REVIEW_PROMPT' --approval-mode plan 실행. 실패 시 최대 3회 재시도. 전원 실패 시 L5 에스컬레이션.")
Agent(name="qwen-plan-reviewer",
      prompt="Bash로 timeout 300 qwen --approval-mode plan -p '$REVIEW_PROMPT' 실행. 실패 시 최대 3회 재시도. 전원 실패 시 L5 에스컬레이션.")
```
3. Claude가 독립적으로 PLAN.md 리뷰 (4번째 관점)
4. 4개 리뷰 결과 수집 → **quorum 합의**: 2개 이상 모델이 지적한 이슈 = MUST FIX
5. 결과를 `.omc/artifacts/consensus-plan-{phase}.md`에 저장

- quorum MUST FIX 이슈 1건 이상 → PLAN.md 수정 후 재검증 (최대 1회)
- MUST FIX 이슈 0건 → QUANT GATE 진행

**QUANT GATE — `files`에 전략/수식 키워드 포함 시에만:**
키워드: `slippage|signal|strategy|executor|funding|futures|triangular|statistical|friction|cost_calculator|regime|hmm|xgboost|onnx|dex|gas_oracle`
```
Agent(subagent_type="quant-validator", name="yeji", model="opus",
      prompt="Phase X 기획 검증: SSOT.md §4 대비 PLAN.md 정합성.
              1) 파라미터 범위 2) 이중계산 여부 3) PnL 영향 4) 수식 일치. PASS/FAIL+근거
              **반환 3K 토큰 이내 요약.**")
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

#### B-Step 1: 개발 + 단위테스트 (TeamCreate)

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

**배치 모드:** 배치 내 US 순차 Stage B-Step 1 → 전부 완료 후 Phase 2 일괄 실행. 동일 도메인은 팀 재사용 가능.

**활성 팀**: IVE(개발) — TeamCreate 협업 (최대 6명)

#### B-Step 2: Shadow 테스트 + 모니터링 (Sub-agents)

> TeamDelete 후, 독립 Sub-agents로 런타임 검증.
> shadow-tester는 `context: fork`로 fresh context 자동 보장.

**B-2-1. Docker 인프라 확인**
- `docker compose up -d timescaledb redis && docker compose ps` (DB/Redis만, 서비스 지정 필수)
- `docker compose stop engine auto-tuner monitoring 2>/dev/null` (이전 코드 실행 방지, 로컬 엔진과 충돌 방지)
- timescaledb healthy + redis healthy 필수
- 오토튜너 테스트 시: `docker compose build engine auto-tuner && docker compose up -d engine auto-tuner` (새 빌드 필수)

**B-2-2. Shadow 실행 (10분 이상 무중단)**
```
Agent(subagent_type="shadow-tester", name="minji",
      prompt="Shadow 10분+: docker compose up -d 확인 후
              cd engine && timeout 600 python -m src.main.
              PnL/WR/crash/DD 보고. 10분 미만 실행 = 자동 FAIL
              **반환 3K 토큰 이내 요약. 상세는 .omc/state/shadow-result-latest.json에 기록.**")
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

**Shadow 필수 조건 (강화된 복합지표 — 시드 무관 절대 지표 기반):**

> **원칙**: 단순 PnL($)은 시드에 따라 상대적 → 절대적 판단 불가. 시드 무관 지표(Sharpe, PF, MDD%, Edge bps)로 판단.

| # | 체크 | 임계값 | 유형 |
|---|------|--------|------|
| 1 | crash | = 0 | 시스템 |
| 2 | 무중단 실행 | >= 10분 | 시스템 |
| 3 | PnL | >= $0 | 기본 (참고용, 절대 지표 아님) |
| 4 | Max Drawdown | < 5% (자본 대비) | **절대 지표** |
| 5 | Profit Factor | > 1.0 (총이익/총손실) | **절대 지표** |
| 6 | 신호 수 | >= 100/day (외삽) | 활성도 |
| 7 | Kill Switch | Not halted | 방어 레이어 |
| 8 | Circuit Breaker | CLOSED | 방어 레이어 |
| 9 | 거래소 Health | >= 95% | 인프라 |
| 10 | loss_capped | = 0 | 리스크 |
| 11 | 활성 전략 trade | 등록된 모든 활성 전략 trade >= 1 | **통합 검증 (dead strategy 방지)** |
| 12 | 방어 레이어 활성 | CB/StaleDetector/OutlierFilter 로그 >= 1건 | **통합 검증** |
| 13 | 결과 파일 기록 | `.omc/state/shadow-result-latest.json` 존재 | 검증 증거 |

> **TF SF 추가 기준**: 위 13항목 + Sharpe >= 2.0 + Calmar > 0 + 전략별 WR > 50% + Expected Edge > 0 bps
> **TF Final 추가 기준**: 위 + Sharpe >= 2.5 + Profit Factor > 1.2 + 리콘실리에이션 오차 < 1%
> **#11 dead strategy 방지**: 7개 전략 중 3개만 작동해도 PnL > 0이면 PASS되던 구멍 차단. trade=0 전략 발견 시 → FAIL (dead wiring 의심)
> **#13 결과 파일**: shadow-tester(Minji)가 반드시 JSON 파일로 결과 기록. Assembly Verifier(C-Step 1) + Karina(C-Step 5)가 이 파일을 검증.

**활성 팀**: NewJeans(테스트, 최대 5명) + ITZY(퀀트 수식검증, 해당 시)

#### B-Step 3: Shadow 실패 시 fix loop (실패 유형별 분류)

```
Shadow FAIL 시 실패 유형 분류 → 유형별 대응:

Type W (Wiring): 전략 trade=0 또는 서브시스템 미활성 → 즉시 L2 (Stage A 재기획)
  근거: 파라미터 문제가 아닌 구조적 미연결. fix loop로 해결 불가.

Type P (Parameter): PnL < 0이지만 전체 전략 활성 (trade > 0) → fix loop 3회
  근거: 임계값/파라미터 조정으로 해결 가능.

Type B (Bug): crash > 0 또는 예외 발생 → debugger fix loop 3회
  근거: 코드 버그 수정 필요.

fix loop 최대 3회 (Type P/B). 3회 초과 시 Stage A 재기획 (에스컬레이션 L2).
Type W는 fix loop 없이 즉시 L2 에스컬레이션.
```

> **3-Stage 핵심 이점**: Shadow 실패 → Stage B 내부에서 fix + re-test = 0 stage 전환.
> (기존 5-Stage: Shadow 실패(D) → B복귀 → pytest(B) → 리뷰(C) → Shadow(D) = 3 stage 전환)
> **실패 유형 분류 이유**: "컴포넌트가 아예 연결 안 됨(Type W)"과 "파라미터가 안 맞음(Type P)"을 동일하게 fix loop 3회 돌리면 시간 낭비. Type W는 아키텍처 재설계가 필요.

### 자동 체크포인트 저장
- 실행: `cd engine && python -m src.workflow.cli --root $(git rev-parse --show-toplevel) checkpoint save --trigger "stage_B_complete"`
- Shadow 결과 메트릭(PnL, WR, crash count)이 체크포인트에 포함됨

**B→C 전환 (동일 메시지 병렬 필수):** pytest PASS + Shadow 13항목 복합지표 전부 PASS (§B-Step 2 표 참조) 확인 즉시 → 아래 2개를 **반드시 동일 메시지에서 병렬 호출**:
1. `state_write(next_stage:"C")`
2. Stage C 리뷰 에이전트 2개 스폰 (jennie + jisoo)
텍스트 출력 금지. tool call만 출력. state_write만 하고 에이전트 안 스폰하면 = BUG.

---

### Stage C — 리뷰 + 릴리스 (Review + Release)

**코드리뷰 → Phase 완료 리뷰(Go/No-Go) → SSOT+git push → 텔레그램을 하나의 Stage로 통합.**

> Stage C의 핵심: "이 코드가 좋은가?" + "이 Phase가 완료되었는가?" + 릴리스.
> 리뷰어가 Shadow 결과를 참조 → 코드 품질 + 런타임 동작 교차 평가 가능.

#### C-Step 1: Assembly Verification — 조립 검증 (코드리뷰 전 필수)

> **"부품이 아니라, 조립된 완성품이 제대로 동작하는가?"**
> 이 단계 없이 코드리뷰 진행 금지. TF QF "단계 3.5"를 매 Phase에 상시 적용.
> Phase S2~S9에서 "코드는 있지만 연결 안 됨" 문제가 반복된 근본 원인: 조립 검증 부재.

```
Agent(subagent_type="oh-my-claudecode:verifier", name="assembly-verifier", model="sonnet",
      prompt="Assembly Verification — 이번 Phase에서 추가/수정된 코드의 조립 상태 검증.
              **4가지 서브 체크 전부 PASS 필수:**

              1. Init Chain 검증:
                 - main.py의 _init_*() 체인에서 이번 Phase 관련 서브시스템 non-None 확인
                 - 새로 추가된 클래스가 Engine 인스턴스에 할당되어 있는지 확인

              2. Signal Flow E2E:
                 - 이번 Phase에서 추가/수정한 전략의 신호 경로 추적
                 - SignalGenerator → Strategy.on_signal() → TradeRequest → Executor 경로 완성 확인

              3. Dead Wiring Detection:
                 - git diff로 이번 Phase에서 새로 생성된 클래스 목록 추출
                 - 각 클래스가 (1) 인스턴스 생성 (2) 소비자에게 주입 (3) 런타임 호출 경로 3가지 모두 존재하는지
                 - 하나라도 없으면 CRITICAL: Dead Wiring

              4. Config Flag Audit:
                 - 이번 Phase에서 추가된 env var/설정이 실제 로드되는지
                 - ENABLE_* 플래그가 True일 때 해당 기능이 활성화되는 코드 경로 확인

              5. US AC Method Trace (거짓 양성 방지):
                 - 해당 Phase US의 acceptanceCriteria에 언급된 메서드/기능 목록 추출
                 - 각 메서드가 (1) 정의됨 (2) 호출 경로 존재 (3) 테스트됨 확인
                 - 정의만 있고 호출 안 됨 = CRITICAL: Dead Method (passes:true 금지)

              **산출물**: 5개 서브체크 결과 + PASS/FAIL + FAIL 시 구체적 미연결 목록.
              FAIL 시 Stage B-Step 1로 복귀 (fix 필요).
              **반환 3K 토큰 이내 요약.**")
```

> Assembly FAIL → Stage B-Step 1 fix 루프 복귀. 코드리뷰 진입 금지.
> Assembly PASS → C-Step 2 멀티모델 감사 진행.

#### C-Step 2: 멀티모델 독립 감사 (Assembly PASS 후, 코드리뷰 전)

> **목적**: 자기 코드의 리뷰자가 되는 Claude의 **확증 편향(confirmation bias) 제거**.
>
> **설계 이유**: Stage B에서 Claude(IVE팀)가 코드를 구현했음. Claude가 자신의 코드를 먼저 리뷰하면 "원래 이렇게 설계했으므로 맞다"는 합리화가 발생 → 맹점 방치. 따라서 **프로젝트 컨텍스트 없는 신선한 눈(3개 외부 모델)이 먼저 봄**. Claude는 외부 모델 결과를 받아본 후 C-Step 3에서 informed하게 심층 리뷰.
>
> **인라인 실행** (AskUserQuestion 없음, TeamCreate 없음 — leviathan 자동 흐름 보장)

**1단계: 3개 외부 모델 병렬 코드 리뷰 (Claude 미포함 = 의도적)**

프롬프트 (`CODE_REVIEW_PROMPT`):
> "이 프로젝트의 Phase X에서 변경된 파일들을 감사하라. `git diff main...HEAD`로 변경사항을 확인하고, 다음을 검증: 로직 오류, 누락된 엣지케이스, 수학적 정확성, 통합 연결(wiring). 이슈별로 CRITICAL/HIGH/MEDIUM 심각도를 부여하라."

```
Agent(name="codex-code-reviewer",
      prompt="Bash로 timeout 300 codex exec -s read-only '$CODE_REVIEW_PROMPT' 실행. 실패 시 최대 3회 재시도. 전원 실패 시 L5 에스컬레이션.")
Agent(name="gemini-code-reviewer",
      prompt="Bash로 timeout 300 gemini -p '$CODE_REVIEW_PROMPT' --approval-mode plan 실행. 실패 시 최대 3회 재시도. 전원 실패 시 L5 에스컬레이션.")
Agent(name="qwen-code-reviewer",
      prompt="Bash로 timeout 300 qwen --approval-mode plan -p '$CODE_REVIEW_PROMPT' 실행. 실패 시 최대 3회 재시도. 전원 실패 시 L5 에스컬레이션.")
```

**2단계: Quorum 합의 (3개 모델만 투표)**
- 3개 리뷰 결과 수집 → **quorum**: 2개 이상 모델이 지적한 이슈 = **MUST FIX**
- 결과를 `.omc/artifacts/consensus-code-{phase}.md`에 저장

**3단계: 보안 스캔 (전략/API 변경 시에만)**
- Agent(subagent_type="oh-my-claudecode:security-reviewer") 직접 수행 (외부 CLI 불필요)

**4단계: 결과 주입**
- quorum 합의 결과를 Jennie(C-Step 3) 코드리뷰 컨텍스트에 주입
- "멀티모델 감사에서 N개 모델이 지적한 MUST FIX 이슈: [목록]" 형태로 전달

> MUST FIX 이슈 0건 → C-Step 3 코드리뷰 진행.
> MUST FIX 이슈 1건 이상 → Stage B-Step 1 fix 루프 복귀.
>
> **Claude의 역할은?** C-Step 3(Jennie/코드리뷰)과 C-Step 5(Karina/최종 판단)에서 외부 모델 결과를 참고하여 정보를 갖춘 심층 리뷰 수행. C-Step 2에서 제외된 것은 **기계적 결함이 아니라 편향 제거의 의도적 설계**.

#### C-Step 3: 코드리뷰 + 보안리뷰 (병렬 Sub-agents)

```
Agent(subagent_type="oh-my-claudecode:code-reviewer", name="jennie", model="opus",
      prompt="Phase X 변경 코드 종합 리뷰. API 계약, 하위호환, 아키텍처 준수, 설계 비판(PLAN.md 대비).
              **품질 체크리스트**: SOLID 원칙 위반, 안티패턴, 에러 핸들링 일관성, 성능 병목, 유지보수성.

              **⚡ 통합 추적 검증 (CRITICAL 우선순위 — 반드시 수행):**
              이번 diff에서 새로 생성된 모든 클래스/인스턴스에 대해:
              1. 생성 위치: 어디서 __init__() 호출? (file:line)
              2. 주입 위치: 어디서 소비자에게 전달? (file:line)
              3. 호출 위치: 어디서 실제 메서드 호출? (file:line)
              → 하나라도 없으면 CRITICAL: Dead Wiring으로 분류.
              → Assembly Verifier 결과와 교차 대조하여 누락 확인.

              **Shadow 결과 참조**: Shadow 복합지표(13항목)와 코드 로직의 정합성 교차 평가.
              특히: trade=0 전략 존재 시 해당 전략 코드 경로 집중 분석.
              **Entry Gate 컨텍스트**: [Karina Entry Gate 결과 ~3K 토큰 주입]
              docs/review/Phase-X_REVIEW.md 작성
              **반환 3K 토큰 이내 요약. 상세는 docs/review/Phase-X_REVIEW.md에 기록.**")

Agent(subagent_type="oh-my-claudecode:security-reviewer", name="jisoo",
      prompt="Phase X 보안 리뷰. JWT/API키/거래실행 보안, OWASP Top 10, 시크릿 노출
              **반환 3K 토큰 이내 요약.**")
```

**조건부 추가 에이전트 (C-Step 3과 병렬):**

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

> 리뷰 결과 수집 → CRITICAL/HIGH 이슈 발견 시 Stage B-Step 1 fix 루프 복귀.
> 결과 수신 즉시 C-Step 4 tool call 발행. 상태 요약/보고 금지.

#### C-Step 4: Phase 완료 멀티모델 토론 (Go/No-Go 판단 보조)

> **목적**: Karina의 Go/No-Go 판단 전에 "이 Phase가 상용급인가?"를 3개 외부 모델이 적대적으로 평가.
> Karina가 Claude이므로 자기 코드에 관대할 수 있음 → 외부 모델의 냉혹한 비판을 사전 주입.
> **인라인 실행** (AskUserQuestion 없음 — leviathan 자동 흐름 보장)

프롬프트 (`GONOGO_PROMPT`):
> "이 Phase의 상용 준비 상태를 냉혹하게 평가하라. 기준: 1) PLAN.md 대비 구현 완성도 2) Shadow 13항목 복합지표 3) CRITICAL/HIGH 해결 여부 4) 통합 연결(wiring) 완전성. Go 또는 No-Go 판정과 근거를 제시하라. 관대하게 평가하지 말 것."

```
Agent(name="codex-gonogo",
      prompt="Bash로 timeout 300 codex exec -s read-only '$GONOGO_PROMPT' 실행. 실패 시 최대 3회 재시도. 전원 실패 시 L5 에스컬레이션.")
Agent(name="gemini-gonogo",
      prompt="Bash로 timeout 300 gemini -p '$GONOGO_PROMPT' --approval-mode plan 실행. 실패 시 최대 3회 재시도. 전원 실패 시 L5 에스컬레이션.")
Agent(name="qwen-gonogo",
      prompt="Bash로 timeout 300 qwen --approval-mode plan -p '$GONOGO_PROMPT' 실행. 실패 시 최대 3회 재시도. 전원 실패 시 L5 에스컬레이션.")
```

- 3개 결과 수집 → 과반수(2+) Go → Go 판정, 아니면 No-Go
- 결과를 `.omc/artifacts/consensus-gonogo-{phase}.md`에 저장
- Karina(C-Step 5) Go/No-Go 컨텍스트에 주입: "외부 모델 Go/No-Go = [판정], 근거: [목록]"

> 결과 수집 즉시 C-Step 5 tool call 발행.

#### C-Step 5: Phase 완료 최종 리뷰 — Karina (PM + Architect)

**이것이 Phase 최종 리뷰. 모든 산출물을 종합하여 Go/No-Go 결정.**

```
Agent(subagent_type="oh-my-claudecode:architect", name="karina", model="opus",
      prompt="Phase 완료 리뷰 (PM+Architect):
              1. 계획 이행: PLAN.md 대비 실제 구현 — 빠진 항목 없는가?
              2. 리뷰 해결: REVIEW.md CRITICAL/HIGH — 전부 해결?
              3. 런타임 검증 (복합지표): Shadow 13항목 전부 PASS?
                 - 절대 지표 확인: MDD<5%, Profit Factor>1.0, 신호>=100/day
                 - 통합 확인: 활성 전략 전부 trade>=1, 방어 레이어 활성
                 - 단순 PnL>0만으로 판단 금지 (시드 대비 상대적 지표)
              4. 조립 검증: Assembly Verifier 4-check 전부 PASS?
                 - Init Chain, Signal Flow, Dead Wiring, Config Audit
              5. 수용기준: prd.json acceptanceCriteria — 전부 충족?
                 - ⚡ WIRING AC 있는 US: 생성→주입→호출 3가지 증거 확인
              6. 3-way 정합성: SSOT.md/prd.json/CLAUDE.md 숫자 일치?
              7. Go/No-Go:
                 - PASS → Sakura에게 SSOT+git 지시
                 - FAIL → 복귀 대상 + 실패 유형 명시:
                   • Dead Wiring(Type W) → Stage A (재기획, L2 에스컬레이션)
                   • 파라미터(Type P) → Stage B-Step 2 (Shadow 재실행)
                   • 코드 결함 → Stage B-Step 1 (개발 fix)
                   • 기획 결함 → Stage A (재기획)
              **반환 3K 토큰 이내 요약.**")
```

> **Agent Teams 불필요**: Karina가 PLAN.md + REVIEW.md + Shadow 결과 + prd.json을 직접 읽으면,
> Jennie/Minji를 소환해서 "회의"하는 것과 정보 품질이 동일. 비용만 3-7x 차이.
> 결과 수신 즉시 C-Step 6 tool call 발행. 상태 요약/보고 금지.

#### C-Step 6: SSOT + Git — Sakura (Go 시에만)

```
Agent(subagent_type="ssot-keeper", name="sakura", model="sonnet",
      prompt="Phase 완료 반영 — 아래 6가지 전부 수행:
              0) **passes:true 전 런타임 증거 확인 (거짓 양성 방지)**:
                 - 각 US의 acceptanceCriteria 기능이 Shadow 10min 로그에서 1회 이상 호출된 증거 확인
                 - 증거 없으면 = dead code → passes:false 유지 (코드 존재만으로 완료 판정 금지)
                 - 확인 방법: grep Shadow 로그 또는 Prometheus 메트릭
              1) prd.json: 런타임 증거 확인된 US만 passes:true 마킹
              2) SSOT.md §2: Phase, 테스트 수, 완료 US 카운트, 다음 작업 업데이트
              2-b) **이월 항목 SSOT.md §7 동기화 (필수)**:
                 - passes:false로 남은 US가 있으면 SSOT.md §7 다음 Phase 섹션에 '이월 항목' 블록 추가
                 - 형식: `- [ ] US-XXX: [제목] (← S[N] 이월, [사유 한줄])`
                 - 이월 사유: 구현 미완, 런타임 증거 미확보, 의존성 미충족 중 택1
                 - SSOT.md §2 '미구현 이월' 라인도 동기화
                 - **사장님이 SSOT.md로 프로젝트 상태를 확인하므로 이월 항목 누락 = 정보 비대칭 = FAIL**
              3) SSOT.md §7 헤더: 'N개 User Stories, M개 완료, K개 미완' 숫자를 prd.json 실제 카운트와 동기화
              4) CLAUDE.md '현재 상태' 섹션: PRD 카운트, 테스트 수, 다음 작업을 SSOT.md §2와 동기화
              5) **검증**: Grep으로 prd.json passes:true/false 카운트 → 3곳(SSOT §2, §7, CLAUDE.md) 숫자 대조. 불일치 시 수정.
              6) **Git**: git add + git commit -m 'Phase X: [US 목록] 완료' + git push origin main. push 누락 = FAIL.
              **반환 3K 토큰 이내 요약.**")
```
> 결과 수신 즉시 다음 블록 tool call 발행. 상태 요약/보고 금지.

- **git commit만 하고 push 안 하는 것 = 미완료**. `git push origin main` 필수.

### 자동 일관성 재검증 + 체크포인트
- 실행: `cd engine && python -m src.workflow.cli --root $(git rev-parse --show-toplevel) check_all`
- 모든 검사 OK 확인 후 git push 진행
- 실행: `cd engine && python -m src.workflow.cli --root $(git rev-parse --show-toplevel) checkpoint save --trigger "phase_complete"`

**활성 팀**: BLACKPINK(코드리뷰, Step 1) + LE SSERAFIM(Phase 완료 리뷰+SSOT, Step 2-3) + ITZY(퀀트, 해당 시)

**산출물**: `docs/review/Phase-X_REVIEW.md`

**C→다음Phase 전환:** Karina Go + Sakura SSOT+git push 즉시 → `state_write(next_stage:"A", next_phase:"Phase-Y")` && 다음 Phase Stage A 시작. 중간 상태 보고 금지.

---

## 3. 팀 구조 (7팀 + TF)

> 팀은 **기능별로 정의**, Stage가 **필요한 팀을 호출**하는 구조.
> Stage B-Step 1만 TeamCreate 사용 (개발자 간 협업 필요), 나머지는 Agent() 서브에이전트 (독립 작업).

### ① 기획팀 [AESPA] — Stage A 활성화 (4명)

| 팀원 | 에이전트 (subagent_type) | 모델 | 역할 |
|------|------------------------|------|------|
| **Karina** | `oh-my-claudecode:architect` | opus | Entry Gate: SSOT/prd.json 정합성, 시스템 설계, 코드 탐색 |
| **NingNing** | `oh-my-claudecode:analyst` | sonnet | 요구사항 분석, acceptanceCriteria 검증, 엣지케이스 도출 |
| **Winter** | `oh-my-claudecode:critic` | opus | 기획 비판: 누락 엣지케이스, 과도한 복잡성, 설계 결함 지적 |
| **Giselle** | `oh-my-claudecode:planner` | sonnet | 태스크 분해, 실행 순서, PLAN.md 작성 |

사전 단계: `oh-my-claudecode:explore` (haiku) — 코드베이스 탐색 후 architect에게 컨텍스트 제공

### ② 개발팀 [IVE] — Stage B-Step 1 활성화 (최대 6명, TeamCreate)

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

### ④ 테스트팀 [NewJeans] — Stage B-Step 2 활성화 (최대 5명)

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
- Stage B-Step 1만 TeamCreate (협업 필요), 나머지 Agent() (독립 작업)
- 병렬 필요 시 같은 역할 추가 스폰 가능 (IVE 최대 6명)
- 모델 라우팅: opus=아키텍처/심층분석, sonnet=구현/표준, haiku=탐색/단순

---

## 4. 완료 기준

| 항목 | 조건 | Stage |
|------|------|-------|
| Entry Gate | SSOT/prd.json/CLAUDE.md 정합성 PASS | A |
| PLAN.md | `docs/planning/Phase-X_PLAN.md` 존재 | A |
| WIRING AC | 새 컴포넌트 US에 `⚡ WIRING:` AC 3개 포함 (생성→주입→호출) | A |
| QUANT GATE | 전략/수식 US 시 PASS | A |
| pytest | 0 failures | B (Step 1) |
| Shadow 13항목 | 복합지표 전부 PASS (§B-Step 2 표 참조) | B (Step 2) |
| Docker | 전 컨테이너 healthy | B (Step 2) |
| **Assembly Gate** | **init chain non-None + signal flow E2E + dead wiring 0건** | **C (Step 1)** |
| 코드리뷰 | CRITICAL/HIGH 0건 + **통합 추적 검증** | C (Step 1) |
| 보안리뷰 | CRITICAL 0건 | C (Step 1) |
| REVIEW.md | `docs/review/Phase-X_REVIEW.md` 존재 | C (Step 1) |
| Phase 완료 리뷰 | Karina Go/No-Go = PASS (**통합 검증 포함**) | C (Step 2) |
| SSOT.md | 해당 섹션 업데이트됨 | C (Step 3) |
| prd.json | `passes: true` | C (Step 3) |
| Git | `git add` + `git commit` + `git push origin main` 일괄 완료 | C (Step 3) |
| **Phase D/H 추가** | Chrome 렌더링 + API 200 + WebSocket + 모바일 반응형 | B+C |

> `npm run build` 성공만으로 Phase D/H 완료 선언 금지. Chrome 실제 렌더링 필수.
> Shadow 10분 미만 실행으로 완료 선언 금지. 실제 10분+ 무중단 필수.
> **Assembly Gate 없이 코드리뷰 진행 금지.** 조립 검증이 코드 품질 리뷰보다 먼저.
> **WIRING AC 없는 새 컴포넌트 US는 Stage A에서 차단.** 기획 시점에 통합 기준 수립 필수.

### 4.1 prd.json Acceptance Criteria 작성 규칙 (WIRING 필수)

> 새 클래스/모듈을 생성하는 모든 US에는 반드시 아래 3개 `⚡ WIRING:` AC를 포함할 것.
> 이 규칙이 없어서 "코드는 있지만 연결 안 됨" 문제가 Phase S2~S9에서 반복 발생.

```
⚡ WIRING 필수 AC 3개:
1. "⚡ WIRING: [파일]에서 [클래스] 인스턴스 생성 확인"
2. "⚡ WIRING: [소비자]에 주입/등록 확인 (예: main.py → RiskGuardian.register())"
3. "⚡ WIRING: Shadow 10min 중 [컴포넌트] 이벤트/호출 >= 1건 (런타임 활성 증거)"
```

예시:
```json
{
  "id": "US-222",
  "acceptanceCriteria": [
    "StrategyManager에 per-strategy loss counter 추가",
    "연속 N건 손실 시 해당 전략 300s 쿨다운",
    "⚡ WIRING: main.py에서 PerStrategyCB 인스턴스 생성 확인",
    "⚡ WIRING: RiskGuardian.register(per_strategy_cb) 호출 확인",
    "⚡ WIRING: Shadow 10min 중 per_strategy_cb 이벤트 >= 1건"
  ]
}
```

## 4.5 에스컬레이션

| Level | 조건 | 대응 |
|-------|------|------|
| L0 | 단순 버그 | 팀 내 즉시 수정 |
| L1 | fix 루프 3회 | 기존 방식 |
| L2 | 3회 초과/구조적 문제 | Stage A 복귀 → 새 PLAN.md |
| L3 | SSOT↔PRD↔코드 모순 | SSOT→PRD 수정 → Stage A 재기획 |
| L4 | Phase 범위 초과 | 새 US → prd.json 추가 |
| **L5** | **동일 Phase 3회 이상 실패** | **텔레그램 알림 → 자동 일시정지 후 재시도** |

L0~L4 자동 처리. L5 텔레그램 알림 후 1회 추가 재시도, 재실패 시 자동 일시정지.

### 4.6 추가 안전장치 (워크플로우 V2 보강)

> 효율성 분석 + 외부 리서치(MAST NeurIPS 2025, Google ADK, VeriMAP) 기반 보강.

**R2. 전략 간 상관관계 체크 (Shadow 14번째 기준):**
- Shadow 13항목 PASS 후 추가 검증: 활성 전략 쌍의 PnL 시계열 상관계수 |r| > 0.7이면 WARNING
- 근거: TF SF 2차 FAIL 근본 원인 — stat_arb와 cross_exchange가 동일 신호 중복 소비
- WARNING 시 전략 간 overlap 분석 → 필요 시 disabled_strategies 조정

**R3. Fix Loop W/P/B 분류는 debugger가 수행:**
- executor(개발자)가 자기 실패를 분류하면 이해충돌 (Type P로 과소 분류 가능)
- Shadow FAIL 시 Hyein(debugger)이 원인 분석 + 유형 분류 → Lead가 에스컬레이션 결정
- 근거: "Team of Rivals" 논문 (2026) — 생산자와 검증자 분리

**R4. 새 Gate에 체크포인트 쓰기:**
- Assembly Gate PASS 후: `checkpoint save --trigger "assembly_gate_pass"`
- Shadow 13항목 PASS 후: `checkpoint save --trigger "shadow_13item_pass"`
- 세션 크래시 시 체크포인트에서 재개 가능 (Gate 재실행 방지)

**R5. 텔레그램 알림 (정보 전달용):**
- Phase 완료 시 텔레그램 알림 전송 (정보 전달 목적, 승인 대기 없음)
- 알림 전송 후 즉시 다음 Phase 자동 진행

**R6. Assembly Gate 조건부 실행:**
- `git diff --name-only`에 `class ` 정의 또는 `__init__` 변경이 포함된 경우에만 실행
- 파라미터/문서/설정 변경만 있는 Phase에서는 스킵 (불필요 오버헤드 제거)
- 스킵 시에도 Shadow 13항목은 반드시 실행 (스킵 불가)

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
| Stage B-Step 1 완료 | pytest PASS + TeamDelete → 즉시 Phase 2 Shadow |
| Stage B-Step 2 완료 | Shadow PASS + checkpoint → 즉시 Stage C |
| Stage C Step 1 완료 | 코드리뷰 PASS → 즉시 Step 2 Karina |
| Stage C Step 2 완료 | Go → 즉시 Step 3 Sakura |
| Stage C Step 3 완료 | SSOT/git push + 텔레그램 → **즉시 다음 Phase Stage A** |

**체크포인트**: `.omc/state/leviathan-progress.json` — 세션 복구 전용.
세션 크래시/수동 `/clear` 시 → `/leviathan` 재호출 → progress 파일로 재개.

**컨텍스트 60% 이상 시:**
1. WORKFLOW_TELEGRAM `send_context_warning()` → 60% 도달 알림 전송
2. 현재 진행 중인 Stage 완료까지 마무리
3. `/clear` 시도
4. 성공 시 → `send_context_clear_success()` 알림 → progress.json으로 자동 재개
5. 실패 시 → `send_context_alert()` 알림 → 사장님 수동 개입 필요
6. **`/compact` 절대 금지** — 결과 소실 위험

---

## 7. TF (Task Force) — Quarter-Final / Semi-Final / Final

> **진입 가드**: Phase G/H/I/J-EXT/K/L/M 전부 `passes:true` 필수. 하나라도 미완료 시 TF 소집 금지.
> TF는 기존 팀원을 TF 전용 역할로 재소집(리스폰). 개발 세션과 **완전 분리** (fresh context).

### TF 핵심 원칙: 회귀 구조

> **TF는 검사 프로세스이지, 새 Phase를 만드는 단계가 아니다.**
> TF FAIL 시 → **회귀 Phase 생성** (원본 Phase의 미비점 보완).
> 회귀 Phase는 SSOT.md에서 **TF 섹션 위, 원본 Phase 다음**에 위치한다.
> 각 회귀 US에는 `(← 원본 Phase US-XXX 사유)` 역추적 주석을 붙인다.

### TF 4-Round 체계 (상세 → `.claude/commands/leviathan-tf.md`)

```
┌─────────────────────────────────────────────────────────────┐
│  TF Quarter-Final (QF) — Development Verification           │
│  "코드가 올바른가?" — 정합성, 체크리스트, 교차검증            │
├─────────────────────────────────────────────────────────────┤
│  TF Semi-Final (SF) — System Validation                     │
│  "24시간 돈을 벌 수 있나?" — 24H Progressive Shadow          │
├─────────────────────────────────────────────────────────────┤
│  TF Pre-Final (PF) — Regression Guard                       │
│  "코드 변경 없이 안정적인가?" — git baseline 비교             │
├─────────────────────────────────────────────────────────────┤
│  TF Final (F) — Operations Readiness                        │
│  "문제 생기면 대응할 수 있나?" — DR, Canary 7일              │
└─────────────────────────────────────────────────────────────┘
```

> **상세 절차**: `.claude/commands/leviathan-tf.md` (394줄) 참조. 아래는 팀 로스터 + 개요만.

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

### 회귀 구조 (4-Round 공통)

```
QF FAIL → 회귀 Phase 생성 → 3-Stage(A→B→C) → QF 재검증
SF FAIL → 회귀 Phase 생성 → 3-Stage(A→B→C) → SF 재검증 (QF 스킵, 구조적 결함 시 QF부터)
PF FAIL → git rollback → PF 재시도 (최대 2회) → 2회 실패 시 PF 스킵 → Final 직행
Final FAIL → 항목별 수정 → 코드 변경 시 SF부터, 구조 변경 시 PF부터
```

> **QF/SF/PF/Final 상세 절차**: `.claude/commands/leviathan-tf.md` 참조.

---

## 8. 텔레그램 3-Bot 체계

> 상세 → CLAUDE.md "텔레그램 3-Bot" 섹션 참조.

| 봇 | 환경변수 | 용도 |
|----|---------|------|
| TradeBot | `TRADE_TELEGRAM_BOT_TOKEN` | 거래 알림 + Kill Switch + 포지션 제어 (20cmd) |
| DevBot | `DEV_TELEGRAM_BOT_TOKEN` | 워크플로우 알림 + Watchdog `/go` 수동 재개 (16cmd) |
| InfraBot | `INFRA_TELEGRAM_BOT_TOKEN` | 인프라 모니터링 /health /resources (7cmd) |

**워크플로우 알림 (DevBot 경유):**
- L5 에스컬레이션: 동일 Phase 3회 실패 → 사장님 판단 요청
- 컨텍스트 60%: `/clear` 예고 → 성공/실패 알림
- Phase 완료: 정보 전달 (승인 대기 없음, 즉시 다음 Phase)
- **DevBot = Watchdog**: tmux 멈춤 감지 → 알림 → 자동 재개

---

## 9. 시작 및 자동 루프

재개 지점 결정 순서:

**1순위 — `.omc/state/leviathan-progress.json`**:
- 존재 시 `next_stage`에 따라 해당 Stage부터 재개
- `plan_file` 있으면 PLAN.md 읽어 복원
- Stage B-Step 2 재개 시 progress.json의 메타데이터로 복원

**2순위 — $ARGUMENTS**: 인수가 있으면 해당 US부터 Stage A

**3순위 — prd.json 스캔**: `passes:false`인 첫 번째 US의 Phase → Stage A

> 모든 US `passes:true`까지 자동 루프. Phase 간 자동 진행 (승인 대기 없음).
> 멈추는 조건: 전 US 완료 OR L5 에스컬레이션 OR 사용자 "stop/cancel/멈춰".

---

## 99. CONTINUATION ANCHOR (압축 후에도 유지 — 이 섹션은 반드시 파일 마지막에 위치)

> 컨텍스트 압축 시 앞부분이 먼저 삭제됨. 이 섹션은 마지막에 있으므로 압축 후에도 살아남음.

**§0 반복 — 압축 후에도 유지:**
- 결과 수신 → 즉시 다음 tool call. 텍스트만 = **BUG**. 요약만 = **BUG**.
- 순서: **A→B(TeamCreate)→B-2(Shadow)→C(Assembly→멀티모델→코드리뷰→Go/No-Go→SSOT+push)→다음A**
- 에이전트 반환: **파일 기반** (`.omc/artifacts/`). 반환 PASS/FAIL+경로만.
- 모든 Agent **foreground**. 외부 CLI **foreground 병렬 + timeout 300 + 3회 재시도 → L5**.
- **모든 응답에 텍스트 1줄 + tool call** (#30625). "다음 세션에서" **금지** (#34238).
- **TeamCreate 30초 무응답 → TeamDelete → Agent() fallback** (#33043).
- **Watchdog**: Dev봇 독립 프로세스. tmux 멈춤 → 알림 → `/go` 재개.
