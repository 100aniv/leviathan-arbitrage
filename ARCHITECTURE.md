# LEVIATHAN — Path-B v2 Architecture

> **Audience**: A future contributor (or future-self in 6 months) who needs to understand
> what Path-B v2 did, how every new module fits together, and where to extend the engine.
>
> **Status**: Day 0-15 + W3 + W4 complete as of 2026-04-21. Live trading BLOCKED pending
> 48-hour paper canary Gate (see §8).

---

## 목차 / Table of Contents

1. [Background — Why Path-B v2?](#1-background--why-path-b-v2)
2. [High-Level Request Flow](#2-high-level-request-flow)
3. [Module Inventory](#3-module-inventory)
4. [Feature Flag Dependency Matrix](#4-feature-flag-dependency-matrix)
5. [Runtime Data Flow — Order Lifecycle](#5-runtime-data-flow--order-lifecycle)
6. [Persistence](#6-persistence)
7. [Reconciliation Cycle](#7-reconciliation-cycle)
8. [Gate Criteria — Live Re-Enable](#8-gate-criteria--live-re-enable)
9. [Extension Points](#9-extension-points)
10. [References](#10-references)

---

## 1. Background — Why Path-B v2?

In April 2026, canary v237 reported engine PnL of **+$0.09** while Binance
`/fapi/v1/income` showed **-$4.92** over the same 24-hour window. Four independent
reviews (Codex, Gemini, exa.ai, external critic) converged on the same diagnosis: the
gap was not a single bug but the accumulated cost of a God-class monolith. `live.py`
had grown to 3,476 lines; `main.py` to 4,221 lines. Order state was tracked with
scattered booleans and `logger.warning` calls rather than an explicit state machine.
Cross-exchange legs were sequential (200-480 ms naked exposure window). The slippage
feedback collector silently read `_pred_bps=0.0` because a two-line wiring connection
was never made. Three other options were formally evaluated and rejected:

| Option | Verdict |
|--------|---------|
| Continue BUG-patch loop | Rejected — 13+ bugs in one session; marginal patch cost exceeds refactor cost |
| Full V4 Rust rewrite | Rejected — problem is correctness, not speed; 3-6 months, zero P&L gain |
| Redis removal from hot path | Rejected — Redis is not in hot path; removing it loses crash-recovery guarantee |

Path-B v2 chose the narrowest path: replace only the orchestration layer
(`live.py` + `main.py` + `executor.py`) with six purpose-built modules while keeping
all adapters, strategies, and friction models untouched.

---

## 2. High-Level Request Flow

```
  Exchanges (11 WS)
  ─────────────────
  Binance / Binance-Fut  ──md──►┐
  Bitget  / Bitget-Fut   ──md──►│
  Upbit / Bithumb / Coinone─md──►│   ┌─────────────────────────────────────────────┐
  Bybit / OKX / OKX-Fut  ──md──►│   │              LEVIATHAN Engine               │
                                 │   │                                             │
                                 └──►│  MarketDataCollector                        │
                                     │   └─► PriceHub (in-process, ≤3 ms)          │
                                     │           │ PriceTick                       │
                                     │           ▼                                 │
                                     │  StrategyRuntime (7 strategies)             │
                                     │   └─► SignalGenerator ──► RawSignal         │
                                     │                │                            │
                                     │                ▼                            │
  Config (engine.json) ─────reload──►│  PreTradeValidator  (11 gates)              │──orders──► Exchanges
                                     │   │ approved/rejected                       │  (REST)
                                     │   │ ReasonCode + metric                     │
                                     │   ▼                                         │
  Operator (CLI/Telegram) ──ctl─────►│  OrderRouter (idempotent, client_order_id)  │──metrics─► Prometheus
                                     │   └─► ExchangeGateway (11 adapters)         │──events──► TimescaleDB
                                     │              │ fill ACK                     │──streams─► Redis
                                     │              ▼                              │──alerts──► Telegram
                                     │  OrderStateMachine (9 states)               │──report──► Daily CSV
                                     │   └─► ExecutionJournal (SQLite-WAL, hashed) │
                                     │                                             │
                                     │  PnLReconciler ◄── ExchangePnLSnapshot      │
                                     │   └─► StrategyBudgetLedger                 │
                                     │   └─► RiskEngine (KillSwitch/CB/RG-11)     │
                                     └─────────────────────────────────────────────┘
```

Key boundaries introduced by Path-B v2:

- **PreTradeValidator**: every signal passes 11 typed gates before any adapter is
  called. No bypass path.
- **OrderRouter**: thin wrapper that assigns `client_order_id = f"{trace_id}.{leg_idx}"`
  and deduplicates retries within a 10-minute cache window.
- **OrderStateMachine**: explicit 9-state lifecycle; illegal transitions raise
  `TransitionError`; every legal transition writes one hash-chained event to the
  `ExecutionJournal`.
- **ExecutionJournal**: append-only SQLite-WAL store with SHA-256 hash chain.
  Authoritative record of every order lifecycle event.

---

## 3. Module Inventory

All 16 modules shipped across Day 1-15. "Day" refers to the Path-B v2 commit sequence.

| Module | Source path | ~LOC | Responsibility | Day committed |
|--------|-------------|-----:|----------------|:-------------:|
| `PnLLedger` | `src/reconciliation/pnl_ledger.py` | 220 | Single authority for operator-facing PnL; reads from exchange income, never from `_stats.total_pnl` | Day 1 |
| `PnLReconciler` | `src/reconciliation/pnl_reconciler.py` | 397 | Engine vs exchange divergence monitor; WARN at $0.50×3, CRITICAL + kill_switch at $1.00×3 | Day 1 |
| `ExchangePnLSnapshot` | `src/reconciliation/exchange_pnl_snapshot.py` | 600 | Polls Binance `/fapi/v1/income` + Bitget `/account/bill` every 60 s; persists to TimescaleDB (JSON fallback) | Day 1 |
| `UniverseMatrix` | `src/core/universe_matrix.py` | 423 | Boot-time valid `(strategy, symbol, leg_a, leg_b)` matrix; immutable post-boot; blocks BUG-225 class of delisted-symbol signals | Day 2 |
| `PreTradeValidator` | `src/execution/pre_trade_validator.py` | 619 | 11 typed gates (kill_switch, circuit_breaker, risk_guardian, notional_bump, dedup, …); every reject emits `ReasonCode` + Prometheus counter + INFO log | Day 2 |
| `ReasonCode` | `src/core/reason_codes.py` | ~80 | 16-value enum covering all rejection and halt reasons; stable public surface used by alerting, Grafana, Telegram | Day 2 |
| `StrategyBudgetLedger` | `src/risk/strategy_budget_ledger.py` | 637 | Per-strategy independent daily loss budget from exchange income only; UTC 00:00 reset; strategy auto-halts on breach; other strategies continue | Day 3 |
| `DailyReconciliationReport` | `src/reconciliation/daily_report.py` | 558 | UTC 00:05 22-column CSV + Telegram template; 6-item variance decomposition (commission, funding, slippage, FX, rollback, unattributed) | Day 3 |
| `ConfigService` | `src/core/config_service.py` | 484 | Pydantic `EngineConfig` schema with 15 nested models; dotted-path accessor; `asyncio` `on_change` broadcast; process-wide singleton | Day 4 |
| `TradingSupervisor` | `src/core/supervisor.py` | 498 | Boot sequence owner (DB → Redis → exchanges → UniverseMatrix → background tasks → signal handlers); idempotent 30-second shutdown; Day-15 runloop owner | Day 4 / Day 15 |
| `StrategyRegistry` | `src/core/strategy_registry.py` | 621 | Reads `strategy_activation.json`; binds UniverseMatrix; subscribes to BudgetLedger / CircuitBreaker events for runtime deactivation | Day 4 |
| `ExecutionJournal` | `src/execution/journal.py` | ~530 | Append-only SQLite-WAL with SHA-256 hash chain (`self_hash = SHA256(prev_hash|order_id|state|payload)`); `verify_chain()` tamper detection; genesis hash `"0"*64`; `aiosqlite` with stdlib fallback | Day 6 |
| `OrderStateMachine` | `src/execution/order_state.py` | ~226 | 9-state explicit lifecycle (PENDING, SENT, ACKED, PARTIAL, FILLED, CANCELLED, REJECTED, ROLLED_BACK, STRANDED); declarative `_LEGAL_TRANSITIONS`; terminal states have empty outgoing sets | Day 7 |
| `OrderRouter` | `src/execution/router.py` | 225 | `submit(order, adapter, trace_id, leg_index) → RouteResult`; formats `client_order_id`; 10-minute in-memory dedup cache; optional `PENDING → SENT` journal hook when `OrderStateMachine` is injected | Day 8 |
| `MarketStats` | `src/core/market_stats.py` | ~180 | Rolling 24-hour USD-volume window per `(exchange, symbol)` from WS trade stream; replaces top-5-depth ADV proxy; 15-minute warmup before activation | Day 10 |
| `CrossExchangeV2Executor` | `src/execution/cross_exchange_v2.py` | ~440 | Both legs concurrent via `asyncio.gather` with per-leg IOC TTL (default 5 s); handles SUCCESS / STRANDED_LEG1 / STRANDED_LEG2 / NEITHER / ROLLED_BACK outcomes; reduces naked-exposure window from 200-480 ms to p95 < 50 ms | Day 11 |

---

## 4. Feature Flag Dependency Matrix

All flags default to `false` in `.env`. Set `FLAG=true` to activate. Rollback = set
`false` and restart; no DB migration needed.

```
EXECUTION_JOURNAL_ENABLED ──────────────────────────────────────► Day 6
        │
        └── EXECUTION_STATE_MACHINE_ENABLED ──────────────────►  Day 7
                   │
                   │  EXECUTION_ROUTER_ENABLED ─────────────────► Day 8
                   │           │
                   │           └──┬── EXECUTION_PARALLEL_LEGS_ENABLED ► Day 11
                   └──────────────┘         (requires all three above)

CORE_REAL_ADV_ENABLED ──────────────────────────────────────────► Day 10
                   │
                   └── EXECUTION_PRETRADE_VALIDATOR_ENABLED ────► Day 12
```

Full table:

| Flag | Activates | Hard requires | Default |
|------|-----------|---------------|---------|
| `EXECUTION_JOURNAL_ENABLED` | SQLite-WAL journal, hash chain writes | — | `false` |
| `EXECUTION_STATE_MACHINE_ENABLED` | 9-state `OrderStateMachine` | `EXECUTION_JOURNAL_ENABLED` | `false` |
| `EXECUTION_ROUTER_ENABLED` | Idempotent `OrderRouter`, `client_order_id` formatting | — | `false` |
| `CORE_REAL_ADV_ENABLED` | Real 24h ADV from WS trade stream | — | `false` |
| `EXECUTION_PARALLEL_LEGS_ENABLED` | `CrossExchangeV2Executor` IOC-TTL gather | `EXECUTION_JOURNAL_ENABLED` + `EXECUTION_STATE_MACHINE_ENABLED` + `EXECUTION_ROUTER_ENABLED` | `false` |
| `EXECUTION_PRETRADE_VALIDATOR_ENABLED` | `PreTradeValidator.validate()` + BookWalk VWAP rejection | — | `false` |
| Gamma calibration cron | Nightly `calibrate_gamma.py` fitted to SlippageFeedbackCollector | — | `false` |

**Mis-configuration guard**: `OrderStateMachine.__init__` calls `ConfigService` and
raises `ConfigError` if `EXECUTION_STATE_MACHINE_ENABLED=true` without
`EXECUTION_JOURNAL_ENABLED=true`. `CrossExchangeV2Executor.__init__` enforces all three
prerequisite flags at construction time. These checks prevent silent no-ops.

---

## 5. Runtime Data Flow — Order Lifecycle

```
Signal born at StrategyGenerator
        │
        │  RawSignal {strategy, symbol, legs, edge_bps, trace_id}
        ▼
PreTradeValidator.validate()
        │
        ├── REJECT ──► ReasonCode + leviathan_signal_rejected_total{reason,strategy}
        │               + INFO log (no silent DEBUG rejects)
        │
        └── APPROVED
              │  ValidatedOrder
              ▼
        OrderRouter.submit(order, adapter, trace_id, leg_idx)
              │  client_order_id = f"{trace_id}.{leg_idx}"
              │  dedup check (10-min cache)
              │
              ├── OrderStateMachine.transition(NONE → PENDING)
              │    └── ExecutionJournal.append(PENDING event, hash-chained)
              │
              ├── [EXECUTION_PARALLEL_LEGS_ENABLED=false]
              │    Sequential: leg1 → leg2 (executor.py:1050, ~200-480 ms gap)
              │
              └── [EXECUTION_PARALLEL_LEGS_ENABLED=true]
                   asyncio.gather(leg1, leg2) with IOC TTL
                         │
         ┌───────────────┼───────────────────────┐
         ▼               ▼                       ▼
    PENDING           PENDING                PENDING
       │                 │                      │
  adapter call       adapter call           (TTL expires)
       │                 │                      │
       ▼                 ▼                      ▼
    SENT             SENT               CANCELLED (auto, no rollback)
       │                 │
       ▼                 ▼
    ACKED            ACKED
       │                 │
    FILLED           FILLED   ──► SUCCESS
    FILLED +          only  ──► STRANDED_LEG1 → StrandedPositionTracker.register()
     error     ──► ROLLED_BACK (reverse market order, concurrent)

State machine terminal states (no outgoing transitions):
  FILLED | CANCELLED | REJECTED | ROLLED_BACK | STRANDED

Every legal transition writes one hash-chained ExecutionEvent to the journal.
Illegal transitions raise TransitionError.
```

---

## 6. Persistence

### TimescaleDB (primary)

| Table | Populated by | Compression | Retention |
|-------|-------------|-------------|-----------|
| `exchange_pnl_snapshots` | `ExchangePnLSnapshot` (60 s poll) | after 30 d | 180 d |
| `pnl_events` | `PnLAttributor` (per fill) | after 14 d | 365 d |
| `positions` | `PositionRegistry` (dual-write WAL) | after 7 d | 90 d |
| `execution_events` | `ExecutionJournal` (per state transition) | after 7 d | 90 d |
| `strategy_budgets` | `StrategyBudgetLedger` (per deduction) | — | 90 d |
| `orderbook_updates` | `MarketDataCollector` (WS tap) | after 7 d | 90 d |

Compression settings: `infra/timescaledb/compression_policy.sql`. Retention is applied
automatically by TimescaleDB scheduler.

### JSON Fallback Paths

When TimescaleDB is unavailable the engine falls back to local JSON:

| Data | Fallback path |
|------|--------------|
| Exchange PnL snapshots | `logs/pnl_snapshots/YYYYMMDD.json` |
| Strategy budgets | `logs/strategy_budgets/YYYYMMDD.json` |
| Daily reconciliation report | `logs/daily_recon/YYYYMMDD.csv` |
| Execution journal | Not applicable — journal is SQLite, not TimescaleDB |

### SQLite (ExecutionJournal)

The execution journal uses a dedicated SQLite-WAL file (path configured in
`engine.json`, defaults to `logs/execution_journal.db`). It is independent of
TimescaleDB so that order lifecycle events survive a DB outage. On restart the engine
calls `journal.replay(since_ts_ms=last_shutdown_ts)` to reconstruct in-flight order
states before accepting new signals.

---

## 7. Reconciliation Cycle

The engine vs exchange PnL reconciliation runs continuously in a background task.

```
Every 60 seconds:
  ExchangePnLSnapshot
    ├── Binance:  GET /fapi/v1/income?startTime=...
    └── Bitget:   GET /api/v3/account/financial-records
          │
          ▼  ExchangePnLSnapshot persisted to TimescaleDB
  PnLReconciler
    ├── engine_pnl  = PnLLedger.get_live_pnl_usd()  (exchange income source)
    ├── exchange_pnl = latest ExchangePnLSnapshot.total_usd
    ├── delta = abs(engine_pnl - exchange_pnl)
    │
    ├── delta > $0.50 for 3 consecutive cycles → WARN log + Telegram
    └── delta > $1.00 for 3 consecutive cycles → CRITICAL + KillSwitch.halt()

UTC 00:05 daily:
  DailyReconciliationReport
    ├── 22-column CSV written to logs/daily_recon/YYYYMMDD.csv
    ├── 6-item variance decomposition:
    │     commission_mismatch | funding_mismatch | slippage_mismatch
    │     fx_mismatch | rollback_mismatch | unattributed (< $0.10 expected)
    └── Telegram summary message via TradeBot
```

**Design rule**: `PnLLedger.get_live_pnl_usd()` always reads from `ExchangePnLSnapshot`
records. The legacy `_stats.total_pnl` field (computed internally) is deprecated and
must not be used for operator-facing display.

---

## 8. Gate Criteria — Live Re-Enable

Live trading remains halted (`mode=paper`, commit `606c97b`) until all seven criteria
pass simultaneously over a 48-hour paper canary with all feature flags enabled.

| # | Criterion | Target |
|---|-----------|--------|
| 1 | 48-hour paper canary | Zero stranded leaks, zero unintended halts |
| 2 | Slippage prediction accuracy (Day 9 + 13) | predicted vs actual p95 < 20 bps; R² > 0.6 |
| 3 | Real ADV accuracy (Day 10) | Engine ADV ±15% of Binance REST `/api/v3/ticker/24hr` for top-10 symbols over 2-hour window |
| 4 | Parallel legs naked exposure (Day 11) | p95 < 50 ms over ≥50 cross-exchange trades |
| 5 | Journal replay completeness (Day 14) | Replay matches live `total_pnl` ±$0.50 for 24-hour window |
| 6 | Exchange PnL cross-check | Engine 24-hour PnL vs `/fapi/v1/income` 24-hour sum within ±$1.00 |
| 7 | Live micro-canary | 30-minute live canary at $50 notional / trade, max 5 trades, green on all above |

To check current Gate status: `engine/.omc/state/mission-state.json`.

---

## 9. Extension Points

### 9.1 Adding a New Exchange

1. Create `src/adapters/<exchange>_adapter.py` implementing `AdapterProtocol`
   (`src/execution/pre_trade_validator.py` lines 59-78 define the contract: `place_order`,
   `cancel_order`, `get_positions`, `get_orderbook`).
2. Add the exchange to `UniverseMatrix` (`src/core/universe_matrix.py`) — the matrix is
   loaded at boot and is immutable afterward. Changing it requires a restart.
3. Add fee and withdrawal cost rows to `src/friction/fee_model.py` `WITHDRAWAL_FEES_USD`
   and the fee table in `SSOT.md §4.2`.
4. Wire the WS connection in `src/core/market_data_collector.py` following the pattern of
   existing adapters.
5. Add the exchange key to `engine.json` under `exchanges` and to `engine/.env`.
6. Add at least one `(strategy, symbol, exchange_a, exchange_b)` entry in
   `config/strategy_activation.json` to make the engine subscribe to signals for this
   exchange.
7. Run `python -m pytest tests/ -x --tb=short` — the adapter contract tests in
   `tests/unit/adapters/` will validate the new adapter against the protocol.

KRW exchange note: Upbit, Bithumb, and Coinone use KRW pairs and require
`auto_symbols.min_exchanges=3` (not 7) in `engine.json`. Do not add them as
`leg_b_exchange` for a cross-exchange strategy without confirming fiat withdrawal cost
(L1-only, ~$2.50-$4.50 per ETH vs $0.06-$0.19 for Arbitrum-capable global exchanges).

### 9.2 Adding a New Strategy

1. Create `src/strategies/<name>.py` implementing `StrategyProtocol`
   (`src/core/strategy_registry.py` defines the bind interface).
2. Register the strategy in `config/strategy_activation.json`.
3. Add a `StrategyBudgetLedger` entry in `engine.json` under `risk.per_strategy_daily_loss_budget_pct`.
4. Add the strategy to `UniverseMatrix` valid pairs.
5. Implement unit tests verifying signal emission, cooldown behavior, and that the
   strategy does not emit signals for pairs not in `UniverseMatrix`.

### 9.3 Adding a New Reconciliation Layer

1. Create a module under `src/reconciliation/`.
2. Implement `ExchangeIncomeSource` protocol (poll interval, `fetch() → IncomeRecord`).
3. Register it with `ExchangePnLSnapshot` in `main.py` (one DI line).
4. Add a TimescaleDB schema file under `src/reconciliation/schema_<name>.sql`.
5. Extend `DailyReconciliationReport` variance decomposition if the new source introduces
   a new mismatch category.

### 9.4 Adjusting Feature Flag Activation Order

The dependency matrix (§4) is enforced at construction time by `ConfigService`. To add a
new flag:

1. Add the env var to `.env.example` with a comment explaining what it gates.
2. Add a `ConfigError` guard in the new module's `__init__` that reads
   `ConfigService.current` and raises if a prerequisite flag is not set.
3. Document the new flag in `§22.3` of the plan and in this file's §4 table.

---

## 10. References

| Document | Path | Purpose |
|----------|------|---------|
| SSOT.md | `/SSOT.md` | Sole design authority; read before every session |
| Module Design | `engine/docs/MODULE_DESIGN.md` | Interface contracts, invariants, concurrency model |
| Refactor Plan | `engine/docs/REFACTOR_PLAN.md` | Day-by-day commit history and LOC deltas |
| Path-B v2 Plan | `/Users/100aniv/.claude/plans/hidden-cuddling-pascal.md` | Gate criteria §5, flag interaction matrix §22.3, critical path §4 |
| CHANGELOG | `/CHANGELOG.md` | Tagged release notes per Keep a Changelog 1.1.0 |
| Operator Runbook | `engine/docs/OPERATOR_RUNBOOK.md` | Daily operator checklist, 16 ReasonCode dictionary |
| Math Models | `.claude/rules/math-models.md` | Slippage, fee, risk, Sharpe, MDD formulas (mirror of SSOT.md §4) |
| PRD | `engine/.omc/prd.json` | 437 user stories; `passes:true` requires runtime call evidence |

---

*Generated 2026-04-21. Maintained by the `ssot-keeper` agent; update alongside any
change to `SSOT.md §2` (Path-B commit table) or `MODULE_DESIGN.md §7`.*
