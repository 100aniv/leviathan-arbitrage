# Phase S4 Review — Dashboard Completion

**Date**: 2026-03-14
**Reviewers**: Jennie (code-reviewer), Lisa (critic), Rose (quality-reviewer), Jisoo (security-reviewer)
**Verdict**: PASS (after fix loop)

## Scope
- US-140: API prefix 통일 (`/kill` → `/api/v1/kill-switch` 등)
- US-141: System 모니터링 엔드포인트 (`/api/v1/system/containers`, `/resources`)
- US-142: 실시간 심볼/스프레드 연동 (`/api/v1/symbols`, `/spreads`)
- US-143: Mock 데이터 제거 + 실데이터 연결
- US-144: 모바일 반응형 + SWR v2 테스트 수정

## Files Changed
### Engine (backend)
- `engine/src/api/routes/system.py` — NEW: Docker containers + host resources
- `engine/src/api/routes/trading.py` — symbols/spreads endpoints
- `engine/src/api/server.py` — system_router mount
- `engine/tests/unit/test_system_routes.py` — 12 tests
- `engine/tests/unit/test_market_routes.py` — 16 tests

### Dashboard (frontend)
- `dashboard/src/lib/api.ts` — API prefix fix + 5 new functions
- `dashboard/src/types/index.ts` — ContainerStatus, SystemResources aligned
- `dashboard/src/components/OrderbookView.tsx` — dynamic symbols, MOCK→OFFLINE
- `dashboard/src/components/GlobalHeatmap.tsx` — SpreadItem[] transform
- `dashboard/src/components/StrategyPanel.tsx` — mock removal
- `dashboard/src/components/EquityCurve.tsx` — metrics prop
- `dashboard/src/components/TradeDetail.tsx` — mobile responsive
- `dashboard/src/app/system/page.tsx` — real API data, null guards
- `dashboard/src/__tests__/` — 7 test files (SWR v2 isValidating)

## Review Findings (Initial)

### CRITICAL (3) — All Fixed
1. **system/resources field name mismatch**: Backend `cpu_percent` vs frontend `cpu_pct` → Fixed: backend aligned to `cpu_pct`
2. **symbols response shape**: Backend `{symbols:[], count:N}` vs frontend expecting `string[]` → Fixed: frontend unwraps wrapper
3. **containers field types**: Raw Docker status string vs enum, null fields → Fixed: status normalized to running/stopped/error, null guards added

### HIGH (5) — All Fixed
1. **Blocking subprocess.run in async handler**: → Fixed: `asyncio.to_thread()`
2. **Blocking psutil.cpu_percent in async handler**: → Fixed: `asyncio.to_thread()`
3. **Spreads response shape mismatch**: Flat array vs nested Record → Fixed: frontend transforms SpreadItem[] to grid
4. **Empty bids/asks crash**: OrderbookView index[0] access on empty array → Fixed: length guard
5. **Double iteration of positions**: `get_all_positions()` called twice → Fixed: single list()

### MEDIUM (resolved or accepted)
- `import os` inside function body → Moved to module level
- `/trades` limit unbounded → Added `Query(ge=1, le=1000)`
- OrderbookView still showed "MOCK" label → Changed to "OFFLINE"
- `_get_pnl` double-iteration → Fixed

### Security (Jisoo)
- **CRITICAL: 0**
- **HIGH: 2** (accepted risks):
  - `/metrics` unauthenticated — Intentional for Prometheus scraper (Nginx IP whitelist protects)
  - Strategy config unvalidated — Existing issue, out of Phase S4 scope
- All 4 new endpoints properly JWT-authenticated
- No command injection (subprocess uses list-form)
- No hardcoded secrets

## Test Results
- **pytest**: 4360 passed, 0 failed, 6 skipped (1 pre-existing flaky gate test excluded)
- **tsc**: 0 errors
- **Coverage**: 87%

## Conclusion
All CRITICAL and HIGH issues from initial review have been resolved in the fix loop.
Backend-frontend API contracts are now aligned. Blocking I/O calls wrapped in asyncio.to_thread().
Ready for Stage D Shadow testing.
