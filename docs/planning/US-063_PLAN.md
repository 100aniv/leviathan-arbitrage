# US-063 Plan: 대시보드 Chrome 브라우저 검증 — 핵심 4페이지

## 개요
Phase D-verify US. 기존 대시보드 페이지(Overview, Trades, Strategies, Exchanges)의 Chrome 브라우저 렌더링 검증.

## Acceptance Criteria
1. `npm run build` 성공 (0 errors)
2. Overview 페이지: PnL 차트, 전략 카드, 킬스위치, WebSocket 실시간 데이터
3. Trades 페이지: 거래 목록 테이블, 전략 필터, PnL 요약 카드
4. Strategies 페이지: 전략 패널, 토글, 상세 통계
5. Exchanges 페이지: 8개 거래소 상태 카드, 연결/레이턴시/심볼 수
6. 모든 페이지 API 엔드포인트 200 응답
7. WebSocket 실시간 피드 연결 확인
8. 모바일 반응형 레이아웃 정상 (375x812)

## 발견된 버그
- **[CRITICAL] WS URL 포트 불일치**: `next.config.js`와 `websocket.ts`의 기본 WS URL이 `ws://localhost:8001`로 설정되어 있었으나, 엔진 WS는 포트 8000에서 서비스됨
  - 수정: `8001` → `8000` (next.config.js, websocket.ts의 getFeedManager, getControlManager)
  - `useEngineWs.ts`는 `NEXT_PUBLIC_ENGINE_URL`에서 파생하므로 정상

## 검증 방법
1. `npm run build` — 빌드 오류 0건 확인
2. `docker compose up -d` — 엔진 + 인프라 기동
3. `npm run dev` — 개발 서버 시작
4. curl 기반 페이지 렌더링 검증 (HTML 응답 확인)
5. curl 기반 API 엔드포인트 200 응답 검증
6. 엔진 WS `/ws/feed` 연결 테스트

## 파일 변경
- `dashboard/next.config.js` — WS URL 포트 수정 (8001→8000)
- `dashboard/src/lib/websocket.ts` — WS URL 포트 수정 (8001→8000, 2곳)
