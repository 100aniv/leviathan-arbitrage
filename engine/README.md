# LEVIATHAN Engine

> Python 3.12 asyncio + Rust PyO3 hot-path crypto arbitrage engine.
> Path-B v2 + Phase 5/6/7 Hexagonal Architecture 완료 (2026-04-26~27).
> Mode: paper (live 거래 중단). Live re-enable 게이트는 라이브 micro 카나리 ($10/trade) 기반.

## Quick Start

```bash
# 1. Docker infra (TimescaleDB + Redis only — 'engine' service conflicts with local python)
cd .. && docker compose up -d timescaledb redis

# 2. Python venv
cd engine && python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Env
cp ../.env.example ../.env  # fill API keys

# 4. Run (paper mode — engine.json mode=paper enforced)
python -m src.main

# 5. Tests (5,205 passing / 14 skipped)
python -m pytest tests/unit/ -x --tb=short --no-cov

# 6. Code quality
ruff check src/
python -m pytest --co -q  # test count
```

## Module Map (Phase 5/6/7 후)

```
src/
├── main.py              # 765 LOC — Engine + EngineState + 6 @property proxies
├── ports/               # Phase 7: 12 Hexagonal Ports (Protocol, runtime_checkable)
│   ├── exchange_adapter_port.py
│   ├── executor_port.py
│   ├── risk_port.py
│   ├── data_feed_port.py
│   ├── journal_port.py
│   ├── ledger_port.py
│   ├── kill_switch_port.py
│   ├── event_bus_port.py        # Phase 7 (Codex SUGGEST)
│   ├── metrics_port.py          # Phase 7
│   ├── config_port.py           # Phase 7
│   ├── alert_port.py            # Phase 7
│   └── exchange_income_fetcher_port.py  # Phase 7
├── adapters/            # Phase 7: Concrete Port adapters
│   ├── config_adapter.py        # ConfigAdapter (config_loader wrap)
│   ├── no_op_metrics.py         # NoOpMetricsAdapter (test fallback)
│   └── no_op_alert.py           # NoOpAlertAdapter (paper-only fallback)
├── listeners/           # Phase 5/6: 14 ExecutionResultListener + Dispatcher
│   ├── _helpers.py              # 8 helpers (extract_legs_info / is_close_leg / get_side / is_status_success / get_status_value / effective_pnl / request_to_summary)
│   ├── dispatcher.py            # ExecutionResultDispatcher (async/sync routing + failure isolation)
│   ├── factory.py               # build_dispatcher_from_engine
│   └── *_listener.py            # 14 SRP listeners
├── core/
│   ├── engine_state.py          # Phase 5.2.1 — 16 mutable runtime fields SSOT
│   ├── config_service.py
│   ├── supervisor.py            # TradingSupervisor (Day 15)
│   ├── strategy_registry.py
│   ├── universe_matrix.py
│   ├── reason_codes.py
│   ├── signal.py                # predicted_slippage_bps (Day 9)
│   ├── price_hub.py
│   └── models.py
├── runtime/             # main.py 4203 → 765 분리
│   ├── lifecycle_manager.py     # Phase 5.3 — Kahn topological sort
│   ├── mode_runner.py           # Phase 5.4 — ModeRunner ABC (Backtest/Paper/Live)
│   ├── bootstrap.py             # config/db/telegram/rust/tuner
│   ├── exchange_init.py         # paper adapter spread_injection_rate=0
│   ├── pipeline_init.py         # SignalGenerator + Strategies
│   ├── ml_loops.py              # 4 ML training loops
│   ├── background_loops.py      # lifecycle + health
│   ├── mode_loops.py            # paper/live/backtest dispatch
│   └── risk_execution.py        # on_execution_result wrapper + _on_execution_result_legacy
├── modes/
│   ├── backtest.py              # 백테스트 모드 (synthetic data only)
│   ├── paper.py                 # 페이퍼 모드 (real WS + simulated execution)
│   └── live.py                  # 라이브 모드 (3,250 LOC, real WS + real execution)
├── modes/
│   ├── live.py          # 3,249 LOC — LiveMode (Day 14 migrate to Journal+StateMachine)
│   └── shadow.py        # (deprecated, merged into live.py paper mode)
├── core/
│   ├── config_service.py       # Day 4 — pydantic EngineConfig + on_change event
│   ├── supervisor.py           # Day 4 — TradingSupervisor lifecycle (Day 15 activate)
│   ├── strategy_registry.py    # Day 4 — StrategyRegistry + universe binding
│   ├── universe_matrix.py      # Day 2 — boot-time (strategy, symbol, exA, exB) validator
│   ├── reason_codes.py         # Day 2 — 16 ReasonCode enum
│   ├── signal.py               # signal generation (Day 9: add predicted_slippage_bps)
│   ├── price_hub.py            # in-memory orderbook hub
│   └── models.py               # Pydantic data models
├── execution/
│   ├── pre_trade_validator.py  # Day 2 — 11 gates, 27 tests
│   ├── executor.py             # 1,587 LOC — AtomicExecutor (Day 14 migrate)
│   ├── atomic.py               # IOC + market-fallback helper (Day 11 extract try_ioc)
│   ├── reconciler.py           # position reconciler
│   ├── trade_consumer.py       # Redis trade request consumer
│   └── [Day 6 journal.py, Day 7 order_state.py, Day 8 router.py, Day 11 cross_exchange_v2.py]
├── reconciliation/              # Day 1 + Day 3
│   ├── pnl_ledger.py           # single source of truth for operator-facing PnL
│   ├── pnl_reconciler.py       # engine vs exchange divergence monitor
│   ├── exchange_pnl_snapshot.py # /fapi/v1/income polling
│   └── daily_report.py         # UTC 00:05 Telegram + CSV
├── risk/
│   ├── strategy_budget_ledger.py  # Day 3 — per-strategy daily loss budget
│   ├── guardian.py                 # 11-check risk guardian
│   ├── kill_switch.py              # 3-tier kill switch
│   ├── circuit_breaker.py          # CLOSED/OPEN/HALF_OPEN
│   ├── flash_guard.py              # price flash detection
│   ├── per_strategy_cb.py          # strategy-scoped CB
│   └── toxicity_filter.py          # orderbook toxicity
├── friction/
│   ├── cost_calculator.py      # net_profit computation
│   ├── fee_model.py            # per-exchange fee table
│   ├── slippage_model.py       # CEXOrderbookSlippage (Day 13 gamma calib)
│   └── slippage_feedback.py    # predicted vs actual (Day 9: fix _pred_bps=0.0)
├── strategies/                 # 7 strategies
│   ├── funding_rate.py
│   ├── futures_futures.py
│   ├── cross_exchange.py
│   ├── spot_futures.py
│   ├── triangular.py
│   ├── statistical_arb.py
│   └── cex_dex.py
├── infra/
│   ├── exchange/               # 11 native adapters (Binance/Bitget/Upbit/Bithumb/Coinone + futures)
│   ├── db/                     # TimescaleDB WAL
│   ├── redis_client.py
│   ├── metrics.py              # Prometheus counters/gauges
│   └── telegram_client.py
└── api/                        # FastAPI routes
    ├── routes/
    │   ├── pnl_attributed.py   # WS-C1 — 7-layer TCA
    │   ├── positions_hedge.py  # WS-C2 — hedge pair unified
    │   └── ...
    └── server.py
```

## Modes

| Mode | Purpose | Entry |
|------|---------|-------|
| `backtest` | Historical replay | `engine.json mode=backtest` |
| `paper` | Live data + simulated fills | `mode=paper` (Path-B v2 default) |
| `live` | Real orders, real capital | `mode=live` (disabled until Gate pass) |

## Environment Variables

See `.env.example`. Key Path-B v2 flags:
- `LEVIATHAN_STRICT_CONFIG=1` — pydantic validation at boot
- `EXECUTION_JOURNAL_ENABLED=false` — Day 6
- `EXECUTION_STATE_MACHINE_ENABLED=false` — Day 7
- `EXECUTION_ROUTER_ENABLED=false` — Day 8
- `EXECUTION_PARALLEL_LEGS_ENABLED=false` — Day 11
- `EXECUTION_PRETRADE_VALIDATOR_ENABLED=false` — Day 12
- `CORE_REAL_ADV_ENABLED=false` — Day 10
- `SUPERVISOR_ACTIVE=false` — Day 15

Each flag activated per Day. Dependency matrix in plan §22.3.

## Testing

```bash
# All unit tests
python -m pytest tests/unit/ -x --tb=short --no-cov

# Specific module
python -m pytest tests/unit/reconciliation/ -v

# Full regression
python -m pytest tests/ --no-cov

# Integration (requires Docker infra)
python -m pytest tests/integration/ --no-cov
```

## Path-B v2 Status (2026-04-22 기준)

| Day | Title | Status | Commit |
|-----|-------|--------|--------|
| 0 | SSOT + 14-doc sync | ✅ | `b861a10` |
| 1 | PnLLedger + Reconciler | ✅ | `b32792e` |
| 2 | UniverseMatrix + PreTradeValidator | ✅ | `3c45a3b` `0784c2b` |
| 3 | StrategyBudgetLedger + DailyReport | ✅ | `974c1ad` `5ff1cd9` |
| 4 | ConfigService + Supervisor + StrategyRegistry | ✅ | `27eaa57` `51f25cc` `5617ecd` |
| 5 | main.py fail-fast boot guard | ✅ | `30b704b` |
| 6 | ExecutionJournal (HIGH) | ✅ | `468785c` |
| 7 | OrderStateMachine (HIGH) | ✅ | `01d9d12` |
| 8 | OrderRouter | ✅ | `72df0e2` |
| 9 | `_pred_bps=0.0` fix | ✅ | `d016849` |
| 10 | Real 24h ADV from WS trades | ✅ | `89b820f` |
| 11 | IOC-TTL parallel legs (HIGH) | ✅ | `74292cc` |
| 12 | PreTradeValidator wire live | ✅ | `db7bb43` |
| 13 | Gamma calibration | ✅ | `782e25e` |
| 14 | Executor migrate (MED) | ✅ | `edb491f` |
| 15 | TradingSupervisor activate | ✅ | `38a99a6` |
| W3 | Dashboard 8-page skeleton | ✅ | `07bd710` |
| W4 | Infra audit (Prometheus/Grafana/Alertmanager) | ✅ | `aed0e92` |
| Post-Day-15 | Review remediation (CRITICAL+HIGH+MEDIUM) | ✅ | `5a276f5` `556ffb7` |
| Paper fix | universe_matrix 0→34 (14h canary 무효 인정) | ✅ | `3d37e91` `e5a28b2` |
| Doc sync | SSOT/PRD/CHANGELOG/RUNBOOK 6 mismatch 정정 | ✅ | `f304355` ~ `f31d410` |
| Gate | 48H paper canary + 7 criteria | 🟡 재실행 필요 (universe_matrix=34 환경) |

**중요**: 14H 카나리(PID 45822, 2026-04-21~22)는 universe_matrix entries=0으로 무효. 수정 후 5분 dry-run 검증 (entries=34, trade=5/$2.18 PnL 양수). 다음 단계: 30분 → 60분 → 6h → 24h → 48h Gate 재실행. **Pre-canary check** 절차 필수 (`docs/OPERATOR_RUNBOOK.md §0.5`).

## References

- Plan: `/Users/100aniv/.claude/plans/hidden-cuddling-pascal.md`
- Architecture: `docs/MODULE_DESIGN.md`
- Progress: `docs/REFACTOR_PLAN.md`
- Runbook: `docs/OPERATOR_RUNBOOK.md`
- Root SSOT: `../SSOT.md`
