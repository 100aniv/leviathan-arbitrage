# PHOENIX v18 Audit — PASS/FAIL Report

> Audited: 2026-04-10
> Auditor: leviathan-planner (Stage A)

---

## Summary

| # | Item | Priority | Verdict | Notes |
|---|------|----------|---------|-------|
| 1 | Rollback idempotency (`_rollback_attempted`) | P0 | **PASS** | `__init__` L145, check L203, mark L247/L252, clear L394/L794 |
| 2 | DeduplicationGate wiring | P0 | **PASS** | `src/execution/dedup.py` exists. Wired in `live.py` L312-314, L888-891, cleanup loop L1506-1510 |
| 3 | MarginTracker wiring | P0 | **PASS** | `src/execution/margin_tracker.py` exists. Wired in `live.py` L318-320, injected into futures_futures strategy L518-523. Strategy calls `check_and_reserve` L471 |
| 4 | StrandedPositionTracker wiring | P0 | **PASS** | `src/execution/stranded.py` exists. Wired in `executor.py` L142-143. `register()` called at L352, L473, L525, L693, L832, L850 (6 call sites). Conditional halt based on $30 USD threshold |
| 5 | TCA/IS Pipeline (`slippage_total`) | P0 | **PASS** | `live.py` L1044-1062: calculates `_is_buy_bps` + `_is_sell_bps`, sums into `_is_total_bps`, passes to execution_log as `slippage_total` |
| 6 | DB schema (migration 008) | P0 | **PASS** | `008_add_fill_price_tracking.sql` adds `reconciliation_status` + `reconciled_at` columns, index on unmatched status |
| 7 | `get_trades()` on exchange adapters | P0 | **PASS** | `native_binance.py` L514 (`/fapi/v1/userTrades`), `native_bitget.py` L506 (`/api/v2/mix/order/fills`) |
| 8 | TradeReconciler wiring | P0 | **FAIL** | File exists (`src/execution/trade_reconciler.py`) but **NOT imported or called** from `main.py`, `live.py`, or any other module. Dead code -- no 10-min periodic invocation |
| 9 | Binance -4168 handling | P1 | **PASS** | `native_binance.py` L258: detects `-4168` (multi-assets mode), logs info, continues order |
| 10 | spot_futures `max_hold_seconds` | P1 | **PASS** | Already confirmed in v21/v22 -- 14400 -> 1800 |
| 11 | futures_min_spread_bps=20 | P1 | **PASS** | Already confirmed in strategy_params.json |

---

## Detailed Findings

### PASS Items (10/11)

**1. Rollback Idempotency** -- PASS
- `executor.py:145` -- `self._rollback_attempted: dict[str, str] = {}`
- `executor.py:203` -- guard: `if order.order_id and order.order_id in self._rollback_attempted`
- `executor.py:247` -- marks success: `self._rollback_attempted[order.order_id] = "success"`
- `executor.py:252` -- marks failure: `self._rollback_attempted[order.order_id] = "failed"`
- `executor.py:394,794` -- clears dict on trade cycle reset
- Verdict: Fully functional idempotency guard preventing double-rollback.

**2. DeduplicationGate** -- PASS
- `src/execution/dedup.py` -- `DeduplicationGate` class with atomic `check_and_register()` via per-key asyncio.Lock
- `live.py:312-314` -- instantiated: `self._dedup_gate = DeduplicationGate(window_s=10.0)`
- `live.py:888-891` -- called: `if not await self._dedup_gate.check_and_register(collision_key)`
- `live.py:1506-1510` -- cleanup loop: `await self._dedup_gate.cleanup_stale()`
- Verdict: Proper atomic dedup replacing old dict-based race-prone check.

**3. MarginTracker** -- PASS
- `src/execution/margin_tracker.py` -- `MarginTracker` class with async-safe `check_and_reserve()` / `release()`
- `live.py:318-320` -- instantiated in live mode init
- `live.py:518-523` -- injected into `futures_futures` strategy via `set_margin_tracker()`
- `strategies/futures_futures.py:469-479` -- strategy calls `check_and_reserve()` before order
- Note: Wired into strategy layer (futures_futures), NOT into executor.py. This is the correct design -- margin check happens at signal evaluation, not execution.

**4. StrandedPositionTracker** -- PASS
- `src/execution/stranded.py` -- Conditional halt: benign codes (22002, 40762) skip halt, real failures accumulate, halt at $30+ total
- `executor.py:142-143` -- instantiated
- 6 call sites in executor.py (L352, L473, L525, L693, L832, L850)
- Method is `register()` (not `record_failure` as originally named in the plan), but functionally equivalent and more descriptive.

**5. TCA/IS Pipeline** -- PASS
- `live.py:1044-1050` -- IS calculation: `abs(fill - expected) / expected * 10000` bps for buy and sell legs
- `live.py:1062` -- `slippage_total=_is_total_bps` passed to execution_log
- Implementation Shortfall properly computed from actual fill prices vs expected prices.

**6. DB Schema** -- PASS
- `008_add_fill_price_tracking.sql` -- adds `reconciliation_status VARCHAR(20) DEFAULT 'pending'` and `reconciled_at TIMESTAMPTZ`
- Partial index on unmatched records for efficient query.

**7. get_trades()** -- PASS
- `native_binance.py:514-550` -- signed `/fapi/v1/userTrades`, returns normalized dict list
- `native_bitget.py:506-545` -- signed `/api/v2/mix/order/fills`, handles both dict and list responses

**9. Binance -4168** -- PASS
- `native_binance.py:258` -- detects multi-assets mode error, logs info, does not abort order

---

### FAIL Items (1/11)

**8. TradeReconciler Wiring** -- FAIL

**Problem**: `src/execution/trade_reconciler.py` exists with full implementation (104 lines), but is dead code.

Evidence:
- `grep -rn "TradeReconciler\|trade_reconciler\|reconcile_period" src/` returns matches ONLY in:
  - `src/execution/trade_reconciler.py` (the file itself)
  - `src/infra/db/migrations/008_add_fill_price_tracking.sql` (a SQL comment)
- NO import in `main.py`, `live.py`, or any other runtime file
- The class has `reconcile_period()` method designed for 10-min periodic calls, but no caller exists
- The existing `PositionReconciler` in main.py (L1482-1541) is a DIFFERENT component -- it reconciles engine positions vs exchange positions, NOT execution_log vs actual fills

**Impact**: Fill price discrepancies between our DB and exchange records go undetected. This is critical for live trading -- if our execution_log says we filled at $100 but Binance says $100.05, we will have phantom PnL.

**Required Fix**:
1. In `live.py` or `main.py`, import and instantiate `TradeReconciler`
2. Create a 10-min periodic task calling `reconciler.reconcile_period()` for each active exchange
3. Pass `db_pool` and `telegram` for alerting on mismatches
4. Wire into the existing `_reconcile_loop()` in main.py (which already runs at 60s intervals for PositionReconciler -- add TradeReconciler at 10-min cadence)

---

## Implementation Plan for FAIL Item

### Task: Wire TradeReconciler into live runtime

**File**: `engine/src/modes/live.py` (preferred) or `engine/src/main.py`

**Steps**:

1. **Import** in `live.py.__init__()`:
   ```python
   from src.execution.trade_reconciler import TradeReconciler
   self._trade_reconciler = TradeReconciler(db_pool=self._db_pool, telegram=self._telegram)
   ```

2. **Periodic task** -- add to `_start_background_tasks()`:
   ```python
   asyncio.create_task(self._trade_reconcile_loop(), name="trade_reconcile")
   ```

3. **Loop implementation**:
   ```python
   async def _trade_reconcile_loop(self) -> None:
       """10-min periodic: reconcile execution_log vs exchange fills."""
       while not self._halt_event.is_set():
           await asyncio.sleep(600)  # 10 minutes
           try:
               since_ms = int((time.time() - 660) * 1000)  # 11-min lookback (overlap)
               for ex_id, adapter in self._exchanges.items():
                   report = await self._trade_reconciler.reconcile_period(
                       exchange_adapter=adapter,
                       exchange_id=ex_id,
                       since_ms=since_ms,
                   )
                   if report.unmatched_internal:
                       logger.warning("trade_recon_mismatch exchange=%s unmatched=%d",
                                      ex_id, len(report.unmatched_internal))
           except Exception as exc:
               logger.error("trade_reconcile_loop error: %s", exc)
   ```

4. **Test**: Add unit test verifying TradeReconciler is instantiated and loop is scheduled

**Estimated effort**: ~30 lines of wiring code + 1 test file

---

## Entry Gate Assessment

- SSOT alignment: TradeReconciler is part of PHOENIX v18 execution safety
- No PRD US conflict (this is bug-fix/wiring, not a new US)
- No file boundary conflict with other active work
- WIRING AC: create (done) -> inject (MISSING) -> call (MISSING)
