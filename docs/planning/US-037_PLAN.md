# US-037: Trade History + Alerts 페이지

## 요약
대시보드에 Trade History 페이지와 Alerts 페이지를 추가한다.

## Acceptance Criteria
1. 전략별 필터링, 시간순 정렬, PnL 표시
2. GET /api/v1/trades?strategy=X&limit=50 API 동작
3. KillSwitch 발동, WS 끊김 등 알림 이력 조회
4. WebSocket 실시간 알림 수신
5. npm run build 성공

## 아키텍처 결정

### Backend (engine/src/api/)
- `routes/trading.py`에 `GET /api/v1/trades` 엔드포인트 추가
  - Query params: `strategy` (optional), `limit` (default 50)
  - EngineContext에 `trade_history: list[dict]` 필드 추가
- `routes/alerts.py` 신규 생성 — `GET /api/v1/alerts`
  - EngineContext에 `alert_history: list[dict]` 필드 추가
  - KillSwitch 발동, WS 끊김 등 시스템 이벤트 기록

### Frontend (dashboard/src/)
- **주의**: Next.js App Router 사용 중 → `app/trades/page.tsx`, `app/alerts/page.tsx`
  (prd.json의 `pages/` 경로는 실제 구조와 다름)
- `app/trades/page.tsx`: 전략 필터 드롭다운, 시간순 테이블, PnL 컬럼
- `app/alerts/page.tsx`: 알림 이력 테이블, 심각도 배지, 실시간 WS 수신
- `components/Sidebar.tsx`: Trades, Alerts 네비게이션 항목 추가
- `lib/api.ts`: getTrades(), getAlerts() 함수 추가
- `types/index.ts`: Trade, Alert 인터페이스 추가

### 기존 패턴 준수
- 터미널 다크 테마 (terminal-bg, terminal-surface, profit/loss 컬러)
- JetBrains Mono 폰트, font-mono 클래스
- useEngineWs() 훅 또는 SWR polling
- JWT auth 헤더 자동 포함 (api.ts의 request() 함수)

## 파일 변경 목록
| 파일 | 변경 유형 | 담당 |
|------|----------|------|
| engine/src/api/server.py | EDIT (EngineContext + alert router mount) | Jennie |
| engine/src/api/routes/trading.py | EDIT (GET /trades 추가) | Jennie |
| engine/src/api/routes/alerts.py | NEW | Jennie |
| dashboard/src/types/index.ts | EDIT (Trade, Alert 타입) | Rosé |
| dashboard/src/lib/api.ts | EDIT (getTrades, getAlerts) | Rosé |
| dashboard/src/components/Sidebar.tsx | EDIT (nav items) | Rosé |
| dashboard/src/app/trades/page.tsx | NEW | Rosé |
| dashboard/src/app/alerts/page.tsx | NEW | Rosé |
