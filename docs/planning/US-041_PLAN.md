# US-041: Mobile Responsive + 전략별 API endpoint

## Acceptance Criteria
1. Tailwind breakpoint 적용 (sm/md/lg)
2. 모바일에서 모든 페이지 사용 가능
3. GET /api/strategies, GET /api/strategies/{id}/trades 동작
4. GET /api/funding-rates 동작
5. WS /ws/strategies 실시간 피드

## 파일 변경
| 파일 | 변경 | 담당 |
|------|------|------|
| engine/src/api/routes/strategies.py | EDIT — GET /{id}/trades | Jennie |
| engine/src/api/server.py | EDIT — /ws/strategies endpoint | Jennie |
| dashboard/src/components/Sidebar.tsx | EDIT — mobile hamburger menu | Rosé |
| dashboard/src/app/layout.tsx | EDIT — responsive container | Rosé |
| dashboard/src/app/*/page.tsx | EDIT — responsive grids (sm/md/lg) | Rosé |
