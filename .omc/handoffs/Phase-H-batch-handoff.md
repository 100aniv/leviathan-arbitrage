# Phase H Batch Handoff: US-069 + US-070 + US-071

> **Plan**: `docs/planning/Phase-H_PLAN.md` | **Phase**: H | **갱신**: 2026-03-11

---

## Rosé 담당: dashboard/src/** 만 수정 (engine/ 절대 금지)

### US-069: Overview 종합 상황판 리디자인

#### 1. types/index.ts — 새 타입 2개 추가
```typescript
export interface SessionPnlPoint {
  timestamp: number;  // Unix ms
  pnl: number;
  win_rate: number;
}
export interface ContainerStatus {
  name: string;
  status: 'running' | 'stopped' | 'error';
  cpu_pct: number;
  memory_mb: number;
  uptime: string;
}
```

#### 2. PortfolioSummary.tsx (새 파일)
- `useEngineWs()` → data.running, data.kill_switch → 상태 뱃지 (RUNNING=green/STOPPED=yellow/ERROR=red)
- `useApi(getExchangeStatus, 10000)` → 거래소 잔고 합산 → Total Balance KPI
- 4 KPI 카드: Total Balance, Today PnL (data.pnl.total), Total PnL (REST /api/v1/pnl), Active Positions (data.position_count)
- 그리드: `grid-cols-2 sm:grid-cols-4`
- 카드 스타일: `bg-terminal-surface border border-terminal-border rounded-lg p-4`
- 값: `text-2xl font-mono tabular-nums`, profit=text-profit, loss=text-loss
- 오프라인: 대시(-) 표시

#### 3. RiskGauge.tsx (새 파일)
- `useApi(getRiskMetrics, 5000)` → drawdown, kill_switch, circuit_breaker
- SVG semicircle arc: max_drawdown_pct (0-100%), green(<5%)/yellow(5-15%)/red(>15%)
- Kill Switch 뱃지: STANDBY(badge-profit) / ACTIVE(badge-loss)
- Circuit Breaker 뱃지: CLOSED/OPEN/HALF_OPEN
- 헤더에 "View all →" 링크 → /risk
- 에러 시 retry 버튼

#### 4. EventFeed.tsx (새 파일)
- `useApi(getAlerts, 10000)` → limit=20
- 스크롤 리스트, 각 행: timestamp(text-terminal-subtle mono) | severity badge(critical=badge-loss, warning=badge-warn, info=badge-accent) | message
- 최신순 정렬, auto-scroll
- 빈 상태: "No recent events"
- 헤더에 "View all →" 링크 → /alerts
- max-height: 300px, overflow-y-auto

#### 5. PerformanceTrend.tsx (새 파일)
- WS state_update → pnl.total 5초 간격 클라이언트 축적 (max 500 points, SessionPnlPoint[])
- Recharts AreaChart (~120px height), PnL area + WR line
- COLORS: `#00ff88` (PnL), `#3b82f6` (WR)
- 데이터 < 2점: "Accumulating data..." 표시
- 로딩 스켈레톤

#### 6. page.tsx 재구성
현재 레이아웃:
```
Header (title + status + KillSwitch)
[GlobalHeatmap | PnLChart]
[StrategyPanel | OrderbookView]
[ShadowPanel]
```
새 레이아웃:
```
<PortfolioSummary /> (상태 뱃지 + 4 KPI + 내장 ExchangeStatusBar)
  ExchangeStatusBar: 8개 거래소 pill (name + dot + latency + symbols), 가로 스크롤
[GlobalHeatmap | PnLChart]
[RiskGauge | PerformanceTrend]
[StrategyPanel | OrderbookView]
[EventFeed] (full width)
[ShadowPanel] (active일 때만)
KillSwitch 우상단 유지
```
- ExchangeStatusBar는 PortfolioSummary 안에 인라인 (별도 파일 불필요)
- 기존 import 유지 + 새 4개 import 추가

### US-070: Attribution/Funding/System 페이지 보강

#### 7. attribution/page.tsx 보강
- Strategy 탭에 Recharts PieChart 추가 (waterfall chart 위에)
- data.by_strategy 배열 → PieChart slices
- profit → `#00ff88`, loss → `#ff4d4d`
- 범례: 전략명 + % 기여도
- 기존 기능 100% 유지

#### 8. funding/page.tsx 보강
- Matrix View 아래 "History" 섹션 추가
- 클라이언트 축적: 10초 polling 스냅샷 저장 (last 50)
- Recharts BarChart: 심볼별 펀딩비 히스토리
- 히스토리 테이블: [Timestamp, Exchange, Symbol, Rate, Cumulative]
- 기존 매트릭스 100% 유지

#### 9. system/page.tsx 보강
- 하드코딩 CONNECTIONS 배열 → `useApi(getExchangeStatus, 5000)` 실 데이터
- 각 거래소: connected dot, latency_ms, symbols_count, balance
- Docker 컨테이너 섹션 추가 (mock: 8 containers with status badges)
- Memory/CPU 섹션 추가 (mock: placeholder for Prometheus)
- 기존 Engine Stats 패널 유지

### US-071: GlobalHeatmap + OrderbookView 실 데이터

#### 10. GlobalHeatmap.tsx 보강
- 하드코딩 EXCHANGES/SYMBOLS → `useApi(getExchangeStatus, 5000)` 동적
- API 응답 키 = 실제 거래소 8개, symbols_count로 심볼 목록 추정
- 연결 상태 = green, latency 기반 intensity
- mock fallback 유지 (API 에러 시)
- "LIVE" / "MOCK" 상태 인디케이터
- 기존 WS market_data 핸들러 보존

#### 11. OrderbookView.tsx 보강
- 하드코딩 SYMBOLS/EXCHANGES → `useApi(getExchangeStatus)` 동적 셀렉터
- 실제 연결된 거래소/심볼로 dropdown 구성
- mock book 생성 fallback 유지
- "LIVE" / "MOCK" 인디케이터
- 기존 WS market_book 핸들러 보존

---

## 스타일 규칙 (반드시 준수)
- Tailwind tokens만: terminal-bg, terminal-surface, terminal-border, terminal-muted, terminal-text, terminal-subtle, profit, loss, accent, warn
- Recharts/style props: `#00ff88`, `#ff4d4d`, `#3b82f6`, `#f59e0b` (Tailwind에 정의된 값만)
- 폰트: JetBrains Mono (mono), Inter (sans)
- 새 npm 의존성 추가 금지 (recharts, swr, lucide-react, clsx 이미 설치)
- 새 hex 색상값 추가 금지

## 검증 게이트
- 각 컴포넌트 완성 후 `npm run build` (0 errors)
- 전체 완료 후 `npm run build && npm run lint`
- 모바일 375px 반응형 확인
