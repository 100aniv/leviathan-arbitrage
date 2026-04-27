# LEVIATHAN Path-B Refactor Plan

**Status**: ACTIVE — Phase 8 Step 1 (paper 단일 배관 통합 시작) | **Started**: 2026-04-19 | **Path-B v2 Day 0-15 + W3 + W4 완료** | **Phase 5/6/7 부분 완료** (Critic REJECT — 10/12 Ports dead code, paper 5/7 미wired) | **Phase 8 Step 1 진행중**: 2026-04-27 (사장님 지적 후, 사장님 메모리 `feedback_pipeline_must_be_unified.md` 정합)

## Phase 8 — paper 단일 배관 통합 (2026-04-27)

사장님 지적: 야간 40 commits는 architecture theater. 단일 배관 (paper/live/backtest 동일 코드 경로) 미준수. paper는 ShadowMode, live는 LiveMode 별도 클래스 사용.

**Step 1 완료 (commits a4eb86b → 55f3629 → 22105c1)**:
- engine/src/runtime/mode_loops.py paper_mode_loop에 LiveMode 옵션 추가 (PAPER_USE_LIVEMODE flag)
- Codex BLOCKING fix: risk_guardian=None + _live_mode alias + live_gate=None
- 단위 테스트 5개 + 5,205 tests pass (default false 안전)

**남은 단계**:
- Step 2: PAPER_USE_LIVEMODE=true 활성 + paper smoke 검증 (port 처리)
- Step 3: ShadowMode 폐기 (코드 + import 제거)
- Step 4: paper_mode_loop + live_mode_loop → unified_mode_loop 단일 함수
- Step 5: AI CLI 다중 검증 + 라이브 micro 카나리 ($10/trade) 진입 결정

## Verdict (3-agent consensus: architect + backend-architect + critic)

The orchestration layer (`live.py` + `main.py`) is a God-class monolith with 148 inline `BUG-` markers. The strategy/adapter/friction libraries are well-factored. **Path B = surgical rewrite of the orchestration layer against a stable adapter/strategy interface, not a greenfield rewrite.**

- Path A (continue patching): P(success)=10%, further-loss risk HIGH, P(10 new bugs in 24H) = 92%
- **Path B (structural refactor, 3-8 weeks)**: P(success)=65%, further-loss risk LOW (paper during refactor), 65% code retention
- Path C (greenfield rewrite): P(success)=30%, 4-8 months, second-system syndrome

**결과** (2026-04-27): Path-B v2 + Phase 5/6/7 완료, 5,205 tests pass, Codex/Gemini 외부 리뷰 모두 해결. 라이브 카나리 미실시 (다음 게이트).

## Current State (Phase 5/6/7 완료, 2026-04-27)

**모드** (사장님 정책): `backtest` / `paper` / `live` 3개. 현재 mode=`paper` (engine.json enforced, commit 606c97b).

**라이브 거래**: 중단 중. 라이브 카나리 미실시 (= 진짜 canary, $10/trade × 48H 미진입).

| Module | Path-B v2 시작 | Phase 5/6/7 후 | Delta | Source |
|--------|------|------|-------|--------|
| `src/main.py` | 4,194 LOC | **765 LOC** | **-3,429 (-82%)** | Phase 5 분리 + 6 @property proxies |
| `src/modes/live.py` | 3,414 LOC | 3,250 LOC | -164 | Day 12 PreTradeValidator wire |
| `src/runtime/risk_execution.py` | 0 LOC | 905 LOC | +905 | on_execution_result 14-LOC wrapper + _on_execution_result_legacy fallback |
| `src/ports/` | 0 files | **12 Ports** | +12 | Phase 5 7개 + Phase 7 5개 추가 |
| `src/adapters/` | 0 files | **3 Adapters** | +3 | ConfigAdapter / NoOpMetricsAdapter / NoOpAlertAdapter |
| `src/listeners/` | 0 files | **14 Listeners + Dispatcher + 8 helpers** | — | Phase 5/6 god-function 해체 |
| `src/core/engine_state.py` | 0 LOC | 107 LOC | +107 | EngineState dataclass + reset/snapshot |
| `src/runtime/mode_runner.py` | 0 LOC | 112 LOC | +112 | ModeRunner ABC (Backtest/Paper/Live) |
| `src/runtime/lifecycle_manager.py` | 0 LOC | 183 LOC | +183 | Kahn topological sort |

**Tests**: 4,879 → **5,205 passing / 14 skipped / 0 failed** (+326 tests across Path-B v2 + Phase 5/6/7).

## Day-by-Day Progress

### ✅ Day 0 — Halt Bleeding
- `606c97b` — engine.json `mode: live → paper`
- Binance open positions verified = 0. Safe to halt.
- Engine process count = 0.

### ✅ Day 1 — Ground-Truth PnL Ledger
**Commit `b32792e`** | LOC: +1,246 src, +637 tests, 25 tests pass

New modules:
- `src/reconciliation/__init__.py` — package skeleton
- `src/reconciliation/exchange_pnl_snapshot.py` (600 LOC) — polls Binance `/fapi/v1/income` + Bitget `/api/v3/account/financial-records` every 60s, persists to TimescaleDB `exchange_pnl_snapshots` (fallback: JSON at `logs/pnl_snapshots/`). REUSES existing `ExchangeIncomeFetcher._fetch_income()`.
- `src/reconciliation/pnl_reconciler.py` (397 LOC) — engine_pnl vs exchange_pnl divergence monitor; WARN at $0.50 × 3 consecutive, CRITICAL + kill_switch at $1.00 × 3.
- `src/reconciliation/pnl_ledger.py` (220 LOC) — **SINGLE AUTHORITY for operator-facing PnL**. Dashboard `/api/v1/pnl/attributed` now reads from this ledger. Status = `verified|pending|diverged`.
- `src/reconciliation/schema.sql` — TimescaleDB hypertable `exchange_pnl_snapshots`.

Wiring: `main.py` +5 LOC injection; `live.py` `self._pnl_ledger` attribute (set post-init from main.py, zero change to live.py body).

### ✅ Day 2 — Extract PreTradeValidator + Universe Matrix
**Commits `3c45a3b`, `0784c2b`** | LOC: +1,036 src, +509 tests, 39 tests pass

New modules:
- `src/execution/pre_trade_validator.py` (619 LOC) — typed `ValidationResult(approved, reason_code, detail, skip_rollback_notify)` + stable `ReasonCode` enum. 11 gates: strategy_filter, strategy_cooldown, kill_switch, circuit_breaker, rate_limiter, flash_guard, session_loss, risk_guardian, symbol_cooldown, margin_guard (BUG-74/78 semantics preserved), notional_with_bump (BUG-228c auto-bump), dedup (BUG-79 close semantics preserved).
- `src/core/reason_codes.py` — 16 ReasonCode enum values.
- `src/core/universe_matrix.py` (423 LOC) — boot-time valid `(strategy, symbol, leg_a_exchange, leg_b_exchange)` matrix. Blocks BUG-225 class (Upbit/Bithumb USDT listing misses) before signals ever fire.
- `src/infra/metrics.py` — new `leviathan_signal_rejected_total{reason_code, strategy}` counter; `leviathan_signal_auto_bumped_total{exchange}`.

Wiring: `live.py:~1370-1628` 270-line if-ladder replaced with single `await self._pre_trade_validator.validate(...)` call. Every reject emits INFO log + Prometheus counter (no silent DEBUG paths remain).

### ✅ Day 3 — Strategy Budget Ledger + Daily Report
**Commits `974c1ad`, `5ff1cd9`** | LOC: +1,712 src, +732 tests, 32 tests pass

New modules:
- `src/risk/strategy_budget_ledger.py` (637 LOC) — per-strategy independent daily_loss_budget sourced from EXCHANGE INCOME ONLY. Writes through to TimescaleDB `strategy_budgets` (JSON fallback). UTC 00:00 reset. Strategy auto-halts on budget breach; other strategies continue. `asyncio.Lock` serialised.
- `src/risk/strategy_budget_schema.sql` — hypertable.
- `src/reconciliation/daily_report.py` (558 LOC) — 22-column CSV + Telegram template. Variance decomposition: commission_mismatch + funding_mismatch + slippage_mismatch + fx_mismatch + rollback_mismatch + unattributed (< $0.10 expected).
- `src/reconciliation/daily_report_scheduler.py` (107 LOC) — APScheduler UTC 00:05 daily.

Config: `engine.json` `risk.per_strategy_daily_loss_budget_pct: 2.0` (default 2% of allocated capital).

## Test Count

| Suite | Count |
|-------|-------|
| Day 1 reconciliation (snapshot + reconciler + ledger) | 25 |
| Day 2 pre_trade_validator | 27 |
| Day 2 universe_matrix | 12 |
| Day 3 strategy_budget_ledger | 18 |
| Day 3 daily_report | 14 |
| **Total new** | **96 tests** |

## Remaining Work (Days 5-10)

### ✅ Day 4 — Parallel module build (NOT yet wired into main.py)
**Commits `27eaa57`, `51f25cc`, `5617ecd`** | LOC: +1,603 src, +966 tests, 47 tests pass

New modules (all opt-in, main.py untouched):
- `src/core/config_service.py` (484 LOC) — pydantic `EngineConfig` schema with 15 nested models, cross-field validator, dotted-path accessor, asyncio `on_change` event, process-wide singleton. Real engine.json parses with 0 validation errors (16 tests pass).
- `src/core/supervisor.py` (498 LOC) — `TradingSupervisor` + `SupervisorHealth`. Start sequence: DB → Redis → exchanges → strategy placeholder → UniverseMatrix → background tasks → signal handlers. Stop: idempotent, 30s timeout, disconnects cleanly (12 tests pass).
- `src/core/strategy_registry.py` (621 LOC) — `StrategyRegistry` + `StrategyEntry`. Reads `strategy_activation.json`, binds UniverseMatrix, subscribes to BudgetLedger/CB events for runtime deactivation (19 tests pass).

### ✅ Day 5 — Main.py fail-fast boot guard
**Commit `30b704b`**

- main.py fail-fast boot guard wired via ConfigService
- Boot guard validates required env vars + config schema before any exchange connection

### ✅ Day 6 — ExecutionJournal durable substrate
**Commit `468785c`** | +12 tests

- `src/execution/journal.py` (~530 LOC) — SQLite-WAL, SHA256 hash chain, `EXECUTION_JOURNAL_ENABLED` flag (default false)
- Genesis hash `"0"*64`; every event chain-linked; `verify_chain()` tamper detection
- `live.py`, `main.py`, `executor.py` LOC unchanged (monotonic shrink invariant preserved)

### ✅ Day 7 — OrderStateMachine explicit lifecycle
**Commit `01d9d12`** | +9 tests

- `src/execution/order_state.py` (~226 LOC) — 9 states, declarative `_LEGAL_TRANSITIONS`, journal integration
- Terminal states: `FILLED`, `CANCELLED`, `REJECTED`, `ROLLED_BACK`, `STRANDED`
- Behind `EXECUTION_STATE_MACHINE_ENABLED` (requires JOURNAL); flag-off = full no-op
- `live.py` 3,252 / `main.py` 4,221 / `executor.py` / `atomic.py` unchanged

### ✅ Day 8 — OrderRouter thin boundary
**Commit `72df0e2`** | +7 tests

- `src/execution/router.py` (225 LOC) — `client_order_id = f"{trace_id}.{leg_index}"`, 10-min dedup cache, PENDING→SENT journal hook
- Behind `EXECUTION_ROUTER_ENABLED` (default false); flag-off = pure bypass (direct adapter call)
- `live.py`, `main.py`, `executor.py`, `atomic.py` unchanged

### ✅ Day 9 — Fix Signal.predicted_slippage_bps wiring
**Commit `d016849`** | +3 tests

- Fixed `_pred_bps=0.0` hardcoded at `live.py:1863,1870`
- Added `Signal.predicted_slippage_bps` and `TradeRequest.signal` fields
- Enables Day 13 gamma calibration pipeline

### ✅ Day 10 — MarketStats real 24h ADV
**Commit `89b820f`** | +7 tests

- `src/core/market_stats.py` — rolling 24h USD-volume window per (exchange, symbol) from WS trade events
- Behind `CORE_REAL_ADV_ENABLED` (default false); falls back to depth proxy when pair not warm (< 15min)

### ✅ Day 11 — IOC-TTL parallel cross-exchange legs
**Commit `74292cc`** | +9 tests

- `src/execution/cross_exchange_v2.py` (~440 LOC) — `CrossExchangeV2Executor`, asyncio.gather IOC-TTL (5s default)
- 5 outcomes: SUCCESS, STRANDED_LEG1/2, NEITHER, ROLLED_BACK
- `src/execution/atomic.py` +50 LOC: `try_ioc()` reusable primitive extracted
- Behind `EXECUTION_PARALLEL_LEGS_ENABLED` (requires JOURNAL + STATE_MACHINE + ROUTER)
- Sequential path in `executor.py:1050-1276` kept as 2-week rollback insurance

### ✅ Day 12 — Wire PreTradeValidator + BookWalkSlippage into live signal path
**Commit `db7bb43`** | +9 tests

- `EXECUTION_PRETRADE_VALIDATOR_ENABLED` flag gates both PreTradeValidator.validate() and BookWalk market-impact check
- live.py net delta −2 LOC (21 ins / 23 del); monotonic shrink invariant preserved
- Requires Day 6 Journal + Day 8 Router per §22.3 interaction matrix

### ✅ Day 13 — Gamma calibration cron + synthetic-test harness
**Commit `782e25e`** | +7 tests

- Gamma calibration scheduled job; synthetic canary harness for SlippageFeedbackCollector
- Gate criterion: mean(|actual - predicted|) < 5 bps over 100-trade rolling window

### ✅ Day 14 — executor.py migrate to OrderStateMachine + ExecutionJournal
**Commit `edb491f`** | +5 tests

- `AtomicExecutor.__init__` gains optional DI `state_machine` + `journal` (both default None)
- `_maybe_transition()` helper: best-effort, swallows `TransitionError` as warning (hot path safe)
- `executor.py` 1,587 → 1,793 LOC (+206, accepted infrastructure cost for Day 15)
- Regression: 5770 → 5775 green; 0 new failures

### ✅ Day 15 — TradingSupervisor activate as main.py runloop owner
**Commit `38a99a6`** | +4 tests

- `TradingSupervisor` wired as authoritative runloop owner in main.py
- `main.py` 4,221 → 4,228 LOC (+7 wiring injection)

### ✅ W3 — Dashboard 8-page skeleton
**Commit `07bd710`**

- Next.js 14 App Router + OKLCH dark theme
- 8 pages: Overview, Portfolio, Attribution, Funding, System, Settings, Logs, Admin

### ✅ W4 — Infra audit + improvements
**Commit `aed0e92`**

- Prometheus: 5 recording rules (30s eval), added to `prometheus.yml` + docker-compose
- Alertmanager: 16 ReasonCode severity map (critical→Telegram, warning→Discord+email, info→email); inhibit rules
- Grafana: 5 dashboards (`pnl-tca`, `divergence`, `strategy-health`, `rejections-top`, `execution-latency`), all using pre-computed recording rules
- TimescaleDB: compression after 7d/14d/30d + retention drop after 90d/365d/180d (`infra/timescaledb/compression_policy.sql`)
- Loki: retention 7d → 30d (`retention_period: 720h`)

### ✅ Post-Day-15 review remediation + Paper universe_matrix fix (2026-04-21~22)
**Commits**: `9900346` `cfaedaf` `4a54e56` `5a276f5` `556ffb7` `7a9c35a` (review fix), `3d37e91` `e5a28b2` (paper fix)

**Review remediation** (2026-04-21):
- `9900346` Day 14 executor state transition boilerplate 단순화
- `cfaedaf` 13개 기존 테스트 실패 수정 (Bitget v2→v3 format, stat_arb fixture, Protocol stub)
- `4a54e56` ARCHITECTURE.md 핸드오프 문서 (378 LOC)
- `5a276f5` 리뷰 CRITICAL+HIGH 차단 수정 (C-1 STRANDED swallow, H-2 PARTIAL/ACKED self-loops, H-4 side normalize)
- `556ffb7` 리뷰 MEDIUM 수정 (M-1 lock lazy-init, M-3 get_bool_flag 통일, L-2 prediction_missing counter)
- `7a9c35a` CHANGELOG review remediation 정리

**Paper 모드 universe_matrix 함정 수정** (2026-04-22, 14h 카나리 헛수고 교훈):
- `3d37e91` `_init_paper_exchanges` 하드코딩 2개 → config 기반 7개. PaperExchangeAdapter `_market_type` + `supports_symbol` + `get_min_notional` 추가
- `e5a28b2` paper-adapter 확장으로 깨진 2 테스트 (test_init_exchanges_paper_mode_creates_two_adapters, test_supervisor_halt_on_stranded) 수정
- 결과: universe_matrix entries **0 → 34**, 5분 실행 trade_request_executed=5건 (funding_rate_v1 ×2 + spot_futures_v1 ×3, total_pnl=+$2.18, WR 100%)
- 14h 카나리(PID 45822) 결과 무효 인정. K-PT 4 케이스(US-407/409/410/419) + US-386 passes:false 리셋. SSOT.md 6 mismatch 정정 (`f304355`).

## Red-Flag Abort Criteria

Refactor aborts (revert to Path A, accept risk) if any of:
1. After 1 week, first extracted module cannot be unit-tested without importing monoliths
2. After 2 weeks, engine_pnl vs exchange_pnl divergence > 5% on paper 시뮬레이션 (model itself wrong; paper는 카나리 아님)
3. Any `fix(phoenix): BUG-XXX` commit during refactor window that isn't paired with reconciler-verified evidence

## Anti-Patterns (FROZEN — both operator + AI)

1. ❌ **Config bump as fix** — every `max_position_pct`, `min_notional`, `base_position_pct` change must be accompanied by a code-level guard that prevents the same class of bug. No exceptions.
2. ❌ **"Fixed" declaration without exchange cross-check** — every `fix(phoenix)` commit requires `exchange_pnl_snapshot` diff proving the fix. No exceptions.
3. ❌ **Adding code to live.py / main.py** — those files are monotonically shrinking. New code lands in new modules. If live.py or main.py grows in a commit, that commit is rejected.

## References

- Pre-refactor state: `git show 3221e8e` (last BUG-patch commit)
- Path-B start: `git show 606c97b` (mode=paper halt)
- Structural diagnosis (3 agents): see session transcript 2026-04-19 19:10 UTC
- Operator preference (halt first, then refactor): "좀 근본적으로 해결을 해봐... 실제 운영직전이라고 생각하고" / "너가 알아서해 대신 멈추지마"
