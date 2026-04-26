# LEVIATHAN Dashboard — Next.js 모니터링 UI

**dashboard/** 폴더는 LEVIATHAN 엔진의 실시간 모니터링 웹 애플리케이션입니다. Next.js 14 (App Router) + TypeScript + Tailwind CSS로 구축했으며 FastAPI 백엔드와 WebSocket으로 연동합니다.

## 백엔드 아키텍처 (2026-04-27, Phase 5/6/7 완료 후)

엔진은 Hexagonal Architecture: 12 Ports + 3 Adapters + 14 Listeners + Dispatcher + EngineState SSOT.
**모드 체계**: `backtest` / `paper` / `live` (3개). 현재 `paper` mode enforced.
**대시보드 API**: FastAPI `/api/v1/*` + WebSocket `/ws` (JWT). 5,205 tests passing.

## 개발 환경 시작

### 1. 의존성 설치
```bash
cd dashboard
npm install
# 또는
yarn install
pnpm install
```

### 2. 환경 설정
```bash
cp .env.example .env.local
# .env.local 파일 열어서 백엔드 URL 확인
# NEXT_PUBLIC_API_URL=http://localhost:8000
```

### 3. 개발 서버 실행
```bash
npm run dev
# 또는
yarn dev
pnpm dev
```

브라우저 열기: **http://localhost:3000**

### 4. 로그인
- 아이디: `admin`
- 암호: `.env`의 `DASHBOARD_PASSWORD` 값

## 페이지 구성

### 인증 필요 페이지 (`src/app/(authenticated)/`)

#### 1. Overview (대시보드 홈)
**경로**: `/`

**포트폴리오 요약** (PortfolioSummary):
- 총자산 (모든 거래소 잔고 합산)
- 일일 PnL (금일 순이익)
- 누적 PnL (전체 누적 수익)
- 활성 포지션 (진행 중 거래)

**리스크 게이지** (RiskGauge):
- Max Drawdown (최대 낙폭 %)
- Kill Switch 상태 (활성/비활성)
- Circuit Breaker 상태 (열림/닫힘)

**성과 추세** (PerformanceTrend):
- 시간대별 PnL 꺾은선 그래프
- 누적 자본 곡선

**이벤트 피드** (EventFeed):
- 신호 발생 알림
- 주문 체결 알림
- 경고 및 에러 (실시간 WebSocket)

**오더북 및 스프레드**:
- 실시간 Binance Spot 오더북
- 거래소×심볼 스프레드 히트맵

#### 2. Strategies (전략 성과)
**경로**: `/strategies`

전략별 상세 메트릭:
| 메트릭 | 설명 |
|--------|------|
| 신호 수 | 전략이 감지한 차익 기회 |
| 체결 수 | 실제 실행한 주문 |
| 승률 (WR) | 수익 주문 / 전체 주문 (%) |
| PnL | 전략별 순이익 |
| Sharpe Ratio | 수익 대비 변동성 |
| Max Drawdown | 최대 낙폭 |

**활성 전략**: cross_exchange, futures_futures, funding_rate, statistical_arb, ...

**비활성 전략**: spot_futures, triangular, cex_dex (설정/개발 중)

#### 3. Portfolio (포트폴리오 분석)
**경로**: `/portfolio`

**자본 곡선** (EquityCurve):
- 일일 시작 → 종료 자본 변화
- X축: 날짜, Y축: 자본 ($)
- 마우스 호버: 상세 메트릭

**리스크 메트릭스**:
| 메트릭 | 설명 |
|--------|------|
| Sharpe Ratio | 수익/변동성 비율 (>2.0 우수) |
| Sortino Ratio | 하락 변동성만 고려 |
| Calmar Ratio | 수익/최대낙폭 비율 |
| Max Drawdown | 최대 낙폭 (%) |
| Profit Factor | 총이익/총손실 (>1.0 수익) |
| Win Rate | 수익 거래 비율 (%) |

#### 4. Settings (설정)
**경로**: `/settings`

**거래소 연결 상태**:
- API 키 유효성 (✓ 연결 / ✗ 실패)
- 거래소별 잔고 (USDT 기준)
- 수수료율 표시

**엔진 설정**:
- 실행 모드 (backtest/paper/live — shadow 모드는 폐기됨, 사장님 정책)
- 활성 전략 선택/해제
- 최소 스프레드 (MIN_EDGE_BPS)
- Kill Switch 활성화/비활성화

**알림 설정**:
- Telegram 봇 토큰 (TradeBot/DevBot/InfraBot)
- 알림 레벨 (ERROR/WARNING/INFO)

#### 5. System (시스템 상태)
**경로**: `/system`

**인프라 모니터링**:
| 컴포넌트 | 메트릭 |
|---------|--------|
| Docker | 실행 중인 컨테이너 / 스토리지 |
| TimescaleDB | 연결 / 데이터 크기 / 상위 테이블 |
| Redis | 메모리 사용량 / Key 수 / 연결 |
| Prometheus | 메트릭 수집 상태 |
| Grafana | 대시보드 활성화 여부 |

### 로그인 페이지 (`src/app/login/`)

**경로**: `/login`

- 사용자명 + 암호 로그인
- JWT 토큰 발급 (60분 유효)
- 쿠키 저장 (`leviathan_token`)
- 잘못된 자격증명 에러 메시지

## API 연동

### 백엔드 요구사항

Engine API (`http://localhost:8000`)에서 제공하는 엔드포인트:

#### REST API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/api/v1/auth/login` | JWT 토큰 발급 |
| GET | `/api/v1/shadow/stats` | Shadow 실시간 메트릭 (PnL, WR, MDD) |
| GET | `/api/v1/portfolio-summary` | 포트폴리오 요약 (총자산, PnL) |
| GET | `/api/v1/portfolio/equity-curve` | 자본 곡선 시계열 |
| GET | `/api/v1/portfolio/metrics` | Sharpe/MDD/Calmar 메트릭 |
| PATCH | `/api/v1/settings/mode` | 실행 모드 전환 |
| GET | `/api/v1/exchanges` | 거래소 연결 상태 + 잔고 |
| GET | `/api/v1/risk/metrics` | Kill Switch/Circuit Breaker 상태 |

#### WebSocket API

| 경로 | 설명 |
|------|------|
| `/ws` | 상태 업데이트 스트림 (1초 간격) |
| `/ws/feed` | 이벤트 피드 (신호, 체결, 경고) |

**예시** (TypeScript):
```typescript
// 상태 업데이트 구독
const ws = new WebSocket(
  `ws://localhost:8000/ws?token=${jwtToken}`
);

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log(data.shadow_stats); // PnL, WR, MDD 업데이트
};
```

### JWT 인증

모든 보호된 엔드포인트는 HTTP 헤더에 JWT 토큰 필요:

```http
Authorization: Bearer <jwt_token>
```

또는 WebSocket 쿼리 매개변수:

```
ws://localhost:8000/ws?token=<jwt_token>
```

## 주요 컴포넌트

### PortfolioSummary
```typescript
// src/components/PortfolioSummary.tsx
// 4개 KPI 카드: 총자산, 일일 PnL, 누적 PnL, 활성 포지션
<PortfolioSummary summary={portfolioSummary} />
```

### RiskGauge
```typescript
// src/components/RiskGauge.tsx
// Max Drawdown 게이지 + Kill Switch/Circuit Breaker 상태
<RiskGauge riskMetrics={riskMetrics} />
```

### PerformanceTrend
```typescript
// src/components/PerformanceTrend.tsx
// 시간대별 PnL 꺾은선 그래프
<PerformanceTrend equityCurve={equityCurve} />
```

### EventFeed
```typescript
// src/components/EventFeed.tsx
// 실시간 이벤트 피드 (WebSocket 스트리밍)
<EventFeed events={recentEvents} />
```

### GlobalHeatmap
```typescript
// src/components/GlobalHeatmap.tsx
// 거래소×심볼 스프레드 히트맵
// 드롭다운: Major 8 / Top 20 / All / Custom
<GlobalHeatmap spreads={spreads} />
```

### OrderbookView
```typescript
// src/components/OrderbookView.tsx
// 실시간 오더북 (Binance Spot 기본, 선택 가능)
<OrderbookView exchange="binance" symbol="BTC/USDT" />
```

### EquityCurve
```typescript
// src/components/EquityCurve.tsx
// 자본 곡선 그래프 + Sharpe/MDD/Calmar 메트릭
<EquityCurve data={equityCurveData} metrics={riskMetrics} />
```

## 타입 정의

### ShadowStats
```typescript
// src/types/index.ts
interface ShadowStats {
  total_pnl: number;           // 누적 PnL ($)
  win_rate: number;            // 승률 (0~1)
  total_trades: number;        // 전체 거래
  max_drawdown: number;        // 최대 낙폭 (0~1)
  sharpe_ratio: number;        // Sharpe 비율
  by_strategy: ShadowStrategyBreakdown[];
}

interface ShadowStrategyBreakdown {
  strategy_id: string;
  pnl: number;
  trades: number;
  win_rate: number;
}
```

### PortfolioSummary
```typescript
interface PortfolioSummary {
  total_balance_usdt: number;
  daily_pnl_usdt: number;
  cumulative_pnl_usdt: number;
  active_positions: number;
  exchanges: ExchangeBalance[];
}

interface ExchangeBalance {
  exchange_id: string;
  balance_usdt: number;
  status: 'connected' | 'disconnected';
}
```

## 개발 시 주의사항

### 환경 변수 (`.env.local`)

```bash
# 백엔드 URL (개발 환경)
NEXT_PUBLIC_API_URL=http://localhost:8000

# 프로덕션 환경
# NEXT_PUBLIC_API_URL=https://api.leviathan.example.com
```

### WebSocket 재연결

EventFeed 컴포넌트는 자동으로 WebSocket을 재연결합니다 (지수 백오프):
- 1초, 2초, 4초, 8초, ... (최대 32초)

### CORS 설정

Engine이 CORS를 지원해야 합니다:
```bash
# engine/.env
CORS_ORIGINS=http://localhost:3000,https://app.leviathan.example.com
```

## 빌드 및 배포

### 개발 빌드
```bash
npm run dev
```

### 프로덕션 빌드
```bash
npm run build
npm run start
```

### Docker 배포
```bash
# docker-compose.yml에 정의됨
docker compose up -d dashboard
```

### 성능 최적화

- **Code Splitting**: Next.js 자동 분할 (라우트별 번들)
- **Image Optimization**: next/image 사용 (자동 리사이징)
- **Static Export**: 정적 페이지 미리 생성 (선택)

## 테스트

### Jest 단위 테스트
```bash
npm run test
```

### E2E 테스트 (Playwright)
```bash
npm run test:e2e
```

### 타입 체크
```bash
npm run type-check
```

## 린팅 및 포매팅

### ESLint
```bash
npm run lint
npm run lint:fix  # 자동 수정
```

### Prettier
```bash
npm run format
```

## 문제 해결

| 문제 | 해결책 |
|------|--------|
| "API 연결 실패" | 백엔드 실행 확인 (`docker compose ps engine`), NEXT_PUBLIC_API_URL 확인 |
| "로그인 안 됨" | DASHBOARD_PASSWORD 환경 변수 확인, 브라우저 쿠키 삭제 후 재시도 |
| "WebSocket 연결 끊김" | 네트워크 안정성 확인, 브라우저 콘솔 에러 로그 확인 |
| "메모리 누수" | `npm run build` 후 `npm run start` (프로덕션 모드 테스트) |
| "TypeScript 에러" | `npm run type-check`, 의존성 업데이트 (`npm install`) |

## 관련 문서

- [../README.md](../README.md) — 프로젝트 개요
- [../engine/README.md](../engine/README.md) — 백엔드 API 명세
- [../SSOT.md](../SSOT.md) — 아키텍처 + 데이터 흐름

## 라이선스

Proprietary — LEVIATHAN Project
