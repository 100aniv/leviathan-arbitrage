# Code Review: PRE-FIX (HIGH 3 + MEDIUM 5) + US-111 + US-112 (J-EXT Wave 2 Batch 3)

**Reviewer:** code-reviewer (opus)
**Date:** 2026-03-12
**Files Reviewed:** 15
**Tests:** 109 passed, 0 failed (test_trading_routes + test_portfolio_routes + test_portfolio + test_api_server_routes)
**LSP Diagnostics:** Python -- clean (0 errors). TypeScript LSP unavailable.

---

## Summary

| Severity | Count | Action |
|----------|-------|--------|
| CRITICAL | 0     | --     |
| HIGH     | 0     | --     |
| MEDIUM   | 2     | Should fix |
| LOW      | 3     | Optional |

**Verdict: APPROVE** -- All 3 previous HIGH issues and 5 MEDIUM issues correctly resolved. US-111/US-112 acceptance criteria met. 2 new MEDIUM issues found (non-blocking).

---

## Part 1: PRE-FIX Verification (Previous Review Issues)

### HIGH-1: Hardcoded 100,000 initial capital -- RESOLVED

**File:** `engine/src/api/routes/portfolio.py:107,118,128`

Both shadow and fallback paths now use:
```python
initial_capital = ctx.runtime_settings.get("initial_capital", 100000)
```

Test coverage: `TestEquityCurveInitialCapital` (2 tests) verifies custom capital and default fallback.

### HIGH-2: Calmar ratio annualization error -- RESOLVED

**File:** `engine/src/api/routes/portfolio.py:158-169`

Calmar now:
1. Defaults to `None` (not `0.0`)
2. Requires `mdd > 0` AND `session_start_ts` present AND `elapsed_days >= 1`
3. Uses correct formula: `(total_pnl / initial_capital * 100) / elapsed_days * 365 / mdd`

Test coverage: `test_calmar_ratio_is_none_when_mdd_zero`, `test_calmar_ratio_is_none_when_session_less_than_one_day` (2 tests).

### HIGH-3: sharpe_ratio always 0.0 -- RESOLVED

**File:** `engine/src/api/routes/portfolio.py:142`

Changed from `"sharpe_ratio": 0.0` to `"sharpe_ratio": None`. Never updated without actual calculation data, so `None` is the honest value.

Test coverage: `test_sharpe_ratio_is_none_without_snapshot`, `test_portfolio_metrics_default_values_without_shadow` (2 tests), plus `test_portfolio_metrics_defaults_to_zero` updated assertion.

### MEDIUM-4: GlobalHeatmap raw fetch -- RESOLVED

**File:** `dashboard/src/components/GlobalHeatmap.tsx:88-97`

Removed the raw `fetch()` call with manual token handling. Now derives symbols from the already-fetched `exchangeStatus` data (via the existing `useApi` hook), eliminating the duplicate fetch and 401-redirect gap.

### MEDIUM-5: portfolio/page.tsx fetchApi usage -- RESOLVED

**File:** `dashboard/src/app/portfolio/page.tsx:41-51`

Replaced `fetchApi` calls with typed helpers: `getPortfolioMetrics()`, `getPortfolioSummary()`, `getEquityCurve()`. These use the `request()` function which handles 401 redirect.

### MEDIUM-6: HTTPException late import in settings.py -- RESOLVED

**File:** `engine/src/api/routes/settings.py:7`

`HTTPException` moved to the top-level import. The `from fastapi import HTTPException` inside the function body is removed.

### MEDIUM-7: daily_pnl == total_pnl duplicate field -- RESOLVED

**File:** `engine/src/api/routes/portfolio.py:94`

`daily_pnl` field removed, replaced with `"pnl_scope": "session"` metadata. Test coverage confirms no `daily_pnl` in response.

### MEDIUM-8: Single-point SVG rendering -- RESOLVED

**File:** `dashboard/src/components/EquityCurve.tsx:24-41`

Added dedicated `data.length === 1` branch that renders a labeled dot with equity value and date, instead of an invisible single-M path.

---

## Part 2: Stage 1 -- Spec Compliance

### US-111: Trade Detail ("Why this trade?")

**PRD Acceptance Criteria:**
1. "거래 클릭 시 상세 패널: 감지된 가격 차이, 예상수익, 실제수수료, 실제수익" -- **MET**
2. "API 거래 데이터에 reason/spread_bps/fee_usd/net_pnl 필드 포함" -- **MET**

**Implementation:**
- `GET /api/v1/trades/{trade_id}` endpoint added (trading.py:109-122) with JWT auth
- Returns 404 when trade not found
- `setdefault()` adds `reason`, `spread_bps`, `fee_usd`, `net_pnl`, `expected_pnl` if missing from stored trade
- `TradeDetail.tsx` side panel shows: ID, symbol, strategy, route, entry/exit prices, size, spread, expected PnL, fee, net PnL, status, reason, timestamp
- Row click triggers side panel display

**Tests:** 6 tests in `TestGetTradeDetail` (200 found, required fields, values correct, 404, auth required, find among multiple)

### US-112: Trade Filtering + CSV Export

**PRD Acceptance Criteria:**
1. "날짜 범위 / 전략 / 거래소 / 페어 필터" -- **MET**
2. "CSV 내보내기 버튼 (필터 적용 상태 그대로)" -- **MET**
3. "API GET /api/v1/trades?from=&to=&strategy=&exchange= 파라미터 지원" -- **MET**

**Implementation:**
- `GET /api/v1/trades` extended with `exchange`, `symbol`, `from`, `to` query params (trading.py:80-106)
- `from`/`to` use FastAPI `Query(alias=...)` for clean parameter names
- `to_date` includes end-of-day padding when only date provided
- Dashboard filter bar: date range inputs, strategy dropdown, exchange dropdown, symbol text input
- CSV export: generates file from current (filtered) trade list
- `getTrades` in api.ts updated to accept object params

**Tests:** 8 tests in `TestListTradesFilters` (strategy, exchange buy/sell side, symbol, date from, date to, combined AND logic, no filter, auth)

---

## Part 3: Stage 2 -- Code Quality Issues

---

### [MEDIUM-1] EquityCurve: btc_benchmark type mismatch -- null not handled in multi-point path

**File:** `dashboard/src/components/EquityCurve.tsx:6,43-44,51`

```typescript
interface DataPoint {
  btc_benchmark: number;  // line 6 — typed as non-null
}

// line 43-44 — Math.min/max with null produces wrong scaling
const maxEquity = Math.max(...data.map(d => Math.max(d.equity, d.btc_benchmark)));
const minEquity = Math.min(...data.map(d => Math.min(d.equity, d.btc_benchmark)));

// line 51 — toY(null) produces NaN in SVG path
const btcPath = data.map((d, i) => `${i === 0 ? 'M' : 'L'}${toX(i)},${toY(d.btc_benchmark)}`).join(' ');
```

**Problem:** The API now returns `btc_benchmark: null` (portfolio.py:120,130). The `api.ts` type correctly declares `btc_benchmark: number | null` (line 165). However:
- `EquityCurve.tsx` declares `btc_benchmark: number` (no null)
- `portfolio/page.tsx:36` also uses `btc_benchmark: number` in state type
- When data has >1 point (future feature), `Math.min(equity, null)` returns `0` (JavaScript coerces null to 0), corrupting Y-axis scaling
- `toY(null)` produces `NaN` in the SVG path string

Currently mitigated by the single-point branch (line 24) which skips the multi-point code, but the type contract is broken.

**Fix:** Update `DataPoint.btc_benchmark` to `number | null`. Guard the multi-point path:
```typescript
interface DataPoint {
  btc_benchmark: number | null;
}
// In multi-point block:
const validBenchmarks = data.filter(d => d.btc_benchmark !== null);
// Only render btcPath if validBenchmarks.length > 0
```
Also update `portfolio/page.tsx:36` state type to match.

---

### [MEDIUM-2] CSV export: no field escaping -- commas in values break CSV format

**File:** `dashboard/src/app/trades/page.tsx:69`

```typescript
const csv = [headers, ...rows].map((r) => r.join(",")).join("\n");
```

**Problem:** If any field value contains a comma (e.g., a symbol like `"1,000SATS/USDT"` or a reason string with commas), the CSV output will have misaligned columns. Values containing commas, quotes, or newlines must be quoted per RFC 4180.

This is a data integrity issue, not a security issue (the CSV is generated client-side from trusted API data).

**Fix:** Wrap each cell in quotes and escape internal quotes:
```typescript
const escapeCell = (v: unknown) => {
  const s = String(v ?? "");
  return s.includes(",") || s.includes('"') || s.includes("\n")
    ? `"${s.replace(/"/g, '""')}"`
    : s;
};
const csv = [headers, ...rows].map((r) => r.map(escapeCell).join(",")).join("\n");
```

---

### [LOW-1] TradeDetail uses in-memory data, not the detail endpoint

**File:** `dashboard/src/app/trades/page.tsx:179`, `dashboard/src/components/TradeDetail.tsx`

**Observation:** When a trade row is clicked, `setSelectedTrade(trade)` passes the list-view trade object directly. The `GET /api/v1/trades/{trade_id}` endpoint (which enriches with `reason`, `spread_bps`, `fee_usd`, `net_pnl` via `setdefault()`) is never called from the frontend.

This works because the trade data in `ctx.trade_history` may already contain these fields from the engine's signal/execution pipeline. The `setdefault()` in the detail endpoint is a fallback for incomplete records. However, clicking a trade with missing fields will show `undefined` values in the detail panel rather than the defaults the API would provide.

**Fix (optional):** Either (a) add a `getTradeDetail(id)` helper in `api.ts` and fetch on click, or (b) document that the detail endpoint is for programmatic/external API consumers while the UI uses pre-loaded data.

---

### [LOW-2] No upper bound on `limit` parameter

**File:** `engine/src/api/routes/trading.py:88`

```python
limit: int = 50,
```

**Observation:** `limit` has no max bound. A client can pass `limit=999999`, causing the server to sort and return all trades. The `trade_history` is a bounded `deque(maxlen=10_000)`, so worst case is sorting/serializing 10K items. Acceptable for this use case but not ideal.

**Fix (optional):** Add `Query(default=50, le=500)` to cap the limit.

---

### [LOW-3] `from_date` comparison relies on ISO string lexicographic ordering

**File:** `engine/src/api/routes/trading.py:100`

```python
if from_date:
    trades = [t for t in trades if t.get("timestamp", "") >= from_date]
```

**Observation:** This comparison works correctly when both sides are ISO 8601 format (e.g., `"2026-03-12"` vs `"2026-03-12T10:30:00+00:00"`) because ISO 8601 is designed for lexicographic ordering. The `to_date` path has the `to_cmp` end-of-day padding which correctly handles date-only values. The `from_date` path does not need similar padding since `"2026-03-12" <= "2026-03-12T00:00:00"` is True. This is correct behavior.

No fix needed -- noting for documentation.

---

## API Contract Evaluation (Breaking Changes)

| Endpoint | Breaking? | Notes |
|----------|-----------|-------|
| `GET /api/v1/trades` | **Soft break** | Signature changed from `(strategy?, limit?)` to `(strategy?, exchange?, symbol?, from?, to?, limit?)`. Backward compatible -- new params are optional, existing callers unaffected. |
| `GET /api/v1/trades/{trade_id}` | No | New endpoint |
| `GET /api/v1/portfolio-summary` | **Soft break** | `daily_pnl` removed, `pnl_scope` added. Consumers relying on `daily_pnl` will see `undefined`. |
| `GET /api/v1/portfolio/equity-curve` | **Soft break** | `btc_benchmark` changed from `100000` to `null`. Consumers expecting a number may break on null. |
| `GET /api/v1/portfolio/metrics` | **Soft break** | `sharpe_ratio` and `calmar_ratio` changed from `0.0` to `null`. Consumers doing arithmetic on these will get NaN. |
| `getTrades` (api.ts) | **Breaking** | Changed from `(strategy?: string, limit?: number)` to `(params?: {...})`. All callers must update. Verified: only `trades/page.tsx` calls it, already updated. |

All soft breaks are intentional improvements (previous values were misleading). Dashboard callers have been updated.

---

## Security Check

| Item | Result |
|------|--------|
| JWT auth (`require_auth`) | PASS -- All new/modified endpoints protected |
| XSS | PASS -- React JSX auto-escaping, no dangerouslySetInnerHTML |
| SQL/Command Injection | PASS -- No DB queries, no subprocess calls |
| Hardcoded secrets | PASS -- None found |
| CSV Injection | N/A -- CSV generated client-side from trusted API data, not user input. Formula injection (=, +, -, @) not a risk here since data originates from the engine |
| Path traversal (trade_id) | PASS -- `trade_id` only used for dict key comparison, not file/DB path |
| Limit DoS | LOW -- No upper bound on `limit` param, but bounded by deque maxlen=10,000 |

---

## Test Coverage Summary

| Test File | Tests | Coverage Area |
|-----------|-------|---------------|
| `test_trading_routes.py` (new) | 14 | US-111 detail (6) + US-112 filters (8) |
| `test_portfolio_routes.py` | 13 new | HIGH-1/2/3 + MEDIUM-7 fixes |
| `test_api_server_routes.py` | +10 | US-107 mode switch + US-108 portfolio + sharpe_ratio fix |
| `test_portfolio.py` (api/) | 2 updated | daily_pnl removal + pnl_scope assertion |
| **Total new/updated** | **39** | |

---

## Final Verdict

### APPROVE

All 3 previous HIGH issues are properly resolved with correct logic and test coverage. All 5 MEDIUM issues from the previous review are addressed. US-111 and US-112 acceptance criteria are fully met. The 2 new MEDIUM issues (btc_benchmark type mismatch, CSV escaping) are non-blocking:

- MEDIUM-1 is currently mitigated by the single-point branch and only manifests when multi-day data is implemented
- MEDIUM-2 only affects edge cases with unusual symbol names containing commas

**Recommended follow-up (non-blocking):**
1. Fix `EquityCurve.tsx` `btc_benchmark` type to `number | null` before multi-day equity curve implementation
2. Add RFC 4180 compliant CSV escaping
3. Consider calling `GET /api/v1/trades/{trade_id}` from the frontend for enriched detail data
