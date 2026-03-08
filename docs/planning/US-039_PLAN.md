# US-039: Strategy Analytics + Funding Rate 모니터

## Acceptance Criteria
1. 전략별 PnL 누적, WR 추이, DD 이력 차트
2. 거래소별 실시간 funding rate 비교 테이블
3. Rate history 차트
4. npm run build 성공

## 파일 변경
| 파일 | 변경 | 담당 |
|------|------|------|
| engine/src/api/routes/trading.py | EDIT — GET /api/v1/strategy-metrics | Jennie |
| engine/src/api/routes/funding.py | NEW — GET /api/v1/funding-rates | Jennie |
| engine/src/api/server.py | EDIT — funding router mount | Jennie |
| dashboard/src/app/analytics/page.tsx | NEW | Rosé |
| dashboard/src/app/funding/page.tsx | NEW | Rosé |
| dashboard/src/components/Sidebar.tsx | EDIT | Rosé |
| dashboard/src/lib/api.ts | EDIT | Rosé |
| dashboard/src/types/index.ts | EDIT | Rosé |
