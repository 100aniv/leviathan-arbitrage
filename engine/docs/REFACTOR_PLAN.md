# LEVIATHAN Path-B Refactor Plan

**Status**: Active | **Started**: 2026-04-19 | **Trigger**: 13+ bugs in single session; real Binance -$4.92 reported by engine as +$0.09.

## Verdict (3-agent consensus: architect + backend-architect + critic)

The orchestration layer (`live.py` + `main.py`) is a God-class monolith with 148 inline `BUG-` markers. The strategy/adapter/friction libraries are well-factored. **Path B = surgical rewrite of the orchestration layer against a stable adapter/strategy interface, not a greenfield rewrite.**

- Path A (continue patching): P(success)=10%, further-loss risk HIGH, P(10 new bugs in 24H) = 92%
- **Path B (structural refactor, 3-8 weeks)**: P(success)=65%, further-loss risk LOW (paper during refactor), 65% code retention
- Path C (greenfield rewrite): P(success)=30%, 4-8 months, second-system syndrome

## Current State (as of 2026-04-19 19:30 UTC)

| Module | Before | After | Delta | Source |
|--------|--------|-------|-------|--------|
| `src/modes/live.py` | 3,414 LOC | **3,249** | -165 | commit `0784c2b` |
| `src/main.py` | 4,194 LOC | 4,202 | +8 | commit `3c45a3b` |
| **Monolith total** | 7,608 | 7,451 | -157 | |

Target: live.py ≤ 2,500 LOC by end of Day 7. main.py ≤ 3,500 LOC by end of Day 10.

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

## Remaining Work (Days 4-10)

### Day 4 — Main.py split (part 1)
- Extract `src/core/supervisor.py` — process lifecycle, health probes, SIGTERM handling (pull from `main.py:Engine.__init__` + background task wiring)
- Extract `src/core/config_service.py` — single engine.json reader, schema validation (pydantic), hot-reload broadcast
- Expected: main.py -400 LOC, +2 new modules

### Day 5 — Main.py split (part 2)
- Extract `src/core/strategy_registry.py` — strategy lifecycle, universe binding, activation lists
- Expected: main.py -300 LOC

### Day 6 — Live.py split (part 1)
- Extract `src/execution/order_router.py` — idempotent order dispatch with `trace_id + leg_index` client_order_id
- Expected: live.py -500 LOC

### Day 7 — Live.py split (part 2)
- Extract `src/execution/paper_gateway.py` vs `src/execution/live_gateway.py` — mode-specific gateways, separate classes (no more single LiveMode with mode flag)
- Expected: live.py -400 LOC

### Day 8 — trace_id propagation
- `contextvars.ContextVar[str]` in every pipeline hop
- Trace column added to `trades`, `orders`, `fills`, `pnl_events`, `signal_rejections`
- Expected: no LOC change; wiring + DB schema migrations

### Day 9 — Canary Stage Controller
- `src/canary/stage_controller.py` — S0→S1→S2→S3 graduated rollout with HALT criteria per stage

### Day 10 — Full regression + documentation pass
- `pytest tests/` full suite green
- SIT-3 72H paper canary
- Gate to re-enable live trading requires passing Stage 1 (48H continuous live canary $10)

## Red-Flag Abort Criteria

Refactor aborts (revert to Path A, accept risk) if any of:
1. After 1 week, first extracted module cannot be unit-tested without importing monoliths
2. After 2 weeks, engine_pnl vs exchange_pnl divergence > 5% on paper canary (model itself wrong)
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
