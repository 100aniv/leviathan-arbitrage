# Changelog

All notable changes to LEVIATHAN are documented here per [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) spec.

## [Unreleased] — Path-B v2 refactor in progress

### Added
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
- Day 10 — `MarketStats` real 24h ADV aggregator (`src/core/market_stats.py`). Rolling 24h USD-volume window per (exchange, symbol) sourced from WS trade events, behind feature flag `CORE_REAL_ADV_ENABLED` (default `false`). `signal.py::_compute_dynamic_adv` switches from the top-5 depth proxy to the real aggregate when the pair is warm (≥15min of data); falls back to proxy otherwise so behaviour is byte-identical by default.
- Day 6 — `ExecutionJournal` durable append-only event-sourcing substrate (`src/execution/journal.py`, ~530 LOC). SQLite-WAL log with per-event SHA256 hash chain (`self_hash = SHA256(prev_hash | order_id | state | canonical_json(payload))`, genesis `"0"*64`). Provides `append()`, `replay(since_ts_ms, order_id)`, `verify_chain()`, `current_hash()`, `pragma_snapshot()`, plus `get_execution_journal()` singleton. Behind feature flag `EXECUTION_JOURNAL_ENABLED` (default `false`) — flag OFF is a full no-op: no DB file created, `append()` returns NOOP sentinel, `replay()` returns `[]`. Uses `aiosqlite` when installed, stdlib `sqlite3 + asyncio.to_thread` fallback otherwise. Foundation for Day 7 `OrderStateMachine` and Day 14 executor migration. Includes 12 unit tests (`tests/unit/execution/test_journal_append.py`, `test_journal_crash_recovery.py`) covering genesis, chain linking, tamper detection, 40-way concurrency, flag-off behaviour, post-restart replay, corruption recovery, WAL+synchronous pragmas. New Prometheus metrics `leviathan_execution_journal_events_total{state}` and `leviathan_execution_journal_write_latency_ms`. `live.py`, `main.py`, `executor.py` unchanged (monotonic shrink invariant preserved).
- Day 8 — `OrderRouter` thin adapter boundary with idempotency + optional SENT journal hook (`engine/src/execution/router.py`, 225 LOC). Provides a stable `submit(order, adapter, trace_id, leg_index) → RouteResult` contract that formats `client_order_id = f"{trace_id}.{leg_index}"` (plan §3.4), deduplicates retries within a 10-minute in-memory TTL cache, and (when a Day 7 `OrderStateMachine` is injected) emits a `PENDING → SENT` transition before the adapter call. Behind feature flag `EXECUTION_ROUTER_ENABLED` (default `false`) — flag OFF performs a pure bypass with zero behaviour change (direct `adapter.place_order` call, no dedup, no journal). `asyncio.Lock` serialises dedup read-modify-write; adapter call happens outside the lock so distinct `client_order_id`s do not serialise. If the adapter raises, the exception propagates and no dedup entry is recorded (retry safe). Includes 7 unit tests (`engine/tests/unit/execution/test_order_router.py`) covering flag-off bypass, basic submit, dedup cache hit, TTL eviction, `client_order_id` format, state-machine SENT emission, and adapter-raise semantics. `live.py`, `main.py`, `executor.py`, `atomic.py` unchanged (monotonic shrink invariant preserved). Day 14 migrates the legacy executor onto this substrate.
- Day 7 — `OrderStateMachine` explicit 9-state order lifecycle layered over Day 6 journal (`src/execution/order_state.py`, ~226 LOC). States: `PENDING`, `SENT`, `ACKED`, `PARTIAL`, `FILLED`, `CANCELLED`, `REJECTED`, `ROLLED_BACK`, `STRANDED`. Declarative `_LEGAL_TRANSITIONS` map — `FILLED`/`CANCELLED`/`REJECTED`/`ROLLED_BACK`/`STRANDED` are terminal (empty outgoing sets). Every legal transition emits exactly one hash-chained `ExecutionEvent` via `journal.append()`; illegal transitions raise `TransitionError`. Behind feature flag `EXECUTION_STATE_MACHINE_ENABLED` (default `false`); `__init__` enforces §22.3 Flag Interaction Matrix dependency (requires `EXECUTION_JOURNAL_ENABLED=true`) via `ConfigError`. Flag-off `transition()` returns `None`, writes nothing, and does NOT raise on illegal (from,to) — full no-op. Includes 9 unit tests across `tests/unit/execution/test_order_state.py` (7 tests: legal path, illegal rejection, journal emission per transition, flag-off no-op, STRANDED terminal, current_state none, flag-dep ConfigError) and `tests/unit/execution/test_order_state_replay_convergence.py` (2 tests: two consumers converge on identical end state; concurrent transitions on distinct order_ids serialise via journal seq). STRANDED payload carries `{exchange,symbol,side,size,value_usd,reason}` for Day 14 `StrandedPositionTracker` forwarding (no direct coupling in Day 7). `live.py` (3,252) / `main.py` (4,221) / `executor.py` / `atomic.py` unchanged — monotonic shrink invariant preserved.

### Changed
- **`mode=live` → `mode=paper`** (commit `606c97b`) — halted live trading after v237 canary confirmed $5.01 engine-vs-Binance PnL divergence
- **"Commercial-grade transition 완료" declaration retracted** — 4 independent reviews (Codex/Gemini/exa.ai/external critic) identified structural defects
- `live.py` 3,476 → 3,249 LOC (−227, Day 2 PreTradeValidator extraction)
- Migration order reversed per Codex: execution-boundary first, lifecycle shell last

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
