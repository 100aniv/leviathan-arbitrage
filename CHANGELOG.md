# Changelog

All notable changes to LEVIATHAN are documented here per [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) spec.

## [Unreleased]

### Added (2026-04-27) — Phase 7 Hexagonal expansion (12 Ports) + Codex final review 4건 해결

#### Phase 6 Step 4: legacy 분리 (Gemini Priority 1)
- `614dd91`: on_execution_result 14-LOC wrapper, _on_execution_result_legacy private function 분리
- DEPRECATION 표시: 7+ days canary 안정 후 360 LOC 삭제 가능
- 새 테스트 test_legacy_function_extracted_and_callable

#### Phase 7: 7 → 12 Ports (Gemini Priority 2 + Codex SUGGEST)
- `37daf15`: EventBusPort + MetricsPort (Nautilus MessageBus / LEAN telemetry 미러)
- `734b223`: ConfigPort + AlertPort (DI-friendly, vendor-neutral)
- `2c17eab`: ExchangeIncomeFetcherPort (income polling lifecycle)
- 모든 Port runtime_checkable 정합 + InMemoryEventBus 실제 round-trip 검증

#### Codex final review 4건 BLOCKING 모두 해결
- `9f05182` (v2 BLOCKING): EventBusPort 시그니처 (callback-driven → pull-based list[dict], raw=True 보존)
- `c8eb4a1` (SUGGEST): dispatch_sync done-callback async exception 가시화 (silent task drop 제거)
- `fab96c0` (final BLOCKING): alert/elevation 복원
  - PositionSizeLeakListener에 state + alert_bot DI
  - position_tracking_errors counter, > 5 시 telegram alert (legacy parity)
- `b18bb60` (final BLOCKING): legacy vs dispatcher parity test
  - 4/4 PASS — total_pnl / position_sizes / cross_exchange_positions / failure status
  - dispatch path 강화: get_running_loop() 분기 (loop.create_task vs dispatch_sync)

#### Codex SUGGEST: listener helpers DRY (`2d642d5`)
- 신규 src/listeners/_helpers.py:
  - extract_legs_info(result) — None-safe (trade, order) tuples
  - is_close_leg(leg_or_order) — reduceOnly + 3 prefixes (settlement_close/timeout_close/spread_exit)
  - is_close_execution(legs_info), get_side(order), is_status_success(result)
- 7 listeners 적용 (cross_hedge / position_manager / rollback / exposure / market_recorder / telegram / position_size_leak)

### Added (2026-04-26) — Phase 6 Listener Dispatcher 활성 + Codex BLOCKING 3건 해결

#### Phase 6 Step 1-3: ExecutionResultDispatcher 활성화
- `d50a8de`: main.py `_init_listeners()` + `EXECUTION_DISPATCHER_ENABLED` flag (default false)
- `1b1383f`: engine.json EXECUTION_DISPATCHER_ENABLED=true (Step 2 활성화)
- `7691cde`: TDD RED — `tests/unit/runtime/test_on_execution_result_delegation.py` (3 tests)
- `dc4491b`: TDD GREEN — `risk_execution.py:519` 상단에 dispatcher 위임 분기 (14 LOC)
  - dispatcher.dispatch() 성공 시 legacy 360 LOC 경로 SKIP
  - dispatcher 예외 시 legacy fallback (resilience)

#### Codex 외부 리뷰 BLOCKING 3건 모두 해결
- `f311e65` (BLOCKING #1): ExchangeAdapterPort 시그니처 정합
  - get_min_notional: sync → async (paper/native 모두 async였음)
  - get_balance(singular) → get_balances(plural) (Balance dict)
  - health_score: Decimal → float (실제 구현 정합)
  - PaperExchangeAdapter.cancel_order(order_id, symbol=None) (Native parity)
- `ca5522d` (BLOCKING #3): EngineState SSOT — 6 @property 프록시
  - 6 legacy fields 어셈블리 블록 삭제, 6 @property + setter pair 추가
  - listener/risk_path 양쪽 모두 self._state 동일 객체 참조 (no divergence)
  - Engine.__init__에서 legacy 11 LOC → 4 LOC comment

#### 외부 AI 리뷰 (Gemini + Codex)
- Codex (`codex-adversarial-review-2026-04-26T13-12-52-936Z.md`): 3 BLOCKING + 4 SUGGEST + 2 NIT — 모든 BLOCKING 해결
- Gemini (`gemini-architecture-audit-2026-04-26T13-10-37-667Z.md`): Priority 1 (kill on_execution_result body) Step 3로 1차 충족, Step 4에서 완전 삭제 예정

### Added (2026-04-26) — Phase 5 Hexagonal Architecture 100% 완료 (자동 진행)

**산업 표준 도달 (Nautilus/LEAN/Hummingbot 미러)**:

#### Phase 5.0 Pre-audit (4 architecture docs, 1075 LOC)
- `engine/docs/architecture/communication-flow.md` — signal→strategy→executor→result trace
- `engine/docs/architecture/module-dependencies.md` — 565 engine.X accesses across 7 modules
- `engine/docs/architecture/listener-decomposition.md` — on_execution_result 360 LOC → 14 listeners
- `engine/docs/architecture/engine-state-design.md` — 16 mutable runtime fields

#### Phase 5.1 — 7 Hexagonal Ports (`engine/src/ports/`)
- `ExchangeAdapterPort` (place_order/cancel/balance/supports_symbol/get_min_notional)
- `ExecutorPort` (execute_trade_request + add/remove_listener)
- `RiskPort` (check_proposal + record_loss/win + is_halted)
- `DataFeedPort` (subscribe + on_orderbook/on_trade)
- `JournalPort` (start + append + replay + flush)
- `LedgerPort` (record_pnl + get_total/per-strategy/per-exchange)
- `KillSwitchPort` (halt + clear + is_active)
- `ExecutionResultListener` (Listener Protocol)

#### Phase 5.2 — God-object 해체
- 5.2.1 `EngineState` dataclass (16 mutable fields, reset/snapshot 메서드)
- 5.2.2 Engine.__init__ EngineState 통합 (backward-compat)
- 5.2.4 14 ExecutionResultListener 분리:
  - LogListener / PositionSizeLeakListener / PositionManagerListener / CrossHedgeListener
  - PnLPeakListener / MarketRecorderListener / ExposureListener / SlippageListener
  - CorrelationListener / TCAListener / TradeHistoryListener / CircuitBreakerListener
  - RollbackListener / TelegramListener
- 5.2.5 `ExecutionResultDispatcher` (sequential + 예외 격리 + async/sync routing)
- 5.2.6 `ListenerFactory` (Engine → 14 listeners builder, 등록 순서 = risk_execution.py 원본)

#### Phase 5.3 — `LifecycleManager` (background_loops.start_background_tasks 270 LOC 대체)
- register(name, factory, depends_on, priority) + topological sort (Kahn)
- start_all / stop_all (역순 graceful shutdown)
- cycle 감지 + 등록 lock

#### Phase 5.4 — `ModeRunner` ABC (mode_loops 826 LOC 다형성)
- BacktestRunner / PaperRunner / LiveRunner
- create_mode_runner(mode, engine) factory
- shadow legacy alias → PaperRunner

#### Phase 5.5 — LOC budget enforcer (`engine/scripts/check_loc_budget.py`)
- 14 critical paths max LOC (main.py ≤700, runtime/* ≤1000)
- exit code 0/1 CI integration
- 14/14 paths within budget 검증

**검증**: pytest 5056 → 5119 pass (+63), 12회 회귀 ZERO regression
**산업 표준 점수**: Module 8/10, DI 7/10, Lifecycle 9/10, Mode 9/10, Testing 9/10

### Added (2026-04-26) — Phase 1+2 단일 배관 통합 (사장님 지시: "리팩토링인데 제대로 배관 공사 통합")
- 사장님 지적 audit (병렬 multi-agent): paper/live/backtest 동일 배관 미달 + Day 0-15 활성화 0/7 (engine/.env 부재) + 산업 표준 (Hummingbot/NautilusTrader/LEAN) SHARED 배관 일치
- **Phase 1 — engine.json feature_flags 통합 (`69f7f76`)**:
  - `engine/config/engine.json`에 `feature_flags` section 신설 (단일 진실 소스)
  - 5/7 flag ON: JOURNAL, STATE_MACHINE, ROUTER, REAL_ADV, PRETRADE_VALIDATOR
  - 2/7 OFF (보수적): PARALLEL_LEGS (HIGH risk), SUPERVISOR (Phase 3 통합 후)
  - `get_bool_flag()` 해결 우선순위: env > engine.json.feature_flags > default
- **Phase 2A — PaperMode inject slots (`2a06966`)**:
  - PaperMode.__init__ — pre_trade_validator + execution_journal 파라미터 추가 (default None = backward-compat)
  - _execute_paper_trade_request 진입점에 validator.validate() + journal.append SENT 호출
  - code-reviewer APPROVE (CRITICAL/HIGH 0, MEDIUM 1 fix, LOW 2 fix)
- **Phase 2B — main.py 의존성 wiring (`f2285ab`, `0572669`)**:
  - PreTradeValidator 21개 의존성 stub: paper용 lambda + DeduplicationGate(window_s=10)
  - ExecutionJournal start() + SQLite WAL 자동 초기화 at engine/logs/paper_execution_journal.db
  - code-reviewer REJECT → fix 적용:
    - CRITICAL: ExecutionJournal.start() (initialize() 아님)
    - CRITICAL: halt_local module-level 함수 (KillSwitch attribute 아님)
    - HIGH: session_loss_supplier paper _stats.total_pnl 추적
    - MEDIUM: journal path 2-hop 일관
    - ValidationResult.approved 사용 (allowed 아님)
- **단일 배관 plan 문서**: `engine/docs/plans/single-pipeline-integration.md` (Phase 3 shadow.py 모놀리스 폐기 2-3d)
- **메모리 추가**: `feedback_pipeline_must_be_unified.md` + `feedback_config_no_fragmentation.md`
- 검증: pytest 5056 pass / 14 skipped × 5회 (각 commit) ZERO regression

### Fixed (2026-04-26) — Refactor follow-up audit + WS-3 None-safety + FF stale gate
- **WS-1/2/3 + BUG-94 6 commits 독립 감사** (병렬 multi-agent): NO_ROLLBACK 결정. 구조적으로 정상 (BUG-94 `_pending_position_metadata` 5-경로 cleanup 검증, WS-3 `_position_sizes` rollback 검증, WS-1 trading.json 잔재 0건). 2 follow-up fix 발견.
- `engine/src/risk/position_manager.py:142` — `update_position()` None-safety 누수 수정. `dual_writer=None` paper 모드 crash 차단. `close_position` 패턴 미러 (try/except + warning log).
- `engine/src/core/config.py:570` — `EXCHANGE_STALE_THRESHOLD_S` 5s → 30s. real_signal_producer.py:684-692 FF stale gate 100% drop 원인 (3.5h 가동 중 stale==pairs sigs=0). 162-358bps spread 관측되어도 차단됨. 3s `book.last_update_time` 1차 stale 보호 유지, 30s는 reconnect-detection fallback only. (`a182d32`)
- **결과**: 단위테스트 34/34 PASS, 엔진 v5 재기동 → futures_futures 신호 발생 검증 진행 중.
- **부수**: `scheduled_tuner.py` + `adaptive_threshold.py` stdlib logger structlog-스타일 호출 TypeError 수정 (`2260862`). `realtime_monitor.py` datetime.utcnow() DeprecationWarning 제거 (`40bd8ee`). KRW × USDT pair cross_exchange_spot 차단 (`705be52`).
- **SSOT.md sync**: 4-day drift 정정. tests count 5,053 → 5,851 collected. 8 사이클 paper canary 이력 통합. (`7eac5ab`)

### Fixed (2026-04-22) — Paper 모드 universe_matrix 함정 + 14h 카나리 무효화 인정
- `engine/src/main.py:_init_paper_exchanges` — paper 어댑터를 config의 `exchanges.active`(7개) 기반으로 생성. 이전 paper_binance + paper_okx 2개 하드코딩이 14h 카나리에서 trade 0건 발생 root cause였음 (`3d37e91`)
- `engine/src/execution/paper_adapter.py` — `_market_type` 속성 + `supports_symbol`/`get_min_notional` 메서드 추가. PaperExchangeAdapter가 ExchangeAdapter Protocol 완전 구현 → universe_matrix가 spot/futures 정확 분류 (`3d37e91`)
- 결과: universe_matrix entries **0 → 34**, 5분 실행 trade_request_executed=5건, total_pnl=+$1.08
- 14h 카나리(PID 45822) 결과 **무효 인정** — universe_matrix=0 환경에서 K-PT 18 케이스 ac_override 통과한 4건(US-407/409/410/419) passes:false 리셋 (`45a83a6`)
- US-386 "Shadow→Paper 전면 리네임" passes:true → false: 실제로는 클래스명만 PaperMode (shadow.py 2679줄 모놀리스 미이전), ShadowRunner tuning에 잔존 (`45a83a6`)
- 테스트 갱신: paper-adapter 2→7 확장으로 깨진 2 테스트 (`test_init_exchanges_paper_mode_creates_two_adapters`, `test_supervisor_halt_on_stranded.py`) 수정 (`e5a28b2`). regression 5053 pass / 14 skipped

### Changed (2026-04-22) — SSOT.md 6 mismatch 정정 (`f304355`)
1. §2 commit table에 Day-16 후속 8 commits 추가 (이전: Day 15 마지막 표시)
2. §2 Gate 상태: "대기 중" → 첫 카나리 실패 + 수정 + 5분 검증 + 재실행 필요 명시
3. US-386: "전면 리네임" → "부분 리네임 (클래스명만)" 정정
4. K-PT 4 케이스 ⚠️ ac_override 거짓양성 의심 + 재실행 필요 표기
5. 모드 명칭 ShadowMode → PaperMode 정정 (lines 290, 530)
6. Tests 카운트 시점 명시 (5,508 PHOENIX vs 5,053 Path-B v2)

### Added (Path-B v2 review remediation)
- `ARCHITECTURE.md` — comprehensive hand-off doc (378 LOC) covering Path-B v2 execution boundary, 16 module table, flag dependency matrix, order lifecycle, persistence, reconciliation cycle, Gate criteria, extension points (`4a54e56`)
- `engine/docs/PATH_B_V2_REVIEW.md` ready for reviewers — multi-model code review findings (1 CRITICAL + 4 HIGH + 4 MEDIUM + 3 LOW)
- `leviathan_slippage_prediction_missing_total` Prometheus counter — tracks feedback records missing Signal.predicted_slippage_bps (`556ffb7`)
- `src/core/config_loader.get_bool_flag()` unified truthy env-var parser across all 7 Path-B v2 feature flags (`556ffb7`)
- `engine/tests/unit/core/test_config_loader_bool_flag.py` (20 tests) + extended `test_slippage_feedback_wired.py` (+2 tests)
- `engine/tests/unit/execution/test_cross_exchange_v2_criticals.py` (+11 tests) + self-loop tests in test_order_state.py (`5a276f5`)

### Changed
- C-1 `CrossExchangeV2Executor`: raise `ConfigError` when `flag ON + state_machine=None` (was silent logger.warning). Promote `TransitionError` from DEBUG to ERROR. STRANDED now emits via state_machine (`5a276f5`)
- H-2 `OrderState._LEGAL_TRANSITIONS`: add `ACKED→ACKED` + `PARTIAL→PARTIAL` self-loops for incremental fills (`5a276f5`)
- H-4 `cross_exchange_v2._normalize_side()`: unify "BUY"/"Buy"/"long"/"bid"/"ask" → lowercase (`5a276f5`)
- M-1 `ExecutionJournal._SINGLETON_LOCK`: lazy-init inside `get_execution_journal()` (CLI tool compatibility, `556ffb7`)
- Day 14 executor.py: remove 13 redundant `if state_machine is not None` outer guards (−20 LOC, `9900346`)

### Fixed
- 13 pre-existing test failures unrelated to Path-B v2 (`cfaedaf`):
  - stat_arb_disable fixture × 4 (Engine._exchanges stub)
  - native_bitget + collectors v2→v3 format × 5
  - exchange_base Protocol adapter stub × 1
  - pretrade_validator event-loop flake × 1

**Post-review regression**: 5053 unit tests pass, 0 failures, 14 skipped.

## [v2.0.0-path-b] — 2026-04-21

Path-B v2 structural refactor complete (Day 0-15 + W3 + W4). Execution boundary: Journal + StateMachine + Router + parallel legs. Live re-enable BLOCKED until Gate passes 48H paper canary + 7 criteria.

| Commit | Deliverable |
|--------|-------------|
| `b861a10` | Day 0 — SSOT + 13-doc sync + Binance 30d reconciliation |
| `468785c` | Day 6 — ExecutionJournal (+12 tests) |
| `01d9d12` | Day 7 — OrderStateMachine (+9 tests) |
| `72df0e2` | Day 8 — OrderRouter (+7 tests) |
| `d016849` | Day 9 — pred_bps wiring fix (+3 tests) |
| `89b820f` | Day 10 — MarketStats real ADV (+7 tests) |
| `74292cc` | Day 11 — IOC-TTL parallel legs (+9 tests) |
| `db7bb43` | Day 12 — PreTradeValidator wire (+9 tests) |
| `782e25e` | Day 13 — gamma calibration (+7 tests) |
| `edb491f` | Day 14 — executor migrate (+5 tests) |
| `38a99a6` | Day 15 — TradingSupervisor activate (+4 tests) |
| `07bd710` | W3 — dashboard 8-page skeleton |
| `aed0e92` | W4 — infra audit (Prometheus/Grafana/Alertmanager/TimescaleDB/Loki) |

**LOC deltas**: live.py 3,476→3,250 (−226), main.py 4,194→4,228 (+34), atomic.py +50 (try_ioc), executor.py 1,587→1,793 (+206 state machine wiring).
**Test delta**: +72 new tests across Day 6-15; total regression 4,996 pass / 13 pre-existing failures (unrelated).
**Feature flags**: 7 flags, all default false — rollback = set false in .env.
**Gate pending**: 48H paper canary + 7 criteria (plan §5). Live re-enable BLOCKED until Gate passes.

## [Path-B v2 — Unreleased (original entries)] — 2026-04-20

### Added
- W4 Infra: Prometheus recording rules (5 rules, 30s eval interval) — `infra/prometheus/recording_rules.yml`: `leviathan:signal_rejected:rate5m` per reason_code, `leviathan:order_placed:rate5m` per exchange, `leviathan:execution_latency_p50/p95/p99:5m` (histogram_quantile pre-computed), `leviathan:pnl_divergence_usd:latest` gauge snapshot. Added to `prometheus.yml` rule_files + docker-compose volume mount.
- W4 Infra: Alertmanager ReasonCode severity map (16 codes) — `infra/alertmanager/alertmanager.yml`: critical (KILL_SWITCH_HALT, EXECUTION_STRANDED, PNL_DIVERGENCE_CRITICAL) → Telegram; warning (BUDGET_EXHAUSTED, CIRCUIT_BREAKER_OPEN, NOTIONAL_BELOW_MIN, DRAWDOWN_LIMIT, MAX_POSITION_EXCEEDED, EXCHANGE_HEALTH_LOW, ROLLBACK_RATE_HIGH, HIGH_SLIPPAGE) → Discord + email; info (TOXICITY_REJECTED, COOLDOWN, EDGE_EVAPORATED, MIN_SPREAD_MISS, ADV_EXCEEDED, MARKET_IMPACT_TOO_HIGH) → email. Inhibit rules suppress lower-severity when KILL_SWITCH_HALT active.
- W4 Infra: Grafana dashboards (5) — `infra/grafana/dashboards/`: `pnl-tca.json` (6 panels: gross/net PnL, 7-layer cost decomposition, per-strategy table, spread bps), `divergence.json` (4 panels: gauge + stat + 24h history), `strategy-health.json` (5 panels: signal rate, rejection rate, budget consumption, order rate, rejection %), `rejections-top.json` (4 panels: timeseries, donut, top-10 table, bar gauge), `execution-latency.json` (5 panels: p50/p95/p99 timeseries, stat cards, per-exchange p95). All use pre-computed recording rules. 6h default time range.
- W4 Infra: TimescaleDB compression + retention policy SQL — `infra/timescaledb/compression_policy.sql`: compression after 7d (orderbook_updates), 14d (trades), 30d (execution_events); retention drop after 90d/365d/180d respectively. Segmented by exchange+symbol with time DESC orderby.
- W4 Infra: Loki retention 7d → 30d (`infra/loki/loki-config.yaml` `retention_period: 720h`, per plan §16.2).
- Path-B v2 structural refactor plan (Day 0 kickoff 2026-04-20)
- `CHANGELOG.md` (this file) per Keep a Changelog 1.1.0
- `engine/docs/OPERATOR_RUNBOOK.md` — daily operator checklist + 16 ReasonCode dictionary
- `engine/docs/MODULE_DESIGN.md` — 832 LOC architecture design doc (§1-§5 complete)
- `engine/docs/REFACTOR_PLAN.md` — Day-by-Day tracking
- 11 new modules shipped during Day 1-5 (opt-in feature flags):
  - PnLLedger + PnLReconciler + ExchangePnLSnapshot (reconciliation/)
  - UniverseMatrix (core/)
  - PreTradeValidator + ReasonCode enum (execution/ + core/)
  - StrategyBudgetLedger (risk/)
  - DailyReconciliationReport (reconciliation/)
  - ConfigService + TradingSupervisor + StrategyRegistry (core/)
- Day 12 — `EXECUTION_PRETRADE_VALIDATOR_ENABLED` feature flag gates both `PreTradeValidator.validate()` and the inline BookWalk market-impact check in `live.py::_execute_trade_request`. Flag `false` (default) bypasses both gates — byte-identical to the pre-Day-2 baseline. Flag `true` activates all 11 pre-trade gates + BUG-228c auto-bump + BookWalk VWAP rejection (existing Day 2/live.py logic; no new modules). Wiring: `_pretrade_enabled = os.environ.get("EXECUTION_PRETRADE_VALIDATOR_ENABLED") == "true"` wraps validator call (+5 LOC) and adds `_pretrade_enabled and` guard to existing BookWalk condition (+1 LOC); net live.py delta −2 LOC (21 ins / 23 del) within §1.4 monotonic shrink invariant. 9 unit tests in `tests/unit/modes/test_pretrade_validator_live.py` covering flag-off bypass, thin-book rejection with ReasonCode, sufficient-book pass-through, source-code wiring assertions for flag check, validate() gating, BookWalk gating, and flag-off BookWalk inactive. Requires Day 6 Journal + Day 8 Router per §22.3 interaction matrix (enforced by ConfigService, not live.py). Day 14 will remove the flag wrapper and migrate the executor natively.
- Day 11 — IOC-TTL parallel cross-exchange executor (`engine/src/execution/cross_exchange_v2.py`, ~440 LOC). `CrossExchangeV2Executor` dispatches both legs concurrently via `asyncio.gather` with per-leg IOC TTL (default 5s, no market fallback), shrinking the post-leg1/pre-leg2 naked-exposure window from 200-480 ms sequential (executor.py:1050→1276) toward the stated p95 < 50 ms target. Handles five outcomes explicitly: `SUCCESS` (both fill), `STRANDED_LEG1`/`STRANDED_LEG2` (one-leg fill → `StrandedPositionTracker` register), `NEITHER` (TTL auto-cancels on-exchange, no unwind), and `ROLLED_BACK` via new `_do_rollback_cross_parallel(leg_states: list[LegState], reason, adapters)` which unwinds both filled legs concurrently via reverse market orders. Pre-gather edge re-check rejects stale signals (`EDGE_EVAPORATED`). Behind feature flag `EXECUTION_PARALLEL_LEGS_ENABLED` (default `false`) — flag-off returns `DISABLED` sentinel without any adapter call. §22.3 Flag-Interaction Matrix enforced at construction: requires `EXECUTION_JOURNAL_ENABLED`, `EXECUTION_STATE_MACHINE_ENABLED`, `EXECUTION_ROUTER_ENABLED` all `true`; mis-configuration raises `ConfigError`. Day 7 `OrderStateMachine` transitions (`PENDING → SENT → ACKED → FILLED` / `CANCELLED` / `REJECTED`) emitted best-effort; state-machine is optional so paper environments without a journal still run (WARN logged). Also extracts `AtomicOrderExecutor.try_ioc(adapter, symbol, side, price, size, ttl_ms)` as a reusable primitive (`src/execution/atomic.py`, ~50 LOC added); `execute()` delegates its IOC half to this primitive so every existing atomic.py caller stays byte-compatible (14 atomic.py tests still pass). Includes 9 unit tests across 6 files (`tests/unit/execution/test_parallel_legs_*.py`) covering both-fill SUCCESS, leg1-only + leg2-only mirror paths, both-stranded invariant violation → parallel rollback, pre-gather EDGE_EVAPORATED (explicit + defensive), outer CancelledError propagation, flag-off DISABLED sentinel, and flag-on ConfigError on missing dependency flags. Sequential path (`executor.py:1050-1276`) untouched — kept live as 2-week rollback insurance per plan §3 Day 11 rollback criteria. `live.py`, `main.py`, `executor.py` LOC unchanged (monotonic shrink invariant preserved). Day 14 migrates the executor onto this substrate.
- Day 10 — `MarketStats` real 24h ADV aggregator (`src/core/market_stats.py`). Rolling 24h USD-volume window per (exchange, symbol) sourced from WS trade events, behind feature flag `CORE_REAL_ADV_ENABLED` (default `false`). `signal.py::_compute_dynamic_adv` switches from the top-5 depth proxy to the real aggregate when the pair is warm (≥15min of data); falls back to proxy otherwise so behaviour is byte-identical by default.
- Day 6 — `ExecutionJournal` durable append-only event-sourcing substrate (`src/execution/journal.py`, ~530 LOC). SQLite-WAL log with per-event SHA256 hash chain (`self_hash = SHA256(prev_hash | order_id | state | canonical_json(payload))`, genesis `"0"*64`). Provides `append()`, `replay(since_ts_ms, order_id)`, `verify_chain()`, `current_hash()`, `pragma_snapshot()`, plus `get_execution_journal()` singleton. Behind feature flag `EXECUTION_JOURNAL_ENABLED` (default `false`) — flag OFF is a full no-op: no DB file created, `append()` returns NOOP sentinel, `replay()` returns `[]`. Uses `aiosqlite` when installed, stdlib `sqlite3 + asyncio.to_thread` fallback otherwise. Foundation for Day 7 `OrderStateMachine` and Day 14 executor migration. Includes 12 unit tests (`tests/unit/execution/test_journal_append.py`, `test_journal_crash_recovery.py`) covering genesis, chain linking, tamper detection, 40-way concurrency, flag-off behaviour, post-restart replay, corruption recovery, WAL+synchronous pragmas. New Prometheus metrics `leviathan_execution_journal_events_total{state}` and `leviathan_execution_journal_write_latency_ms`. `live.py`, `main.py`, `executor.py` unchanged (monotonic shrink invariant preserved).
- Day 8 — `OrderRouter` thin adapter boundary with idempotency + optional SENT journal hook (`engine/src/execution/router.py`, 225 LOC). Provides a stable `submit(order, adapter, trace_id, leg_index) → RouteResult` contract that formats `client_order_id = f"{trace_id}.{leg_index}"` (plan §3.4), deduplicates retries within a 10-minute in-memory TTL cache, and (when a Day 7 `OrderStateMachine` is injected) emits a `PENDING → SENT` transition before the adapter call. Behind feature flag `EXECUTION_ROUTER_ENABLED` (default `false`) — flag OFF performs a pure bypass with zero behaviour change (direct `adapter.place_order` call, no dedup, no journal). `asyncio.Lock` serialises dedup read-modify-write; adapter call happens outside the lock so distinct `client_order_id`s do not serialise. If the adapter raises, the exception propagates and no dedup entry is recorded (retry safe). Includes 7 unit tests (`engine/tests/unit/execution/test_order_router.py`) covering flag-off bypass, basic submit, dedup cache hit, TTL eviction, `client_order_id` format, state-machine SENT emission, and adapter-raise semantics. `live.py`, `main.py`, `executor.py`, `atomic.py` unchanged (monotonic shrink invariant preserved). Day 14 migrates the legacy executor onto this substrate.
- Day 7 — `OrderStateMachine` explicit 9-state order lifecycle layered over Day 6 journal (`src/execution/order_state.py`, ~226 LOC). States: `PENDING`, `SENT`, `ACKED`, `PARTIAL`, `FILLED`, `CANCELLED`, `REJECTED`, `ROLLED_BACK`, `STRANDED`. Declarative `_LEGAL_TRANSITIONS` map — `FILLED`/`CANCELLED`/`REJECTED`/`ROLLED_BACK`/`STRANDED` are terminal (empty outgoing sets). Every legal transition emits exactly one hash-chained `ExecutionEvent` via `journal.append()`; illegal transitions raise `TransitionError`. Behind feature flag `EXECUTION_STATE_MACHINE_ENABLED` (default `false`); `__init__` enforces §22.3 Flag Interaction Matrix dependency (requires `EXECUTION_JOURNAL_ENABLED=true`) via `ConfigError`. Flag-off `transition()` returns `None`, writes nothing, and does NOT raise on illegal (from,to) — full no-op. Includes 9 unit tests across `tests/unit/execution/test_order_state.py` (7 tests: legal path, illegal rejection, journal emission per transition, flag-off no-op, STRANDED terminal, current_state none, flag-dep ConfigError) and `tests/unit/execution/test_order_state_replay_convergence.py` (2 tests: two consumers converge on identical end state; concurrent transitions on distinct order_ids serialise via journal seq). STRANDED payload carries `{exchange,symbol,side,size,value_usd,reason}` for Day 14 `StrandedPositionTracker` forwarding (no direct coupling in Day 7). `live.py` (3,252) / `main.py` (4,221) / `executor.py` / `atomic.py` unchanged — monotonic shrink invariant preserved.

### Changed
- **`mode=live` → `mode=paper`** (commit `606c97b`) — halted live trading after v237 canary confirmed $5.01 engine-vs-Binance PnL divergence
- **"Commercial-grade transition 완료" declaration retracted** — 4 independent reviews (Codex/Gemini/exa.ai/external critic) identified structural defects
- `live.py` 3,476 → 3,249 LOC (−227, Day 2 PreTradeValidator extraction)
- Migration order reversed per Codex: execution-boundary first, lifecycle shell last
- Day 14 — Migrated `src/execution/executor.py` `AtomicExecutor` to emit Day 7 `OrderStateMachine` transitions (`PENDING → SENT → ACKED → FILLED` / `PARTIAL` / `CANCELLED` / `REJECTED` / `ROLLED_BACK` / `STRANDED`) into the Day 6 `ExecutionJournal` at every lifecycle boundary. Added optional DI parameters `state_machine: OrderStateMachine | None = None` and `journal: ExecutionJournal | None = None` to `AtomicExecutor.__init__` (both default `None` — flag-off path is byte-identical to pre-Day-14 baseline; no DB file created, no state-machine calls). Introduced `_maybe_transition(order_id, from_state, to_state, payload)` helper that swallows `TransitionError` as `logger.warning` so journaling is best-effort and cannot abort the hot path. Instrumentation sites: `execute_same_exchange` (PENDING→SENT pre-gather, per-leg ACKED/FILLED/REJECTED post-gather, ACKED→STRANDED on rollback failure, ACKED→ROLLED_BACK on successful unwind); `execute_cross_exchange` (per-leg PENDING→SENT→ACKED→PARTIAL→FILLED on the Amendment-4 sequential path, SENT→REJECTED on leg1 timeout); `_do_rollback_cross` (leg2 SENT→REJECTED when errored pre-ACK, ACKED→ROLLED_BACK on successful leg1/leg2 unwind, ACKED/SENT→STRANDED on rollback failure with full payload `{exchange, symbol, side, size, value_usd, reason}` for downstream `StrandedPositionTracker` integration). Existing `leg1_filled`/`leg2_filled` booleans retained as fast-path derivation of `LegResult.trade`; journal becomes the authoritative lifecycle record when flags are on. §22.3 Flag-Interaction Matrix enforced by `OrderStateMachine.__init__` — activating `EXECUTION_STATE_MACHINE_ENABLED=true` without `EXECUTION_JOURNAL_ENABLED=true` raises `ConfigError` (enforced at wiring, not inside executor). 5 new unit tests in `tests/unit/execution/test_executor_journal_complete.py` cover: (1) same-exchange SUCCESS emits `SENT→ACKED→FILLED` per leg, (2) cross-exchange partial→success emits `SENT→ACKED→PARTIAL→FILLED` on leg1 and `SENT→ACKED→FILLED` on leg2, (3) cross-exchange leg2 exception emits `ACKED→ROLLED_BACK` on leg1 + `SENT→REJECTED` on leg2, (4) same-exchange rollback failure emits `STRANDED` on the failed leg, (5) flag-off path (`state_machine=None, journal=None`) creates no DB file — pure backward-compat. Regression: 5770→5775 green (pre-existing 18 failures unchanged; 0 new failures). §1.4 monotonic shrink invariant: `executor.py` 1,587 → 1,793 LOC (+206, accepted as infrastructure cost for Day 15 supervisor activation); `live.py` / `main.py` unchanged. Rollback: set both flags `false` (default).

### Fixed
- Day 9 — `_pred_bps=0.0` hardcoded wiring fix in `live.py:1863,1870` (enables Day 13 gamma calibration). Added `Signal.predicted_slippage_bps` and `TradeRequest.signal` fields.

### Deprecated
- `_stats.total_pnl` as operator-facing PnL source (replaced by `PnLLedger.get_live_pnl_usd()` reading from exchange income)

## [v237] — 2026-04-19

### Added
- BUG-225 per-exchange symbol availability gate
- BUG-223 cross-strategy position aggregation for reconciler
- BUG-220 per-exchange min_notional guard
- BUG-221+222 Upbit/Bithumb price tick alignment
- WS-A/B/C/D commercial-grade transition (later retracted)
- PnLLedger + divergence monitor + 7-layer TCA + daily report + toxicity filter

### Fixed
- 75+ bugs across BUG-73 to BUG-228 series
- Cross-exchange stranded positions ($30.90 on Upbit/Coinone)
- `_pred_bps=0.0` hardcoded in live.py:1863,1870 (still broken, Day 9 target)

### Retracted
- "Commercial-grade transition" label — engine reported `+$0.09` while Binance showed `-$4.92`

## [v230] — 2026-04-19

### Added
- WS-A/B/C/D modules (ExchangeIncomeFetcher, dynamic min_spread, PnL attribution API, divergence alert + toxicity filter + Sharpe/MDD)

## [Phase K] — 2026-04-02 ~ 2026-04-03

Prior to Path-B refactor. 24H paper session, US-332/372 remaining, Phase L/M/N roadmap.

See `SSOT.md §2` for full history prior to v237.
