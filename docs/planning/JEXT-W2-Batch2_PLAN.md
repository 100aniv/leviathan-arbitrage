# J-EXT Wave 2 Batch 2: US-109 + US-113

## 배치 근거
- 동일 Phase (J-EXT), 동일 도메인 (dashboard)
- 파일 겹침: PortfolioSummary.tsx, page.tsx → 순차 실행 (US-109 먼저, US-113 이후)

---

## US-109: 오버뷰 개선 (ROI%, 성능위젯, 동적 모드명)

### 구현
1. **PortfolioSummary.tsx** 수정
   - KPI 5번째: ROI % = (total_pnl / initial_capital) × 100
   - initial_capital: total_balance - total_pnl (또는 100,000 기본값)

2. **page.tsx (Overview)** 수정
   - 시스템 성능 위젯 추가 (SystemPerf 인라인 또는 컴포넌트)
   - API: GET /api/v1/system 이미 존재 (CPU/Memory/latency)
   - 위젯: CPU%, Memory MB, WS 업타임, 평균 레이턴시

3. **ShadowPanel.tsx** 수정
   - 제목 "Shadow Monitor" → 현재 모드명 동적 변경
   - useEngineWs의 data.mode를 사용: shadow→"시뮬레이션 모니터", paper→"연습 모니터", live→"실거래 모니터"

### AC
- ROI % KPI 추가 (총수익/초기자본)
- 시스템 성능 위젯 (CPU/Memory/평균레이턴시/WS업타임)
- Shadow Monitor 패널 제목이 현재 모드명으로 동적 변경

---

## US-113: 용어 친화화 + 툴팁

### 구현
1. **page.tsx (Overview)** 수정
   - "War Room" → "대시보드" 제목 변경
   - "Real-time arbitrage engine status" → "실시간 차익거래 엔진 현황"

2. **PortfolioSummary.tsx** 수정
   - KPI 라벨 한글화: Total Balance→총 자산, Today PnL→오늘 수익, Total PnL→총 수익, Active Positions→활성 포지션, ROI→수익률

3. **settings/page.tsx** 수정
   - "MIN_EDGE_BPS" 라벨 → "최소 수익 기준 (BPS)"
   - "Trading Parameters" → "거래 파라미터"
   - 핵심 수치에 info 아이콘 + 툴팁: ⓘ hover 시 설명 표시
   - Tooltip 컴포넌트: inline span with relative positioned tooltip

### AC
- War Room → 대시보드 명칭 변경
- MIN_EDGE_BPS → 최소 수익 기준 레이블
- 핵심 수치에 info 아이콘 + 설명 툴팁 추가

---

## 파일 소유권
- **Rosé** (designer): dashboard/ 전체

## QUANT GATE: 해당 없음
