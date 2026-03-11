# Phase J-EXT Wave 2 Batch 3: PRE-FIX + US-111 + US-112

## 배치 구성
- **PRE-FIX**: US-107/108/110 리뷰 HIGH 3건 + MEDIUM 5건 수정
- **US-111**: 거래 설명 기능 ("왜 이 거래를?")
- **US-112**: 트레이드 필터링 + CSV 내보내기

## PRE-FIX 상세 (code-reviewer minji 지적사항)

### HIGH-1: 기준 자본 하드코딩 (portfolio.py:116,128)
- **현재**: `100000` 고정값
- **수정**: `ctx.runtime_settings.get("initial_capital", 100000)` 사용
- **BTC benchmark**: 현재 세션에서는 BTC 가격 추적 부재 → `null` 반환

### HIGH-2: Calmar 연율화 공식 오류 (portfolio.py:157-159)
- **현재**: `total_pnl / (mdd/100*100000) * 365` — 일별 PnL 가정
- **수정**: 세션 경과 시간 기반 연율화. 경과 < 1일이면 `null` 반환
- **공식**: `annualized_return = (total_pnl / initial_capital) / elapsed_days * 365; calmar = annualized_return / mdd_pct`

### HIGH-3: sharpe_ratio 항상 0.0 (portfolio.py:140)
- **현재**: 초기화 후 갱신 없음
- **수정**: snapshot에 returns 데이터 부재 → `null` 반환 + 대시보드에서 "—" 표시
- **향후**: ShadowMode가 hourly returns 추적 시 실 계산

### MEDIUM-4: GlobalHeatmap 원시 fetch (GlobalHeatmap.tsx:91-107)
- **수정**: `getExchangeStatus()` API 헬퍼를 재사용하되, symbols 추출 로직은 별도 함수

### MEDIUM-5: portfolio/page.tsx fetchApi → 타입 헬퍼 (page.tsx:41-56)
- **수정**: `getPortfolioMetrics()`, `getPortfolioSummary()`, `getEquityCurve()` 사용

### MEDIUM-6: HTTPException 늦은 import (settings.py:62)
- **수정**: 모듈 상단 import로 이동

### MEDIUM-7: daily_pnl == total_pnl 중복 (portfolio.py:94)
- **수정**: `daily_pnl` 제거, `pnl_scope: "session"` 메타데이터 추가

### MEDIUM-8: 단일 포인트 SVG 렌더링 (EquityCurve.tsx:30)
- **수정**: `data.length === 1`일 때 dot + label 렌더링

## US-111: 거래 설명 기능

### Engine (trading.py)
- `GET /api/v1/trades/{trade_id}` 신규 엔드포인트
- trade_history 각 항목에 reason/spread_bps/fee_usd/net_pnl 필드 보장
  - 이미 trade_history에 이 데이터가 있으면 그대로 전달
  - 없으면 빈 값/계산값 반환

### Dashboard
- `TradeDetail.tsx` (신규 컴포넌트): 거래 상세 패널
  - 감지된 가격 차이 (spread_bps)
  - 예상 수익 (expected_pnl)
  - 실제 수수료 (fee_usd)
  - 실제 수익 (net_pnl)
  - 거래 사유 (reason)
- `trades/page.tsx`: 행 클릭 → 사이드 패널 열기

### Trade 타입 확장 (types/index.ts)
```typescript
// 기존 필드에 추가
reason?: string;
spread_bps?: number;
fee_usd?: number;
net_pnl?: number;
expected_pnl?: number;
```

## US-112: 트레이드 필터링 + CSV 내보내기

### Engine (trading.py)
- `GET /api/v1/trades` 쿼리 파라미터 확장:
  - `from`: ISO datetime (시작일)
  - `to`: ISO datetime (종료일)
  - `strategy`: 전략 ID
  - `exchange`: 거래소 ID (buy 또는 sell)
  - `symbol`: 심볼
- CSV 내보내기: 클라이언트 사이드 (서버 부하 회피)

### Dashboard (trades/page.tsx)
- 필터 바: 날짜범위 + 전략 드롭다운 + 거래소 드롭다운 + 심볼 입력
- CSV 내보내기 버튼: 현재 필터 상태 그대로 다운로드
- api.ts에 getTrades 파라미터 확장

## 파일 소유권
- **Jennie (executor)**: engine/src/api/routes/portfolio.py, trading.py, settings.py
- **Rosé (designer)**: dashboard/src/ 전체 (TradeDetail, trades/page, portfolio/page, GlobalHeatmap, EquityCurve, api.ts, types)
- **Lisa (test-engineer)**: engine/tests/ + dashboard 빌드 검증

## 완료 기준
- [ ] HIGH 3건 수정 완료
- [ ] MEDIUM 5건 수정 완료
- [ ] US-111: 거래 클릭 시 상세 패널 표시 (reason, spread_bps, fee_usd, net_pnl)
- [ ] US-112: 필터 + CSV 내보내기 동작
- [ ] pytest 0 failures
- [ ] npm run build 성공
