# LEVIATHAN Engine

> Python 3.12 asyncio + Rust PyO3 hot-path crypto arbitrage engine.
> Path-B v2 refactor in progress — see `/Users/100aniv/.claude/plans/hidden-cuddling-pascal.md`.

## Quick Start

```bash
# 1. Docker infra (TimescaleDB + Redis only — 'engine' service conflicts with local python)
cd .. && docker compose up -d timescaledb redis

# 2. Python venv
cd engine && python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Env
cp ../.env.example ../.env  # fill API keys

# 4. Run (paper mode — default during Path-B v2)
python -m src.main

# 5. Tests
python -m pytest tests/unit/ -x --tb=short --no-cov

# 6. Code quality
ruff check src/
python -m pytest --co -q  # test count
```

## Module Map (Day 1-5 built)

```
src/
├── main.py              # 4,221 LOC — Engine class (Day 15 → TradingSupervisor)
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

## Path-B v2 Status

| Day | Title | Status |
|-----|-------|--------|
| 0 | SSOT + 14-doc sync | 🟡 in progress |
| 9 | `_pred_bps=0.0` fix | ⏳ pending |
| 10 | Real 24h ADV from WS trades | ⏳ |
| 6 | ExecutionJournal (HIGH) | ⏳ |
| 7 | OrderStateMachine (HIGH) | ⏳ |
| 8 | OrderRouter | ⏳ |
| 11 | IOC-TTL parallel legs (HIGH, 5d) | ⏳ |
| 12 | PreTradeValidator wire live | ⏳ |
| 13 | Gamma calibration | ⏳ |
| 14 | Executor migrate (MED) | ⏳ |
| 15 | Supervisor activate | ⏳ |
| Gate | 48H paper canary + 7 criteria | ⏳ |

## References

- Plan: `/Users/100aniv/.claude/plans/hidden-cuddling-pascal.md`
- Architecture: `docs/MODULE_DESIGN.md`
- Progress: `docs/REFACTOR_PLAN.md`
- Runbook: `docs/OPERATOR_RUNBOOK.md`
- Root SSOT: `../SSOT.md`
