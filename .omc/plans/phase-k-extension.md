# Phase K Extension PLAN (US-377 ~ US-384)

> Stage A Output | Created: 2026-04-03
> Planner: leviathan-planner (opus)
> Status: **APPROVED** for Stage B entry

---

## 0. Entry Gate Results (6/6 PASS)

| # | Check | Result | Detail |
|---|-------|--------|--------|
| 1 | SSOT alignment | PASS | US-377~384 = Phase K, BacktestMode + orderbook_snapshots + API routes. Consistent with SSOT sec 3. |
| 2 | PRD linkage | PASS | All 8 US exist in `.omc/prd.json` with `passes:false`. Total 384 US, 369 passes:true. |
| 3 | Dependencies | PASS | US-368 (Batch1) true, US-370 (Batch3) true, US-371 (Batch4) true. US-369 false (expected, US-384 re-runs). |
| 4 | Math model | PASS | Synthetic orderbook spread_bps per exchange matches SSOT sec 4.1. CEXOrderbookSlippage for pre-filter only. |
| 5 | File boundary | PASS* | No active US conflicts. *PRD US-378 lists `007_add_source_column.sql` but `006` already exists -- migration 007 NOT needed. |
| 6 | WIRING AC | N/A | US-383 (API route) needs WIRING AC. Others are scripts/data/runners -- no new component injection. |

### CRITICAL BUG DISCOVERED (Blocks US-377/378)

**ohlcv_downloader.py column name mismatch:**

| What downloader uses | Actual DB schema (001_init_schema.sql) |
|---------------------|----------------------------------------|
| `timestamp` | `ts` (TIMESTAMPTZ) |
| `bids` | `bids_json` (JSONB) |
| `asks` | `asks_json` (JSONB) |
| (missing) | `best_bid` (NUMERIC, NOT NULL) |
| (missing) | `best_ask` (NUMERIC, NOT NULL) |
| (missing) | `spread_bps` (NUMERIC, DEFAULT 0) |
| (missing) | `mid_price` (NUMERIC, DEFAULT 0) |

The existing `ohlcv_downloader.py` INSERT uses wrong column names and is missing 4 required NOT NULL columns.
The `market_recorder.py` uses the correct names: `ts, bids_json, asks_json, best_bid, best_ask, spread_bps, mid_price`.

**Resolution**: US-377/378 scripts MUST follow `market_recorder.py` INSERT pattern. The existing
`ohlcv_downloader.py` is a reference for logic flow only -- its SQL is broken.

The `_load_snapshots()` query in `backtest.py` reads: `SELECT exchange, symbol, bids_json, asks_json, EXTRACT(EPOCH FROM ts) as timestamp`
-- confirming the DB columns are `ts`, `bids_json`, `asks_json`. New scripts must INSERT with these names.

---

## 1. Scope

Replace Binance-proxy backtest data (K-B-17~23) with real exchange OHLCV synthetic orderbooks,
add Bybit/OKX single-exchange cases, Paper P-24~P-31, an API endpoint, and re-run US-369.

| US | Title | Type | Effort |
|----|-------|------|--------|
| US-377 | Historical Data Download Script | Script | M |
| US-378 | OHLCV to Synthetic Orderbook Converter | Script + DB | M |
| US-379 | Backtest Batch5 (Bybit: K-B-17/18/24) | Run | S |
| US-380 | Backtest Batch6 (OKX+Gate.io: K-B-19/20/21/25/26) | Run | S |
| US-381 | Backtest Batch7 (MEXC/BingX/LBank: K-B-22/23/27) | Run | S |
| US-382 | Paper P-24~P-31 (Bybit/OKX/MEXC/Gate.io/BingX 4H) | Run | L |
| US-383 | GET /api/v1/config/exchanges endpoint | Code | S |
| US-384 | US-369 re-run (Bitget/Coinone cross-quote synthetic) | Run | S |

---

## 2. Execution Sequence

```
Phase 1 (Sequential): US-377 -> US-378
Phase 2 (Parallel):   US-379 | US-380 | US-381
Phase 3 (Parallel):   US-383 | US-384
Phase 4 (Sequential): US-382 (requires WS connectivity, 4H wall-clock)
```

### Rationale
- US-377/378 must complete first (data download + conversion = prerequisite for all backtests)
- US-379/380/381 can run in parallel (independent batch groups, same runner)
- US-383 is independent (API route, no data dependency)
- US-384 depends on US-377/378 (needs Bitget/Coinone synthetic data)
- US-382 last because Paper tests need live WS and take 4H wall-clock each

---

## 3. Detailed Task Breakdown

### US-377: Historical Data Download Script

**File**: `engine/scripts/download_historical.py`

**Tasks**:
1. Implement multi-exchange OHLCV REST downloader supporting:
   - Bybit: `GET /v5/market/kline` (public, no API key)
   - OKX: `GET /api/v5/market/candles` (public, no API key)
   - MEXC: `GET /api/v3/klines` (public, Binance-compatible format)
   - BingX: `GET /openApi/swap/v2/quote/klines` (public)
   - LBank: `GET /v2/kline` (public)
   - Gate.io: `GET /api/v4/spot/candlesticks` (public)
   - Bitget: `GET /api/v2/spot/market/candles` (public, for US-384)
   - Coinone: `GET /public/v2/chart/{quote_currency}/{target_currency}` (public, for US-384)
2. Symbols: BTC/USDT, ETH/USDT (minimum). Cross-quote: ETH/BTC, SOL/BTC (for triangular)
3. Period: 2026-03-29 to 2026-04-02 (5 days), 1h interval
4. Output: raw OHLCV JSON files in `engine/data/ohlcv/` (intermediate, for audit trail)
5. Rate limiting: 100ms between requests per exchange
6. Error handling: retry 3x with exponential backoff, skip on persistent failure

**Acceptance Criteria**:
- [ ] Script runs: `cd engine && python scripts/download_historical.py --exchanges bybit,okx,mexc,bingx,lbank,gateio`
- [ ] Each exchange produces >= 100 candles per symbol (5 days * 24h = 120 expected)
- [ ] Output files: `engine/data/ohlcv/{exchange}_{symbol}_{start}_{end}.json`
- [ ] `python -m pytest tests/ -x --tb=short` passes (0 failures)
- [ ] No API keys required (all public endpoints)

**Edge Cases / Risks**:
- Coinone KRW-only: OHLCV for ETH/BTC may not exist. Fallback: use KRW pairs and compute synthetic cross-quote
- BingX/LBank may have lower granularity or missing data. Accept >= 80 candles as sufficient
- Gate.io may rate-limit aggressively. Use 200ms delay if 429 detected

---

### US-378: OHLCV to Synthetic Orderbook Converter

**File**: `engine/scripts/ohlcv_to_orderbook.py`

**Tasks**:
1. Read OHLCV JSON files from `engine/data/ohlcv/`
2. Generate 5-level synthetic orderbook per candle:
   ```
   mid_price = candle.close
   spread_bps = {bybit: 5, okx: 3, mexc: 10, bingx: 10, lbank: 20, gateio: 8, bitget: 5, coinone: 15}
   bid_L1 = mid_price * (1 - spread_bps / 20000)
   ask_L1 = mid_price * (1 + spread_bps / 20000)
   Levels 2-5: 0.1% gap between levels, $1000 notional per level
   ```
3. INSERT into `orderbook_snapshots` using CORRECT column names:
   ```sql
   INSERT INTO orderbook_snapshots
     (ts, exchange, symbol, bids_json, asks_json, best_bid, best_ask, spread_bps, mid_price, source)
   VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7, $8, $9, $10)
   ON CONFLICT DO NOTHING
   ```
   - `source` = `'{exchange}_ohlcv_synthetic'` (e.g., `bybit_ohlcv_synthetic`)
4. **Migration 007 NOT needed** -- migration 006 already added the `source` column
5. Batch INSERT for performance (use `executemany` or `COPY`)

**CRITICAL**: Must compute all NOT NULL columns:
- `best_bid` = float(bid_L1)
- `best_ask` = float(ask_L1)
- `spread_bps` = spread_bps value from config
- `mid_price` = float(mid_price)

**Acceptance Criteria**:
- [ ] Script runs: `cd engine && python scripts/ohlcv_to_orderbook.py --exchanges bybit,okx,mexc,bingx,lbank,gateio`
- [ ] DB query: `SELECT COUNT(*), exchange FROM orderbook_snapshots WHERE source LIKE '%_ohlcv_synthetic' GROUP BY exchange` shows rows for each exchange
- [ ] `_load_snapshots()` in backtest.py reads synthetic data correctly (manual verify with psql)
- [ ] `python -m pytest tests/ -x --tb=short` passes (0 failures)
- [ ] PRD US-378 correction: remove `migrations/007_add_source_column.sql` from files list (006 suffices)

**Edge Cases / Risks**:
- Decimal precision: use `Decimal` for price calculations, convert to `float` only for DB insert
- Duplicate data: ON CONFLICT DO NOTHING prevents duplicates on re-runs
- 5-level depth volume distribution: use candle volume / 10 per level (both sides)

---

### US-379: Backtest Batch5 (Bybit)

**Cases**: K-B-17 (triangular), K-B-18 (stat_arb), K-B-24 (Binance x Bybit cross_exchange)

**Tasks**:
1. Verify synthetic data loaded: `SELECT COUNT(*) FROM orderbook_snapshots WHERE exchange='bybit' AND source LIKE '%synthetic%'`
2. Run: `cd engine && python scripts/run_k2b_backtests.py --cases K-B-17,K-B-18,K-B-24`
3. Collect results from `.omc/state/backtest-results-K-B-*.json`

**Acceptance Criteria**:
- [ ] K-B-17: `backtest-summary-K-B-17.json` generated, crash=0
- [ ] K-B-18: `backtest-summary-K-B-18.json` generated, crash=0
- [ ] K-B-24: `backtest-summary-K-B-24.json` generated, crash=0
- [ ] Each summary contains: trades, pnl_usd, sharpe, mdd fields
- [ ] Triangular trades may be 0 (synthetic spread < fee threshold) -- document in summary
- [ ] `python -m pytest tests/ -x --tb=short` passes (0 failures)

---

### US-380: Backtest Batch6 (OKX + Gate.io)

**Cases**: K-B-19 (OKX tri), K-B-20 (OKX spot_futures), K-B-21 (Gate.io tri), K-B-25 (BN x OKX CE), K-B-26 (BYF x OXF FF)

**Tasks**:
1. Verify data: `SELECT COUNT(*), exchange FROM orderbook_snapshots WHERE exchange IN ('okx','okx_futures','gateio') AND source LIKE '%synthetic%' GROUP BY exchange`
2. Run: `cd engine && python scripts/run_k2b_backtests.py --cases K-B-19,K-B-20,K-B-21,K-B-25,K-B-26`
3. K-B-20 (OKX spot_futures) and K-B-26 (BYF x OXF futures_futures) need futures data -- ensure `okx_futures`, `bybit_futures` synthetic data also loaded in US-377/378

**Acceptance Criteria**:
- [ ] 5 summary JSON files generated, all crash=0
- [ ] K-B-20: requires both `okx` and `okx_futures` data
- [ ] K-B-26: requires both `bybit_futures` and `okx_futures` data
- [ ] Each summary: trades, pnl_usd, sharpe, mdd fields present

**Risk**: Futures OHLCV endpoints differ from spot. US-377 must handle:
- Bybit futures: `/v5/market/kline` with category=linear
- OKX futures: `/api/v5/market/candles` with instType=SWAP

---

### US-381: Backtest Batch7 (MEXC/BingX/LBank)

**Cases**: K-B-22 (MEXC tri), K-B-23 (BingX tri), K-B-27 (LBank tri)

**Tasks**:
1. Verify data loaded for mexc, bingx, lbank
2. Run: `cd engine && python scripts/run_k2b_backtests.py --cases K-B-22,K-B-23,K-B-27`

**Acceptance Criteria**:
- [ ] 3 summary JSON files generated, all crash=0
- [ ] K-B-27 (LBank): low liquidity expected. signal >= 1 sufficient per batch config
- [ ] Triangular trades likely 0 (synthetic spread too tight for fees). Document in summary

---

### US-382: Paper P-24~P-31

**Cases**: 8 Paper tests, 4H each, real WS connections

| Case | Exchange(s) | Strategy | Duration |
|------|------------|----------|----------|
| P-24 | Bybit | triangular | 4H |
| P-25 | Bybit | stat_arb | 4H |
| P-26 | OKX | triangular | 4H |
| P-27 | OKX | spot_futures | 4H |
| P-28 | MEXC | triangular | 4H |
| P-29 | Gate.io | triangular | 4H |
| P-30 | BingX | triangular | 4H |
| P-31 | Binance + Bybit | cross_exchange | 4H |

**Tasks**:
1. Configure `engine/config/strategy_params.json` for each case
2. Run each case with `DATA_MODE=real_public EXECUTION_MODE=paper`
3. Monitor for crash=0, trade >= 1 (or signal >= 1 for low-liquidity)
4. Can run 2-3 cases in parallel if different exchanges (no WS conflict)

**Acceptance Criteria**:
- [ ] All 8 cases: crash=0 over 4H
- [ ] At least 5/8 cases produce trade >= 1
- [ ] Result files in `.omc/state/paper-result-P-{N}.json`
- [ ] No API keys needed (public WS only for Paper mode)

**Risk**: Wall-clock 4H per case. With 3-way parallelism = ~12H total minimum.
Consider running P-24/P-26/P-28 parallel, then P-25/P-27/P-29, then P-30/P-31.

---

### US-383: GET /api/v1/config/exchanges

**File**: `engine/src/api/routes/config.py` (new file)

**Tasks**:
1. Create `config.py` with APIRouter(prefix="/api/v1/config")
2. `GET /exchanges` reads `engine/config/exchanges_meta.json` and returns it
3. Register router in `engine/src/api/server.py`

**WIRING AC** (required for new component):
- [ ] **Create**: `engine/src/api/routes/config.py` with `router = APIRouter(prefix="/api/v1/config")`
- [ ] **Inject**: `server.py` line ~160: `from src.api.routes.config import router as config_router; app.include_router(config_router)`
- [ ] **Call**: `GET /api/v1/config/exchanges` returns JSON from `exchanges_meta.json`

**Acceptance Criteria**:
- [ ] `curl http://localhost:8000/api/v1/config/exchanges` returns exchanges_meta.json content
- [ ] Auth required (JWT dependency)
- [ ] `python -m pytest tests/ -x --tb=short` passes
- [ ] Response includes all 10+ exchanges with backtest metadata

---

### US-384: US-369 Re-run (Bitget/Coinone cross-quote)

**Tasks**:
1. Load Bitget ETH/BTC, SOL/BTC OHLCV synthetic data (via US-377/378 scripts with `--exchanges bitget`)
2. Load Coinone ETH/BTC, SOL/BTC OHLCV synthetic data (via US-377/378 scripts with `--exchanges coinone`)
3. Re-run K-B-05 (Bitget triangular) and K-B-08 (Coinone triangular)
4. Verify trades > 0 with cross-quote data available

**Acceptance Criteria**:
- [ ] `backtest-summary-K-B-05-v2.json` generated with trades > 0
- [ ] `backtest-summary-K-B-08-v2.json` generated with trades > 0
- [ ] crash=0
- [ ] If trades still 0: document reason (spread < fee threshold) and accept as architecture validation

---

## 4. Risk Register

| # | Risk | Severity | Mitigation |
|---|------|----------|------------|
| R1 | ohlcv_downloader.py SQL column mismatch | CRITICAL | US-377/378 scripts use market_recorder.py pattern (ts, bids_json, asks_json, best_bid, best_ask, spread_bps, mid_price). Do NOT reuse ohlcv_downloader.py SQL. |
| R2 | Futures OHLCV endpoint differences | HIGH | US-377 must handle spot vs futures API variants per exchange. Document in script. |
| R3 | Coinone KRW-only pairs | MEDIUM | ETH/BTC cross-quote may not exist on Coinone REST API. Fallback: compute from ETH/KRW and BTC/KRW. |
| R4 | Paper P-24~P-31 wall-clock 32H | MEDIUM | Parallelize 3-way max. Accept 12H minimum. Run overnight. |
| R5 | Triangular trades = 0 on synthetic data | LOW | Expected: synthetic spread_bps < triangular fee threshold. Document as "architecture validation" not "profitability test". |
| R6 | PRD lists migration 007 but 006 already exists | LOW | Correct PRD US-378 files list. Remove 007 reference. |
| R7 | Gate.io/BingX/LBank rate limiting | LOW | Use 200ms delay, retry 3x, accept partial data. |
| R8 | bybit_futures / okx_futures synthetic data needed for K-B-20/K-B-26 | HIGH | US-377 download script must include futures endpoints. US-378 must convert with exchange_id including `_futures` suffix. |

---

## 5. PRD Corrections Required

1. **US-378 files list**: Remove `engine/src/infra/db/migrations/007_add_source_column.sql` (migration 006 already adds the `source` column)
2. **US-371 note**: Update AC to reflect that K-B-17~23 are now re-run with real synthetic data (not Binance proxy)
3. **US-380 dependencies**: Should include US-377 and US-378 (already correct in PRD)

---

## 6. Test Strategy

**Unit tests** (per US):
- US-377: Mock HTTP responses, verify OHLCV parsing (no DB needed)
- US-378: Mock DB pool, verify SQL column names match schema, verify 5-level orderbook math
- US-383: FastAPI TestClient, verify /api/v1/config/exchanges returns expected JSON

**Integration tests** (after all backtests):
- Verify `.omc/state/backtest-summary-K-B-*.json` files exist for K-B-17 through K-B-27
- Verify each summary has non-zero `snapshots_replayed`
- Run `python -m pytest tests/ -x --tb=short` -- must remain at 5,454+ passed, 0 failed

**No Shadow test needed**: These are backtest/paper/API tasks, not engine behavior changes.

---

## 7. Stage B Entry Decision

**APPROVED** -- All 6 Entry Gate checks pass. One critical bug discovered (R1) with clear mitigation path.

**Execution order**:
```
US-377 (download) --> US-378 (convert+load) --> [US-379 | US-380 | US-381] (backtests parallel)
                                              --> [US-383 | US-384] (API + re-run parallel)
                                              --> US-382 (Paper, last, 12H+)
```

**Estimated effort**: 2-3 sessions (excluding Paper wall-clock time).
