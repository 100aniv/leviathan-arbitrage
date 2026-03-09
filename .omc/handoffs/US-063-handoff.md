# US-063 Handoff: Dashboard Chrome Browser Verification

## Task
D-verify: 핵심 4페이지 (Overview, Trades, Strategies, Exchanges) Chrome 렌더링 검증

## Bug Fix Applied
- `NEXT_PUBLIC_WS_URL` default: `ws://localhost:8001` → `ws://localhost:8000`
- Files: `next.config.js`, `src/lib/websocket.ts` (getFeedManager, getControlManager)

## Verification Checklist
- [x] npm run build — 17/17 pages, 0 errors
- [ ] Docker compose up — engine + redis + timescaledb healthy
- [ ] Dev server (npm run dev) — localhost:3000 accessible
- [ ] Login page renders and auth flow works
- [ ] Overview page — renders PnL chart, strategies, kill switch
- [ ] Trades page — renders trade table with filters
- [ ] Strategies page — renders strategy panel with toggles
- [ ] Exchanges page — renders 8 exchange status cards
- [ ] API endpoints return 200 (health, status, strategies, trades, exchanges)
- [ ] WebSocket feed connects on port 8000
- [ ] Mobile responsive layout at 375x812
