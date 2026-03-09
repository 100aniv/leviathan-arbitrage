# US-053: Dashboard Attribution 페이지

## 개요
전략/거래소/페어별 수익 귀속(Attribution) 차트를 대시보드에 추가.

## Acceptance Criteria
1. 전략/거래소/페어별 수익 귀속 차트
2. Waterfall chart + Heatmap (페어 × 거래소)
3. `npm run build` 성공

## 구현 계획

### 변경 파일
1. `dashboard/src/app/attribution/page.tsx` — 메인 Attribution 페이지 (NEW)
2. `dashboard/src/components/Sidebar.tsx` — Attribution 네비게이션 추가
3. `dashboard/src/lib/api.ts` — getAttribution API 함수 추가
4. `dashboard/src/types/index.ts` — Attribution 타입 추가
5. `engine/src/api/routes/attribution.py` — 백엔드 API 엔드포인트 (NEW)
6. `engine/src/api/server.py` — 라우트 등록

### 데이터 흐름
- 엔진 `/api/v1/attribution` → PerformanceAttribution.summary() → JSON
- 대시보드: fetch → 3탭 (Strategy/Exchange/Pair) + Waterfall + Heatmap

### UI 구성
1. **Summary Cards**: Total PnL, Total Trades, Best Strategy, Best Exchange
2. **탭 전환**: Strategy | Exchange | Pair | Hour
3. **Waterfall Chart**: CSS 바 기반 누적 PnL 기여도 (양수=green, 음수=red)
4. **Heatmap**: Pair × Exchange 매트릭스 — 색상 강도로 PnL 표현
5. **테이블**: 각 차원별 상세 (key, pnl, trades, win_rate)

### 스타일
- 기존 terminal 테마 (bg-terminal-surface, border-terminal-border, font-mono)
- lucide-react 아이콘 (PieChart)
- 차트 라이브러리 없음 — 순수 CSS 구현

## 팀 배정
- Jennie (Backend): attribution API 엔드포인트
- Rosé (Frontend): attribution 페이지 + sidebar
- Lisa (QA): npm run build 검증
