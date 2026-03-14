# Phase S5: Data Pipeline + Shadow Loss Strategy Disable

> **Phase**: S5 (Regression S-series)
> **US Targets**: US-145, US-146, US-147, US-148, US-156
> **Created**: 2026-03-14
> **Status**: READY FOR EXECUTION

---

## 1. Context

Phase S4 (Dashboard) completed. Phase S5 addresses Data Pipeline gaps discovered during TF Semi-Final:
- Auto-Tuner cannot use real execution data (NotImplementedError)
- ScheduledTuner not wired into main engine process
- Attribution engine is in-memory only (loses data on restart)
- Shadow drawdown tracks only absolute USD (no percentage)
- Loss-making strategies still execute in Shadow mode

## 2. Dependency Graph

```
US-145 (TimescaleDB Loader)
  |
  v
US-146 (ScheduledTuner main.py wiring) -- depends on US-145

US-147 (Attribution TimescaleDB) -- independent
US-148 (Shadow MDD pct + Rebalancer feed) -- independent
US-156 (Shadow disabled strategies .env) -- independent
```

**Execution order**: US-145 -> US-146 (sequential), US-147 / US-148 / US-156 (parallel, independent)

---

## 3. Detailed Implementation Plan

### 3.1 US-145: Auto-Tuner TimescaleDB Async Loader

**Problem**: `ScheduledTuner._run_with_timescaledb()` (line 161 of `engine/src/tuning/scheduled_tuner.py`) raises `NotImplementedError`. The `DataLoader` class exists at `engine/src/tuning/data_loader.py` with `load_execution_log_as_ohlcv()` but has **schema mismatches** in its SQL queries.

**Schema mismatch** (CRITICAL):
- `DataLoader.load_execution_log_as_ohlcv()` references columns `executed_at`, `price` -- these do NOT exist
- `DataLoader.load_execution_spreads()` references columns `executed_at`, `exchange`, `fee` -- these do NOT exist
- Actual `execution_log` schema (from `docker/init.sql`): `ts`, `strategy_id`, `signal_id`, `buy_exchange`, `sell_exchange`, `symbol`, `buy_price`, `sell_price`, `size`, `gross_spread_bps`, `fee_total`, `slippage_total`, `net_pnl`, `status`, `metadata`

**Changes**:

| File | Action | Details |
|------|--------|---------|
| `engine/src/tuning/data_loader.py` | FIX | Fix `load_execution_log_as_ohlcv()` SQL: `executed_at` -> `ts`, `price` -> `(buy_price + sell_price) / 2`, `size` -> `size` |
| `engine/src/tuning/data_loader.py` | FIX | Fix `load_execution_spreads()` SQL: `executed_at` -> `ts`, `exchange` -> `buy_exchange \|\| '-' \|\| sell_exchange`, `fee` -> `fee_total` |
| `engine/src/tuning/scheduled_tuner.py` | MODIFY | Replace `_run_with_timescaledb()` NotImplementedError with working async implementation using `DataLoader` |

**Implementation detail for `_run_with_timescaledb()`**:
```python
def _run_with_timescaledb(self, optimizer, params, strategy):
    try:
        import os
        import asyncio
        dsn = os.getenv("DATABASE_URL", "")
        if not dsn:
            raise ValueError("DATABASE_URL not set")
        # Strip asyncpg scheme for raw asyncpg connection
        dsn_clean = dsn.replace("postgresql+asyncpg://", "postgresql://")
        loader = DataLoader(dsn=dsn_clean)

        # Run async load in current event loop or new one
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                ohlcv = pool.submit(asyncio.run, loader.load_execution_log_as_ohlcv(strategy=strategy, days=7)).result()
        else:
            ohlcv = asyncio.run(loader.load_execution_log_as_ohlcv(strategy=strategy, days=7))

        if ohlcv.length < 10:
            raise ValueError(f"Insufficient data: {ohlcv.length} candles")

        # Run backtest with real data
        return optimizer._engine.run(params, ohlcv)
    except Exception as exc:
        logger.warning("TimescaleDB load failed for %s (%s); falling back to synthetic", strategy, exc)
        return optimizer._engine.run_with_synthetic_data(params)
```

**Tests to add/modify**:
- `engine/tests/unit/tuning/test_data_loader_sql.py` -- verify corrected SQL column references
- `engine/tests/unit/tuning/test_scheduled_tuner.py` -- add test for `_run_with_timescaledb` success path (mock asyncpg)

**Acceptance criteria**:
- [ ] `DataLoader.load_execution_log_as_ohlcv()` SQL matches `execution_log` schema columns
- [ ] `DataLoader.load_execution_spreads()` SQL matches `execution_log` schema columns
- [ ] `_run_with_timescaledb()` does NOT raise `NotImplementedError`
- [ ] When `TUNER_DATA_SOURCE=timescaledb` and DB has data, optimization uses real data
- [ ] When DB has no data or connection fails, graceful fallback to synthetic
- [ ] All existing tuning tests still pass

---

### 3.2 US-146: ScheduledTuner main.py Wiring

**Problem**: `ScheduledTuner` runs only as a standalone Docker container (`auto-tuner` service). It is NOT imported or started in `engine/src/main.py`. For in-process tuning (dev/staging), it should optionally start within the engine.

**Changes**:

| File | Action | Details |
|------|--------|---------|
| `engine/src/main.py` | ADD | Import ScheduledTuner, add `_init_tuner()` method, call `start_scheduler()` in `_start_background_tasks()` |

**Implementation detail**:
- Guard with env var `ENABLE_INLINE_TUNER=true` (default: false, since docker-compose already runs separate container)
- Add to `EngineOrchestrator.__init__`: `self._tuner = None`
- Add `_init_tuner()` after risk init section (~line 830)
- Add tuner start in `_start_background_tasks()` (~line 1209)
- Log: `"Scheduled tuner started (inline, every Sunday 02:00 UTC)"`

**Tests to add**:
- `engine/tests/unit/test_main_tuner.py` -- verify `_init_tuner()` creates ScheduledTuner when `ENABLE_INLINE_TUNER=true`
- Verify cron trigger mock (existing test pattern in `test_scheduled_tuner.py`)

**Acceptance criteria**:
- [ ] `ENABLE_INLINE_TUNER=true` -> engine startup logs `"Scheduled tuner started"`
- [ ] `ENABLE_INLINE_TUNER` unset/false -> no tuner started (no error)
- [ ] Weekly Sunday 02:00 UTC cron fires optimization (mock test)
- [ ] No conflict with existing docker-compose `auto-tuner` service

---

### 3.3 US-147: Attribution TimescaleDB Integration + Materialized Views

**Problem**: `PerformanceAttribution` (`engine/src/analysis/attribution.py`) stores trades only in-memory (`self._trades` list). Engine restart loses all attribution data. Materialized views DDL exists in both `attribution.py:migration_sql()` and `docker/init.sql` (lines 267-297) but Attribution class cannot query them.

**Changes**:

| File | Action | Details |
|------|--------|---------|
| `engine/src/analysis/attribution.py` | ADD | Add `async load_from_db(dsn, days=7)` class method to query `execution_log` |
| `engine/src/analysis/attribution.py` | ADD | Add `async refresh_views(dsn)` to refresh materialized views |
| `docker/init.sql` | VERIFY | Confirm materialized views DDL is present (already confirmed: lines 267-297) |

**Implementation detail for `load_from_db()`**:
```python
@classmethod
async def load_from_db(cls, dsn: str, days: int = 7) -> "PerformanceAttribution":
    """Load trades from TimescaleDB execution_log."""
    import asyncpg
    conn = await asyncpg.connect(dsn.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        rows = await conn.fetch("""
            SELECT signal_id, ts, strategy_id, buy_exchange, sell_exchange, symbol, net_pnl, size
            FROM execution_log
            WHERE ts >= NOW() - make_interval(days => $1)
              AND status = 'filled'
            ORDER BY ts ASC
        """, days)

        attr = cls()
        for r in rows:
            attr.add_trade(TradeRecord(
                trade_id=r["signal_id"] or "",
                timestamp=r["ts"],
                strategy_id=r["strategy_id"],
                exchange_buy=r["buy_exchange"],
                exchange_sell=r["sell_exchange"],
                pair=r["symbol"],
                pnl=float(r["net_pnl"]) if r["net_pnl"] else 0.0,
                size_usd=float(r["size"]) if r["size"] else 0.0,
            ))
        return attr
    finally:
        await conn.close()
```

**Materialized view refresh**:
```python
@staticmethod
async def refresh_views(dsn: str) -> None:
    """Refresh attribution materialized views."""
    import asyncpg
    conn = await asyncpg.connect(dsn.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        for view in ["strategy_daily_pnl", "exchange_daily_pnl", "pair_daily_pnl"]:
            await conn.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {view}")
    except Exception as exc:
        logger.warning("Failed to refresh view: %s", exc)
    finally:
        await conn.close()
```

**Tests to add**:
- `engine/tests/unit/analysis/test_attribution_db.py` -- mock asyncpg, verify `load_from_db()` returns correct TradeRecords
- Verify `refresh_views()` calls REFRESH on all 3 views

**Acceptance criteria**:
- [ ] `PerformanceAttribution.load_from_db(dsn)` returns attribution with trades from DB
- [ ] Engine restart -> `load_from_db()` recovers past trade history
- [ ] `refresh_views()` refreshes all 3 materialized views without error
- [ ] Materialized view DDL in `docker/init.sql` matches `migration_sql()` output
- [ ] Existing in-memory attribution path unchanged

---

### 3.4 US-148: Shadow MDD Percentage + InventoryRebalancer Balance Feed

**Problem A**: `ShadowStats.max_drawdown` (line 344 of shadow.py) is absolute USD only. `_compute_drawdown()` (line 1694) calculates `peak_pnl - pnl` in USD. No percentage field exists.

**Problem B**: `InventoryRebalancer` initialized in main.py (line 816) with `balance_feed=NOT_CONNECTED` log. The `BalanceTracker` instance is created but never receives real balance data in Live mode.

**Changes**:

| File | Action | Details |
|------|--------|---------|
| `engine/src/modes/shadow.py` | ADD | Add `max_drawdown_pct: float = 0.0` to `ShadowStats` dataclass |
| `engine/src/modes/shadow.py` | MODIFY | Update `_compute_drawdown()` to also compute percentage MDD |
| `engine/src/modes/shadow.py` | MODIFY | Update `_get_daily_summary_data()` and `get_strategy_report()` to include `mdd_pct` |
| `engine/src/main.py` | MODIFY | Wire `BalanceTracker` to exchange adapter balance polling in Live mode |

**Implementation detail for `_compute_drawdown()` update**:
```python
def _compute_drawdown(self) -> None:
    """Update peak_pnl, max_drawdown (USD), and max_drawdown_pct."""
    pnl = self._stats.total_pnl
    if pnl > self._stats.peak_pnl:
        self._stats.peak_pnl = pnl

    drawdown = self._stats.peak_pnl - pnl
    if drawdown > self._stats.max_drawdown:
        self._stats.max_drawdown = drawdown

    # Percentage MDD (0~1 range, guarded against tiny peak)
    if self._stats.peak_pnl > 0.01:  # guard: avoid division by tiny peak
        dd_pct = drawdown / self._stats.peak_pnl
        if dd_pct > self._stats.max_drawdown_pct:
            self._stats.max_drawdown_pct = dd_pct
```

**Implementation detail for BalanceTracker feed** (main.py):
- In Live/Sandbox mode, after exchange adapter init, start a background task that polls `adapter.fetch_balance()` every 5 minutes
- Feed results into `self._balance_tracker.record_balance()`
- Update log message from `balance_feed=NOT_CONNECTED` to `balance_feed=LIVE`

**Tests to add**:
- `engine/tests/unit/modes/test_shadow_mdd_pct.py` -- verify `max_drawdown_pct` calculation
- Verify `max_drawdown_pct` in range [0, 1]
- Verify edge case: peak_pnl near zero -> no division error
- `engine/tests/unit/test_main_balance_feed.py` -- verify BalanceTracker wiring in Live mode

**Acceptance criteria**:
- [ ] `ShadowStats.max_drawdown_pct` field exists (float, 0~1 range)
- [ ] `_compute_drawdown()` updates both `max_drawdown` (USD) and `max_drawdown_pct`
- [ ] Daily summary and strategy report include `mdd_pct`
- [ ] InventoryRebalancer receives real balance data when `ExecutionMode.LIVE`
- [ ] Log message shows `balance_feed=LIVE` in live mode

---

### 3.5 US-156: Shadow Disabled Strategies .env Configuration

**Problem**: `SHADOW_DISABLED_STRATEGIES` env var parsing already exists in `shadow.py` (lines 509-513), but the env var is not set in either `.env` file, and not passed through `docker-compose.yml`.

**Known loss-making strategies** (from CLAUDE.md): `statistical_arb_v1`, `spot_futures_v1`, `latency_arb_v1`

**Changes**:

| File | Action | Details |
|------|--------|---------|
| `engine/.env` | ADD | `SHADOW_DISABLED_STRATEGIES=statistical_arb_v1,spot_futures_v1,latency_arb_v1` |
| `.env` (root) | ADD | Same line (sync requirement) |
| `docker-compose.yml` | VERIFY | Engine service uses `env_file: .env` (line 33) -- env var auto-passed. No explicit addition needed |

**Verification plan**:
- Shadow 10min run -> grep logs for `shadow_mode.strategy_disabled` for the 3 disabled strategies
- Verify 0 trades from `stat_arb`, `spot_futures`, `latency_arb`
- Verify overall Shadow PnL > 0

**Tests to add**:
- `engine/tests/unit/modes/test_shadow_disabled_env.py` -- verify env var parsing and signal/trade rejection

**Acceptance criteria**:
- [ ] `engine/.env` contains `SHADOW_DISABLED_STRATEGIES=statistical_arb_v1,spot_futures_v1,latency_arb_v1`
- [ ] Root `.env` contains identical line
- [ ] `docker-compose.yml` engine service receives the env var (via `env_file: .env`)
- [ ] Shadow 10min: stat_arb / spot_futures / latency_arb trade count = 0
- [ ] Shadow 10min: PnL > 0 (loss strategies excluded)

---

## 4. Batch Execution Plan

| Batch | US | Domain | Parallelism | Estimated Effort |
|-------|-----|--------|-------------|-----------------|
| 1a | US-145 | engine/tuning | Sequential (first) | MEDIUM |
| 1b | US-146 | engine/main.py | Sequential (after 1a) | LOW |
| 2 | US-147 | engine/analysis | Parallel with Batch 3,4 | MEDIUM |
| 3 | US-148 | engine/modes + main.py | Parallel with Batch 2,4 | MEDIUM |
| 4 | US-156 | config/.env | Parallel with Batch 2,3 | LOW |

**Total files modified**: ~10
**Total new test files**: ~5
**Estimated complexity**: MEDIUM

## 5. Test Plan

```bash
# Unit tests (per-US)
cd engine && python -m pytest tests/unit/tuning/ -x --tb=short -v
cd engine && python -m pytest tests/unit/analysis/ -x --tb=short -v
cd engine && python -m pytest tests/unit/modes/ -x --tb=short -v

# Full regression
cd engine && python -m pytest tests/ -x --tb=short

# Shadow validation (Stage D)
cd engine && timeout 600 python -m src.main
# Verify: stat_arb/spot_futures/latency_arb = 0 trades
# Verify: PnL > 0
# Verify: mdd_pct field in summary output
```

## 6. Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| DataLoader SQL schema mismatch causes runtime error | HIGH | Fix SQL columns FIRST (US-145), test with mock asyncpg |
| ScheduledTuner in main.py conflicts with docker container | MEDIUM | Guard with `ENABLE_INLINE_TUNER` env var (default off) |
| Materialized view REFRESH CONCURRENTLY requires unique index | MEDIUM | Verify index exists or use non-concurrent refresh |
| Division by zero in MDD pct when peak_pnl ~0 | LOW | Guard with `peak_pnl > 0.01` threshold |
| .env sync forgotten between engine/.env and root .env | LOW | Explicit checklist item in US-156 |

## 7. Success Criteria (Phase-level)

- [ ] All 5 US pass acceptance criteria
- [ ] `python -m pytest tests/ -x` -- 0 failures
- [ ] Shadow 10min: PnL > 0, crash 0, disabled strategies 0 trades
- [ ] `ShadowStats.max_drawdown_pct` field visible in summary
- [ ] `TUNER_DATA_SOURCE=timescaledb` path exercised (at least in tests)
- [ ] `PerformanceAttribution.load_from_db()` recovers trades from DB
