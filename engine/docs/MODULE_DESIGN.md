# LEVIATHAN — Module Design (MD)

| Field | Value |
|---|---|
| Document | `engine/docs/MODULE_DESIGN.md` |
| Version | 0.1 (§1-§4 draft, pending exa.ai industry benchmark + codex/gemini independent review) |
| Date | 2026-04-19 KST |
| Status | DRAFT — architecture approval pending |
| Authors | Architect (OMC), Path-B Day 1–5 authors |
| Reviewers | Operator, Critic, backend-architect, pending Codex + Gemini |
| Supersedes | implicit architecture inside `src/main.py` + `src/modes/live.py` |
| Related | `engine/docs/REFACTOR_PLAN.md`, `SSOT.md §3`, `SSOT.md §4` |
| Scope | §1 Architecture + Data Flow, §2 Responsibility Matrix, §3 Interface Contracts, §4 Migration Plan |

---

## §1. Architecture and Data Flow

### 1.1 Context Diagram (C4 L1)

```
                    ┌────────────────────────────────────────┐
                    │              LEVIATHAN Engine          │
                    │   (single Python process + Rust PYO3)  │
                    └────────────────────────────────────────┘
IN                                                                           OUT
──                                                                           ───
Exchanges (11 WS):                     │  engine  │   Exchanges (REST):
  Binance / Binance-Fut  ──── md ────► │          │ ── orders ─► Binance/Bitget/…
  Bitget / Bitget-Fut    ── fills ───► │          │ ── cancels ►
  Upbit / Bithumb / Coinone            │          │
  Bybit / OKX / OKX-Fut  ── funding ─► │          │
                                       │          │
Exchange income feeds (poll):          │          │
  /fapi/v1/income, /account/bill ────► │          │   TimescaleDB (WAL):
                                       │          │ ── positions / pnl_events /
Operator (CLI, Telegram cmd) ───ctl──► │          │    exchange_pnl_snapshots ─►
                                       │          │
Config (engine.json git) ──reload────► │          │   Redis Streams: OB/24h ─►
                                       │          │
                                       │          │   Prometheus (scrape) ────►
                                       │          │   Grafana dashboards ────►
                                       │          │   Alertmanager / Telegram ►
                                       │          │   Daily CSV report ──────►
                                       └──────────┘
```

External actors: 11 exchanges (md/trading), TimescaleDB (state), Redis (ephemeral), Prometheus + Grafana + Loki + Alertmanager, Telegram (ops alerts + ops commands), and a human operator.

**IN**: orderbook L2 + trades + funding (WS), fill ACKs (WS), income events (REST poll), operator commands, config file.
**OUT**: orders/cancels, structured metrics, structured logs, Telegram alerts, UTC-00:05 daily reconciliation CSV.

### 1.2 Container Diagram (C4 L2)

```
                        ┌──────────────────┐
                        │  ConfigService   │  (pydantic schema, on_change broadcast)
                        └────────┬─────────┘
                                 │ subscribe
             ┌───────────────────┼────────────────────────┐
             ▼                   ▼                        ▼
     TradingSupervisor    ExchangeGateway         ObservabilityPlane
     (boot order,      (REST+WS+auth+sym-map,     (Prom, structlog,
      signals,          token-bucket, 11 adps)     trace_id ctxvar,
      shutdown)                ▲                   daily_report)
             │                 │                       ▲
             │ owns            │ consumed by every     │ all events
             ▼                 │ network-side effect   │
   ┌──────────────────┐        │                       │
   │ MarketDataColl.  ├─WS md─►│                       │
   │  → PriceHub      │        │                       │
   └──────┬───────────┘        │                       │
          │ tick               │                       │
          ▼                    │                       │
   ┌──────────────────┐        │                       │
   │ StrategyRuntime  │  (FR, FF, SF, XE, TRI, STAT)   │
   │ + StrategyRegstr.│                                │
   └──────┬───────────┘                                │
          │ Signal                                     │
          ▼                                            │
   ┌──────────────────┐     reject (ReasonCode) ───────┤
   │ SignalPipeline   │─►PreTradeValidator─►OrderRouter│
   └──────┬───────────┘     11 gates       idempotent  │
          │ Order(legs)                     client_id  │
          ▼                                            │
          └──── ExchangeGateway ────► Exchange API     │
                       ▲ fill ACK                      │
                       │                               │
          ┌────────────┴────────────┐                  │
          ▼                         ▼                  │
 PositionReconciler        PnLAttributor ──► Strategy- │
 (engine vs exchange       (income-primary)  Budget-   │
  positions, divergence)   7-layer TCA       Ledger    │
          │                         │                  │
          └──► RiskEngine ◄─────────┘                  │
               (kill_switch, circuit_breaker,          │
                flash_guard, budget_ledger,            │
                reconciler HALT at 5% Δ) ──────────────┘
```

**Container map:** `ConfigService` (`src/core/config_service.py`), `ExchangeGateway` (facade over `src/adapters/*`), `MarketDataCollector` + `PriceHub` (`src/core/price_hub.py`), `StrategyRuntime` (`src/strategies/*`), `StrategyRegistry` (`src/core/strategy_registry.py`), `SignalPipeline` + `PreTradeValidator` (`src/execution/pre_trade_validator.py`) + `OrderRouter` (Day-6), `PositionReconciler` (today inside `live.py`, Day-6 target), `PnLAttributor` (`src/reconciliation/*`), `RiskEngine` (`src/risk/*` + `strategy_budget_ledger`), `ObservabilityPlane` (`src/infra/metrics.py`, structlog), `TradingSupervisor` (`src/core/supervisor.py`).

### 1.3 Data Flow — Signal Lifecycle

| # | Producer → Consumer | Payload | Failure mode | Latency budget |
|---|---|---|---|---|
| 1 | WS adapter → PriceHub | `OrderbookUpdate{ex,sym,bid,ask,ts,seq}` | stale detector → reject downstream | ≤3 ms |
| 2 | PriceHub → SignalGenerator | `PriceTick` per strategy subscription | None fires signal on stale tick | ≤1 ms |
| 3 | SignalGenerator → SignalPipeline | `RawSignal{strategy,symbol,legs,edge_bps}` | trace_id assigned via `contextvars`; missing id → drop + metric | ≤2 ms |
| 4 | SignalPipeline → PreTradeValidator | `TradeRequest` | 11 gates fail-fast; every reject → `ReasonCode` + `leviathan_signal_rejected_total{reason,strategy}` + INFO log | ≤3 ms |
| 5 | Validator(approved) → OrderRouter | `ValidatedOrder{legs, client_order_id=f(trace_id,leg_idx)}` | Bump path sets `NOTIONAL_BUMP_EXCEEDS_RISK` if cap violated | ≤1 ms |
| 6 | OrderRouter → ExchangeGateway | Per-leg REST/WS order | Rate limited → `RATE_LIMITED`; 429 → backoff + CB increment | ≤20 ms |
| 7 | ExchangeGateway → Exchange API | Signed POST / WS msg | Timeout/HTTP 5xx → `CircuitBreaker.record_failure()`, rollback leg 1 if only one filled | ≤150 ms p95 |
| 8 | Fill ACK → PositionReconciler | `FillEvent{order_id,qty,price,fee}` | Divergence >1% → WARN; >5% → engine HALT | ≤500 ms to DB |
| 9 | Fill → PnLAttributor | pairs with `/fapi/v1/income` next 60s poll | Unattributed >$0.10 → daily report `unattributed` col | 60 s poll |
| 10 | PnLAttributor → StrategyBudgetLedger | ΔPnL per strategy | Budget ≤0 → strategy-scoped HALT; other strategies unaffected | ≤10 ms |
| 11 | All events → ObservabilityPlane | log + metric by trace_id | Log write failure swallowed; metric failure swallowed with 1 CRITICAL/day | n/a |

The **trace_id** (`contextvars.ContextVar[str]`) is born at step 3 and propagates through every downstream record — structured log line, Prometheus exemplar, DB row, CSV report line.

### 1.4 Invariants (system-wide, MUST hold)

1. Operator-facing PnL (`PnLLedger.get_live_pnl_usd()`) is sourced from exchange income, never from `_stats.total_pnl`.
2. Every `TradeRequest` has exactly one `trace_id`; same `trace_id` never crosses requests.
3. `risk.max_position_pct` is a HARD cap with no bypass path (auto-bump included — `NOTIONAL_BUMP_EXCEEDS_RISK` rejects).
4. Every rejection increments exactly one `leviathan_signal_rejected_total{reason_code,strategy}` counter and emits one `live_mode.rejected_by_<code>` INFO log.
5. Engine PnL vs exchange PnL must reconcile within 5% for 3 consecutive 60 s cycles or the engine auto-HALTs (WS-D rule).
6. No mutation of a `PositionRegistry`, `PnLLedger`, or `StrategyBudgetLedger` without an event being emitted.
7. Every background `asyncio.Task` is created with `task.set_name(...)` for supervision.
8. The kill switch is honored before every network-side effect (`ExchangeGateway.send()` is the choke point).
9. DB migrations are additive; existing production tables are never dropped by runtime code.
10. `UniverseMatrix` is immutable post-boot; any re-binding requires a restart (prevents silent re-entry of delisted symbols).
11. A `fix(phoenix): BUG-…` commit requires an accompanying `exchange_pnl_snapshot` diff proving the fix.
12. `live.py` and `main.py` are monotonically shrinking until Day 10; any PR that grows them is rejected.

### 1.5 Concurrency Model

asyncio-first single event loop, Python 3.12 `TaskGroup`. Shared mutable state is replaced with **owned state + async queues**: `PnLLedger`, `StrategyBudgetLedger`, `PositionRegistry`, and `PreTradeValidator.dedup_gate` each have a single writer. The only `asyncio.Lock`s are on ledger-write paths and the budget deduction critical section. Rate limiting uses a shared per-exchange `TokenBucket` (5 rps / 10 burst). CPU-bound cost math lives in the Rust PyO3 hot-path and is called with `loop.run_in_executor` only when the call duration >200 µs.

### 1.6 Cross-Cutting Concerns

- **trace_id** via `contextvars.ContextVar[str]` (Day-8 work); current proxy is `TradeRequest.request_id`.
- **Metrics**: every Counter/Gauge has a stable `leviathan_*` name; labels follow a closed vocabulary (`exchange`, `strategy`, `symbol`, `reason_code`, `severity`).
- **Logging**: JSON structlog; every record has `module`, `trace_id`, `reason_code?`, `ts_utc`.
- **Error handling**: errors are never silently swallowed. Pattern is: catch → log (ERROR/CRITICAL) → metric (`leviathan_*_errors_total`) → re-raise or return typed `ErrorResult`. No bare `except: pass`.
- **Observability overhead budget**: <5% CPU at peak WS throughput (enforced by nightly perf test, Day-10).

### 1.7 What Gets Persisted

| Data | Store | Durability | TTL | Why |
|---|---|---|---|---|
| Positions | TimescaleDB `positions` | dual-write WAL | forever | operator audit + crash recovery |
| PnL events | TimescaleDB `pnl_events` | single-write | 90 d | TCA, daily reconciliation |
| Exchange income | TimescaleDB `exchange_pnl_snapshots` (+ JSON fallback) | single-write | 90 d | ground-truth PnL |
| Daily reports | `logs/daily_recon/YYYYMMDD.csv` | single-write | forever | audit trail |
| Config | `config/engine.json` | git-versioned | forever | change traceability |
| Strategy budgets | TimescaleDB `strategy_budgets` + JSON fallback | dual-write | 90 d | crash-safe budget resumption |
| Orderbook snapshots | Redis Streams | 24 h | 24 h | recent replay for post-mortem |
| Metrics | Prometheus TSDB | 30 d | 30 d | alerting + Grafana |
| Structured logs | Loki | 14 d | 14 d | debugging window |

### 1.8 Design Principles

1. **Exchange API is ground truth.** Engine calc is diagnostic only; the `PnLLedger` reads from `ExchangePnLSnapshot`.
2. **Pre-trade validation cannot be bypassed.** Every order path flows through `PreTradeValidator.validate()`.
3. **No direct `get_config()`.** All readers use `ConfigService.current` so reloads are atomic.
4. **Typed public surfaces.** Every cross-module call has a `Protocol` or dataclass — no dict-as-interface.
5. **Monotonic shrinkage during refactor.** Net LOC of `live.py`+`main.py` only decreases until Day 10.
6. **No code-level fix without reconciler evidence.** A "fixed" claim requires an `exchange_pnl_snapshot` diff.
7. **Single owner per mutable state.** One writer, many readers; updates flow through the owner's queue.

---

> §2-§4 appended from staging files `MODULE_DESIGN_SEC2.md`, `MODULE_DESIGN_SEC3.md`, `MODULE_DESIGN_SEC4.md` below.
## §2. Responsibility Matrix

Every `def` in `src/modes/live.py` (3,249 LOC) and `src/main.py` (4,221 LOC) mapped to target module. Legend: `DONE`=Day 1-5 done, `D6-D9`=pending Day, `DEAD`=delete, `KEEP`=shell.

### Table A — live.py (48 defs)

| Line | Function | Target | Tag |
|------|----------|--------|-----|
| 48 | LiveGateFailed | TradingSupervisor | D6 |
| 59-78 | ExecutorProtocol + 3 methods | OrderRouter contracts (§3) | D6 |
| 92 | PerStrategyStats | PnLAttributor/ObservabilityPlane | DONE/D9 |
| 104 | LiveModeStats | ObservabilityPlane | D9 |
| 170 | LiveMode (class) | Shell=TradingSupervisor | D6 |
| 183 | __init__ | SPLIT: Supervisor+ConfigService(DONE) | D6 |
| 525 | start | TradingSupervisor.run | D6 |
| 903 | _prewarm_connections | ExchangeGateway | D7 |
| 926 | _prewarm_one | ExchangeGateway | D7 |
| 943 | _reconcile_positions_on_startup | PositionReconciler | D7 |
| 1036 | stop | TradingSupervisor.stop | D6 |
| 1114 | _on_orderbook | StrategyRuntime.on_orderbook | D8 |
| 1297 | _route_signal_to_strategies | StrategyRuntime.route_signal | D8 |
| 1360 | _execute_trade_request (669 LOC) | SPLIT: PreTradeValidator(DONE)+OrderRouter+PnLAttributor(DONE)+RiskEngine | DONE/D6-7 |
| 2032 | _pm_err_cb | OrderRouter | D7 |
| 2092 | _execute_direct_signal | OrderRouter.execute_direct | D7 |
| 2130 | _is_reduceonly_request | OrderRouter util | D7 |
| 2140 | _legs_to_orders | OrderRouter | D7 |
| 2192 | _route_to_executor | OrderRouter.dispatch | D7 |
| 2256 | _notify_pre_exec_rollback | ObservabilityPlane.alert | D9 |
| 2294 | _halt_local | RiskEngine.halt | D6/DEAD |
| 2299 | _notify_session_loss_alert | ObservabilityPlane.alert | D9 |
| 2311 | _clear_strategy_pending_entry | StrategyRuntime | D8 |
| 2325 | _build_collision_key | OrderRouter | D7 |
| 2346 | _record_first_trade | ObservabilityPlane | D9 |
| 2374 | _compute_pnl_from_result | PnLAttributor | DONE |
| 2475 | _compute_pnl | PnLAttributor | DONE |
| 2480 | _update_pnl_stats | PnLAttributor+PnLLedger | DONE |
| 2536 | _publish_orderbook_for_observability | ObservabilityPlane | D9 |
| 2551 | _publish_trade_for_observability | ObservabilityPlane | D9 |
| 2564 | _pnl_divergence_monitor_loop | PnLReconciler | DONE |
| 2587 | _sum_exchange_income | PnLReconciler | DONE |
| 2667 | _perf_loop | ObservabilityPlane | D9 |
| 2701 | _funding_rate_loop | PnLAttributor | DONE |
| 2746 | _accrue_funding_cycle | PnLAttributor | DONE |
| 2844 | _lookup_mark_price | PnLAttributor util | DONE |
| 2867 | _lookup_funding_rate | PnLAttributor util | DONE |
| 2887 | _realize_funding_on_close | PnLAttributor | DONE |
| 2939 | _daily_summary_loop | DailyReport | DONE |
| 2956 | _send_summary | DailyReport | DONE |
| 2980 | _persist_stats | ObservabilityPlane+PnLLedger | DONE partial |
| 3005 | _krw_rate_loop | ObservabilityPlane | DEAD? |
| 3041 | _cleanup_collision_map | OrderRouter | D7 |
| 3051 | _dedup_cleanup_loop | OrderRouter | D7 |
| 3142 | _margin_refresh_loop | ExchangeGateway | D7 |
| 3183 | _trade_reconciler_loop | PositionReconciler | D7 |
| 3240-48 | stats/running/execution_mode | shell properties | KEEP |

### Table B — main.py (76 defs)

| Line | Function | Target | Tag |
|------|----------|--------|-----|
| 51 | _get_fallback_exchanges | ConfigService | DONE |
| 64 | DataMode | ConfigService | DONE |
| 73 | EngineState | TradingSupervisor | D6 |
| 80 | Engine (class) | TradingSupervisor shell | D6 |
| 96 | __init__ | TradingSupervisor.__init__ | D6 |
| 207 | run | TradingSupervisor.run | D6 |
| 241 | stop | TradingSupervisor.stop | D6 |
| 381 | _setup_signal_handlers | entrypoint | KEEP |
| 389 | _handle_signal | entrypoint | KEEP |
| 398 | _apply_trading_json_defaults | ConfigService | DONE |
| 401 | _setdefault | ConfigService | DONE |
| 441 | _init_config | ConfigService | DONE |
| 479 | _validate_config | ConfigService.validate | DONE |
| 524 | _resolve_symbols | UniverseMatrix | DONE |
| 583 | _init_infrastructure | TradingSupervisor.bootstrap | D6 |
| 641 | _init_database | TradingSupervisor.bootstrap | D6 |
| 723 | _init_telegram | ObservabilityPlane.init_alerts | D9 |
| 746 | _init_rust_bridge | TradingSupervisor.bootstrap | D6 |
| 755 | _init_tuner | StrategyRuntime.init_tuner | D8 |
| 767 | _tuner_reload_callback | StrategyRuntime | D8 |
| 803 | _init_exchanges | ExchangeGateway.init | D7 |
| 827 | _init_paper_exchanges | ExchangeGateway.init_paper | D7 |
| 862 | _init_sandbox_exchanges | ExchangeGateway.init_sandbox | D7 |
| 868 | _init_live_exchanges | ExchangeGateway.init_live | D7 |
| 882 | _init_native_exchanges | ExchangeGateway.init_native | D7 |
| 929 | _init_signal_pipeline | StrategyRuntime.init_signals | D8 |
| 1113 | _init_strategies | StrategyRegistry+StrategyRuntime | DONE/D8 |
| 1143 | _load_strategy_params | StrategyRegistry | DONE |
| 1160 | _load_activation_disabled_ids | StrategyRegistry | DONE |
| 1176 | _register_default_strategies | StrategyRegistry | DONE |
| 1261 | _strategy_max_pos | StrategyBudgetLedger | DONE |
| 1399 | _build_dex_adapter | ExchangeGateway.init_dex | D7 |
| 1436 | _init_risk | RiskEngine.init | D6 |
| 1443 | cb_state_callback | RiskEngine | D6 |
| 1623 | _init_execution | OrderRouter.init+PositionReconciler.init | D7 |
| 1760 | _auto_close_orphan | PositionReconciler | D7 |
| 1788 | _on_reconcile_discrepancy | PositionReconciler | D7 |
| 1833 | _build_risk_check_fn | RiskEngine.build_check | D6 |
| 1839 | risk_check | RiskEngine.check | D6 |
| 1922 | _on_execution_result | PnLAttributor+StrategyRuntime | DONE/D8 |
| 2114 | _on_exp_done | RiskEngine | D6 |
| 2281 | _rebalancer_loop | ExchangeGateway | D7 |
| 2314 | _cancel_open_orders | OrderRouter.cancel_all | D7 |
| 2345 | _close_all_positions_on_shutdown | OrderRouter.close_all+PositionReconciler | D7 |
| 2390 | _record_alert | ObservabilityPlane.record_alert | D9 |
| 2407 | _populate_context | TradingSupervisor | D6 |
| 2439 | _start_background_tasks | TradingSupervisor.start_loops | D6 |
| 2591 | _strategy_manager_loop | StrategyRuntime | D8 |
| 2600 | _trade_consumer_loop | OrderRouter | D7 |
| 2609 | _backtest_mode_task | shell+StrategyRuntime | KEEP/D8 |
| 2714 | _orderbook_feed_loop | ExchangeGateway.feed | D7 |
| 2724 | make_callback | ExchangeGateway | D7 |
| 2726 | on_orderbook | StrategyRuntime.on_orderbook | D8 |
| 2758 | _paper_signal_simulator_loop | — | DEAD |
| 2791 | _real_data_feed_loop | ExchangeGateway.feed | D7 |
| 2807 | on_orderbook (dup) | — | DEAD |
| 2897 | _live_mode_loop | TradingSupervisor.drive_live | D6 |
| 3004 | _regime_detect_loop | StrategyRuntime (ML) | D8 |
| 3037 | _adaptive_threshold_loop | StrategyRuntime | D8 |
| 3115 | _hmm_training_loop | StrategyRuntime (ML) | D8 |
| 3179 | _xgb_training_loop | StrategyRuntime (ML) | D8 |
| 3264 | _paper_mode_loop | TradingSupervisor.drive_paper | D6 |
| 3420 | _strategy_validation_loop | — | DEAD |
| 3502 | _progressive_shadow_loop | — | DEAD |
| 3619 | _health_check_loop | ObservabilityPlane.health | D9 |
| 3629 | _run_health_check | ObservabilityPlane.health | D9 |
| 3674 | _startup_position_scan | PositionReconciler.startup_scan | D7 |
| 3719 | _startup_compliance_audit | TradingSupervisor.bootstrap | D6 |
| 3747 | _strategy_exit_poll_loop | StrategyRuntime | D8 |
| 3793 | _reconcile_loop | PositionReconciler.loop | D7 |
| 3884 | _peak_equity_persist_loop | PnLLedger | DONE |
| 3970 | _heartbeat_loop | ObservabilityPlane | D9 |
| 3987 | _pm_drain_loop | OrderRouter+PositionReconciler | D7 |
| 4017 | _redis_halt_watch_loop | RiskEngine.watch_redis_halt | D6 |
| 4052 | _btc_price_update_loop | ObservabilityPlane | D9 |
| 4073 | _dashboard_feed_loop | ObservabilityPlane | D9 |
| 4170-73 | _StubCostCalculator + estimate_cost | — | DEAD |
| 4185 | build_app | entrypoint | KEEP |
| 4191 | main | entrypoint | KEEP |

### Summary

- **live.py 48** — DONE:18, D6:10, D7:14, D8:4, D9:6, KEEP:3, DEAD?:1.
- **main.py 76** — DONE:17, D6:14, D7:17, D8:10, D9:10, KEEP:4, DEAD:5.
- **Target LOC**: live.py 3,249→≤500 (shell: class, properties, delegation, compat re-exports); main.py 4,221→≤300 (entrypoint: build_app, main, signal handlers, mode dispatch); new modules ≤600 each (TradingSupervisor~550, OrderRouter~600, ExchangeGateway~500, PositionReconciler~400, RiskEngine~450, StrategyRuntime~600, ObservabilityPlane~500).

### Dead Code Candidates

1. `main.py:2758 _paper_signal_simulator_loop` — unreachable; paper-mode uses `_paper_mode_loop`(3264). Only def-site reference.
2. `main.py:2807 on_orderbook` (nested, 2nd) — duplicates 2724/2726 factory.
3. `main.py:3420 _strategy_validation_loop` — superseded by `live_gate_continuous.py` same 6-check metrics.
4. `main.py:3502 _progressive_shadow_loop` — shadow mode deprecated (`feedback_shadow_is_paper.md`, 2026-04).
5. `main.py:4170 _StubCostCalculator` + `estimate_cost` — test stub leaked; real impl in `friction/cost_calculator.py`. 15 refs confined to main.
6. `main.py:51 _get_fallback_exchanges` — redundant; ConfigService owns defaults.
7. `live.py:3005 _krw_rate_loop` — suspect superseded by `core/price_hub.py`; audit then delete or move to Observability.
8. `live.py:2294 _halt_local` — duplicates `risk/kill_switch.halt`; delete, delegate to RiskEngine.
9. `live.py:2346 _record_first_trade` — Phase B probe; superseded by `DailyReport.first_trade_event`.
10. `live.py:2587 _sum_exchange_income` — already in PnLReconciler; inline copy dies with `_pnl_divergence_monitor_loop`.

### Duplicate / Overlapping Logic

1. **Min-notional validation** — 14 files (`main.py`, `live.py:_execute_trade_request`, `execution/trade_consumer.py`, `execution/executor.py`, `infra/exchange/min_notional_registry.py`, 8 adapters, `pre_trade_validator.py`, `strategies/funding_rate.py`). Resolve: `PreTradeValidator`(DONE) is sole authority; adapters keep only exchange-specific rounding.
2. **PnL computation** — `live.py:2374/2475/2480` plus scattered trade writes. Resolve: `PnLAttributor`(DONE) computes, `PnLLedger`(DONE) persists.
3. **OB callback wiring** — `live.py:1114` + `main.py:2726/2807`. Resolve: single `StrategyRuntime.on_orderbook`; Gateway subscribes.
4. **Halt flag propagation** — `live.py:2294 _halt_local`, `main.py:4017 _redis_halt_watch_loop`, `risk/kill_switch.py`. Resolve: `RiskEngine.halt()` sole mutator; watcher inside RiskEngine.
5. **Position reconciliation** — `live.py:943/3183` + `main.py:3674/3793/3987` + `execution/reconciler.py` + `execution/trade_reconciler.py` + `execution/position_recovery.py`. Resolve: `PositionReconciler`(D7) umbrella; delegates to primitives; 4 loops→1 scheduler with phase tags.
6. **Shutdown close/cancel** — `main.py:2314/2345`, `live.py:1036 stop`, `kill_switch` Tier-2/3. Resolve: `OrderRouter.cancel_all`/`close_all` primitives; `TradingSupervisor.stop()` orchestrates; kill-switch shares primitives.
# §3 Interface Contracts

This section formalizes Protocols, DTOs, events, and error contracts every module MUST satisfy after Day 1-5. Contracts target `mypy --strict`; deviations below are Day 6+ migration debt.

Conventions:

* `@runtime_checkable` only on trust-boundary Protocols (adapters, config). Everything else nominal.
* DTOs are `@dataclass(frozen=True, slots=True)` unless justified.
* Event queues: `asyncio.Queue[<Payload>]` owned by ONE producer, fan-out via callbacks.
* `None` is documented (never accidental).
* Timeouts declared on the Protocol docstring, not buried at the call site.

---

## 3.1 ExchangeAdapter ↔ anyone

**Protocol** — `src/infra/exchange/base.py`

```python
@runtime_checkable
class ExchangeAdapterProtocol(Protocol):
    exchange_id: str
    @property
    def health_score(self) -> float: ...
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def __aenter__(self) -> "ExchangeAdapterProtocol": ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...
    async def place_order(self, order: Order) -> Trade: ...
    async def cancel_order(self, order_id: str, *, symbol: str | None = None) -> bool: ...
    async def get_balances(self) -> dict[str, Balance]: ...
    async def get_positions_strict(self) -> list[Position]: ...  # raises on stale
    async def get_min_notional(self, symbol: str) -> Decimal: ...
    def supports_symbol(self, symbol: str) -> bool: ...
```

**Invariants** — caller MUST `await connect()` (or `async with`) before trading. **Guarantees** — `place_order()` returns a `Trade` with `exchange_order_id` set, or raises; no silent partials. `cancel_order()` tolerates legacy callers via TypeError fallback (BUG-120).

**Non-conformance** — `base.py:47` `cancel_order(order_id)` lacks `symbol=`; `atomic.py` already passes it. Widen Protocol. `get_positions_strict` and `supports_symbol` not yet present; `get_positions()` swallows errors.

---

## 3.2 Config ↔ everyone

**Protocol** — `src/core/config_service.py::ConfigService`

```python
class ConfigReaderProtocol(Protocol):
    @property
    def current(self) -> EngineConfig: ...                       # typed
    def get(self, path: str, default: Any = None) -> Any: ...    # legacy
    @property
    def on_change(self) -> asyncio.Event: ...
```

**Rules** — preferred `svc.current.risk.max_position_pct` (typed). Legacy `svc.get("risk.max_position_pct", default=6)` allowed with `# migration: typed-access` tag. **Event** — `on_change` fires on `reload()`; consumers `await`, then `.clear()`.

**Non-conformance** — `PreTradeValidator.__init__` at `pre_trade_validator.py:110` takes `get_config: Callable[..., Any]`. Replace with `config_reader: ConfigReaderProtocol` in Day 6.

---

## 3.3 PreTradeValidator ↔ SignalPipeline

```python
class PreTradeValidatorProtocol(Protocol):
    async def validate(
        self, request: TradeRequest, strategy_id: str,
        context: Mapping[str, Any] | None = None,
    ) -> ValidationResult: ...
```

**Input** — `TradeRequest` from `strategies/base.py`. **Output** — `ValidationResult(approved, reason_code: ReasonCode | None, detail, metric_labels, skip_rollback_notify)` at `pre_trade_validator.py:55`.

**Error contract** — business rejects NEVER raise; `approved=False` + `reason_code` is the path. Raises only on programmer error. `skip_rollback_notify=True` MUST be respected (BUG-78/79).

**Extension** — new gate REQUIRES new `ReasonCode` member + stable Prometheus label + log tag `live_mode.rejected_by_<code>`. No free-form strings.

**Non-conformance** — `ValidationResult` not frozen; gates mutate `ctx` side-channel (`risk_blocked`). Legacy stats debt — documented, not blocking.

---

## 3.4 OrderRouter ↔ ExchangeGateway

```python
class OrderRouterProtocol(Protocol):
    async def route(self, order: Order, *, timeout_ms: int = 15_000) -> Trade: ...
    async def cancel(self, order: Order) -> bool: ...
```

**Idempotency** — `order.client_order_id = f"{trace_id}.{leg_index}"` (≤36 chars, exchange-native charset). Router refuses duplicate IDs within a 5-minute Redis `SET NX` window.

**Retry** — at most 1 retry on `httpx.TransportError` / HTTP 5xx, same `client_order_id`. 4xx never retried.

**Timeout** — `execution.leg_timeout_ms=15000`; router wraps `place_order` in `asyncio.wait_for`. Timeout → rollback.

**Rollback** — on any leg failure, caller receives `LegFailed` event; PositionManager owns rollback, router owns cancel-other-legs.

**Non-conformance** — `src/execution/atomic.py` composes `client_order_id` ad-hoc per exchange; no enforced `{trace_id}.{leg_index}` format. Day 6.

---

## 3.5 PositionReconciler ↔ anyone

```python
class PositionReconcilerProtocol(Protocol):
    async def get_position(self, exchange: str, symbol: str) -> Position | None: ...
    async def reconcile(self, engine_positions: dict[str, Position]) -> ReconciliationResult: ...
    @property
    def delta_queue(self) -> asyncio.Queue["PositionDelta"]: ...
```

**Outgoing event** — `PositionDelta(exchange_id, symbol, size_delta: Decimal, price: Decimal, trace_id, ts)` pushed on every divergence. Consumers drain via task.

**Query API** — `get_position()` returns `None` iff no open position; raises `ReconcilerStale` iff unknown.

**Divergence policy** — cycle 1: INFO + counter. Cycle 2: CRITICAL + `on_discrepancy()` + stranded mark (BUG-164/202). Cycle 3: halt candidate.

**Non-conformance** — `reconciler.py:72` uses `on_discrepancy: Callable` callback, not a queue. Multi-subscriber (dashboard/alerting/halt) wants queue fan-out. Day 7.

---

## 3.6 PnLAttributor ↔ PnLLedger

```python
@dataclass(frozen=True, slots=True)
class PnLEvent:
    trace_id: str; strategy_id: str; exchange_id: str; symbol: str
    realized_pnl: Decimal; commission: Decimal; funding: Decimal
    slippage_bps: Decimal
    pnl_status: Literal["verified", "pending", "diverged"]
    ts: datetime
    def to_dict(self) -> dict[str, Any]: ...

class PnLAttributorProtocol(Protocol):
    def subscribe(self, cb: Callable[[PnLEvent], Awaitable[None]]) -> Callable[[], None]: ...
    async def publish(self, event: PnLEvent) -> None: ...
```

**Subscribe** — returned `unsubscribe()` is idempotent. Internal `asyncio.Queue[PnLEvent]` fans out; slow subscribers drop to bounded per-subscriber queue, never blocking the producer.

**Status** — mirrors `pnl_ledger.py::PnLStatus`. `verified` iff `|div| < $0.10`; `diverged` iff `> $0.50`; else `pending`.

**Non-conformance** — `PnLLedger` is pull-only (`get_live_pnl_usd()`); no `subscribe()`. Day 6.

---

## 3.7 RiskEngine ↔ PreTradeValidator

```python
@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: bool
    reason_code: Literal[
        "kill_switch", "circuit_breaker", "flash_guard",
        "budget", "position_limit", "net_exposure",
    ] | None
    detail: str | None

class RiskEngineProtocol(Protocol):
    async def check(self, request: TradeRequest, *, total_capital_usd: float) -> RiskDecision: ...
    @property
    def budget_ledger(self) -> "StrategyBudgetLedgerProtocol": ...
```

**Vocabulary** — six `reason_code` strings ONLY; PreTradeValidator translates to `ReasonCode` enum (KILL_SWITCH_HALT / CIRCUIT_BREAKER_OPEN / FLASH_GUARD_BLOCKED / BUDGET_EXHAUSTED / RISK_GUARDIAN_REJECTED).

**Composition** — `StrategyBudgetLedger` is a member, not a sibling. Tests inject via RiskEngine ctor.

**Non-conformance** — `RiskGuardian` returns `bool`; `pre_trade_validator.py:388` inspects `approve()` vs `check_trade_request()` via hasattr. Fold to `check() -> RiskDecision`.

---

## 3.8 ObservabilityPlane ↔ everyone

```python
from contextvars import ContextVar
trace_id: ContextVar[str] = ContextVar("trace_id", default="")

class ObservabilityProtocol(Protocol):
    def bind_logger(self, *, module: str) -> "BoundLogger": ...
    def counter(self, name: str, labels: tuple[str, ...]) -> Counter: ...
    def histogram(self, name: str, labels: tuple[str, ...]) -> Histogram: ...
```

**Every log** — `logger.bind(trace_id=trace_id.get(), module="order_router").info("routed", legs=2)`. No bare `logger.info(f"...")` in new code.

**Metrics** — `leviathan_{noun}_{verb}_total` | `..._usd` | `..._count` | `..._seconds`. Closed label set: `strategy`, `exchange`, `symbol`, `reason_code`. New labels require a design note.

**Non-conformance** — ~40% of metrics already `leviathan_*`; `SIGNAL_REJECTED_TOTAL` / `RECONCILER_DISCREPANCY_TOTAL` need rename + label audit. Day 7.

---

## 3.9 StrategyRegistry ↔ StrategyRuntime

```python
class StrategyRegistryProtocol(Protocol):
    def get_active(self) -> list[StrategyEntry]: ...
    def get(self, sid: str) -> StrategyEntry | None: ...
    def activate(self, sid: str) -> bool: ...             # operator only
    def deactivate(self, sid: str, reason: str) -> None:  # RiskEngine only
```

**Entry** — `StrategyEntry(strategy_id, instance, is_active, allocation_pct: Decimal, daily_loss_budget_usd: Decimal, last_health_ts, error_count, deactivation_reason)` at `core/strategy_registry.py:91`.

**Events** — `strategy_activated` / `strategy_deactivated` counters + structured logs. Day 7 adds `asyncio.Queue[RegistryEvent]` for dashboard SSE.

**Access rule** — StrategyRuntime is read-only. Only RiskEngine + operator API may `deactivate()`. Naming convention today; type-level gate in Day 7.

**Non-conformance** — `deactivate()` is publicly callable by any module. Protocol documents intent; enforcement is Day 7.

---

## 3.10 TradingSupervisor ↔ everything

```python
class SupervisorState(StrEnum):
    INIT = "init"; BOOTING = "booting"; READY = "ready"
    DRAINING = "draining"; STOPPED = "stopped"

class TradingSupervisorProtocol(Protocol):
    @property
    def state(self) -> SupervisorState: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def get_health(self) -> SupervisorHealth: ...
    @property
    def shutdown_event(self) -> asyncio.Event: ...
```

**Lifecycle** — `INIT → BOOTING → READY → DRAINING → STOPPED`. Monotonic. `start()` idempotent from INIT; `stop()` idempotent always.

**SIGTERM** — handler at `supervisor.py:482` flips DRAINING, sets `shutdown_event`, schedules `stop()`. Bounded by `SHUTDOWN_TIMEOUT=30s`.

**Health** — `get_health() -> SupervisorHealth(is_ready, background_tasks_count, last_heartbeat_ts, errors)` at `supervisor.py:53`.

**Non-conformance** — `TradingSupervisor` uses `_is_ready: bool` + `_stopped: bool`, not `SupervisorState` enum. Collapse in Day 5 finalization.

---

## §3 Closing — Typing Rules

1. **No `Any` in public signatures.** `mypy --strict` per module in `pyproject.toml` as each migrates. New modules ship compliant.
2. **All dataclasses `frozen=True, slots=True`** unless mutation is load-bearing (document inline — e.g. `ValidationResult.ctx`).
3. **Every public coroutine declares a timeout policy** — `timeout_ms=` kwarg OR docstring: "caller-provided; default `ExecutionConfig.leg_timeout_ms`".
4. **Every DTO exposes `to_dict()`** for JSON serialization (dashboard / Redis Streams / structured logs). pydantic gets it free via `model_dump()`.
5. **`None` is semantic.** Every `X | None` return documents the iff condition. Example: `get_position → None iff no open position; raises ReconcilerStale iff unsynced."

Day 6+ lands the migrations; Day 5 shipped the scaffolding.

---

## §7. Path-B v2 Execution Summary (2026-04-21)

### 7.1 Shipped vs Planned

| Item | Planned | Shipped | Notes |
|------|---------|---------|-------|
| ExecutionJournal | Day 6 | Day 6 ✅ `468785c` | SQLite-WAL, hash chain, singleton |
| OrderStateMachine | Day 7 | Day 7 ✅ `01d9d12` | 9 states, declarative transitions |
| OrderRouter | Day 8 | Day 8 ✅ `72df0e2` | thin boundary, 10-min dedup TTL |
| Signal.predicted_slippage_bps | Day 9 | Day 9 ✅ `d016849` | _pred_bps=0 bug fixed |
| MarketStats real ADV | Day 10 | Day 10 ✅ `89b820f` | 24h rolling, WS trade stream |
| IOC-TTL parallel legs | Day 11 | Day 11 ✅ `74292cc` | CrossExchangeV2Executor, try_ioc primitive |
| PreTradeValidator live wire | Day 12 | Day 12 ✅ `db7bb43` | flag-gated, live.py net -2 LOC |
| Gamma calibration | Day 13 | Day 13 ✅ `782e25e` | cron + synthetic harness |
| Executor migration | Day 14 | Day 14 ✅ `edb491f` | DI state_machine+journal, best-effort |
| TradingSupervisor activate | Day 15 | Day 15 ✅ `38a99a6` | main.py runloop owner |
| Dashboard 8-page skeleton | W3 | W3 ✅ `07bd710` | Next.js + OKLCH dark |
| Infra audit | W4 | W4 ✅ `aed0e92` | Prometheus/Grafana/Alertmanager/TimescaleDB/Loki |
| CanaryStageController | Day 9 (orig) | Deferred | Gate replaced with 48H paper canary |
| trace_id DB columns | Day 8 (orig) | Partial | ContextVar wired; DB migrations deferred |
| paper_gateway / live_gateway split | Day 7 (orig) | Not yet | Deferred; TradingSupervisor owns routing |

### 7.2 LOC Deltas

| File | Before (Day 0) | After (Day 15) | Delta | Note |
|------|---------------|----------------|-------|------|
| `src/modes/live.py` | 3,476 | 3,250 | −226 | Day 2 PreTradeValidator extraction (−227), Day 12 net −2 LOC, Day 12 minor +3 |
| `src/main.py` | 4,194 | 4,228 | +34 | Day 2 +8, Day 15 +7 supervisor wiring, other injection |
| `src/execution/executor.py` | 1,587 | 1,793 | +206 | Day 14 state machine + journal wiring (accepted infra cost) |
| `src/execution/atomic.py` | ~225 | 275 | +50 | Day 11 try_ioc() primitive extracted |

### 7.3 New Modules (16 total)

**Day 1-5 opt-in (11 modules):**

| Module | LOC | Tests | Commit |
|--------|-----|-------|--------|
| `reconciliation/exchange_pnl_snapshot.py` | ~600 | 25 | `b32792e` |
| `reconciliation/pnl_reconciler.py` | ~397 | — | `b32792e` |
| `reconciliation/pnl_ledger.py` | ~220 | — | `b32792e` |
| `execution/pre_trade_validator.py` | ~619 | 27 | `0784c2b` |
| `core/reason_codes.py` | — | — | `0784c2b` |
| `core/universe_matrix.py` | ~423 | 12 | `3c45a3b` |
| `risk/strategy_budget_ledger.py` | ~637 | 18 | `974c1ad` |
| `reconciliation/daily_report.py` | ~558 | 14 | `5ff1cd9` |
| `core/config_service.py` | ~484 | 16 | `27eaa57` |
| `core/supervisor.py` | ~498 | 12 | `51f25cc` |
| `core/strategy_registry.py` | ~621 | 19 | `5617ecd` |

**Day 6-15 new (5 modules):**

| Module | LOC | Tests | Commit |
|--------|-----|-------|--------|
| `execution/journal.py` | ~530 | 12 | `468785c` |
| `execution/order_state.py` | ~226 | 9 | `01d9d12` |
| `execution/router.py` | ~225 | 7 | `72df0e2` |
| `execution/cross_exchange_v2.py` | ~440 | 9 | `74292cc` |
| `core/market_stats.py` | — | 7 | `89b820f` |

### 7.4 Test Count Delta

| Phase | New Tests |
|-------|-----------|
| Day 1-3 | +96 |
| Day 4 | +47 |
| Day 6-15 | +43 (Day 6: +12, Day 7: +9, Day 8: +7, Day 9: +3, Day 10: +7, Day 11: +9, Day 12: +9, Day 13: +7, Day 14: +5, Day 15: +4) |
| **Total new** | **+~186 across all Days (including Day 0 sync)** |
| Regression baseline | 4,996 pass / 13 pre-existing failures (unrelated to Path-B) |

### 7.5 Feature Flag Chain

All 7 flags default `false`. Activation order enforced by `§22.3 Flag Interaction Matrix`:

```
EXECUTION_JOURNAL_ENABLED          (Day 6)   — no deps
  └─ EXECUTION_STATE_MACHINE_ENABLED (Day 7) — requires JOURNAL
EXECUTION_ROUTER_ENABLED           (Day 8)   — no deps
CORE_REAL_ADV_ENABLED              (Day 10)  — no deps
EXECUTION_PARALLEL_LEGS_ENABLED    (Day 11)  — requires JOURNAL + STATE_MACHINE + ROUTER
EXECUTION_PRETRADE_VALIDATOR_ENABLED (Day 12) — no deps
```

ConfigError raised at construction if dependency flags not satisfied. Flag-off paths are byte-identical to pre-Day-0 baseline — rollback is `false` in `.env`.

### 7.6 Gate Status (as of 2026-04-21)

- **Gate**: 48H paper canary + 7 criteria (plan §5)
- **Live re-enable**: BLOCKED until Gate passes
- **Mode**: `paper` enforced (commit `606c97b`; `mode=paper` in `config/engine.json`)
- **Next step**: start 48H paper canary run; verify 7 Gate criteria; then re-enable live with `EXECUTION_JOURNAL_ENABLED=true` + `EXECUTION_STATE_MACHINE_ENABLED=true` + `EXECUTION_ROUTER_ENABLED=true`
## §4 Migration + Rollback Plan

### 4.1 Pre-migration Gate

Before Day 6 begins, ALL of these must be TRUE:

- [ ] §1-3 of `MODULE_DESIGN.md` approved by operator
- [ ] All Day 1-5 modules have >=90% unit test coverage in their own test file
- [ ] Full unit test regression passes: 4763+ green, 11 known pre-existing failures frozen in a named exclusion list
- [ ] Engine currently in paper mode, Binance/Bitget positions = 0
- [ ] Operator signs off on this rollback plan in writing

If any row fails, Day 6 does not start. No exceptions.

### 4.2 Day 6 — ConfigService adoption (LOW RISK)

Replace every string-keyed `get_config("risk.max_position_pct")` read with typed `ConfigService.current.risk.max_position_pct`.

- **Scope**: 96 call sites (confirmed grep baseline).
- **Batching**: 5-10 files per commit, 10-12 commits total.
- **Tests**: re-run affected module tests after each file; full regression after each batch.
- **Rollback**: each commit is small and self-contained — `git revert <sha>` reverses it. `get_config()` function stays in the codebase as a safety net through Day 10.
- **Success criteria**: full regression green + boot smoke test `python -c "from src.main import Engine"` exits 0.

### 4.3 Day 7 — TradingSupervisor cutover (HIGH RISK)

Replace `Engine.__init__` body (266 LOC) with `await TradingSupervisor.start()`. Preserve exact boot ordering: DB -> Redis -> exchanges -> strategies -> background tasks.

Keep OLD `Engine` class as a thin shim that delegates to Supervisor; full removal deferred to Day 10.

- **Baseline capture (mandatory, before cutover)**: run current main.py in paper mode for 2h, record signal count/min, rejection rate, boot time.
- **Rollback criteria** (any one triggers immediate revert):
  - Engine fails to boot in paper mode within 30s.
  - Any background task fails to start (logs: `task.start_failed`).
  - Strategy signal count drops >20% in the 1h paper canary vs baseline.
  - Boot-time log contains any new ERROR-level line not present in baseline.
- **Success criteria**: paper canary boots clean, 2h paper operation, signal rate within ±20% of baseline, zero unhandled exceptions.
- **LOC impact**: main.py -266.
- **Operator sign-off required before merge.**

### 4.4 Day 8 — StrategyRegistry cutover (MEDIUM RISK)

Replace `Engine._register_default_strategies` (~90 LOC) with `StrategyRegistry.load_active_from_config`.

- **Scope**: 5 strategy types (FR, FF, SF, XE, TRI).
- **Tests**: per-type instantiation tests must pass; each strategy must emit >=1 mock signal through the registry path.
- **Rollback**: `git revert` — the legacy `_register_default_strategies` method remains commented (not deleted) until Day 10 for fast restore.
- **Success criteria**: active strategy list unchanged vs baseline config, signals emitted correctly, no silent strategy dropouts.
- **LOC impact**: main.py -90.

### 4.5 Day 9 — PreTradeValidator integration audit (LOW RISK, already shipped)

- Audit: all 11 pre-trade gates in `live.py` must route through `PreTradeValidator`.
- Identify and remove any remaining inline gates (expected = 0 after Day 2 commit `0784c2b`).
- Promote `LEVIATHAN_STRICT_CONFIG=1` to default; retain env-var escape hatch only for incident response.
- **Success criteria**: grep for inline gate patterns returns 0 matches; validator unit tests green.

### 4.6 Day 10 — OrderRouter + ExchangeGateway split (HIGH RISK, may span 2 days)

Extract two new modules:

- `src/execution/order_router.py` — idempotent submission, retry, timeout, `client_order_id` dedup.
- `src/execution/exchange_gateway.py` — facade over `native_*` adapters with rate limiting and retry.

Replace current `_route_to_executor` + parts of `atomic.py`.

- **Rollback criteria**:
  - Paper canary order success rate drops >5% vs baseline.
  - Any `client_order_id` collision or duplicate fill observed.
  - Rate-limit 429 rate increases >2x.
  - Any reconciler "engine shows X, exchange shows Y" divergence event.
- **Success criteria**: 48H paper canary, 0 crashes, order success rate unchanged, 0 divergence events.
- **LOC impact**: live.py -500 to -700.
- **Operator sign-off required before merge.**

### 4.7 Day 11 — PositionReconciler hardening

Promote `execution/reconciler.py` to `PositionReconciler` with event emission and bidirectional sync. Current reconciler is polling-only; add a fill-push path so fills update state immediately.

- **Success criteria**: reconciler divergence count < 1 per 24h on paper; fill-push latency p99 < 500ms.

### 4.8 Day 12 — Final regression + SIT-3 paper canary 72H

- Full `pytest tests/` green; `check_all` returns 9/9 OK.
- 72H continuous paper mode with simulated fills.
- Variance decomposition residual < $0.10/day.
- Zero new log WARNING lines in the first hour of a fresh boot.
- Exchange adapter health checks green throughout.

### 4.9 Live Re-enable Gate

After §4.8 passes, live trading resumes only in Stage-1 canary:

- $10 per strategy, 48H continuous, 0 manual interventions.
- Auto-HALT triggers: daily loss > 2%, reconciler divergence > 2%, any stranded inventory event.
- Stage advancement follows `REFACTOR_PLAN.md` §6 exactly — no shortcuts.

### 4.10 Red Flags (universal abort criteria)

During ANY day, abort to rollback if:

- 2+ unrelated regression failures appear in the same batch.
- Pre-migration baseline signal or rejection rate drifts >30% without a traced cause.
- Operator observes any "engine reports X but exchange shows Y" divergence pattern.
- A newly shipped module's test coverage drops below 80%.
- A known-fail test starts passing (investigate before continuing — something moved).

### 4.11 Communication Cadence

- **Daily EOD**: commit tree snapshot + LOC delta + test count posted to Slack and Telegram.
- **Operator sign-off**: required before Day 7 and Day 10 (both high-risk days).
- **Incident comms**: if rollback triggered, summary (trigger, revert SHA, next step) posted within 1 hour.
- **Weekly**: §4.8 and §4.9 status published in `SSOT.md` §2.
# LEVIATHAN — Module Design §5 Industry Benchmark

| Field | Value |
|---|---|
| Document | `engine/docs/MODULE_DESIGN_SEC5_INDUSTRY.md` |
| Version | 0.1 |
| Date | 2026-04-19 KST |
| Status | DRAFT — informs §1–§4 before Codex / Gemini critique |
| Method | exa.ai search only; each claim cites URL or `inferred from code` |
| Related | `engine/docs/MODULE_DESIGN.md` §1–§4 |

§1–§4 were codebase-outward. §5 reverses it: how commercial quant engines structure the same concerns, so §1–§4 can be validated before Day-6. Systems: Hummingbot, Freqtrade, QuantConnect LEAN, CCXT, LMAX Disruptor, Backtrader, Jane Street Incremental, BitMEX sample, Alpaca-py, Jesse, NautilusTrader.

---

## 5.1 Hummingbot (Python, market-making)

- `core` (clock, events, `ConnectorBase`), `connector/{exchange,derivative,gateway}/*` per venue, `strategy*` (v1 `StrategyBase`, v2 `ControllerBase`), `api_throttler`, `client_order_tracker` ([architecture](https://hummingbot.org/developers/strategies/architecture), [connector arch](https://hummingbot.org/developers/connectors/architecture)).
- Strategy calls `buy()`/`sell()`, receives `OrderFilledEvent`, `BuyOrderCompletedEvent`, `OrderCancelledEvent`, `MarketOrderFailureEvent` via event bus ([order lifecycle](https://hummingbot.org/connectors/connectors/architecture/order_lifecycle/)).
- Per-venue subclass, NOT monolithic. `ClientOrderTracker` single order truth ([PR #5138](https://github.com/hummingbot/hummingbot/pull/5138)); fail-then-fill hardest edge ([issue #7294](https://github.com/hummingbot/hummingbot/issues/7294)).
- `AsyncThrottler` + `RateLimit` + `LinkedLimitWeightPair` (per-endpoint/pools/weighted) ([throttler docs](https://hummingbot.org/connectors/connectors/api_throttler/)). pydantic `ClientConfigMap`; `kill_switch` on P&L ([docs](https://hummingbot.org/global-configs/kill-switch)).

## 5.2 Freqtrade (Python, retail)

- `freqtradebot.py`, `strategy/`, `exchange/` (CCXT wrapper), `persistence/` (SQLAlchemy `Trade`/`Order`/`PairLocks`), `wallets.py`, `rpc/`, `optimize/`, `freqai/` ([rpc.py](https://github.com/freqtrade/freqtrade/blob/develop/freqtrade/rpc/rpc.py), [overview](https://deepwiki.com/freqtrade/freqtrade/1.1-architecture-overview)).
- Bot owns loop; strategy = `populate_*`/`custom_*` only; `wallets` balances; `persistence` = WAL. Engine-calc PnL; no income reconciliation.
- Single JSON schema; `dry-run` first-class ([docs](https://docs.freqtrade.io/en/latest/configuration/)); `rpc/` multicasts Telegram/Discord/WebUI; no Prometheus.

## 5.3 QuantConnect LEAN (C#, institutional)

- `Engine/{Setup,DataFeeds,RealTime,Results,TransactionHandlers}`, `Algorithm/{Portfolio,Risk,Execution,Alpha,Universe}`, `Brokerages/`, `Launcher/` ([engine docs](https://www.quantconnect.com/docs/v2/lean-engine/getting-started)).
- Every concern is an interface: `ISetupHandler`, `IDataFeed`, `ITransactionHandler`, `IRealTimeHandler`, `IResultHandler` ([IRealTimeHandler](https://www.lean.io/docs/v2/lean-engine/class-reference/cs/interfaceQuantConnect_1_1Lean_1_1Engine_1_1RealTime_1_1IRealTimeHandler.html)); concrete impls swap per env via `config.json`.
- `IBrokerage` interface; backtest = simulated brokerage, same interface. Engine-authoritative `Portfolio`+`SecurityHolding`; `PortfolioConstructionModel` sizes ([source](https://github.com/QuantConnect/Lean/blob/master/Algorithm/Portfolio/PortfolioConstructionModel.cs)). Lifecycle: Submitted→PartiallyFilled→Filled|Canceled|Invalid.
- `IResultHandler` → console/GUI/cloud; `RiskManagementModel` subclasses ([ref](https://www.lean.io/docs/v2/lean-engine/class-reference/cs/classQuantConnect_1_1Algorithm_1_1Framework_1_1Risk_1_1RiskManagementModel.html)).

## 5.4 CCXT (JS/Python)

- `base/Exchange`, `base/ws/Client`, one file per 100+ venues ([manual](https://docs.ccxt.com/README), [overview](https://www.bitget.com/academy/ccxt-library-guide)).
- Canonical unified interface: swap `ccxt.binance()` for `ccxt.kraken()`, callers unchanged. Normalised `status ∈ {open,closed,canceled,expired,rejected}`; retries/dedup are caller's job.
- Built-in token-bucket throttler ([issue #9744](https://github.com/ccxt/ccxt/issues/9744)); exception hierarchy `NetworkError`/`ExchangeError`/`InsufficientFunds`/`AuthenticationError`.

## 5.5 LMAX Disruptor (Java, HFT)

- `RingBuffer`+`EventFactory`+`EventHandler`+`Sequencer`+`SequenceBarrier`+`WaitStrategy` ([user guide](https://lmax-exchange.github.io/disruptor/user-guide/), [paper](https://lmax-exchange.github.io/disruptor/files/Disruptor-1.0.pdf)).
- **Single-writer principle**: one writer per location; consumers advance own `Sequence`. Queues fail this via head/tail contention + false sharing ([Baeldung](https://www.baeldung.com/lmax-disruptor-concurrency)). Sequence counters = exact lag.
- Lesson: LEVIATHAN §1.5 "single owner" IS this in asyncio.

## 5.6 Backtrader (Python, backtest)

- `Cerebro`+`Strategy`+`Broker`+`DataFeed`+`Indicator`+`Analyzer`+`Observer`+`Writer`+`Sizer` ([concepts](https://www.backtrader.com/docu/concepts/), [analyzers](https://www.backtrader.com/docu/analyzers/analyzers/)).
- Cerebro = clock + `run()`; strategy sees `notify_order`/`notify_trade`; Analyzers ride strategy. States Submitted→Accepted→Completed/Partial/Canceled/Rejected/Margin.

## 5.7 Jane Street Incremental (OCaml)

- `Var`, `Incr` (node/bind/map/map2/if_), `Scope`, `State` ([interface](https://github.com/janestreet/incremental/blob/master/src/incremental_intf.ml), [blog](https://blog.janestreet.com/introducing-incremental/)).
- Computation graph; inputs change, only dependent nodes refire on `stabilize ()`.
- Lesson: "Derived values are functions of inputs" formalised — `PnLLedger`, `BudgetLedger`, `UniverseMatrix` candidates.

## 5.8 BitMEX sample market maker (Python)

- `market_maker/market_maker.py` (`OrderManager`), `bitmex.py`, `ws/ws_thread.py` ([repo](https://github.com/BitMEX/sample-market-maker), [market_maker.py](https://github.com/BitMEX/sample-market-maker/blob/master/market_maker/market_maker.py)).
- `place_orders()` = seam; `converge_orders()` diffs desired vs live → amend/create/cancel bulk-ops — no state machine, just convergence.

## 5.9 Alpaca-py (US equities SDK)

- `alpaca.trading.client.TradingClient`, `requests.OrderRequest`, `stream.TradingStream` ([orders API](https://alpaca.markets/sdks/python/api_reference/trading/orders.html)).
- **17-state `OrderStatus`**: `new`, `partially_filled`, `filled`, `done_for_day`, `canceled`, `expired`, `replaced`, `pending_cancel`, `pending_replace`, `accepted`, `pending_new`, `accepted_for_bidding`, `stopped`, `rejected`, `suspended`, `calculated`, `held` ([enum](https://docs.rs/alpaca-websocket/latest/alpaca_websocket/enum.OrderStatus.html)). Caller `client_order_id` = idempotency ([docs](https://docs.alpaca.markets/v1.4.2/docs/orders-at-alpaca)). Paper = identical surface.

## 5.10 Jesse (Python, crypto CLI)

- `jesse/strategies/Strategy`, `modes/{backtest,optimize,livetrade,papertrade}`, `services`, `store`, `indicators/`, `routes` ([pypi](https://pypi.org/project/jesse/), [site](https://jesse.trade/)).
- Hooks: `should_long`/`should_short`/`go_long`/`go_short`/`update_position`/`on_open_position`/`on_close_position`/`on_cancel`. `routes.py` multiplexes `(exchange, symbol, timeframe, strategy)` tuples.

## 5.11 NautilusTrader (Rust+Python, modern reference)

- `core`, `model`, `common`, `data`, `execution`, `persistence`, `portfolio`, `risk`, `trading`, `backtest`, `live`, `system` kernel ([architecture](https://nautilustrader.io/docs/latest/concepts/architecture/), [backtest crate](https://crates.io/crates/nautilus-backtest)).
- `NautilusKernel` shared across Backtest/Sandbox/Live. Event-driven MessageBus (Pub/Sub, Req/Rep); single-threaded core for deterministic ordering.
- **Explicit `OrderStatus`**: DENIED, EMULATED, RELEASED, SUBMITTED, ACCEPTED, REJECTED, CANCELED, EXPIRED, TRIGGERED, PENDING_UPDATE, PENDING_CANCEL, PARTIALLY_FILLED, FILLED ([lifecycle](https://deepwiki.com/nautechsystems/nautilus_trader/4.2-order-types-and-lifecycle), [issue #299](https://github.com/nautechsystems/nautilus_trader/issues/299)). Crash-only design.

---

## 5.12 Cross-Reference Matrix — LEVIATHAN 12 modules vs industry

| LEVIATHAN module | Industry parallel | Gap | Our design vs best practice |
|---|---|---|---|
| `ConfigService` | LEAN env handlers, Freqtrade schema, Hummingbot `ClientConfigMap` | aligned | Typed schema right. LEAN/Hummingbot let config name concrete plugin class per env. |
| `ExchangeGateway` (facade) | Hummingbot `ConnectorBase` per venue, CCXT `Exchange`, LEAN `IBrokerage`, Nautilus `ExecutionClient` | consensus against monolith | Rename facade → "registry"; per-venue `ExchangeAdapterProtocol` stays. |
| `MarketDataCollector`+`PriceHub` | Nautilus `DataEngine`+`MessageBus`, LEAN `IDataFeed` | aligned | No formal MessageBus. |
| `StrategyRuntime` | Hummingbot `StrategyBase`, Jesse `Strategy`, Backtrader, LEAN `IAlgorithm` | mostly aligned | Industry has `on_start`/`on_stop`/`on_tick`/`on_fill`/`on_cancel`/`on_open_position`/`on_close_position`; we only `on_orderbook`. |
| `StrategyRegistry` | LEAN Alpha/Universe, Freqtrade `StrategyResolver`, Jesse routes | aligned | Config-driven class+params (LEAN) vs hard-coded. |
| `SignalPipeline`+`PreTradeValidator` | LEAN `RiskManagementModel`, Hummingbot `TradingRule` | aligned, richer | `ReasonCode` enum stronger. Per-venue validators scattered across adapters. |
| `OrderRouter` | Nautilus `ExecutionEngine`, Hummingbot `ClientOrderTracker`, Alpaca client-order-id, BitMEX `converge_orders` | missing state machine | Industry always has explicit `OrderState`; ours implicit. |
| `PositionReconciler` | Nautilus `Portfolio`+`Cache`, Hummingbot `PositionTracker`, LEAN `SecurityPortfolioManager` | aligned intent | Industry fill-pushes; we poll. Day-11 fill-push = right. |
| `PnLAttributor`+`PnLLedger` (income-primary) | LEAN `Portfolio`, Hummingbot `PerformanceMetricCollector`, Jesse log | pattern gap | Income-primary stronger than engine-reconstructed norm. Pull-only; event-sourced `PnLEvent` would bulletproof §1.8 #1. |
| `RiskEngine` (11-check) | LEAN `RiskManagementModel`, Hummingbot `KillSwitch`, Nautilus `RiskEngine` | richer | 11-check beats Hummingbot threshold kill. `AsyncThrottler` independent in Hummingbot; we merge into gateway. |
| `ObservabilityPlane` | LEAN `IResultHandler`, Freqtrade `rpc`, Hummingbot `EventReporter`, Nautilus bus | aligned, best combo | LEAN pluggable handler slot; we hard-code Prom+Loki+Telegram. |
| `TradingSupervisor` | Nautilus `NautilusKernel`, LEAN `Launcher`, Freqtrade `FreqtradeBot` | aligned | Nautilus kernel shared all envs; ours only live. |

## 5.13 Design gaps — industry has, LEVIATHAN lacks

1. **Event-sourced positions** — Nautilus+CQRS rebuild from fill log ([CQRS](https://touch-fire.com/en/technology/cqrs-event-sourcing/index.html)); we mutate dict.
2. **Order state machine** — Alpaca 17, Nautilus `OrderStatus`; ours implicit.
3. **Strategy lifecycle hooks** — missing `on_start`/`on_stop`/`on_fill`/`on_cancel`/`on_open_position`/`on_close_position`.
4. **Weighted rate-limit pools** — Hummingbot `LinkedLimitWeightPair`; ours flat 5rps/10burst.
5. **Cross-restart idempotency** — our 5-min SET NX has no restart history.
6. **Backpressure-as-block** — Nautilus MPSC blocks; we drop bounded.
7. **Continuous mark-to-market** — LEAN revalues per tick; we only on close/funding.
8. **Position keeper distinct** — Nautilus `Cache`+`Portfolio`; ours merged in reconciler.
9. **Cross-boundary correlation IDs** — Nautilus tags every event; our `trace_id` stops before DB/Redis/Telegram.
10. **Time sync** — Hummingbot `time_synchronizer`, Alpaca server-time; we assume local UTC.
11. **Environment parity** — Nautilus one kernel backtest/sandbox/live; our backtest is sibling loop.
12. **Pluggable handlers per env** — LEAN config-driven; we branch inline on `execution_mode`.

## 5.14 Final Recommendation — 5 concrete §1–§4 changes

1. **Add `OrderState` enum to §3.4.** `PENDING_NEW → SENT → ACKED → PARTIALLY_FILLED → FILLED | REJECTED | CANCELED | EXPIRED`. `Order.state` mandatory; `OrderRouter.route()` forward-only. Source: Alpaca, Nautilus.
2. **Event-sourced positions in §1.7.** New `fill_events` table = single writer; `PositionRegistry` = projection rebuilt at boot. Preserves §1.8 #6 by construction. Source: Nautilus, CQRS.
3. **Strategy lifecycle Protocol in §3.9.** `on_start`, `on_stop`, `on_tick`, `on_fill(Order)`, `on_cancel(Order)`, `on_open_position(Position)`, `on_close_position(Position, PnLEvent)`. Source: Jesse, Hummingbot.
4. **Split `ExchangeGateway` → `ExchangeRegistry` + per-venue `ExchangeAdapter` + `AsyncThrottler`.** Each adapter ships own `RateLimit`+`LinkedLimitWeightPair`. Source: Hummingbot, CCXT.
5. **`TradingSupervisor` → `EngineKernel` common across backtest/paper/live.** Paper/backtest loops become kernel-hosted phases. Source: Nautilus `NautilusKernel`, LEAN environments.

---

## 운영자용 한국어 요약

1. exa.ai로 업계 11개 엔진(Hummingbot, Freqtrade, LEAN, CCXT, LMAX Disruptor, Backtrader, Jane Street Incremental, BitMEX sample, Alpaca-py, Jesse, NautilusTrader) 8축 비교 후 LEVIATHAN 12개 모듈과 매트릭스 매핑.
2. 결정적 갭 3가지: `OrderState` 상태머신 부재, 포지션 event-sourced 아닌 mutable dict, 전략 라이프사이클 훅 `on_orderbook` 하나뿐.
3. 정렬 부분: 11-check RiskGuardian, income-primary PnL, trace_id+structlog+Prometheus, pydantic config — 업계 대비 동급/강함.
4. §1–§4 반영 5변경(§5.14): OrderState enum, event-sourced position, StrategyProtocol 훅, ExchangeGateway 분해, EngineKernel 공통화.
5. 다음: Codex/Gemini 리뷰(#64/#65)로 교차 검증 후 §1–§4 개정. 본 문서 DRAFT, 코드 변경 유발 없음.
