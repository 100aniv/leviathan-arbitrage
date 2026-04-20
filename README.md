# LEVIATHAN

Commercial-grade crypto arbitrage engine for multi-exchange trading.

## Status

| Item | Value |
|---|---|
| Mode | PAPER — paused for Path-B v2 refactor |
| Version | v238 in progress (Path-B v2, Day 6-15) |
| Tests | 4,879 passing (unit, no Docker required) |
| Engine LOC | ~70k Python + Rust (PyO3 hot-path) |
| Engine vs Exchange PnL gap | -$5.01 (target: ±$1.00 after Day 15) |

Path-B v2 is a correctness-first refactor. Root cause of the gap: sequential cross-exchange legs (200-480ms naked exposure), `_pred_bps=0.0` wiring bug, and ADV proxied from top-5 depth instead of real 24h volume. Days 6-15 fix each in order.

---

## Architecture (C4 L1)

```
  External Actors                LEVIATHAN Engine                       Outputs
  ───────────────                ────────────────                       ───────
  Binance (spot+fut) ──WS md──►  ┌──────────────────────────────────┐
  Bitget  (spot+fut) ──WS md──►  │  MarketDataCollector             │
  Upbit              ──WS md──►  │   └─► PriceHub (in-process)      │──orders──► Exchanges (REST)
  Bithumb            ──WS md──►  │                                  │
  Coinone            ──WS md──►  │  StrategyRuntime (8 strategies)  │──metrics─► Prometheus/Grafana
  Bybit (spot)       ──WS md──►  │   └─► SignalPipeline             │
  OKX   (spot+fut)   ──WS md──►  │        └─► OrderRouter           │──events──► TimescaleDB (WAL)
                                 │             └─► ExchangeGateway  │
  Exchange income feeds ──poll──►│                                  │──streams─► Redis (OB/trades)
  Operator (CLI/Telegram) ──ctl─►│  RiskEngine                      │
  Config (engine.json git) ─────►│   (KillSwitch/CircuitBreaker/    │──alerts──► Telegram (3 bots)
                                 │    RiskGuardian 11-check)        │
                                 │                                  │──report──► Daily CSV (UTC 00:05)
                                 │  PnLAttributor + Reconciler      │
                                 └──────────────────────────────────┘
```

**Single Python process** (asyncio) + Rust PyO3 for hot-path orderbook ops. No ccxt. All exchange connectivity is native WebSocket.

---

## Exchange Map

| Exchange | Market | WS Latency (warm) | Role |
|---|---|---|---|
| Binance | USDT Spot | ~41ms | Primary spot leg |
| Binance Futures | USDT-M Perp | ~41ms | Primary futures leg |
| Bitget | USDT Spot | ~45ms | Secondary spot leg |
| Bitget Futures | USDT-M Perp | ~45ms | Secondary futures leg |
| Bybit | USDT Spot | ~55ms | Tertiary spot leg |
| OKX | USDT Spot | ~60ms | Tertiary spot leg |
| OKX Futures | USDT-M Perp | ~60ms | Tertiary futures leg |
| Upbit | KRW | ~80ms | KRW kimchi-premium leg |
| Bithumb | KRW | ~80ms | KRW kimchi-premium leg |
| Coinone | KRW | ~80ms | KRW kimchi-premium leg |
| Binance/Bitget | Funding | poll | Funding-rate arbitrage |

Total: 11 exchange endpoints, 0 ccxt dependencies.

---

## Strategies

| Strategy | Module | Description |
|---|---|---|
| Cross-Exchange | `strategies/cross_exchange.py` | Spot price spread between 2+ USDT exchanges |
| Spot-Futures | `strategies/spot_futures.py` | Cash-and-carry basis between spot and perp |
| Futures-Futures | `strategies/futures_futures.py` | Perp basis spread between two exchanges |
| Triangular | `strategies/triangular.py` | 3-leg cycle within a single exchange |
| Funding Rate | `strategies/funding_rate.py` | Perp funding collection with hedged spot |
| Statistical Arb | `strategies/statistical_arb.py` | OU-process mean reversion on correlated pairs |
| Latency Arb | `strategies/latency_arb.py` | Cross-exchange latency edge exploitation |
| CEX-DEX | `strategies/cex_dex.py` | Uniswap V3 vs CEX price divergence |

---

## Quick Start

### Prerequisites

- Python 3.12+
- Docker (for TimescaleDB + Redis only)
- Exchange API keys (Binance minimum; others optional)

### 1. Clone and configure

```bash
git clone https://github.com/your-org/leviathan.git
cd leviathan
cp engine/.env.example engine/.env
# Edit engine/.env:
#   BINANCE_API_KEY / BINANCE_SECRET
#   DATABASE_URL=postgresql+asyncpg://leviathan:password@localhost:5432/leviathan
#   REDIS_URL=redis://localhost:6379/0
```

### 2. Start infrastructure

```bash
# Start DB + Redis only (engine runs locally — avoids port 8000 conflict)
docker compose up -d timescaledb redis
docker compose ps
```

### 3. Install engine

```bash
cd engine
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

### 4. Run (paper mode)

```bash
cd engine
python -m src.main
# Ctrl-C to stop; logs in engine/logs/
```

### 5. Run tests

```bash
cd engine
python -m pytest tests/ -x --tb=short
```

---

## Execution Modes

| Mode | Data Source | Executor | Use |
|---|---|---|---|
| `backtest` | Synthetic (GBM) | Paper (simulated) | Strategy validation offline |
| `paper` | Live WS | Paper (simulated fills) | Pipeline integration testing |
| `live` | Live WS | Live (real orders) | Real-capital trading (LiveGate required) |

Mode is set by `engine/config/engine.json` → `"mode"` field. Environment variables are secondary.

**Live gate is currently locked.** See Path-B v2 re-enable criteria in `engine/docs/REFACTOR_PLAN.md §5`.

---

## Current Refactor: Path-B v2

Plan: `/Users/100aniv/.claude/plans/hidden-cuddling-pascal.md`
Architecture spec: `engine/docs/MODULE_DESIGN.md`
Progress tracker: `engine/docs/REFACTOR_PLAN.md`

### Day sequence

```
Day 6  ExecutionJournal   ──► Day 7  OrderStateMachine ──► Day 14 Executor migrate
                          │                              │
                          └──► Day 8  OrderRouter ───────┼──► Day 11 Parallel legs (HIGH RISK)
                                                         │
Day 9  pred_bps fix ──────┐                              │
                          ├──► Day 12 Wire live ──► Day 13 Gamma calib
Day 10 Real ADV ──────────┘                              │
                                                         └──► Day 15 Supervisor activate
```

Key metrics being fixed:

| Metric | Now | Target (Day 15) |
|---|---|---|
| Engine vs Binance 24h PnL gap | -$5.01 | ±$1.00 |
| Slippage prediction error p95 | ~120 bps (pred=0 bug) | <20 bps |
| Cross-exchange naked exposure p95 | 200-480ms | <50ms |
| Crash state recovery | unrecoverable | 100% journal replay |

---

## Documentation

| Document | Purpose |
|---|---|
| `SSOT.md` | Single source of truth — project state, architecture, math models |
| `engine/docs/MODULE_DESIGN.md` | C4 L1/L2 architecture + module responsibility matrix |
| `engine/docs/REFACTOR_PLAN.md` | Path-B v2 day-by-day progress and gates |
| `engine/docs/OPERATOR_RUNBOOK.md` | Daily operations, alerts, recovery procedures |
| `engine/README.md` | Developer guide — adapters, modules, env vars, testing |
| `dashboard/README.md` | Frontend overview — pages, API mapping, local dev |
| `dashboard/docs/DESIGN.md` | Design system spec — colors, typography, components |
| `CHANGELOG.md` | Release history |

---

## Infrastructure Ports

| Service | Port | Notes |
|---|---|---|
| Engine API | 8000 | FastAPI REST + WebSocket |
| Dashboard | 3000 | Next.js dev server |
| TimescaleDB | 5432 | Primary data store |
| Redis | 6379 | Position cache + Streams |
| Prometheus | 9090 | Metrics scrape target |
| Grafana | 3001 | Metrics visualization |

---

## License

Proprietary — LEVIATHAN Project. All rights reserved.
