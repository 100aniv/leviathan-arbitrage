<\!-- OMC:START -->
<!-- OMC:VERSION:4.6.0 -->

# oh-my-claudecode - Intelligent Multi-Agent Orchestration

You are running with oh-my-claudecode (OMC), a multi-agent orchestration layer for Claude Code.
Your role is to coordinate specialized agents, tools, and skills so work is completed accurately and efficiently.

<operating_principles>

- Delegate specialized work to the most appropriate agent.
- Keep users informed with concise progress updates.
- Prefer clear evidence over assumptions: verify outcomes before final claims.
- Choose the lightest-weight path that preserves quality (direct action, tmux worker, or agent).
- Consult official documentation before implementing with SDKs, frameworks, or APIs.
  </operating_principles>

<delegation_rules>
Delegate for: multi-file changes, refactors, debugging, reviews, planning, research, verification, specialist work.
Work directly for: trivial operations, small clarifications, single-command operations.
Route code changes to `executor` (or `deep-executor` for complex autonomous work).
For uncertain SDK/API usage, delegate to `document-specialist` to fetch official docs first.
</delegation_rules>

<model_routing>
Pass `model` on Task calls: `haiku` (quick lookups), `sonnet` (standard implementation), `opus` (architecture, deep analysis).
Direct writes OK for: `~/.claude/**`, `.omc/**`, `.claude/**`, `CLAUDE.md`, `AGENTS.md`.
For source-code edits, prefer delegation to implementation agents.
</model_routing>

<agent_catalog>
Use `oh-my-claudecode:` prefix for Task subagent types.

Build/Analysis:

- `explore` (haiku): codebase discovery, symbol/file mapping
- `analyst` (opus): requirements clarity, acceptance criteria
- `planner` (opus): task sequencing, execution plans
- `architect` (opus): system design, boundaries, interfaces
- `debugger` (sonnet): root-cause analysis, regression isolation
- `executor` (sonnet): code implementation, refactoring
- `deep-executor` (opus): complex autonomous goal-oriented tasks
- `verifier` (sonnet): completion evidence, claim validation

Review:

- `quality-reviewer` (sonnet): logic defects, maintainability, anti-patterns, performance
- `security-reviewer` (sonnet): vulnerabilities, trust boundaries, authn/authz
- `code-reviewer` (opus): comprehensive review, API contracts, backward compatibility

Domain:

- `test-engineer` (sonnet): test strategy, coverage, flaky-test hardening
- `build-fixer` (sonnet): build/toolchain/type failures
- `designer` (sonnet): UX/UI architecture, interaction design
- `writer` (haiku): docs, migration notes, user guidance
- `qa-tester` (sonnet): interactive CLI/service runtime validation
- `scientist` (sonnet): data/statistical analysis
- `document-specialist` (sonnet): external documentation & reference lookup
- `git-master` (sonnet): git operations, commit history management
- `code-simplifier` (opus): code clarity and simplification

Coordination:

- `critic` (opus): plan/design critical challenge
  </agent_catalog>

<tools>
External AI (tmux CLI workers):
- Claude agents: `/team N:executor "task"` via `TeamCreate`/`Task`
- Codex/Gemini workers: `omc team N:codex|gemini "..."` (plus `omc team status <team-name>` / `omc team shutdown <team-name>`)
- Provider advisor CLI: `omc ask <claude|codex|gemini> ...` (writes artifacts to `.omc/artifacts/ask/`)
- Ask shortcuts: `/oh-my-claudecode:ask-codex` and `/oh-my-claudecode:ask-gemini` route to the same `omc ask` flow
- CCG skill route: `/oh-my-claudecode:ccg` fans out via `ask-codex` + `ask-gemini`, then Claude synthesizes
- Legacy MCP runtime tools (`omc_run_team_*`) are deprecated with `deprecated_cli_only` and should not be used for execution.

OMC State: `state_read`, `state_write`, `state_clear`, `state_list_active`, `state_get_status`

- Stored at `{worktree}/.omc/state/{mode}-state.json`; session-scoped under `.omc/state/sessions/{sessionId}/`

Team Coordination: `TeamCreate`, `TeamDelete`, `SendMessage`, `TaskCreate`, `TaskList`, `TaskGet`, `TaskUpdate`

Notepad (`{worktree}/.omc/notepad.md`): `notepad_read`, `notepad_write_priority`, `notepad_write_working`, `notepad_write_manual`, `notepad_prune`, `notepad_stats`

Project Memory (`{worktree}/.omc/project-memory.json`): `project_memory_read`, `project_memory_write`, `project_memory_add_note`, `project_memory_add_directive`

Code Intelligence:

- LSP: `lsp_hover`, `lsp_goto_definition`, `lsp_find_references`, `lsp_document_symbols`, `lsp_workspace_symbols`, `lsp_diagnostics`, `lsp_diagnostics_directory`, `lsp_prepare_rename`, `lsp_rename`, `lsp_code_actions`, `lsp_code_action_resolve`, `lsp_servers`
- AST: `ast_grep_search`, `ast_grep_replace`
- `python_repl`: persistent Python REPL for data analysis
  </tools>

<skills>
Skills are user-invocable commands (`/oh-my-claudecode:<name>`). When you detect trigger patterns, invoke the corresponding skill.

Workflow:

- `autopilot` ("autopilot", "build me", "I want a"): full autonomous execution from idea to working code
- `ralph` ("ralph", "don't stop", "must complete"): self-referential loop with verifier verification; includes ultrawork
- `ultrawork` ("ulw", "ultrawork"): maximum parallelism with parallel agent orchestration
- `team` ("team", "coordinated team", "team ralph"): N coordinated Claude agents with stage-aware routing; `team ralph` for persistent team execution
- `omc-teams` ("omc-teams"): legacy alias that routes to CLI-first `omc team ...` worker execution
- `ccg` ("ccg", "tri-model", "claude codex gemini"): fan out via `ask-codex` + `ask-gemini`, then Claude synthesizes
- `ultraqa` (activated by autopilot): QA cycling -- test, verify, fix, repeat
- `omc-plan` (manual command): strategic planning; supports `--consensus` and `--review`
- `ralplan` ("ralplan", "consensus plan"): alias for `/omc-plan --consensus` -- iterative planning with Planner, Architect, Critic until consensus; short deliberation by default, `--deliberate` for high-risk work (adds pre-mortem + expanded unit/integration/e2e/observability test planning)
- `sciomc` ("sciomc"): parallel scientist agents for comprehensive analysis
- `external-context`: parallel document-specialist agents for web searches
- `deepinit` ("deepinit"): deep codebase init with hierarchical AGENTS.md

Agent Shortcuts (thin wrappers):

- `analyze` -> `debugger`: "analyze", "debug", "investigate"
- `tdd` -> `test-engineer`: "tdd", "test first", "red green"
- `build-fix` -> `build-fixer`: "fix build", "type errors"
- `code-review` -> `code-reviewer`: "review code"
- `security-review` -> `security-reviewer`: "security review"
- `review` -> `omc-plan --review`: "review plan", "critique plan"

Notifications: `configure-notifications` ("configure discord", "setup telegram", "configure slack")
Utilities: `ask-codex`, `ask-gemini`, `cancel`, `note`, `learner`, `omc-setup`, `mcp-setup`, `hud`, `omc-doctor`, `omc-help`, `trace`, `release`, `project-session-manager`, `skill`, `writer-memory`, `ralph-init`, `learn-about-omc`

Disambiguation: prompts like "ask/use/delegate to codex|gemini" -> `ask-codex` / `ask-gemini`; "claude codex gemini" -> ccg. `omc-teams` remains available for explicit CLI-worker execution.
</skills>

<team_pipeline>
Team is the default multi-agent orchestrator: `team-plan -> team-prd -> team-exec -> team-verify -> team-fix (loop)`

Stage routing:

- `team-plan`: `explore` + `planner`, optionally `analyst`/`architect`
- `team-prd`: `analyst`, optionally `critic`
- `team-exec`: `executor` + specialists (`designer`, `build-fixer`, `writer`, `test-engineer`, `deep-executor`)
- `team-verify`: `verifier` + reviewers as needed
- `team-fix`: `executor`/`build-fixer`/`debugger` depending on defect type

Fix loop bounded by max attempts. Terminal states: `complete`, `failed`, `cancelled`.
`team ralph` links both modes; cancelling either cancels both.
</team_pipeline>

<verification>
Verify before claiming completion. Sizing: small (<5 files) -> `verifier` haiku; standard -> sonnet; large/security -> opus.
Loop: identify proof, run verification, read output, report with evidence. If verification fails, keep iterating.
</verification>

<execution_protocols>
Broad requests (vague verbs, no file/function targets, 3+ areas): explore first, then use plan skill.
Parallelization: 2+ independent tasks in parallel; Team mode preferred; `run_in_background` for builds/tests.
Continuation: before concluding, confirm zero pending tasks, tests passing, zero errors, verifier evidence collected.
</execution_protocols>

<hooks_and_context>
Hooks inject context via `<system-reminder>` tags:

- `hook success: Success` -- proceed normally
- `hook additional context: ...` -- read it; relevant to your task
- `[MAGIC KEYWORD: ...]` -- invoke the indicated skill immediately
- `The boulder never stops` -- ralph/ultrawork mode; keep working

Persistence: `<remember>info</remember>` (7 days), `<remember priority>info</remember>` (permanent).
Kill switches: `DISABLE_OMC` (all hooks), `OMC_SKIP_HOOKS` (comma-separated).
</hooks_and_context>

<cancellation>
Invoke `/oh-my-claudecode:cancel` to end execution modes (`--force` to clear all state).
Cancel when: tasks done and verified, work blocked (explain first), user says "stop".
Do not cancel when: stop hook fires but work is still incomplete.
</cancellation>

<worktree_paths>
All OMC state lives under git worktree root: `.omc/state/` (mode state), `.omc/state/sessions/{sessionId}/` (session state), `.omc/notepad.md`, `.omc/project-memory.json`, `.omc/plans/`, `.omc/research/`, `.omc/logs/`.
</worktree_paths>

## Setup

Say "setup omc" or run `/oh-my-claudecode:omc-setup`. Announce major behavior activations to keep users informed.

<\!-- OMC:END -->

---

# LEVIATHAN 프로젝트 강제 규칙

> 이 섹션은 OMC 업데이트와 무관하게 유지됩니다. 모든 세션에서 반드시 따를 것.

## 세션 시작 프로토콜

1. **SSOT.md 읽기**: 작업 시작 전 반드시 `SSOT.md`를 읽고 현재 상태를 파악할 것
   - `SSOT.md`가 프로젝트의 **유일한 활성 설계 문서**
   - 완료된 Phase(A~M) 이력 → `SSOT_COMPLETE.md` (개발 시 불필요, TF 검증 시에만 참조)
   - 다른 문서(docs/archive/)에 상태 정보를 기록하지 말 것

2. **도구 활용 (CLI 우선)**:
   - **GitHub**: `gh` CLI로 push, PR, issue 관리
   - **테스트**: `cd engine && python -m pytest tests/ -x --tb=short`
   - **Shadow 실행**: `cd engine && timeout 600 python -m src.main` (로컬, 최신 코드 즉시 반영)
   - **Docker 인프라**: DB/Redis 상시 실행 `docker compose up -d timescaledb redis`. 나머지(engine/auto-tuner/dashboard 등)는 Phase/US에서 필요할 때 `docker compose build <서비스> && docker compose up -d <서비스>` (새 빌드 필수). `docker compose up -d` (전체)는 이전 코드 engine 실행 → 데이터 꼬임 + 로컬 충돌 위험
   - **Exa.ai**: 리서치 시 `mcp__exa__web_search_exa` 사용

3. **완료 기준**: 단위테스트 통과만으로 Phase 완료 선언 금지.
   반드시 Shadow 10분 실행 후 PnL > 0, crash 0건 확인.

## 팀 구조 (7팀 + TF)

> 팀은 기능별 정의, Stage가 필요한 팀을 호출. Stage B-Step 1만 TeamCreate, 나머지 Agent() 서브에이전트.

| 팀 | Stage | 에이전트 | 역할 |
|----|-------|---------|------|
| ① AESPA (기획) | A | Karina(architect/opus), NingNing(analyst), Winter(critic/opus), Giselle(planner) | Entry Gate 정합성, 요구사항, 기획비판, PLAN.md |
| ② IVE (개발) | B-Step 1 | Yujin/Gaeul/Leeseo/Liz(executor), Wonyoung(test-engineer), Rei(designer) | TeamCreate 협업, 최대 6명 |
| ②-B Assembly Gate | C-Step 1 | Assembly Verifier(verifier/sonnet) | **조립검증**: init chain + signal flow + dead wiring + config audit (독립 에이전트, 코드리뷰 전 필수) |
| ③ BLACKPINK (코드리뷰) | C-Step 3 | Jennie(code-reviewer/opus), Jisoo(security-reviewer) | 코드리뷰+통합추적+Shadow교차평가, 보안 (Assembly Gate PASS 후에만 진행) |
| ④ NewJeans (테스트) | B-Step 2 | Minji(shadow-tester), Hanni(qa-tester/haiku), Danielle(scientist/haiku), Haerin(browser-verifier), Hyein(debugger) | Shadow 13항목 복합지표, QA, 모니터링 |
| ⑤ LE SSERAFIM (릴리스) | C-Step 5~6 | Karina(architect/opus), Sakura(ssot-keeper/sonnet) | Phase완료리뷰(7항목)+Go/No-Go, SSOT+git push |
| ⑥ ITZY (퀀트) | A+B | Yeji(quant-validator/opus), Ryujin(scientist), Lia(ml-pipeline), Chaeryeong(dex-specialist), Yuna(analyst) | 수학 검증, ML, DEX |
| ⑦ Fix 루프 | L1+ | Joy(debugger), Irene(build-fixer), Wendy(code-simplifier/opus) | 에스컬레이션 시 활성화 |
| TF TWICE | QF/SF/Final | Nayeon(TF리더), Karina, Jeongyeon, Momo, Sana, Mina, Dahyun, Chaeyoung, Tzuyu (9명+Jisoo차출) | 상용화 최종 검증 (3-Round) |

**사이클**: Stage A(기획)→B(구현+검증)→C(리뷰+릴리스+사장님승인)→다음Phase

## 커스텀 에이전트 (.claude/agents/)

- `quant-validator` — 슬리피지/마찰력/수익성 수학 검증 + ML 모델 수학 검증
- `shadow-tester` — Shadow 모드 실 실행 및 결과 분석 + ML Canary 검증
- `ssot-keeper` — SSOT.md 유일 관리자
- `browser-verifier` — Chrome 브라우저 대시보드 통합 검증
- `ml-pipeline` — HMM 레짐 분류, XGBoost 학습, ONNX 추론 파이프라인
- `dex-specialist` — 가스비 오라클, Uniswap V3, CEX-DEX 스프레드 통합

## 기술 스택 + 파일 구조

- **엔진**: Python 3.12+ (AsyncIO) + Rust (PyO3) — `engine/src/`
- **대시보드**: Next.js 14 (App Router) — `dashboard/src/app/`
- **DB**: TimescaleDB + Redis — `docker-compose.yml`
- **거래소**: 10 native WS adapters (7 spot + 3 futures, ccxt 미사용) — `engine/src/collectors/`
- **전략 7개**: `engine/src/strategies/` (cross_exchange, spot_futures, futures_futures, triangular, funding_rate, statistical_arb, cex_dex) — latency_arb는 US-194에서 cross_exchange로 병합
- **API**: `engine/src/api/` (FastAPI + JWT)
- **테스트**: `cd engine && python -m pytest tests/ -x --tb=short`
- **슬리피지**: CEXOrderbookSlippage만 활성 (PowerLaw k=0.0 비활성)
- **설정**: `engine/.env` (엔진용) + 루트 `.env` (Docker용) — **두 파일 반드시 동기화**
- **워크플로우 알림**: `WORKFLOW_TELEGRAM_BOT_TOKEN` + `WORKFLOW_TELEGRAM_CHAT_ID` (기존 `TELEGRAM_BOT_TOKEN` 거래 알림과 분리)
- **워크플로우 자동화**: 순수 Python (sqlite3 + jsonschema + TypedDict) — `engine/src/workflow/`
  - 체크포인트: `.omc/state/checkpoints.db` (SQLite, 워크플로우 전용 — TimescaleDB 거래 데이터와 분리)
  - 일관성 검사: `cd engine && python -m src.workflow.cli check_all`
  - 체크포인트 저장: `cd engine && python -m src.workflow.cli checkpoint save`
  - 체크포인트 복원: `cd engine && python -m src.workflow.cli checkpoint restore`

## 자주 틀리는 패턴 (반드시 숙지)

- **이중 슬리피지 금지**: SignalGenerator의 CEXOrderbookSlippage가 유일한 슬리피지 소스. PaperExecutor에 PowerLaw 적용 절대 금지
- **ENGINE_ENV**: `dev|staging|prod|test`만 허용 (`development` 사용 금지)
- **KRW 거래소**: upbit, bithumb, coinone은 KRW 페어 자동 매핑. auto-symbols `min_exchanges=3` 필수 (7로 하면 0개)
- **Bithumb stale data**: 증분 orderbook에서 소형코인 2-10x 가격 오차 → fake spread. Phase G에서 해결
- **Stage B-Step 2 중 /compact 금지**: Shadow/QA 백그라운드 에이전트 실행 중 압축하면 결과 소실. Stage C 완료 + git push 후에만
- **Coinone 수수료**: 0.20% → 0.02% (API 할인 적용)
- **cancel_order**: order.symbol 전달 필수 (Binance rollback). TypeError fallback for legacy adapters
- **friction prefix**: cost_calculator가 `paper_`/`sandbox_` prefix 자동 strip
- **passes:true 거짓 양성 금지**: 코드 존재만으로 완료 판정 금지. Shadow 10min 런타임에서 해당 기능 호출 증거(로그/메트릭) 필수. dead code(정의만 있고 호출 안 됨) = passes:false

## 플랜 파일 (유저 레벨 — 레포 밖)

- **강화 계획**: `/Users/100aniv/.claude/plans/smooth-tickling-giraffe.md` (Phase G~F 재편)
- **기존 플랜**: `/Users/100aniv/.claude/plans/jazzy-wishing-avalanche.md` (Phase A~SR, 25 Parts)
- **수동 백업**: `/Users/100aniv/Development/arbitrage_OMC/LEVIATHAN_PROJECT_TOTAL_REPLAN.md`

## 현재 상태 (SSOT.md §2 참조)

- **Phase 순서**: A~M✅ → S1~S14 ✅ → TF QF 7차 ✅ → TF SF Stage 4 PASS → TF SF 9H 중단 → **Phase S15 완료 (2026-03-19)** → **Phase S16~S21** → TF QF → TF SF → TF Final → Live
- **Tests**: 4,940 passed, 0 failed, 12 skipped
- **PRD**: `.omc/prd.json` (243+54개 US, 243 passes:true / 54 passes:false)
- **Docker 필수**: Shadow 실행 전 `docker compose up -d` — DB 없으면 데이터 미저장
- **다음 작업**: Phase S16 (동적 임계치 + 고급 기능) → S17~S21 → TF QF → TF SF → TF Final → Live
- **TF SF 9H 중단**: CRITICAL 6건 + 수학 오류 3건 발견 → Phase S15~S21 회귀 (2026-03-19)
- **회귀 사유**: profit_factor 계산 버그, LiveGate 차단 미동작, ML 미연결, 전략 평가 기준 위반
- **계획서**: `.claude/plans/parallel-finding-sparrow.md` (7 Phase, 63 US)
- **Phase S13**: ✅ 완료 (2026-03-18) — 22 US (US-221~233, US-235~243): 기관급 전략 완전체 + 7개 전략 로직 개선 + 1H Shadow PnL +$1,674 (9,338 trades, WR 76.2%)
- **Phase S14**: ✅ 완료 (2026-03-18) — 1 US (US-234): Auto-tuner Shadow 통합 + Optuna 미니 튜너
- **Phase S13~S14 플랜**: `.claude/plans/snuggly-chasing-spark.md` (15 Part, 5라운드 검증, 9명 전문가 리뷰)
- **Upbit 수수료**: Maker 0.05% / Taker 0.139%
- **GAP 분석**: `.claude/plans/modular-seeking-wreath.md` (6-관점 통합 분석)

## 워크플로우 강화 (2026-03-19 적용)

> 워크플로우 구조 분석 결과 반영. 근본 원인: "누가 통합 연결을 검증하는가?" — 아무도 안 했음.
> 학술 근거: MAST (NeurIPS 2025, 1642 트레이스 분석), Google/MIT 팀 규모 연구

- **Assembly Gate (C-Step 1)**: 코드리뷰 전 조립 검증 (init chain + signal flow + dead wiring + config audit). TF QF 단계 3.5를 매 Phase에 상시화
- **Shadow 13항목 복합지표**: 시드 무관 절대 지표(MDD%, Profit Factor, Sharpe, Calmar) + 통합 검증(전략별 trade>=1, 방어 레이어 활성)
- **WIRING AC 필수**: 새 컴포넌트 US에 `⚡ WIRING:` AC 3개 (생성→주입→호출)
- **Jennie 통합 추적**: 코드리뷰에서 새 클래스의 생성→주입→호출 경로 추적 (CRITICAL 우선순위)
- **Fix Loop 유형 분류**: Type W(Wiring→즉시 L2) / Type P(Parameter→3회) / Type B(Bug→3회)
- **검증 유틸리티**: `.claude/hooks/assembly-gate.sh` + `shadow-evidence-gate.sh` (에이전트 수동 호출용, 자동 Stop hook 아님)
- **Shadow 결과 파일**: `.omc/state/shadow-result-latest.json` 필수 기록 (Assembly Verifier + Karina 검증용)
- **멀티모델 독립 감사 (C-Step 2)**: Assembly Gate 후, 코드리뷰 전에 3개 외부 모델(Codex+Gemini+Qwen) 인라인 병렬 호출 → quorum 합의. Claude 편향 구조적 제거 목적. 과반수 이슈 지적 시 MUST FIX.

## 멀티모델 감사 (2026-03-19 도입)

> 근본 원인: "클로드가 자기 프로젝트의 맹점을 못 본다" — CCG는 Claude가 최종 종합하므로 편향 제거 불가.
> 해결: 3개 외부 CLI(Codex+Gemini+Qwen)를 Agent()로 직접 병렬 호출. Claude 편향 구조적 제거.

- **자동 실행 (leviathan 내)**: Agent()로 CLI 직접 호출 → AskUserQuestion 0개, TeamCreate 0개 → 멈추지 않음
  - Stage A: 3개 모델 PLAN.md 병렬 리뷰 → quorum 합의
  - C-Step 2: 3개 모델 코드 병렬 감사 → quorum 합의 (MUST FIX 판정)
  - C-Step 4: 3개 모델 Go/No-Go 병렬 토론 → 과반수 판정
- **수동 실행 (직접 호출)**: `/consensus-code-review`, `/consensus-plan-review`, `/octo-security`, `/octo-debate` — 대화형 (TeamCreate + AskUserQuestion 포함)
- **CLI**: Codex(`codex exec`), Gemini(`gemini -p`), Qwen(`qwen -p`) — 3개 인증 완료. Kimi는 `kimi-cli login` 후 추가 가능
- **Stage C 워크플로우**: Assembly Gate(1) → **멀티모델 감사(2)** → 코드리뷰(3) → **멀티모델 토론(4)** → Go/No-Go(5) → SSOT(6) → Telegram(7)

## 실행 워크플로우 (ralph autopilot)

**3-Stage Sequential 연속 실행** (leviathan.md 참조):
1. **Stage A** (기획): [Entry Gate(karina) 순차 → NingNing+Winter+Giselle 병렬] → PLAN.md + WIRING AC → QUANT GATE → **즉시 Stage B**
2. **Stage B** (구현+검증): TeamCreate(IVE) → pytest PASS → TeamDelete → Shadow 13항목 복합지표(NewJeans) → **즉시 Stage C**
3. **Stage C** (리뷰+릴리스): [**C-Step 1 Assembly Gate(조립검증)**] → [**C-Step 2 멀티모델 인라인 감사(3CLI 병렬→quorum)**] → [**C-Step 3 코드리뷰**(Jennie+Jisoo 통합추적)] → [**C-Step 4 멀티모델 Go/No-Go 토론(3CLI 병렬)**] → [**C-Step 5 최종리뷰**(Karina 7항목+Go/No-Go)] → [**C-Step 6 SSOT+git**(Sakura)] → [**C-Step 7 텔레그램**(사장님 승인 대기)]

**세션 관리**: Stage A→B→C 연속 실행 (세션 초기화 없음, ralph 루프 유지).
**`/compact` 절대 금지**. 컨텍스트 60% 시 텔레그램 알림 → `/clear` 시도 → 성공/실패 모두 텔레그램 알림.
**체크포인트 복구**: `.omc/state/leviathan-progress.json` — 세션 크래시 시 `/leviathan` 재호출로 자동 재개.
**에스컬레이션**: L0(팀 내) → L1(fix 루프) → L2(Stage A 재기획) → L3(SSOT 수정) → L4(Phase 재편) → **L5(텔레그램→사장님)**
