# US-040: Exchange Status 대시보드

## Acceptance Criteria
1. WS 연결 상태, latency, orderbook depth 표시
2. 거래소별 잔고 표시 (Live 모드용)
3. npm run build 성공

## 파일 변경
| 파일 | 변경 | 담당 |
|------|------|------|
| engine/src/api/routes/exchanges.py | NEW — GET /api/v1/exchanges | Jennie |
| engine/src/api/server.py | EDIT — exchanges router + exchange_status field | Jennie |
| dashboard/src/app/exchanges/page.tsx | NEW | Rosé |
| dashboard/src/components/Sidebar.tsx | EDIT | Rosé |
| dashboard/src/lib/api.ts | EDIT | Rosé |
| dashboard/src/types/index.ts | EDIT | Rosé |
