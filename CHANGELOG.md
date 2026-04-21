# Changelog

All notable changes to LEVIATHAN are documented here per [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) spec.

## [Unreleased]

### Added
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
