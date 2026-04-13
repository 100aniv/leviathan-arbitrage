# US-387 Plan: K-BT OHLCV Download (Strategy-Optimal Periods)

## Entry Gate Check

| # | Gate Item | Status | Evidence |
|---|-----------|--------|----------|
| 1 | SSOT.md alignment | PASS | US-387 in SSOT.md K-BT section line 680 |
| 2 | PRD connection | PASS | US-387 in prd.json, passes:false |
| 3 | Dependency check | PASS | US-361 (OHLCV downloader), US-362 (ohlcv_to_orderbook) both passes:true |
| 4 | Math model alignment | N/A | No new math model; uses existing synthetic orderbook spread logic |
| 5 | File boundary | PASS | Modifies only scripts/download_historical.py + config/backtest_batches.json (no active US conflict) |
| 6 | WIRING AC | N/A | Not a new component; extends existing script |

**Entry Gate Decision: PASS -- Stage B entry approved.**

---

## Risk Analysis (Critic Findings)

### CRITICAL: DB Retention Policy Deletes 2024 Data

**Problem**: `orderbook_snapshots` has a 30-day retention policy (migration 005). Inserting 2024 data will trigger auto-deletion by TimescaleDB background worker.

**Mitigation**: Must remove/extend the retention policy BEFORE inserting historical data.

```sql
-- Option A: Remove retention policy entirely for backtest data
SELECT remove_retention_policy('orderbook_snapshots', if_exists => true);

-- Option B: Extend to 3 years (covers 2024-01 ~ 2026-04)
SELECT add_retention_policy('orderbook_snapshots', INTERVAL '3 years', if_not_exists => true);
```

**Recommendation**: Option A (remove entirely), since this table stores both live and backtest data. A separate migration 006 should be added.

### MEDIUM: Existing ohlcv_downloader.py Uses Wrong Column Names

`src/infra/db/ohlcv_downloader.py` (US-362) uses columns `(timestamp, exchange, symbol, bids, asks, source)` but actual DB schema is `(ts, exchange, symbol, bids_json, asks_json, best_bid, best_ask, spread_bps, mid_price)`. This means the US-362 downloader would fail on real DB.

**Decision**: Use `download_historical.py` as the base (correct column names). Add missing exchange fetchers to it.

### MEDIUM: Interval Mismatch (5min vs 1H)

Current `download_historical.py` uses 5-minute candles. The AC specifies 1H OHLCV. For backtest quality, 1H is sufficient and produces ~18x fewer rows (reducing DB load).

**Decision**: Add `--interval` parameter. Default to `1h` for K-BT. Keep 5min support for future use.

### LOW: Missing Cross-Quote Symbols for Triangular

Triangular strategy requires cross-quote pairs (ETH/BTC, SOL/BTC). Not all exchange APIs support these.

**Decision**: Download where available. Triangular backtest on exchanges lacking cross-quotes will produce trades=0 (expected).

---

## Implementation Plan

### Task 1: Migration 006 -- Remove Retention Policy

**File**: `engine/src/infra/db/migrations/006_remove_orderbook_retention.sql`

```sql
-- Migration 006: Remove orderbook_snapshots retention policy
-- Required for K-BT historical data (2024-01 ~ 2025-03)
SELECT remove_retention_policy('orderbook_snapshots', if_exists => true);
```

Also run manually on existing DB before download.

### Task 2: Add Missing Exchange Fetchers to download_historical.py

Add 4 new fetcher functions:

#### 2a. fetch_binance (Spot)
- Endpoint: `https://api.binance.com/api/v3/klines`
- Params: symbol (BTCUSDT), interval (1h), startTime, endTime, limit=1000
- Pattern: Reuse logic from `ohlcv_downloader.py._fetch_klines()`
- Symbol map: `{"BTC/USDT": "BTCUSDT", "ETH/USDT": "ETHUSDT", ...}`

#### 2b. fetch_binance_futures
- Endpoint: `https://fapi.binance.com/fapi/v1/klines`
- Params: same as spot
- Symbol map: `{"BTC/USDT": "BTCUSDT", ...}`

#### 2c. fetch_upbit
- Endpoint: `https://api.upbit.com/v1/candles/minutes/60`
- Params: market (KRW-BTC), to (ISO8601), count=200
- KRW pairs only: `{"BTC/KRW": "KRW-BTC", "ETH/KRW": "KRW-ETH", ...}`
- Note: Upbit paginates backwards (newest first), max 200/request

#### 2d. fetch_bithumb
- Endpoint: `https://api.bithumb.com/public/candlestick/{symbol}_KRW/1h`
- KRW pairs only: `{"BTC/KRW": "BTC", "ETH/KRW": "ETH", ...}`
- Note: Returns up to 1440 candles per request (60 days)

### Task 3: Add --interval Parameter

Modify `main()` and all fetchers to accept interval parameter:
- `1h` (default for K-BT): 1-hour candles
- `5m`: existing 5-minute candles
- Map to exchange-specific interval codes

Interval mapping per exchange:

| Exchange | 1h code | 5m code |
|----------|---------|---------|
| Binance | "1h" | "5m" |
| Binance Futures | "1h" | "5m" |
| Bybit | "60" | "5" |
| OKX | "1H" | "5m" |
| Bitget | "1h" | "5min" |
| MEXC | "1h" | "5m" |
| Gate.io | "1h" | "5m" |
| BingX | "1h" | "5m" |
| LBank | "hour1" | "5min" |
| Upbit | candles/minutes/60 | candles/minutes/5 |
| Bithumb | /1h | /5m |
| Coinone | "1h" | "5m" (TBD) |

### Task 4: Expand Symbol Maps

Add all required symbols for K-BT strategies:

```python
SYMBOL_MAP = {
    "binance": {
        "BTC/USDT": "BTCUSDT", "ETH/USDT": "ETHUSDT", "SOL/USDT": "SOLUSDT",
        "BNB/USDT": "BNBUSDT", "XRP/USDT": "XRPUSDT",
        "ETH/BTC": "ETHBTC", "SOL/BTC": "SOLBTC",
    },
    "binance_futures": {
        "BTC/USDT": "BTCUSDT", "ETH/USDT": "ETHUSDT", "SOL/USDT": "SOLUSDT",
    },
    "upbit": {
        "BTC/KRW": "KRW-BTC", "ETH/KRW": "KRW-ETH", "SOL/KRW": "KRW-SOL",
        "XRP/KRW": "KRW-XRP",
    },
    "bithumb": {
        "BTC/KRW": "BTC", "ETH/KRW": "ETH", "SOL/KRW": "SOL",
        "XRP/KRW": "XRP",
    },
    # ... existing maps retained for bybit, okx, mexc, gateio, bitget, etc.
}
```

### Task 5: Rewrite backtest_batches.json for K-BT-01~18

Replace the current 27-case structure with the 18-case K-BT structure.
Each batch entry specifies:
- `id`: K-BT-01 through K-BT-18
- `exchange_ids`: list of exchanges
- `strategy_ids`: list of strategies
- `seed_capital`: per SSOT ($20 spot / $30 futures)
- `period`: strategy-specific start/end dates
- `symbols`: required symbols for that strategy/exchange combo
- `data_source`: "ohlcv_historical" (not "ohlcv_synthetic" GBM)

New structure:

```json
{
  "note": "K-BT: 18 cases with strategy-optimal historical periods (2024-01~2025-03)",
  "ac": {"sharpe_min": 1.0, "mdd_max_pct": 15.0, "win_rate_min": 45.0, "pf_min": 1.2, "trades_min": 20},
  "batches": [
    {
      "id": "K-BT-01",
      "exchange_ids": ["binance", "binance_futures"],
      "strategy_ids": ["funding_rate_v1", "triangular_v1", "statistical_arb_v1", "spot_futures_v1"],
      "seed_capital": 1000.0,
      "periods": {
        "funding_rate_v1": {"start": "2024-01-10", "end": "2024-03-31"},
        "spot_futures_v1": {"start": "2024-01-10", "end": "2024-03-31"},
        "triangular_v1": {"start": "2024-01-10", "end": "2024-06-30"},
        "statistical_arb_v1": {"start": "2024-04-01", "end": "2024-09-30"}
      },
      "symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "ETH/BTC", "SOL/BTC"]
    },
    ...
  ]
}
```

### Task 6: Download Execution Script

Create `engine/scripts/download_kbt_data.sh` -- a convenience wrapper that runs all downloads in sequence:

```bash
#!/bin/bash
# K-BT OHLCV Data Download -- all 18 cases
cd /Users/100aniv/Development/arbitrage_OMC/engine

# Step 0: Remove retention policy
python -c "import asyncio, asyncpg; ..."

# Step 1: funding_rate / spot_futures / futures_futures period (2024-01-10 ~ 2024-03-31)
python scripts/download_historical.py \
  --exchanges binance,binance_futures,bybit,bybit_futures,okx,okx_futures,bitget \
  --symbols BTC/USDT,ETH/USDT,SOL/USDT \
  --start 2024-01-10 --end 2024-03-31 --interval 1h

# Step 2: triangular period (2024-01-10 ~ 2024-06-30)
python scripts/download_historical.py \
  --exchanges binance,bybit,okx,bitget,coinone,upbit,bithumb,mexc,gateio \
  --symbols BTC/USDT,ETH/USDT,SOL/USDT,ETH/BTC,SOL/BTC,BTC/KRW,ETH/KRW,SOL/KRW \
  --start 2024-01-10 --end 2024-06-30 --interval 1h

# Step 3: statistical_arb period (2024-04-01 ~ 2024-09-30)
python scripts/download_historical.py \
  --exchanges binance,bybit,okx,coinone,mexc,gateio \
  --symbols BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT \
  --start 2024-04-01 --end 2024-09-30 --interval 1h

# Step 4: cross_exchange KRW period (2025-01-01 ~ 2025-03-31)
python scripts/download_historical.py \
  --exchanges binance,upbit,bithumb,coinone \
  --symbols BTC/USDT,ETH/USDT,SOL/USDT,BTC/KRW,ETH/KRW,SOL/KRW \
  --start 2025-01-01 --end 2025-03-31 --interval 1h

# Step 5: cross_exchange global period (2024-01-10 ~ 2024-03-31)
python scripts/download_historical.py \
  --exchanges binance,bybit,okx,bitget \
  --symbols BTC/USDT,ETH/USDT,SOL/USDT \
  --start 2024-01-10 --end 2024-03-31 --interval 1h
```

### Task 7: Verification

- Run `--dry-run` for each download batch to confirm API connectivity and candle counts
- Verify DB row counts per exchange/period after download
- Run `python -m pytest tests/ -x --tb=short` to ensure 5454+ passed

---

## Data Volume Estimate

| Period | Duration | 1H Candles/Symbol | Exchanges | Symbols | Estimated Rows |
|--------|----------|-------------------|-----------|---------|----------------|
| 2024-01-10 ~ 2024-03-31 | ~81 days | ~1,944 | 10 | 5 | ~97,200 |
| 2024-01-10 ~ 2024-06-30 | ~172 days | ~4,128 | 9 | 8 | ~297,216 |
| 2024-04-01 ~ 2024-09-30 | ~183 days | ~4,392 | 6 | 5 | ~131,760 |
| 2025-01-01 ~ 2025-03-31 | ~90 days | ~2,160 | 4 | 6 | ~51,840 |
| **Total (deduplicated)** | | | | | **~400,000** |

Well within DB capacity. ON CONFLICT DO NOTHING handles overlapping downloads.

---

## Execution Sequence (for Stage B executor)

```
T1: Migration 006 (retention policy removal)       -- 5 min
T2: Add Binance/BinFut/Upbit/Bithumb fetchers      -- 30 min
T3: Add --interval parameter to all fetchers        -- 20 min
T4: Expand symbol maps                             -- 10 min
T5: Rewrite backtest_batches.json (K-BT-01~18)     -- 15 min
T6: Create download wrapper script                  -- 10 min
T7: Execute downloads (--dry-run first, then real)  -- 30 min (API rate limits)
T8: Verify DB counts + pytest 5454+                 -- 10 min
```

**Total estimated**: ~2.5 hours

**Dependencies**: T1 must complete before T7. T2/T3/T4 can run in parallel. T5/T6 can run in parallel with T2-T4.

---

## AC Traceability

| AC# | Description | Verification |
|-----|-------------|-------------|
| 1 | Strategy x exchange OHLCV 1H download complete | DB query: `SELECT exchange, COUNT(*), MIN(ts), MAX(ts) FROM orderbook_snapshots WHERE ts >= '2024-01-01' GROUP BY exchange` |
| 2 | ohlcv_to_orderbook synthetic conversion | download_historical.py already includes inline conversion (ohlcv_to_levels). Separate ohlcv_to_orderbook.py not needed -- download_historical.py does both |
| 3 | backtest_batches.json K-BT-01~18 | File review: 18 entries with correct exchange/strategy/period/symbol combos |
| 4 | pytest 5454+ passed | `cd engine && python -m pytest tests/ -x --tb=short` |

---

## Blocked Items: None

All prerequisites met. Stage B can proceed immediately.
