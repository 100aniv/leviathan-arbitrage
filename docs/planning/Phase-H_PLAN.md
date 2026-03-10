# Phase H: US-069 + US-070 + US-071 — Dashboard Enhancement Plan

**Created**: 2026-03-11
**Phase**: H (Dashboard Integration)
**Mode**: DELIBERATE (consensus with pre-mortem + expanded test plan)
**Scope**: 3 US across ~12 files, 4 new components, 3 page enhancements, 2 component upgrades

---

## RALPLAN-DR Summary

### Principles (5)
1. **Enhance, don't rewrite** — All pages/components already exist. Extend them with real data bindings, not greenfield rebuilds.
2. **WS-first, REST-fallback** — `useEngineWs` (state_update) provides core KPIs every 1s. Use REST polling only for data not in state_update (exchanges, risk/metrics, alerts, attribution, funding).
3. **Terminal dark theme consistency** — All new components must use existing Tailwind tokens (`terminal-*`, `profit`, `loss`, `accent`, `warn`) and JetBrains Mono font. No new color values.
4. **Zero backend changes** — All required API endpoints already exist. No engine modifications for US-069/070/071.
5. **Progressive data binding** — Mock data fallback when engine is offline (existing pattern in GlobalHeatmap/OrderbookView). Real data replaces mocks seamlessly when connected.

### Decision Drivers (Top 3)
1. **Data availability**: `state_update` WS provides {running, kill_switch, mode, strategies[], pnl, positions[], shadow_stats}. Exchange status, risk metrics, alerts, attribution, funding require REST polling.
2. **market_data/market_book WS not broadcasting**: Engine `_dashboard_feed_loop` only sends `state_update`. No `market_data` or `market_book` WS events exist yet. GlobalHeatmap and OrderbookView must use REST polling fallback (exchange status + existing mock pattern).
3. **Existing component architecture**: Two WS systems coexist — `useEngineWs` (simple hook, state_update only) and `useWebSocket` + `WebSocketManager` (more complex, used by GlobalHeatmap/OrderbookView). New components should use `useEngineWs` for state_update data and `useApi` for REST polling.

### Viable Options

**Option A: Incremental Enhancement (SELECTED)**
- Enhance existing pages in-place, add new components alongside existing ones
- Use `useEngineWs` + `useApi` hooks for all data binding
- Add 4 new components (PortfolioSummary, RiskGauge, EventFeed, PerformanceTrend)
- Pros: Minimal risk, preserves working code, clear diff boundaries
- Cons: Some duplication between overview KPI cards and existing PnLChart summary

**Option B: Overview Page Full Redesign**
- Gut the Overview page and rebuild with a new component hierarchy
- Pros: Cleaner architecture, single source of truth
- Cons: Higher risk, may break existing functionality, larger diff
- **Invalidated**: Violates Principle #1 (Enhance, don't rewrite). Current page works; we need to add sections, not replace them.

---

## ADR: Dashboard Enhancement Architecture

**Decision**: Incremental enhancement with new sub-components injected into existing page layouts.

**Drivers**: (1) Existing pages work and render correctly with mock/REST data. (2) WS state_update covers ~60% of required data. (3) REST endpoints cover remaining 40%. (4) No backend changes needed.

**Alternatives considered**: Full page rewrites, new page routes, custom WS events for market data.

**Why chosen**: Lowest risk path that meets all acceptance criteria. All API endpoints exist. Component-level additions are independently testable.

**Consequences**: Some data fetching may be duplicated (e.g., PnL from both WS and REST in different components). Acceptable trade-off for isolation.

**Follow-ups**: When engine adds `market_data` WS broadcast (future), migrate GlobalHeatmap/OrderbookView from REST polling to WS.

---

## Pre-Mortem: 3 Failure Scenarios

### Scenario 1: `npm run build` Type Errors
**What happens**: New components introduce TypeScript type mismatches with existing `StateUpdateData` interface or API response shapes.
**Mitigation**: Define all new types in `types/index.ts` before implementation. Verify against actual engine API response shapes documented in route files. Run `npm run build` after each component, not just at the end.

### Scenario 2: REST Polling Overload
**What happens**: Overview page polls 4+ REST endpoints simultaneously (exchanges, risk/metrics, alerts, pnl) at 2-5s intervals, overwhelming the engine API.
**Mitigation**: Stagger polling intervals (exchanges: 10s, risk: 5s, alerts: 10s, performance: 30s). Use SWR deduplication (`dedupingInterval: 2000`). Overview page gets most data from WS state_update (free, already streaming).

### Scenario 3: Mobile Layout Breakage
**What happens**: New KPI cards, risk gauge, and event feed overflow on mobile viewports. Sidebar overlap with new content.
**Mitigation**: Use responsive grid classes consistently (`grid-cols-1 sm:grid-cols-2 xl:grid-cols-4`). Test at 375px width. The existing layout (Sidebar + main content) already handles mobile via hamburger menu.

---

## Expanded Test Plan

### Unit Tests (Jest + Testing Library)
- `PortfolioSummary.test.tsx`: Renders 4 KPI cards with correct values from WS data; handles null data gracefully
- `RiskGauge.test.tsx`: Renders drawdown gauge, kill switch state, circuit breaker; handles API error state
- `EventFeed.test.tsx`: Renders alert list, severity badges, auto-scroll; handles empty state
- `PerformanceTrend.test.tsx`: Renders 7-day mini chart with PnL/WR data; handles loading state
- `GlobalHeatmap.test.tsx` (enhance): Verify REST polling fallback when WS not connected
- `OrderbookView.test.tsx` (enhance): Verify REST polling for orderbook data

### Integration Tests
- Overview page renders all sections (system status + KPIs + exchange bar + risk + events + performance + heatmap + PnL + strategies + orderbook + shadow)
- Attribution page fetches and displays pie chart data
- Funding page renders history table alongside existing matrix
- System page shows exchange connection details from `/api/v1/exchanges`

### E2E Tests (manual, browser-verifier agent)
- Navigate all 4 pages, verify no blank sections when engine is running
- Verify mobile responsive layout at 375px, 768px, 1280px breakpoints
- Verify `npm run build` produces 0 errors and 0 type warnings

### Observability
- Each REST-polled component logs connection state (loading/error/connected) via existing `useApi` error handling
- WS connection indicator already present in header (`LIVE`/`OFFLINE`)

---

## Data Flow Architecture

```
Engine (Python)
  |
  +-- WS /ws/feed (1s interval) ──> useEngineWs hook
  |     state_update: {running, kill_switch, mode, strategies[], pnl{}, positions[], shadow_stats}
  |     Used by: OverviewPage (status badge, KPI cards), PnLChart, StrategyPanel, ShadowPanel
  |
  +-- REST /api/v1/exchanges (10s poll) ──> useApi hook
  |     {exchange_id: {connected, latency_ms, symbols_count, balance}}
  |     Used by: ExchangeStatusBar (new), SystemPage (enhance)
  |
  +-- REST /api/v1/risk/metrics (5s poll) ──> useApi hook
  |     {kill_switch_active, circuit_breaker_state, max_drawdown_pct, daily_loss_pct, position_count}
  |     Used by: RiskGauge (new)
  |
  +-- REST /api/v1/alerts (10s poll) ──> useApi hook
  |     [{id, type, severity, message, timestamp}]
  |     Used by: EventFeed (new)
  |
  +-- REST /api/v1/pnl (30s poll) ──> useApi hook
  |     {realized_pnl, unrealized_pnl, total_pnl}
  |     Used by: PerformanceTrend (new) — historical accumulation
  |
  +-- REST /api/v1/attribution (5s poll) ──> useApi hook (existing)
  |     {by_strategy[], by_exchange[], by_pair[], by_hour[]}
  |     Used by: AttributionPage (enhance with pie chart)
  |
  +-- REST /api/v1/funding-rates (10s poll) ──> useApi hook (existing)
  |     {exchange: {symbol: {rate, next_funding_time}}}
  |     Used by: FundingPage (enhance with history chart)
  |
  +-- REST /api/v1/strategy-metrics (10s poll) ──> useApi hook
  |     {strategies: {id: {signals, fills, pnl}}}
  |     Used by: AttributionPage pie chart, PerformanceTrend
  |
  +-- REST /health + /api/v1/status (5s poll) ──> useApi hook (existing)
  |     Used by: SystemPage (enhance)
  |
  No market_data/market_book WS exists yet:
  +-- GlobalHeatmap: Uses exchange status REST + mock pattern (existing)
  +-- OrderbookView: Uses REST polling + mock pattern (existing)
```

---

## New TypeScript Types

Add to `/Users/100aniv/development/arbitrage_OMC/dashboard/src/types/index.ts`:

```typescript
// ─── Performance Trend Types ─────────────────────────────────────────────────

export interface SessionPnlPoint {
  timestamp: number;      // Unix ms
  pnl: number;
  win_rate: number;
}

// ─── System Status Types (for enhanced System page) ─────────────────────────

export interface ContainerStatus {
  name: string;
  status: 'running' | 'stopped' | 'error';
  cpu_pct: number;
  memory_mb: number;
  uptime: string;
}
```

**Note**: Most types already exist (`ExchangeStatus`, `RiskMetrics`, `Alert`, `AttributionBreakdown`, etc.). Only 2 new types needed.

---

## Step-by-Step Implementation

### Step 1: US-069 — Overview Page Redesign (4 new components + page enhancement)

#### 1.1 Create `PortfolioSummary.tsx` (NEW)
**File**: `/Users/100aniv/development/arbitrage_OMC/dashboard/src/components/PortfolioSummary.tsx`
**Data source**: `useEngineWs` (state_update) for PnL + position count; `useApi(getExchangeStatus)` for total balance
**Content**:
- System status badge: RUNNING (green) / STOPPED (yellow) / ERROR (red) — from `data.running` + `data.kill_switch`
- 4 KPI cards in a `grid-cols-2 sm:grid-cols-4` grid:
  - **Total Balance**: Sum of all exchange balances from `/api/v1/exchanges` (USDT equivalent)
  - **Today PnL**: `data.pnl.total` from WS state_update
  - **Total PnL**: Accumulated from `/api/v1/pnl` REST endpoint
  - **Active Positions**: `data.position_count` from WS state_update
- Each card: `bg-terminal-surface border border-terminal-border rounded-lg p-4` with label (text-terminal-subtle), value (text-2xl font-mono tabular-nums), and delta indicator

**Acceptance criteria**:
- [x] Status badge shows RUNNING/STOPPED/ERROR with correct color
- [x] 4 KPI cards render with real data when engine is connected
- [x] Graceful fallback (dashes) when engine is offline
- [x] Mobile: 2x2 grid at sm, 4x1 at xl

#### 1.2 Create `RiskGauge.tsx` (NEW)
**File**: `/Users/100aniv/development/arbitrage_OMC/dashboard/src/components/RiskGauge.tsx`
**Data source**: `useApi(getRiskMetrics)` with 5s refresh
**Content**:
- Drawdown gauge: SVG semicircle arc showing `max_drawdown_pct` (0-100%). Color transitions: green (<5%) -> yellow (5-15%) -> red (>15%)
- Kill Switch indicator: badge-profit (STANDBY) or badge-loss (ACTIVE)
- Circuit Breaker: badge showing state (CLOSED/OPEN/HALF_OPEN)
- Daily loss percentage with color coding

**Acceptance criteria**:
- [x] Drawdown gauge renders with correct percentage and color
- [x] Kill switch state matches `/api/v1/risk/metrics` response
- [x] Circuit breaker state displayed
- [x] Error state shows retry button (consistent with existing pattern)

#### 1.3 Create `EventFeed.tsx` (NEW)
**File**: `/Users/100aniv/development/arbitrage_OMC/dashboard/src/components/EventFeed.tsx`
**Data source**: `useApi(getAlerts)` with 10s refresh, limit=20
**Content**:
- Scrollable list of recent 20 events/alerts
- Each row: timestamp (text-terminal-subtle, 10px mono) | severity badge (critical=badge-loss, warning=badge-warn, info=badge-accent) | message (text-terminal-text)
- Auto-scroll to newest entry on update
- Empty state: "No recent events"

**Acceptance criteria**:
- [x] Renders up to 20 alerts sorted by timestamp (newest first)
- [x] Severity badges use correct colors (critical=red, warning=amber, info=blue)
- [x] Scrollable container with max-height
- [x] Empty state displayed when no alerts

#### 1.4 Create `PerformanceTrend.tsx` (NEW)
**File**: `/Users/100aniv/development/arbitrage_OMC/dashboard/src/components/PerformanceTrend.tsx`
**Data source**: WS `state_update` (pnl.total) + client-side accumulation (same pattern as PnLChart)
**Content**:
- Recharts `AreaChart` showing **Session PnL Trend** (since page load, ~120px height)
- Accumulates PnL snapshots every 5s from WS state_update into local array (max 500 points)
- Win rate computed from positions data (secondary axis)
- Uses same COLORS constants (`#00ff88`, `#ff4d4d`, `#3b82f6`) as PnLChart
- Note: No historical PnL API exists — "7-day" deferred to future US with backend endpoint

**Acceptance criteria**:
- [x] Mini chart renders with PnL area and WR line
- [x] Consistent color scheme with PnLChart
- [x] Graceful handling of insufficient data (< 2 points)
- [x] Loading skeleton while fetching

#### 1.5 Enhance `ExchangeStatusBar` (inline in page.tsx)
**Data source**: `useApi(getExchangeStatus)` with 10s refresh
**Content**:
- Horizontal bar showing 8 exchanges as compact pills
- Each pill: exchange name (uppercase, 10px) | connection dot (green/red) | latency (ms) | symbol count
- Scrollable horizontally on mobile

#### 1.6 Enhance `page.tsx` (Overview)
**File**: `/Users/100aniv/development/arbitrage_OMC/dashboard/src/app/page.tsx`
**Changes**:
- Replace existing header section with `<PortfolioSummary />` (includes status badge + 4 KPIs)
- Add Exchange Status Bar below PortfolioSummary
- Add `<RiskGauge />` panel
- Keep existing layout grid: `GlobalHeatmap + PnLChart` (top), `StrategyPanel + OrderbookView` (middle)
- Add new row: `<EventFeed />` + `<PerformanceTrend />` (side by side, xl:grid-cols-2)
- Keep `<ShadowPanel />` at bottom (full width, visible only when active)
- Keep `<KillSwitch />` in top-right (existing)

**Acceptance criteria**:
- [x] All 7 acceptance criteria from US-069 met
- [x] Mobile responsive: single column stacking
- [x] `npm run build` 0 errors

---

### Step 2: US-070 — Attribution / Funding / System Page Enhancement

#### 2.1 Enhance `attribution/page.tsx`
**File**: `/Users/100aniv/development/arbitrage_OMC/dashboard/src/app/attribution/page.tsx`
**Changes**:
- Add **pie chart** (Recharts `PieChart`) for strategy PnL contribution in the Strategy tab
- Pie chart uses `data.by_strategy` array, slices colored by pnl sign (profit/loss shades)
- Add it above the existing waterfall chart when `activeTab === "strategy"`
- Pie chart legend shows strategy name + percentage contribution
- Keep all existing functionality (tabs, waterfall, heatmap, detail table)

**Acceptance criteria**:
- [x] Pie chart renders on Strategy tab with correct proportions
- [x] Existing waterfall chart and table still work
- [x] All 4 tabs still navigate correctly
- [x] Real-time data from existing `/api/v1/attribution` endpoint

#### 2.2 Enhance `funding/page.tsx`
**File**: `/Users/100aniv/development/arbitrage_OMC/dashboard/src/app/funding/page.tsx`
**Changes**:
- Add **funding history section** below existing matrix table
- Recharts `BarChart` showing historical funding rate per symbol (accumulate data client-side from polling, last N snapshots)
- Add toggle to switch between "Matrix View" (existing) and "History View" (new)
- History table: columns [Timestamp, Exchange, Symbol, Rate, Cumulative Revenue]
- Keep existing exchange summary cards

**Acceptance criteria**:
- [x] Funding history chart renders below matrix
- [x] Matrix view still works unchanged
- [x] Real-time data from existing `/api/v1/funding-rates` endpoint
- [x] `npm run build` 0 errors

#### 2.3 Enhance `system/page.tsx`
**File**: `/Users/100aniv/development/arbitrage_OMC/dashboard/src/app/system/page.tsx`
**Changes**:
- Replace hardcoded CONNECTIONS array with real data from `useApi(getExchangeStatus)` with 5s refresh
- Show per-exchange: connection status (dot), latency_ms, symbols_count, last_update, balance summary
- Add **Docker container status section** (mock data initially since no Docker API endpoint — display 8 containers from known set with status badges)
- Add memory/CPU display section (mock data — placeholder for future Prometheus integration)
- Keep existing Engine Stats panel, enhance with real exchange data

**Acceptance criteria**:
- [x] Exchange connections show real per-exchange status from API
- [x] Docker container section visible (mock data acceptable)
- [x] Memory/CPU section visible (mock data acceptable)
- [x] Existing health/status panels still work
- [x] `npm run build` 0 errors

---

### Step 3: US-071 — GlobalHeatmap + OrderbookView Real Data Binding

#### 3.1 Enhance `GlobalHeatmap.tsx`
**File**: `/Users/100aniv/development/arbitrage_OMC/dashboard/src/components/GlobalHeatmap.tsx`
**Changes**:
- **Primary data source**: `useApi(getExchangeStatus)` with 5s refresh to get real exchange list + symbol counts
- Build spread grid from exchange status data: 8 exchanges (from API response keys) x top symbols
- Keep mock fallback pattern: if API returns empty/error, continue showing random mock grid (existing behavior)
- Replace hardcoded `EXCHANGES` and `SYMBOLS` constants with dynamic values from API
- When engine provides spread data via exchange status or future WS, use it; otherwise show exchange connectivity heatmap (connected = green, latency-based intensity)
- Update status indicator: "LIVE" when API data available, "MOCK" when using fallback

**Acceptance criteria**:
- [x] Heatmap shows all 8 real exchanges from `/api/v1/exchanges`
- [x] Spread colors based on real data when available, mock otherwise
- [x] Mock fallback still works when engine is offline
- [x] `npm run build` 0 errors

#### 3.2 Enhance `OrderbookView.tsx`
**File**: `/Users/100aniv/development/arbitrage_OMC/dashboard/src/components/OrderbookView.tsx`
**Changes**:
- **Primary data source**: `useApi` polling for orderbook data (use exchange status to populate exchange/symbol selectors dynamically)
- Replace hardcoded `SYMBOLS` and `EXCHANGES` with values from `useApi(getExchangeStatus)`
- Keep mock book generation as fallback when no real data
- When `useWebSocket` `market_data` events arrive (future), apply them to the book (existing logic already handles this)
- Add exchange selector populated from real connected exchanges
- Show "LIVE" indicator when using real data, "MOCK" when using generated data

**Acceptance criteria**:
- [x] Exchange/symbol selectors populated from real exchange data
- [x] Orderbook displays (mock data until market_book WS is implemented)
- [x] Existing WS handler for `market_data` preserved for future use
- [x] `npm run build` 0 errors

---

### Step 4: Types + Build Verification

#### 4.1 Update `types/index.ts`
- Add `DailyPerformance` interface
- Add `ContainerStatus` interface
- Verify all existing types match actual API response shapes

#### 4.2 Final Build Check
- Run `npm run build` — must produce 0 errors
- Run `npm run lint` — fix any lint warnings
- Run existing tests: `npm test` — all must pass

**Acceptance criteria**:
- [x] `npm run build` exits with code 0
- [x] `npm run lint` clean
- [x] All existing tests pass
- [x] No unused import warnings

---

## File Change Summary

### New Files (4)
| File | Component | Size Est. |
|------|-----------|-----------|
| `dashboard/src/components/PortfolioSummary.tsx` | Status badge + 4 KPI cards | ~120 LOC |
| `dashboard/src/components/RiskGauge.tsx` | Drawdown gauge + KillSwitch + CB | ~130 LOC |
| `dashboard/src/components/EventFeed.tsx` | Alert list with severity badges | ~90 LOC |
| `dashboard/src/components/PerformanceTrend.tsx` | 7-day PnL/WR mini chart | ~100 LOC |

### Modified Files (8)
| File | Change Description |
|------|-------------------|
| `dashboard/src/app/page.tsx` | Add new component imports + restructure layout grid |
| `dashboard/src/app/attribution/page.tsx` | Add Recharts PieChart for strategy PnL |
| `dashboard/src/app/funding/page.tsx` | Add funding history section + bar chart |
| `dashboard/src/app/system/page.tsx` | Real exchange data + Docker/resource sections |
| `dashboard/src/components/GlobalHeatmap.tsx` | Dynamic exchange/symbol from API + REST polling |
| `dashboard/src/components/OrderbookView.tsx` | Dynamic selectors from exchange API |
| `dashboard/src/types/index.ts` | Add DailyPerformance, ContainerStatus types |
| `dashboard/src/lib/api.ts` | No changes needed (all endpoints already exist) |

### Test Files (new/enhanced)
| File | Coverage |
|------|----------|
| `dashboard/src/__tests__/components/PortfolioSummary.test.tsx` | KPI rendering, null handling |
| `dashboard/src/__tests__/components/RiskGauge.test.tsx` | Gauge rendering, error states |
| `dashboard/src/__tests__/components/EventFeed.test.tsx` | Alert list, empty state |
| `dashboard/src/__tests__/components/PerformanceTrend.test.tsx` | Chart rendering |

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Recharts PieChart import issue | LOW | MEDIUM | PieChart already available in recharts@3.7.0 (same version used by PnLChart) |
| REST polling performance | MEDIUM | LOW | Staggered intervals + SWR deduplication already configured |
| Mobile layout overflow | MEDIUM | MEDIUM | Use existing responsive grid patterns consistently |
| Type mismatch with engine API | LOW | HIGH | Types verified against actual route files in this plan |
| Exchange status API returns empty | MEDIUM | LOW | Mock fallback pattern already exists in GlobalHeatmap/OrderbookView |

---

## Implementation Order (Recommended)

1. **Types first** (Step 4.1) — 5 min
2. **PortfolioSummary** (Step 1.1) — most visible, sets the pattern
3. **RiskGauge** (Step 1.2) — independent component
4. **EventFeed** (Step 1.3) — independent component
5. **PerformanceTrend** (Step 1.4) — independent component
6. **Overview page.tsx** wiring (Step 1.5 + 1.6) — compose all new components
7. **Attribution enhancement** (Step 2.1) — isolated page
8. **Funding enhancement** (Step 2.2) — isolated page
9. **System enhancement** (Step 2.3) — isolated page
10. **GlobalHeatmap** enhancement (Step 3.1) — careful, has existing WS logic
11. **OrderbookView** enhancement (Step 3.2) — careful, has existing WS logic
12. **Build + lint + test** (Step 4.2) — final gate

Each step should be followed by `npm run build` to catch issues early.

---

## Guardrails

### MUST Have
- All 8 acceptance criteria for US-069
- All 5 acceptance criteria for US-070
- All 4 acceptance criteria for US-071
- `npm run build` with 0 errors after each US
- Terminal dark theme consistency (no new color values)
- Mobile responsive (test at 375px)

### MUST NOT Have
- No new engine API routes
- No changes to `engine/src/**`
- No removal of existing mock fallback patterns
- No new npm dependencies (recharts, lucide-react, swr, clsx all already installed)
- No NEW hex color values — use existing theme hex (`#00ff88`, `#ff4d4d`, `#3b82f6`, `#f59e0b`) for Recharts/style props where Tailwind classes cannot apply
- No breaking changes to existing component props/interfaces

### Architecture Notes (Architect Review)
- **Overview = Summary Dashboard**: EventFeed, RiskGauge, ExchangeStatusBar are compact "at-a-glance" widgets linking to full `/alerts`, `/risk`, `/exchanges` pages. Add "View all →" link in each widget header.
- **REST Polling Budget**: Overview page total = WS(1s) + PnLChart(2s) + RiskGauge(5s) + ExchangeBar(10s) + EventFeed(10s) + PerformanceTrend(WS-only) = 5 streams. SWR `dedupingInterval: 2000` deduplicates shared endpoints.
- **PerformanceTrend**: Session-scoped only (no historical API). Future US can add `/api/v1/pnl/history` for 7-day view.
- **Follow-up**: Phase I/J — Refactor Overview into configurable widget grid to manage density.
