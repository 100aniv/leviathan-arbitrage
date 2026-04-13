# Phase L — Dashboard Redesign PLAN.md (US-432~437)

> 작성: 2026-04-04 | Phase K 완료 후 Phase L 대시보드 섹션
> 브랜드: **XXX STUDIO** — Bloomberg/TradingView 스타일 프로 터미널
> 레퍼런스: 업비트, 토스증권
> 실행: US 단위 순차 (Stage A->B->C per US)

---

## RALPLAN-DR Summary

### Principles (5)
1. **Dense but Fast** — 정보 밀도 최대화하되 렌더링 성능 타협 없음
2. **Single Source of Style** — globals.css CSS 변수가 유일한 디자인 토큰 소스
3. **Component Reuse** — MetricCard/StatusBadge/SparkLine 3종으로 전 페이지 통일
4. **API-First Assets** — 신규 Assets 페이지는 엔진 엔드포인트 선행 구현
5. **Progressive Enhancement** — SWR 캐싱 + 가상화로 체감 성능 최적화

### Decision Drivers (Top 3)
1. **Brand Identity**: #00B8FF 시안 블루 + #0A0A0A 배경 = XXX STUDIO 시그니처
2. **Performance**: Dense 레이아웃에서 60fps 유지 (가상화 + SWR dedup + CSS containment)
3. **신규 Assets 페이지**: 거래소별 잔고/미체결/포지션 — BalanceTracker 노출 필요

### Options

**Option A: Incremental Reskin (선택)**
- globals.css 변수 교체 + 공통 컴포넌트 3종 -> 페이지별 순차 재설계
- Pros: 안전, 페이지별 검증 가능, 기존 22개 컴포넌트 재활용
- Cons: 일부 구 컴포넌트와 신규 디자인 불일치 과도기

**Option B: Full Rewrite (기각)**
- 전체 dashboard/src/ 재작성
- Pros: 완전한 일관성
- Cons: 14페이지+22컴포넌트 전면 재작성 = 2~3일, 기존 기능 회귀 위험 높음
- **기각 사유**: 기존 API 연동/WS/인증 모두 정상 동작 중. 스타일링만 변경하면 되므로 전면 재작성은 과도

---

## Design System (확정)

```css
/* globals.css — XXX STUDIO Design Tokens */
:root {
  /* Backgrounds */
  --bg-primary:    #0A0A0A;
  --bg-secondary:  #141414;
  --bg-tertiary:   #1E1E1E;

  /* Brand */
  --accent:        #00B8FF;   /* XXX STUDIO Cyan Blue */
  --accent-hover:  #33C9FF;
  --accent-subtle: rgba(0, 184, 255, 0.12);

  /* Semantic */
  --success:       #00C896;
  --danger:        #FF4757;
  --warning:       #F59E0B;

  /* Text */
  --text-primary:  #F0F0F0;
  --text-secondary:#AAAAAA;
  --text-muted:    #888888;

  /* Border */
  --border:        rgba(255, 255, 255, 0.08);
  --border-hover:  rgba(255, 255, 255, 0.15);

  /* Font */
  --font-sans:     'IBM Plex Sans', sans-serif;
  --font-mono:     'IBM Plex Mono', monospace;
}
```

---

## US-432: 디자인 시스템 구축

**목표**: globals.css 변수 교체 + 공통 컴포넌트 3종 생성
**작업량**: M (3~4개 파일 생성/수정)

### 파일 변경
| 파일 | 작업 |
|------|------|
| `dashboard/src/app/globals.css` | CSS 변수 전면 교체 (위 토큰), `.card`/`.badge-*` 유틸리티 업데이트 |
| `dashboard/src/components/ui/MetricCard.tsx` | **신규** — 숫자+변화율+화살표+sparkline slot |
| `dashboard/src/components/ui/StatusBadge.tsx` | **수정** — 기존 StatusBadge를 디자인 토큰 기반으로 리팩터 |
| `dashboard/src/components/ui/SparkLine.tsx` | **신규** — 7일 미니 SVG 차트 (외부 라이브러리 없음, SVG path) |
| `dashboard/src/app/layout.tsx` | 폰트 import 확인 (IBM Plex Sans/Mono) |

### MetricCard 스펙
```tsx
interface MetricCardProps {
  label: string;           // "Cumulative PnL"
  value: string | number;  // "$1,234.56"
  change?: number;         // +12.3 (%)
  changeLabel?: string;    // "24h"
  variant?: 'default' | 'success' | 'danger';
  sparkData?: number[];    // 최근 24개 포인트
  mono?: boolean;          // 숫자에 monospace 적용
}
```

### SparkLine 스펙
```tsx
interface SparkLineProps {
  data: number[];          // y값 배열
  width?: number;          // default 80
  height?: number;         // default 24
  color?: string;          // default var(--accent)
  showLastDot?: boolean;   // 마지막 점 강조
}
```
- **구현**: 순수 SVG `<polyline>` — 외부 차트 라이브러리 불필요
- **성능**: `React.memo` + `useMemo`로 path 계산 캐싱

### AC (Acceptance Criteria)
- [ ] globals.css 변수가 위 토큰과 일치
- [ ] MetricCard: 양수=green, 음수=red, 중립=muted 자동 색상
- [ ] SparkLine: 80x24 SVG, data 변경 시에만 리렌더
- [ ] 기존 페이지 로드 시 깨짐 없음 (CSS 변수 하위호환)
- [ ] `font-variant-numeric: tabular-nums` 적용 확인

---

## US-433: Home/Strategies/Exchanges/Trades 4페이지 재설계

**목표**: Bloomberg 터미널 밀도의 4개 핵심 페이지
**작업량**: XL (4개 페이지 x 100~200줄 수정)
**선행**: US-432

### Page 1: Home (`app/page.tsx`)
**현재**: 9개 패널 혼재, 1~2컬럼 반응형
**목표**: Trading Command Center — 3단 레이아웃

```
+-- TopBar: 누적PnL | 오늘거래수 | WR% | Sharpe (MetricCard x4)
+-- Main Area (2/3):
|   +-- PnL 차트 (Recharts, 1min 간격, area chart)
|   +-- 최근 체결 10건 (가상화 테이블, 자동 스크롤)
+-- Right Panel (1/3):
|   +-- 전략 카드 리스트 (7개, SparkLine 포함)
|   +-- 거래소 상태 미니 (StatusBadge x11)
+-- Bottom: EventFeed (최근 20건, 고정 높이 스크롤)
```

**데이터**: 기존 useEngineWs() + getAttribution() 그대로 활용
**성능**: `contain: layout style` CSS 적용, PnL 차트 `isAnimationActive={false}`

### Page 2: Strategies (`app/strategies/page.tsx`)
**현재**: Health score 카드 (100점제)
**목표**: 전략별 성과 대시보드

```
+-- 7개 전략 그리드 (xl:4col, md:2col, sm:1col)
|   각 카드:
|   +-- 전략명 + ON/OFF 토글
|   +-- MetricCard 4개: Trades | PnL | WR% | Sharpe
|   +-- SparkLine (7일 PnL 추세)
|   +-- StatusBadge (enabled/disabled/error)
+-- 전략 클릭 -> 상세 모달 (by_strategy 데이터)
```

**데이터**: `getStrategyMetrics()` + `getPaperStats()` (리네임 후)

### Page 3: Exchanges (`app/exchanges/page.tsx`)
**현재**: 연결 상태 표
**목표**: 거래소 헬스 매트릭스

```
+-- 11개 거래소 그리드 (xl:4col)
|   각 카드:
|   +-- 거래소 로고/이름 + StatusBadge(connected/disconnected)
|   +-- Latency 게이지 (<100ms=green, <500ms=yellow, >500ms=red)
|   +-- Orderbook depth (top 5 bid/ask)
|   +-- 마지막 업데이트 타임스탬프
+-- Reconnect 버튼 (POST /exchanges/reconnect)
```

**데이터**: `getExchangeStatus()` (기존 엔드포인트)

### Page 4: Trades (`app/trades/page.tsx`)
**현재**: 기본 테이블 + 필터
**목표**: 거래 분석 뷰

```
+-- Summary Bar: MetricCard x3 (총PnL | 총거래수 | 승률)
+-- 필터 바: 전략 | 거래소 | 심볼 | 날짜 (기존 로직 유지)
+-- 거래 테이블 (가상화):
|   Timestamp | Strategy | Symbol | Buy->Sell | Size | PnL | Status
|   PnL 컬럼: 양수=green, 음수=red (font-weight:600)
+-- CSV Export 버튼 (기존 로직)
```

**성능**:
- `react-window` 가상화 (100+ 행 시)
- SWR `refreshInterval: 5000` + `dedupingInterval: 2000` (기존)

### AC (전체)
- [ ] Home: 4개 MetricCard 상단 배치, PnL 차트 Area 타입
- [ ] Strategies: 7개 카드 각각 SparkLine + toggle 동작
- [ ] Exchanges: 11개 거래소 카드, latency 색상 코딩
- [ ] Trades: 가상화 테이블, PnL 색상 코딩
- [ ] 전 페이지 `--bg-primary` 배경, `--accent` #00B8FF 포인트
- [ ] 모바일 (<768px) 1컬럼 정상 표시
- [ ] 각 페이지 로드 < 1초 (Chrome DevTools Performance 확인)

---

## US-434: Settings Hot-Reload API

**목표**: 설정 변경 시 엔진 재시작 없이 즉시 반영
**작업량**: M
**선행**: US-430 (shadow->paper 리네임)

### 엔진 측 변경
| 파일 | 작업 |
|------|------|
| `engine/src/api/routes/settings.py` | `PUT /api/v1/settings` 응답에 `applied_at` 타임스탬프 추가 |
| `engine/src/core/config.py` | `reload_runtime_params()` — strategy_params.json 재로드 + Redis pub |
| `engine/src/main.py` | Redis SUB -> config 변경 감지 -> 전략 매니저 파라미터 갱신 |

### 대시보드 측 변경
| 파일 | 작업 |
|------|------|
| `dashboard/src/app/settings/page.tsx` | 저장 후 "Applied" 토스트 + `applied_at` 표시 |

### Hot-reload 범위 (재시작 불필요)
- `min_edge_bps`, `capital_per_exchange_usd`, `max_position_usd`, `max_daily_loss_usd`
- 전략 enable/disable toggle
- 거래소 활성/비활성

### 재시작 필수 (변경 불가)
- `execution_mode` 전환 (backtest/paper/live)
- 거래소 API 키 변경

### AC
- [ ] `PUT /settings` -> 엔진 로그에 "Config reloaded" 즉시 출력
- [ ] 대시보드에서 min_edge 변경 -> 다음 시그널부터 새 값 적용 확인
- [ ] 전략 toggle -> 즉시 반영 (hot-reload 보장)

---

## US-435: /api/v1/paper 라우트

**목표**: `/api/v1/shadow/*` -> `/api/v1/paper/*` 라우트 통일
**작업량**: S (기존 paper.py 라우터 있음, shadow.py alias 추가)

### 변경
| 파일 | 작업 |
|------|------|
| `engine/src/api/routes/shadow.py` | `GET /api/v1/paper/stats` alias 추가 (기존 `/shadow/stats` 유지 + deprecation 헤더) |
| `engine/src/api/server.py` | paper_router 이미 등록됨 — shadow stats를 paper router로 이동 검토 |
| `dashboard/src/lib/api.ts` | `getShadowStats()` -> `getPaperStats()` 리네임, URL `/api/v1/paper/stats` |
| `dashboard/src/types/index.ts` | `ShadowStats` -> `PaperStats` type alias |

### AC
- [ ] `GET /api/v1/paper/stats` 200 응답
- [ ] `GET /api/v1/shadow/stats` 여전히 동작 (하위호환, `Deprecation` 헤더 포함)
- [ ] 대시보드 ShadowPanel -> PaperPanel 컴포넌트명 변경

---

## US-436: E2E 브라우저 검증

**목표**: Chrome DevTools MCP로 전 페이지 시각/기능 검증
**작업량**: M
**선행**: US-433 + US-435

### 검증 체크리스트
| 페이지 | 검증 항목 |
|--------|----------|
| Home | 4개 MetricCard 렌더링, PnL 차트 데이터 표시, WS 연결 |
| Strategies | 7개 전략 카드, toggle 동작, SparkLine 렌더링 |
| Exchanges | 11개 거래소 카드, 연결 상태 색상, reconnect 버튼 |
| Trades | 테이블 로드, 필터 동작, CSV 다운로드 |
| Settings | 모드 변경, 파라미터 슬라이더, hot-reload 확인 |
| Assets (US-437) | 거래소별 잔고 표시, 데이터 정합성 |
| Login | 인증 -> 리다이렉트 |

### 성능 기준
- FCP (First Contentful Paint) < 1.5s
- LCP (Largest Contentful Paint) < 2.5s
- CLS (Cumulative Layout Shift) < 0.1
- 메모리: < 200MB (30분 연속 실행)

### AC
- [ ] 7개 페이지 스크린샷 캡처 완료
- [ ] API 에러 0건 (Network 탭 확인)
- [ ] WS /ws/feed 연결 유지 > 5분
- [ ] 모바일 뷰포트 (375px) 정상 렌더링

---

## US-437: Assets 페이지 (신규)

**목표**: 거래소별 자산 현황 — 잔고/미체결/포지션P&L/출금한도
**작업량**: L (엔진 API 신규 + 대시보드 페이지 신규)
**선행**: US-432 (디자인 시스템)

### 엔진 신규 엔드포인트

#### `GET /api/v1/assets`
```json
{
  "total_balance_usd": 1234.56,
  "exchanges": [
    {
      "exchange_id": "binance",
      "connected": true,
      "total_usd": 500.00,
      "available_usd": 450.00,
      "locked_usd": 50.00,
      "pnl_usd": 12.34,
      "pnl_pct": 2.5,
      "assets": [
        {"symbol": "USDT", "free": 400.0, "locked": 50.0, "usd_value": 450.0},
        {"symbol": "BTC", "free": 0.001, "locked": 0.0, "usd_value": 50.0}
      ],
      "open_orders": 3,
      "open_positions": 1,
      "withdrawal_limit": {
        "daily_usd": 10000.0,
        "used_usd": 0.0,
        "remaining_usd": 10000.0
      }
    }
  ],
  "last_updated": "2026-04-04T12:00:00Z"
}
```

#### 엔진 구현 파일
| 파일 | 작업 |
|------|------|
| `engine/src/api/routes/assets.py` | **신규** — GET /api/v1/assets |
| `engine/src/api/server.py` | assets_router 등록 |
| `engine/src/core/balance_tracker.py` | `get_all_snapshots()` 메서드 추가 (기존 BalanceTracker 활용) |

#### 데이터 소스
- **잔고**: `BalanceTracker.get_latest(exchange_id)` -> BalanceSnapshot (이미 존재)
- **미체결**: `PositionManager.get_open_orders(exchange_id)` 또는 exchange adapter
- **포지션 P&L**: `PositionManager.get_positions()` 필터링
- **출금한도**: exchange adapter의 withdrawal limits (Live에서만 실제 값, Paper에서는 mock)

### 대시보드 페이지 레이아웃

```
+-- Total Balance: MetricCard ($1,234.56, +2.5% 24h)
+-- 거래소별 그리드 (xl:3col):
|   각 카드:
|   +-- 거래소명 + StatusBadge
|   +-- 총 잔고 / 가용 / 잠금 (bar chart)
|   +-- 포지션 P&L (양수=green, 음수=red)
|   +-- 미체결 주문 수
|   +-- 자산 내역 테이블 (symbol | free | locked | USD)
|   +-- 출금한도 진행 바 (used/daily)
+-- 하단: 자산 분포 도넛 차트 (거래소별 비중)
```

#### 대시보드 파일
| 파일 | 작업 |
|------|------|
| `dashboard/src/app/assets/page.tsx` | **신규** — Assets 페이지 |
| `dashboard/src/lib/api.ts` | `getAssets()` 함수 추가 |
| `dashboard/src/types/index.ts` | `AssetsResponse`, `ExchangeAsset` 타입 추가 |
| `dashboard/src/components/Sidebar.tsx` | Assets 네비게이션 링크 추가 |

### AC
- [ ] `GET /api/v1/assets` -> 200 응답, 연결된 거래소별 잔고 반환
- [ ] Paper 모드: VirtualBalanceTracker 기반 mock 잔고 표시
- [ ] Live 모드: 실제 거래소 API 잔고 표시
- [ ] 거래소 카드 클릭 -> 자산 내역 펼침 (accordion)
- [ ] 출금한도 진행 바 정상 렌더링
- [ ] 데이터 5초 간격 자동 갱신 (SWR)

---

## 성능 전략 (Dense + Fast)

### 렌더링 최적화
1. **CSS `contain: layout style`** — 각 카드/패널에 적용, 리플로우 격리
2. **`React.memo`** — MetricCard, SparkLine, StatusBadge 전부 memoize
3. **`font-variant-numeric: tabular-nums`** — 숫자 레이아웃 시프트 방지
4. **`isAnimationActive={false}`** — Recharts 차트 애니메이션 비활성

### 데이터 최적화
1. **SWR** — `dedupingInterval: 2000` (중복 요청 방지), `refreshInterval: 5000`
2. **WebSocket 우선** — PnL/포지션은 WS로, 나머지는 SWR 폴링
3. **Incremental loading** — Trades 페이지 `limit=50` + 무한 스크롤

### 가상화
1. **`react-window`** — Trades 테이블 100+ 행 시 가상화
2. **EventFeed** — 고정 높이 20건, overflow-y: auto

---

## 실행 순서 (의존성 기반)

```
US-432 (디자인 시스템) ------+
                             +---> US-433 (4페이지 재설계)
US-435 (paper 라우트) -------+
                             +---> US-436 (E2E 검증)
US-434 (hot-reload) ---------+

US-432 ---> US-437 (Assets 신규) ---> US-436에 포함 검증
```

**권장 실행 병렬화**:
- **Wave 1**: US-432 + US-435 (독립, 동시 가능)
- **Wave 2**: US-433 + US-434 (US-432 완료 후)
- **Wave 3**: US-437 (엔진 API + 대시보드 동시)
- **Wave 4**: US-436 (전체 E2E)

---

## ADR (Architecture Decision Record)

| 항목 | 내용 |
|------|------|
| **Decision** | Incremental Reskin (Option A) — globals.css 토큰 교체 + 공통 컴포넌트 3종 + 페이지별 순차 재설계 |
| **Drivers** | 브랜드 통일(#00B8FF), Dense 성능, Assets 신규 페이지 필요 |
| **Alternatives** | Full Rewrite (기각 — 14페이지+22컴포넌트 전면 재작성 불필요, 기능 회귀 위험) |
| **Why Chosen** | 기존 API 연동/WS/인증 모두 정상. 스타일+레이아웃만 변경하면 목표 달성 가능 |
| **Consequences** | 구 컴포넌트 중 미수정 페이지(portfolio, risk, analytics 등)는 Phase L 이후 점진 적용 |
| **Follow-ups** | Phase M에서 나머지 10개 페이지 디자인 통일, Storybook 도입 검토 |

---

## Guardrails

### Must Have
- 전 페이지 `--bg-primary: #0A0A0A`, `--accent: #00B8FF` 통일
- MetricCard/StatusBadge/SparkLine 3종 재사용
- Assets 페이지 엔진 엔드포인트 포함
- 모바일 반응형 (375px~)
- 기존 인증/WS/API 동작 보존

### Must NOT Have
- Kraken purple (#7132F5) 사용 금지 — XXX STUDIO 브랜드 아님
- 외부 UI 프레임워크 도입 (shadcn, MUI 등) — Tailwind + CSS 변수 유지
- 차트 라이브러리 추가 (Recharts 기존 유지, SparkLine은 순수 SVG)
- 페이지 로드 > 2초
- 14개 전체 페이지 재설계 (이번 Phase는 4+1페이지만)
