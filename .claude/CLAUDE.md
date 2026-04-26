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
   - `SSOT.md`가 프로젝트의 **유일한 설계 문서**
   - 다른 문서(docs/archive/)에 상태 정보를 기록하지 말 것

2. **도구 활용 (CLI 우선)**:
   - **GitHub**: `gh` CLI로 push, PR, issue 관리
   - **테스트**: `cd engine && python -m pytest tests/ -x --tb=short`
   - **Shadow 실행**: `cd engine && timeout 600 python -m src.main`
   - **Docker**: `docker compose up -d timescaledb redis && docker compose ps` (compose 'engine' 서비스는 로컬 python과 port 8000 충돌 — DB/Redis만 기동)
   - **Exa.ai**: 리서치 시 `mcp__exa__web_search_exa` 사용

3. **완료 기준**: 단위테스트 통과만으로 Phase 완료 선언 금지.
   반드시 Shadow 10분 실행 후 PnL > 0, crash 0건 확인.

## 팀 구조 (5팀 체제)

| 팀 | 에이전트 | 역할 |
|----|---------|------|
| 기획팀 | architect(opus), planner(opus), analyst(opus) | SSOT 읽기, 리서치, 태스크 분해, 우선순위 |
| 개발팀 | executor(sonnet), deep-executor(opus), build-fixer(sonnet), designer(sonnet) | 구현, 빌드 수정, UI 반영 |
| 퀀트팀 | scientist(sonnet), analyst(opus), quant-validator | 수학 검증, 백테스트, 파라미터 민감도 |
| 테스트팀 | test-engineer(sonnet), qa-tester(sonnet), shadow-tester | 단위/통합/Shadow 테스트, E2E |
| 검증팀 | code-reviewer(opus), security-reviewer(sonnet), critic(opus), ssot-keeper, browser-verifier(sonnet) | 코드 리뷰, 보안, SSOT 업데이트, Chrome 브라우저 검증(Kazuha) |

**사이클**: 기획→개발→퀀트→테스트→검증→(gaps→기획 복귀, 없으면 commit+push+SSOT 업데이트)

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
- **거래소**: 8 native WS adapters (ccxt 미사용) — `engine/src/collectors/`
- **전략 8개**: `engine/src/strategies/` (cross_exchange, spot_futures, futures_futures, triangular, funding_rate, statistical_arb, latency_arb, cex_dex)
- **API**: `engine/src/api/` (FastAPI + JWT)
- **테스트**: `cd engine && python -m pytest tests/ -x --tb=short`
- **슬리피지**: CEXOrderbookSlippage만 활성 (PowerLaw k=0.0 비활성)
- **설정**: `engine/.env` (엔진용) + 루트 `.env` (Docker용) — **두 파일 반드시 동기화**

## 자주 틀리는 패턴 (반드시 숙지)

- **이중 슬리피지 금지**: SignalGenerator의 CEXOrderbookSlippage가 유일한 슬리피지 소스. PaperExecutor에 PowerLaw 적용 절대 금지
- **ENGINE_ENV**: `dev|staging|prod|test`만 허용 (`development` 사용 금지)
- **KRW 거래소**: upbit, bithumb, coinone은 KRW 페어 자동 매핑. auto-symbols `min_exchanges=3` 필수 (7로 하면 0개)
- **Bithumb stale data**: 증분 orderbook에서 소형코인 2-10x 가격 오차 → fake spread. Phase G에서 해결
- **Phase C 중 /compact 금지**: Shadow/리뷰 백그라운드 에이전트 실행 중 압축하면 결과 소실. Phase C 완료 + git push 후에만
- **Coinone 수수료**: 0.20% → 0.02% (API 할인 적용)
- **cancel_order**: order.symbol 전달 필수 (Binance rollback). TypeError fallback for legacy adapters
- **friction prefix**: cost_calculator가 `paper_`/`sandbox_` prefix 자동 strip

## 플랜 파일 (유저 레벨 — 레포 밖)

- **강화 계획**: `/Users/100aniv/.claude/plans/smooth-tickling-giraffe.md` (Phase G~F 재편)
- **기존 플랜**: `/Users/100aniv/.claude/plans/jazzy-wishing-avalanche.md` (Phase A~SR, 25 Parts)
- **수동 백업**: `/Users/100aniv/Development/arbitrage_OMC/LEVIATHAN_PROJECT_TOTAL_REPLAN.md`

## 현재 상태 (SSOT.md §2 참조)

- **다음 Phase**: H (대시보드 통합, US-072 남음) → I → J → K → L → M → F(최종검수, LAST)
- **Tests**: `pytest --co -q | tail -1` 실시간 확인 (하드코딩 금지)
- **PRD**: `.omc/prd.json` — `jq '.total_stories, (.stories|length), (.phases|length)'`로 실시간 조회
- **Docker 필수**: Shadow 실행 전 `docker compose up -d timescaledb redis` — DB/Redis만 기동 (engine 컨테이너는 로컬 python과 port 충돌)
- **다음 작업**: US-072 (계좌 정보/총자산/거래소별 잔고 — Phase H)

## 실행 워크플로우 (ralph autopilot)

**3-Phase Sequential 연속 실행** (leviathan.md 참조):
1. **Phase A** (기획): ralplan → PLAN.md → QUANT GATE → checkpoint 저장 → **즉시 Phase B**
2. **Phase B** (개발): TeamCreate + Step 2.5 통합검증 → pytest PASS → TeamDelete → checkpoint 저장 → **즉시 Phase C**
3. **Phase C** (검증): Shadow + code-reviewer + critic → SSOT 업데이트 → git push → checkpoint 저장 → **즉시 다음 US**

**연속 실행 원칙**: Phase A→B→C는 끊김 없는 단일 흐름. Phase 간 `/clear` 없음. `/compact` 절대 금지.
**체크포인트 복구**: `.omc/state/leviathan-progress.json` — 세션 크래시/수동 `/clear` 시 `/leviathan` 재호출로 자동 재개.
**에스컬레이션**: L0(팀 내) → L1(fix 루프) → L2(Phase A 재기획) → L3(SSOT 수정) → L4(Phase 재편)

## LEVIATHAN 워크플로우 핵심 규칙 (3줄)

- **Docker**: Phase B + Phase C 진입 시 `docker compose up -d timescaledb redis && docker compose ps` 필수 (engine 컨테이너는 로컬 python과 port 8000 충돌)
- **Chrome**: Phase D/H US → Rosé(Phase B) + Kazuha(Phase C)가 Chrome DevTools MCP로 실제 브라우저 검증. `npm run build`만으로 완료 선언 금지.
- **멈춤 금지**: Phase 간 사용자 확인 요청 절대 금지. 모든 응답에 tool call 포함.

---

## Path-B v2 Refactor Rules (2026-04-20 active)

- Mode: paper only until Gate passes (commit 606c97b enforcement)
- Every new feature behind feature flag (default false, activated per Day)
- 14-doc sync required on every Day completion
- Stage A-H workflow per §17 of plan
- HIGH risk Days (6/7/11/14): /freeze + /careful + worktree mandatory
- Evidence A+B+C required before commit (§12.1)
- Silent DEBUG reject logs FORBIDDEN (§12.3)
- live.py + main.py monotonically shrinking (reject PRs that grow either)
- Binance API cross-check on every pnl claim

## Phase 5 Hexagonal Architecture Rules (2026-04-26 active)

- main.py LOC budget: ≤ 700 (현재 696). PR이 700 초과 시 거부.
- god-object 'engine' 인자 사용 금지 — 신규 함수/클래스는 specific Port DI 사용 (ExchangeAdapterPort, ExecutorPort, RiskPort, DataFeedPort, JournalPort, LedgerPort, KillSwitchPort).
- 7 Ports는 `typing.Protocol(runtime_checkable=True)`로만 선언. 구체 구현 import 금지.
- EngineState (`src/core/engine_state.py`)는 16 mutable runtime field의 SSOT — 새 mutable field 추가 시 EngineState 우선.
- ModeRunner ABC 패턴 — paper/live/backtest dispatch는 if-elif 금지, 반드시 BacktestRunner/PaperRunner/LiveRunner 클래스 사용.
- LifecycleManager Kahn topological sort — 컴포넌트 시작/종료 순서는 declarative dependency.
- LOC budget enforcer: `python engine/scripts/check_loc_budget.py` (CI/pre-commit 통합 필수).

## Phase 6 Listener Dispatcher Rules (2026-04-26 active)

- 14 ExecutionResultListener는 SRP 준수 — 한 개 listener는 한 개 책임만 (log/position_size/cross_hedge/pnl_peak/market_recorder/exposure/slippage/correlation/tca/trade_history/circuit_breaker/rollback/telegram/position_manager).
- ExecutionResultDispatcher는 async/sync 자동 라우팅 + failure isolation — 한 listener exception이 다른 listener 호출을 막지 않음.
- factory.py `build_dispatcher_from_engine(engine)` — 14 listeners 일괄 wiring entry point.
- env flag `EXECUTION_DISPATCHER_ENABLED` (engine.json.feature_flags) — false=legacy, true=dispatcher 경로.
- 신규 listener 추가 시 ExecutionResultListener Protocol 준수 + factory.py 등록 + unit test 필수.

## Paper 모드 정의 (2026-04-26 확정)

- **Paper = 실제 WebSocket 데이터 (real WS) + 시뮬레이션 체결**. Synthetic GBM/spread injection 절대 금지.
- `PaperExchangeAdapter(spread_injection_rate=0.0, spread_injection_bps=0)` — synthetic 비활성화 강제.
- Synthetic data는 **backtest 모드 전용** — paper와 backtest 코드 경로 분리.
- Shadow 모드는 **폐기됨 (DEPRECATED 2026-04-26)** — `src/modes/shadow.py`는 paper.py로 forward shim. 신규 코드 import 금지.

## Paper 모드 + universe_matrix 게이트 룰 (2026-04-22 신규, 14h 헛수고 교훈)

- **Paper 어댑터는 config 기반으로 생성** — `_init_paper_exchanges`는 config의 `exchanges.active`(7개)를 그대로 사용. paper_binance/paper_okx 같은 하드코딩 절대 금지. 거래소 ID는 data collector ID와 일치 (binance, bitget, ...).
- **PaperExchangeAdapter는 ExchangeAdapter Protocol 완전 구현** — `_market_type` 속성 (`_futures` suffix → "futures", else "spot"), `supports_symbol(symbol)`, `get_min_notional(symbol)` 메서드 필수. 누락 시 universe_matrix=0.
- **universe_matrix entries=0이면 즉시 paper 카나리 중단** — 어떤 trade도 발생 불가. CLAUDE.md "Shadow 10분 PnL > 0" 규칙은 entries > 0 전제.
- **Day N 완료 게이트**: pytest pass + Shadow 10분 + `universe_matrix entries > 0` + `trade_request_executed >= 1`. 4개 동시 충족 안 되면 Day 완료 선언 금지.
- **K-PT/Paper 케이스에 ac_override 사용 금지** — 기준 충족 못 하는 케이스를 ac_override로 PASS 선언하면 거짓양성 누적.
- **참조**: 메모리 `feedback_paper_universe_matrix_zero_trap.md` (2026-04-22)

## Strategy Loss Cap 룰 (2026-04-22 신규, catastrophic loss 차단)

- **single trade loss cap = $1** (paper + live 모두). engine/.env에 `PAPER_MAX_LOSS_PER_TRADE_USD=1.0`, `LIVE_MAX_LOSS_PER_TRADE_USD=1.0`.
- **strategy별 cap JSON**: `STRATEGY_LOSS_CAP_JSON={"spot_futures_basis":1.0,"funding_rate_arb":1.0,"futures_futures":1.0,"triangular":1.0}`.
- **이전 기본값 $10이라 60min stage 중 spot_futures -$5.02 단일 손실 발생**. cap 미작동 → 즉시 $1로 변경 + engine 재시작.
- **자본 보호**: $140 자본 0.7% 이상 단일 trade 손실 영구 차단.
- **paper도 동일 룰** — paper에서 catastrophic loss 패턴 학습되면 live에서 재현될 위험. 두 모드 cap 통일.
- **실시간 모니터링**: `engine/scripts/realtime_monitor.py` + `loss_capped > 0` 메트릭 alert.
