# J-EXT Wave 2 Batch 1: US-107 + US-108 + US-110

## 배치 근거
- 동일 Phase (J-EXT), 동일 도메인 (dashboard)
- 파일 겹침 없음 → 병렬 실행 가능
- US-107: ModeSwitch.tsx, page.tsx, overview/page.tsx
- US-108: portfolio/page.tsx, EquityCurve.tsx, engine/api/routes/portfolio.py
- US-110: GlobalHeatmap.tsx

---

## US-107: 모드 전환 UI 연결 + 친화적 명칭

### 구현
1. **ModeSwitch.tsx** (신규 컴포넌트)
   - 현재 모드 표시: 시뮬레이션(shadow) / 연습(paper) / 실거래(live)
   - 드롭다운 또는 라디오 버튼으로 전환
   - Live 전환 시 확인 다이얼로그 + LiveGate 체크 결과 표시
   - API: `PATCH /api/v1/settings/mode` 호출

2. **overview/page.tsx**
   - 헤더에 ModeSwitch 통합
   - 현재 모드에 따라 배지 색상 변경 (shadow=blue, paper=yellow, live=red)

3. **engine/src/api/routes/settings.py**
   - `PATCH /api/v1/settings/mode` 엔드포인트 추가 (body: {"mode": "shadow|paper|live"})
   - Live 전환 시 LiveGate 체크 결과 반환

### AC
- ModeSwitch Overview 헤더에 통합
- 모드 표시: 시뮬레이션(shadow)/연습(paper)/실거래(live)
- Live 전환 시 확인 다이얼로그 + LiveGate 체크 결과 표시
- API PATCH /api/v1/settings/mode 연동

---

## US-108: 포트폴리오 별도 탭

### 구현
1. **portfolio/page.tsx** (신규 페이지)
   - Equity curve 차트 (일별 잔고 추이)
   - 자산배분 파이차트 (거래소별)
   - 일별/주별 수익률 히트맵
   - Sharpe/MDD/Calmar 리스크 메트릭스 카드
   - vs BTC Hold 벤치마크 비교선

2. **EquityCurve.tsx** (신규 컴포넌트)
   - SVG 기반 line chart (Recharts 또는 순수 SVG)
   - BTC benchmark 오버레이

3. **engine/src/api/routes/portfolio.py**
   - GET /api/v1/portfolio/equity-curve (일별 잔고 이력)
   - GET /api/v1/portfolio/metrics (Sharpe/MDD/Calmar)

### AC
- Equity curve 차트 (일별 잔고 추이)
- 자산배분 파이차트 (거래소별)
- Sharpe/MDD/Calmar 리스크 메트릭스
- vs BTC Hold 벤치마크 비교선

---

## US-110: 히트맵 심볼 확장 (드롭다운)

### 구현
1. **GlobalHeatmap.tsx** 수정
   - 드롭다운 추가: Major 8 / Top 20 / All / Custom
   - Major 8: BTC, ETH, XRP, SOL, BNB, DOGE, ADA, AVAX
   - Top 20: 상위 20개 시가총액 코인
   - All: 엔진의 전체 심볼 (API에서 가져옴)
   - Custom: 콤마 구분 입력 (로컬 저장)

### AC
- Major 8 / Top 20 / All / Custom 드롭다운 선택
- All 선택 시 엔진의 175개 심볼 표시
- Custom 입력 시 콤마 구분 심볼 지정

---

## 파일 소유권
- **Jennie** (executor): engine/src/api/routes/settings.py, engine/src/api/routes/portfolio.py
- **Rosé** (designer): dashboard/ 전체 (ModeSwitch, portfolio/page, EquityCurve, GlobalHeatmap, overview/page)
- **Lisa** (test-engineer): tests/

## QUANT GATE: 해당 없음
