# LEVIATHAN

Commercial-grade crypto arbitrage engine for multi-exchange trading.

## Status (2026-04-27)

| Item | Value |
|---|---|
| Mode | PAPER — `engine.json mode=paper` (commit 606c97b enforcement, live 거래 중단) |
| Architecture | Hexagonal Architecture (12 Ports + 3 Adapters + 14 Listeners + Dispatcher) |
| Version | Path-B v2 + Phase 5/6/7 완료 (구조 리팩토링 끝, 운영 검증 단계) |
| Tests | 5,205 passing / 14 skipped (unit, no Docker required) |
| Engine LOC | main.py 4,203 → 765 LOC (Phase 5 -82%) + 14 Listeners 분리 |
| Path-B v2 PnL gap | -$5.01 → engine 측 wiring fix 완료, 운영 검증 미완 |
| Live re-enable gate | 48h paper 안정 + dispatcher error-rate 검증 + exchange PnL 정합 후 라이브 micro 카나리 ($10/trade) |

### Hexagonal Architecture (Phase 5/6/7, 2026-04-26~27)

- **12 Ports**: ExchangeAdapter / Executor / Risk / DataFeed / Journal / Ledger / KillSwitch / EventBus / Metrics / Config / Alert / ExchangeIncomeFetcher
- **3 Adapters**: ConfigAdapter / NoOpMetricsAdapter / NoOpAlertAdapter
- **14 Listeners + Dispatcher**: log/position_size/position_manager/cross_hedge/pnl_peak/market_recorder/exposure/slippage/correlation/tca/trade_history/circuit_breaker/rollback/telegram (failure isolation + async/sync routing)
- **EngineState SSOT**: 16 mutable runtime fields dataclass + 6 @property proxies (no divergence)
- **ModeRunner ABC**: Backtest/Paper/Live 다형성 (if-elif 제거)
- **LifecycleManager**: Kahn topological sort (declarative dependencies)

Path-B v2 root cause: sequential cross-exchange legs (200-480ms naked exposure), `_pred_bps=0.0` wiring bug, ADV proxied from top-5 depth. Days 6-15 fix each + Phase 5-7 god-object 해체.

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

**중요 (2026-04-22)**: 본격 카나리 시작 전 5분 dry-run으로 4 항목 점검:
- universe_matrix entries > 0 (paper 어댑터 + ExchangeAdapter Protocol 완전성)
- paper trade fill 발생 (`paper_mode.trade_request_executed` ≥ 1)
- crash 0
- PnL > 0

상세 절차: `engine/docs/OPERATOR_RUNBOOK.md §0.5 Pre-canary 점검`. 2026-04-21 14h 카나리는 universe_matrix=0으로 trade 0건이었음 — 같은 함정 재발 방지 룰.

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

## Current Refactor: Path-B v2 (2026-04-22 status)

Plan: `/Users/100aniv/.claude/plans/hidden-cuddling-pascal.md`
Architecture spec: `engine/docs/MODULE_DESIGN.md`
Progress tracker: `engine/docs/REFACTOR_PLAN.md`

**Day 0-15 + W3 + W4 commits 모두 main에 landed** (`b861a10` ~ `aed0e92`). 후속 review remediation + paper universe_matrix fix 완료 (`5a276f5`, `556ffb7`, `3d37e91`, `e5a28b2`).

### Status summary

| Metric | Pre-Path-B (v237) | Post-paper-fix (2026-04-22) |
|---|---|---|
| Engine vs Binance 24h PnL gap | -$5.01 | 측정 대기 (24h Gate 후) |
| universe_matrix entries | N/A | 34 (was 0 in 14h canary) |
| paper trade_request_executed (5분) | N/A | 5건 (funding_rate ×2, spot_futures ×3) |
| 5분 total_pnl | N/A | +$2.18 |
| Slippage prediction wiring | _pred_bps=0 bug | wired (`d016849`) |
| Cross-exchange parallel legs | sequential 200-480ms | IOC-TTL gather (`74292cc`) |
| Crash state recovery | unrecoverable | journal replay (`468785c`) |
| Order state lifecycle | scattered booleans | 9-state machine (`01d9d12`) |
| Regression | 4,996 pass | 5,053 pass / 14 skipped |

**Next**: Gate 재실행 (universe_matrix=34 환경) — 30분 → 60분 → 6h → 24h → 48h paper canary, 7 criteria. Live 재개는 Gate 통과 후.

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
