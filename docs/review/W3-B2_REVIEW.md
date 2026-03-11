# Code Review: Wave 3 Batch 2 — US-116 TCA + US-120 Rebalancer Wiring

**Reviewer**: code-reviewer (opus)
**Date**: 2026-03-12
**Files Reviewed**: 12
**Total Issues**: 5

---

## By Severity

| Severity | Count | Action |
|----------|-------|--------|
| CRITICAL | 1 | Must fix |
| HIGH | 1 | Should fix |
| MEDIUM | 2 | Consider fixing |
| LOW | 1 | Optional |

---

## Stage 1: Spec Compliance

### US-116 — Transaction Cost Analysis (TCA)

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Implementation Shortfall (IS) tracking | PASS | `tca.py:69` — `abs(fill - expected) / expected * 10_000` bps |
| Rolling percentile windows (P50/P95/P99) | PASS | `PercentileTracker` with configurable `window_size` |
| Latency tracking | PASS | `latency_ms` recorded per execution, P50/P95/P99 in summary |
| Fill rate tracking | PASS | `filled_ratio` clamped [0,1], averaged in `get_summary()` |
| API endpoint GET /api/v1/tca/summary | PASS | `routes/tca.py:10` — returns 7-field JSON |
| Dashboard widget | PASS | `TCAWidget.tsx` — 3-column layout (IS, latency, fill rate) |
| Engine wiring (record on execution) | PASS | `main.py:810-828` — feeds TCA on `_on_execution_result` |
| Tests | PASS | 19 unit + 2 API route = 21 tests |

**Verdict**: Spec compliance PASS.

### US-120 — Inventory Rebalancer Wiring

| Requirement | Status | Evidence |
|-------------|--------|----------|
| InventoryRebalancer instantiation from env vars | PASS | `main.py:738-742` — 3 env vars with defaults |
| Background loop with periodic check | PASS | `main.py:850-882` — `_rebalancer_loop` |
| Telegram alert on imbalance | PASS | `main.py:856-876` — critical + warning alerts |
| Context population | PASS | `main.py:912` — `self.context.rebalancer = self._rebalancer` |
| Tests | PASS | 7 wiring tests |

**Verdict**: Spec compliance PASS.

---

## Stage 2: Code Quality Issues

### [CRITICAL] Missing authentication on TCA API endpoint

**File**: `engine/src/api/routes/tca.py:10`
**Issue**: The `/api/v1/tca/summary` endpoint has NO `Depends(require_auth)` guard. Every other data endpoint in the project (attribution, portfolio, shadow, trading, settings, alerts, funding, exchanges, risk, strategies) uses `dependencies=[Depends(require_auth)]`. Only `/health` is intentionally unauthenticated (liveness probe). TCA data (execution quality, latency, fill rates) is exposed to unauthenticated callers.

**Evidence**: `tca.py` imports only `APIRouter, Request` from FastAPI — no `Depends` import, no `require_auth` reference. Compare with `attribution.py:11` which imports `from src.api.auth import require_auth` and uses `dependencies=[Depends(require_auth)]` on every route.

**Fix**:
```python
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from src.api.auth import require_auth

router = APIRouter(prefix="/api/v1/tca", tags=["tca"])

@router.get("/summary", dependencies=[Depends(require_auth)])
async def get_tca_summary(request: Request) -> JSONResponse:
    ...
```

---

### [HIGH] Rebalancer uses disconnected BalanceTracker

**File**: `engine/src/main.py:737`
**Issue**: `_balance_tracker = BalanceTracker()` creates a brand-new, empty tracker that is never connected to any exchange balance data source. The `InventoryRebalancer` will call `tracker.get_total_balance()` and `tracker.get_all_exchanges()` on an always-empty tracker, so `has_critical_imbalance()` always returns `False` and `check_and_suggest()` always returns `[]`. The rebalancer loop runs every 4 hours doing nothing.

The correct approach is to either:
1. Wire the same `BalanceTracker` instance that receives balance updates from exchange adapters, or
2. Feed balance snapshots into this tracker from the existing `_exchanges` dict or position manager.

**Fix**: Pass the engine's actual balance tracker (if one exists), or wire `_balance_tracker` to receive balance updates in the exchange adapter callbacks. Example:
```python
# If engine already has a balance tracker:
self._rebalancer = InventoryRebalancer(
    tracker=self._balance_tracker_from_exchanges,
    ...
)
# Or feed balances in the health check loop:
for eid, adapter in self._exchanges.items():
    balance = await adapter.get_balance()
    self._rebalancer.tracker.update(eid, balance)
```

---

### [MEDIUM] Rebalancer loop sleeps before first check

**File**: `engine/src/main.py:854`
**Issue**: `_rebalancer_loop` calls `await asyncio.sleep(self._rebalancer.check_interval_s)` at the TOP of the loop body. With the default 4-hour interval, the first imbalance check won't happen until 4 hours after engine startup. A pre-existing imbalance at startup will go undetected for 4 hours.

**Fix**: Move the sleep to the end of the loop body or add an initial check before entering the loop:
```python
async def _rebalancer_loop(self) -> None:
    while self.state.running:
        try:
            # Check first, then sleep
            if self._rebalancer.has_critical_imbalance() and self._telegram:
                ...
            suggestions = self._rebalancer.check_and_suggest()
            ...
            await asyncio.sleep(self._rebalancer.check_interval_s)
        except asyncio.CancelledError:
            break
```

---

### [MEDIUM] TCA expected_price fallback silently drops records

**File**: `engine/src/main.py:815-818`
**Issue**: The expected_price extraction chain falls back to `0` when neither `trade_request.expected_price` nor `leg.order.price` is available:
```python
expected = float(
    getattr(trade_request, 'expected_price', None)
    or getattr(getattr(leg, 'order', None), 'price', 0) or 0
)
```
When `expected` is 0, `TCAAnalyzer.record_execution()` silently returns (line 67-68: `if expected_price <= 0: return`). Executions without an expected_price are silently dropped with no logging, which could bias the TCA sample toward only certain trade types.

**Fix**: Add a debug log when expected_price is unavailable:
```python
if expected <= 0:
    logger.debug("TCA: skipping leg with no expected_price (strategy=%s)", trade_request.strategy_id)
    continue
self._tca_analyzer.record_execution(...)
```

---

### [LOW] Dashboard TCAWidget uses non-null assertions

**File**: `dashboard/src/components/TCAWidget.tsx:69-88`
**Issue**: Multiple `data!.field` non-null assertions (e.g., `data!.is_p50_bps.toFixed(1)`). While guarded by the `empty` check on line 46, a future refactor could break the guard, causing runtime crashes. This is a minor TypeScript style concern.

**Fix**: Use optional chaining or narrow with an early return:
```tsx
if (!data || data.sample_count === 0) {
  return <EmptyState />;
}
// After this point, `data` is non-null without assertions
return (
  <div>
    <MetricCard value={data.is_p50_bps.toFixed(1)} ... />
  </div>
);
```

---

## Positive Observations

1. **Clean module design**: `TCAAnalyzer` is well-encapsulated with zero external dependencies (no numpy). The `PercentileTracker` is a clean, reusable utility with correct linear interpolation.

2. **Pattern consistency**: US-116/120 init blocks follow the exact same try/except non-fatal pattern as US-114/115/117/118 in Wave 3 Batch 1. Context population and background task registration match existing patterns.

3. **Input validation**: `record_execution()` properly guards against `expected_price <= 0`, clamps `latency_ms` to non-negative, and clamps `filled_ratio` to `[0, 1]`.

4. **Dashboard UX**: TCAWidget has proper empty state, 5-second auto-refresh with cleanup, color-coded thresholds (green/amber/red), and Korean labels for the target audience.

5. **Test quality**: 28 tests total with good edge case coverage (empty state, single value, window overflow, clamping, negative values, sorted order independence).

6. **API graceful degradation**: Route returns zero-filled response when `tca_analyzer` is None, preventing 500 errors during startup or if TCA init fails.

7. **No secrets or injection**: No hardcoded credentials, no SQL, no unsafe string interpolation. Clean.

---

## LSP Diagnostics

| File | Errors | Warnings |
|------|--------|----------|
| `engine/src/analysis/tca.py` | 0 | 0 |
| `engine/src/api/routes/tca.py` | 0 | 0 |
| `engine/src/api/server.py` | 0 | 0 |
| `engine/src/main.py` | 0 | 0 |
| Dashboard (TS) | N/A | TS LSP not installed |

---

## Recommendation

### **CONDITIONAL PASS**

The CRITICAL auth issue (unauthenticated TCA endpoint) and HIGH wiring issue (disconnected BalanceTracker) must be fixed before merge.

| Priority | Issue | Blocking? |
|----------|-------|-----------|
| CRITICAL | Add `require_auth` to TCA route | YES |
| HIGH | Wire BalanceTracker to real balance data | YES |
| MEDIUM | Rebalancer sleep-first loop | No (4h delay acceptable for first release, fix in next iteration) |
| MEDIUM | TCA expected_price silent drop | No (add debug log in next iteration) |
| LOW | TS non-null assertions | No |

**Fix the CRITICAL + HIGH, then this batch is ready to merge.**
