# Phase S4: Dashboard Completion PLAN

> **Phase**: S4 -- Dashboard Real-Data Integration & Polish
> **Status**: READY (S3 COMPLETE)
> **US**: US-140, US-141, US-142, US-143, US-144 (5 US, dashboard domain)
> **작성일**: 2026-03-14 (v2 -- codebase audit 반영)
> **산출물**: `docs/planning/Phase-S4_PLAN.md`

---

## 1. 목적

TF Semi-Final에서 발견된 대시보드 결함 5건을 수정한다:
- API 경로 prefix 불일치 정리 + SWR key 감사
- System 페이지의 MOCK_CONTAINERS 하드코딩 데이터 제거
- GlobalHeatmap/OrderbookView의 mock 데이터 생성 함수 제거 + 실 API/WS 연동
- StrategyPanel/PositionTable/OrderFlow의 mock 데이터 제거 + Portfolio Daily Returns 완성
- 테스트 업데이트 + 모바일 레이아웃 최적화

**핵심 원칙**: mock 데이터 제거, 실 API/WS 연동, graceful empty state UI 제공.

---

## 2. 코드베이스 현황 (v2 Audit 결과)

### 2.1 이미 완료된 항목 (기존 PLAN v1 대비)

아래 항목은 이전 Phase에서 이미 구현 완료됨. 재작업 불필요:

| 항목 | 현재 상태 |
|------|----------|
| `api.ts` API prefix | `/api/v1/kill-switch`, `/api/v1/strategies`, `/api/v1/strategies/${id}/toggle` 이미 정상 |
| `PnLChart.tsx` SWR key | `/api/v1/pnl` 이미 정상 |
| `engine/src/api/routes/system.py` | containers + resources 엔드포인트 이미 존재 |
| `server.py` system_router | 이미 include됨 (L145-146) |
| `api.ts` 클라이언트 함수 | `getSystemContainers`, `getSystemResources`, `getSymbols`, `getSpreads`, `getDailyReturns` 이미 존재 |
| Engine `trading.py` | `/api/v1/symbols`, `/api/v1/spreads` 엔드포인트 이미 존재 |
| SWR v2 isValidating | `RiskGauge.test.tsx`, `EventFeed.test.tsx` 이미 수정 완료 |

### 2.2 잔존 Mock 데이터 목록 (제거 대상)

| 파일 | Mock 항목 | 용도 |
|------|----------|------|
| `components/StrategyPanel.tsx` L28-33 | `MOCK_STRATEGIES` (4개 가짜 전략) | API 빈 응답 시 fallback |
| `components/StrategyPanel.tsx` L17-26 | `mockStats()` (시드 기반 가짜 통계) | StrategyCard stats |
| `components/PositionTable.tsx` L11-17 | `MOCK` (5개 가짜 포지션) | API 빈 응답 시 fallback |
| `components/GlobalHeatmap.tsx` L28-37 | `genMockGrid()` (랜덤 spread) | WS 미연결 시 fallback |
| `components/GlobalHeatmap.tsx` L10 | `FALLBACK_EXCHANGES` | 정적 거래소 목록 |
| `components/GlobalHeatmap.tsx` L126-133 | 3초 간격 mock refresh loop | WS 미연결 시 시뮬레이션 |
| `components/OrderbookView.tsx` L10-11 | `FALLBACK_SYMBOLS`, `FALLBACK_EXCHANGES` | 정적 심볼/거래소 |
| `components/OrderbookView.tsx` L39-60 | `genBook()` (랜덤 orderbook) | WS 미연결 시 fallback |
| `components/OrderbookView.tsx` L97-101 | 800ms 간격 mock refresh loop | WS 미연결 시 시뮬레이션 |
| `components/OrderFlow.tsx` L23-46 | `genOrder()` + 하드코딩 STRATEGIES/EXCHANGES/SYMBOLS | 전체 랜덤 데이터 생성 |
| `components/PnLChart.tsx` L37-49 | `genSeedData()` (사인파 seed) | 초기 차트 데이터 |
| `app/system/page.tsx` L29-38 | `MOCK_CONTAINERS` (8개 Docker 컨테이너) | 하드코딩 컨테이너 목록 |

### 2.3 엔진 API 미구현 항목

| 엔드포인트 | 상태 | 필요 US |
|-----------|------|---------|
| `GET /api/v1/portfolio/daily-returns` | 미구현 (api.ts에 클라이언트만 존재) | US-143 |

---

## 3. 대상 User Stories (수정)

| US | 제목 | 잔존 작업 | 심각도 |
|----|------|----------|--------|
| US-140 | API prefix 통일 (/api/v1/*) | SWR key 감사, `getHealth` prefix 정책 문서화 | LOW (대부분 완료) |
| US-141 | 대시보드 mock 데이터 제거 -- System 페이지 | `MOCK_CONTAINERS` 제거, `useApi` 연동 | MEDIUM |
| US-142 | 대시보드 심볼/거래소 선택 -- 엔진 반영 | Heatmap/Orderbook/OrderFlow mock 제거, API/WS 연동 | HIGH |
| US-143 | Strategy/Position/Portfolio 완성 | `MOCK_STRATEGIES`, `mockStats`, `MOCK` positions 제거, daily-returns 엔진 엔드포인트, PnLChart seed 제거 | HIGH |
| US-144 | 모바일 반응형 + 에러 상태 + 재연결 | TradeDetail 모바일, empty state UI, 테스트 업데이트 | MEDIUM |

---

## 4. 배치 분석 및 의존성

```
US-140 (SWR key 감사 — 30분)
   |
   +---> US-141 (System mock 제거)     --+
   +---> US-142 (Heatmap/Orderbook)     |---> US-144 (테스트 + 모바일)
   +---> US-143 (Strategy/Position)    --+
```

- **US-140 먼저**: SWR key 정합성이 다른 US의 데이터 fetching에 영향
- **US-141, US-142, US-143 병렬**: 서로 독립적인 컴포넌트
- **US-144 마지막**: 다른 US의 변경이 완료된 후 테스트 수정

**단일 배치**: 전부 dashboard 도메인. 엔진 변경은 `portfolio.py`에 daily-returns 1개 엔드포인트 추가만.

---

## 5. 파일별 변경 계획

### 5.1 US-140: API prefix 통일 + SWR key 감사

**현재 상태**: `api.ts`의 모든 API 호출이 이미 `/api/v1/` prefix 사용 (`/health` 제외, 의도적).

**잔존 작업**:

1. **SWR key 전수 감사**: 모든 `useApi()` 호출의 SWR key가 실제 API path와 일치하는지 확인
   - `system/page.tsx`: `'/health'`, `'/status'`, `'/exchanges'` -- API path와 일치 확인
   - `PnLChart.tsx`: `'/api/v1/pnl'` -- 정상
   - `PositionTable.tsx`: `'/trading/positions'` -- API path는 `/api/v1/positions` -> **불일치, 수정 필요**
   - `RiskGauge.tsx`, `EventFeed.tsx`, `GlobalHeatmap.tsx` 등 전체 점검

2. **`/health` 정책 문서화**: health endpoint는 의도적으로 prefix 없음 (public liveness probe). 코드 주석 추가.

**Acceptance Criteria**:
- [ ] 전체 SWR key가 실제 API path와 일치 (key=path 원칙)
- [ ] `/health` 무인증 정책 코드 주석 추가
- [ ] `npx tsc --noEmit` 0 errors

---

### 5.2 US-141: System 페이지 실데이터 연동

**현재 문제**:

`dashboard/src/app/system/page.tsx`:
1. `MOCK_CONTAINERS` (L29-38): 8개 Docker 컨테이너 하드코딩 데이터
2. Resource Usage 하드코딩 값 존재 가능 (실 API 미연동 확인 필요)
3. 컨테이너 수 하드코딩

**엔진 API**: `/api/v1/system/containers`와 `/api/v1/system/resources` 이미 존재. `api.ts`에 `getSystemContainers()`와 `getSystemResources()` 이미 존재.

**변경 사항**:

1. **`MOCK_CONTAINERS` 상수 완전 제거** (L29-38)
2. **Docker Containers 섹션**: `useApi('/system/containers', getSystemContainers)` SWR 훅 사용
   - 데이터 없을 때 loading skeleton + "Docker status unavailable" empty state
   - 컨테이너 수 동적 표시 (`running/total`)
3. **Resource Usage 섹션**: `useApi('/system/resources', getSystemResources)` SWR 훅 사용
   - 하드코딩 값 제거
   - psutil 미설치 시 (API 반환 null) "Resource data unavailable" 표시
4. **에러 상태**: API 연결 실패 시 graceful error UI + Retry 버튼

**대상 파일**:
- `dashboard/src/app/system/page.tsx` (변경)

**Acceptance Criteria**:
- [ ] `MOCK_CONTAINERS` 상수 완전 제거
- [ ] Docker 컨테이너 목록이 `/api/v1/system/containers` API에서 동적 로드
- [ ] Resource Usage가 `/api/v1/system/resources` API에서 동적 로드
- [ ] 엔진 미연결 시 graceful error state + Retry 버튼
- [ ] 컨테이너 수 동적 표시 (running/total)

---

### 5.3 US-142: Heatmap/OrderbookView/OrderFlow 실데이터 연동

**현재 문제**:

**GlobalHeatmap.tsx**:
1. `genMockGrid()` (L28-37): 랜덤 spread 데이터 생성
2. `FALLBACK_EXCHANGES` (L10): 정적 거래소 목록 (API 미응답 시 fallback)
3. 초기 state가 `genMockGrid()` (L117): 첫 렌더링부터 mock 표시
4. WS 미연결 시 3초 간격 mock refresh (L126-133)
5. `'○ MOCK'` 상태 표시 (L231)

**OrderbookView.tsx**:
1. `FALLBACK_SYMBOLS` (L10): 5개 하드코딩
2. `FALLBACK_EXCHANGES` (L11): 4개 하드코딩
3. `genBook()`: 랜덤 orderbook 데이터 생성
4. WS 미연결 시 800ms 간격 mock refresh
5. 심볼 드롭다운이 `FALLBACK_SYMBOLS` 고정 (L145)

**OrderFlow.tsx**:
1. `STRATEGIES`, `EXCHANGES`, `SYMBOLS` (L23-25): 하드코딩 목록
2. `genOrder()` (L28-46): 전체 랜덤 주문 생성
3. WS 미연결 시 2초 간격 mock 주문 생성

**엔진 API**: `/api/v1/symbols`, `/api/v1/spreads`, `/api/v1/exchanges` 이미 존재.

**변경 사항**:

1. **GlobalHeatmap.tsx**:
   - `genMockGrid()` 함수 제거
   - `FALLBACK_EXCHANGES` 제거 -- API 미응답 시 빈 배열
   - 초기 state를 빈 grid `{}` 로 설정
   - WS 미연결 시 mock refresh 루프 제거
   - REST `/api/v1/spreads` 폴링 (5s, `useApi` 사용) + WS `market_data` 오버레이
   - `'○ MOCK'` -> `'○ OFFLINE'` / `'● LIVE'` 로 변경
   - 데이터 없을 때 "Waiting for spread data..." empty state + loading skeleton

2. **OrderbookView.tsx**:
   - `FALLBACK_SYMBOLS`, `FALLBACK_EXCHANGES` 제거
   - `genBook()` 함수 제거
   - 심볼 드롭다운: `/api/v1/symbols` API에서 동적 로드 (`useApi` 사용)
   - 거래소 드롭다운: `/api/v1/exchanges` API에서 동적 로드
   - WS 미연결 시 mock refresh 루프 제거
   - 데이터 없을 때 "Select symbol and exchange" empty state
   - 심볼 175개 UX: 검색 필터 input 추가 (type-ahead filtering)

3. **OrderFlow.tsx**:
   - `genOrder()` 함수 제거
   - 하드코딩 `STRATEGIES`, `EXCHANGES`, `SYMBOLS` 제거
   - WS `trade` / `order` 메시지에서 실 주문 데이터 표시
   - WS 미연결 시 "Waiting for order flow..." empty state (mock 생성 제거)
   - trade_history REST fallback: `/api/v1/trades?limit=20` 폴링

**대상 파일**:
- `dashboard/src/components/GlobalHeatmap.tsx` (변경)
- `dashboard/src/components/OrderbookView.tsx` (변경)
- `dashboard/src/components/OrderFlow.tsx` (변경)

**Acceptance Criteria**:
- [ ] `genMockGrid()` 함수 완전 제거
- [ ] `genBook()` 함수 완전 제거
- [ ] `genOrder()` 함수 완전 제거
- [ ] `FALLBACK_SYMBOLS`, `FALLBACK_EXCHANGES` (두 파일 모두) 완전 제거
- [ ] GlobalHeatmap이 REST/WS에서 실 스프레드 데이터 표시
- [ ] OrderbookView 심볼/거래소 드롭다운이 API에서 동적 로드
- [ ] OrderFlow가 WS/REST에서 실 주문 데이터 표시
- [ ] 모든 컴포넌트에 데이터 없을 때 graceful empty state
- [ ] `'MOCK'` 표시 제거

---

### 5.4 US-143: Strategy/Position/Portfolio/PnLChart 완성

**현재 문제**:

**StrategyPanel.tsx**:
1. `MOCK_STRATEGIES` (L28-33): 4개 가짜 전략 하드코딩
2. `mockStats()` (L17-26): 시드 기반 가짜 통계 생성 -- `StrategyCard`가 L46에서 호출
3. L190: `data && data.length > 0 ? data : MOCK_STRATEGIES` -- API 빈 응답 시 mock

**PositionTable.tsx**:
1. `MOCK` (L11-17): 5개 가짜 포지션 하드코딩
2. L61: `data && data.length > 0 ? data : MOCK` -- API 빈 응답 시 mock

**PnLChart.tsx**:
1. `genSeedData()` (L37-49): 사인파 기반 가짜 PnL 시계열 (60포인트)
2. L62: `useState<PnLPoint[]>(genSeedData)` -- 초기 상태가 mock

**Portfolio page.tsx**:
1. Daily Returns 섹션: placeholder 텍스트 ("Historical return data requires extended Shadow/Live operation")
2. 엔진에 `GET /api/v1/portfolio/daily-returns` 엔드포인트 미구현

**변경 사항**:

1. **StrategyPanel.tsx**:
   - `MOCK_STRATEGIES` 상수 제거
   - `mockStats()` 함수 제거
   - API 빈 응답 시 empty state: "No strategies registered"
   - `StrategyCard`의 stats를 엔진 API에서 `/api/v1/strategy-metrics` 데이터로 교체
   - 대안: Strategy 타입에 metrics 필드 추가하여 `/api/v1/strategies` 응답에 포함

2. **PositionTable.tsx**:
   - `MOCK` 상수 제거
   - API 빈 응답 시 empty state: "No open positions"
   - `data && data.length > 0 ? data : MOCK` -> `data ?? []`

3. **PnLChart.tsx**:
   - `genSeedData()` 함수 제거
   - 초기 상태를 빈 배열 `[]` 로 설정
   - REST API 첫 응답까지 loading skeleton 표시
   - 데이터 부족 시 "Awaiting PnL data..." 메시지

4. **Portfolio Daily Returns**:
   - **엔진 신규 엔드포인트**: `engine/src/api/routes/portfolio.py`에 `GET /api/v1/portfolio/daily-returns` 추가
     - TimescaleDB `time_bucket('1 day')` 집계 또는 trade_history에서 일별 PnL 합산
     - Shadow/Paper 모드: trade_history deque에서 날짜별 그룹핑
   - Portfolio page: placeholder 텍스트 제거, Recharts BarChart 렌더링 (양수=green, 음수=red)
   - 데이터 부족 시: "Insufficient data -- requires 2+ days of operation" 메시지

**대상 파일**:
- `dashboard/src/components/StrategyPanel.tsx` (변경)
- `dashboard/src/components/PositionTable.tsx` (변경)
- `dashboard/src/components/PnLChart.tsx` (변경)
- `dashboard/src/app/portfolio/page.tsx` (변경)
- `engine/src/api/routes/portfolio.py` (변경 -- daily-returns 엔드포인트 추가)

**Acceptance Criteria**:
- [ ] `MOCK_STRATEGIES` 상수 완전 제거
- [ ] `mockStats()` 함수 완전 제거
- [ ] `MOCK` positions 상수 완전 제거
- [ ] `genSeedData()` 함수 완전 제거
- [ ] 전략/포지션/PnL 0건일 때 빈 상태 UI 표시
- [ ] StrategyCard stats가 실 API metrics 데이터 사용
- [ ] `GET /api/v1/portfolio/daily-returns` 엔진 엔드포인트 동작
- [ ] Portfolio Daily Returns 실 차트 (BarChart, 일별 수익률)

---

### 5.5 US-144: 모바일 반응형 + 에러 상태 + 재연결

**현재 문제**:

**모바일 레이아웃**:
- `TradeDetail.tsx`: `w-80` (320px 고정) -- 모바일에서 화면 밖으로 밀림
- Sidebar: 이미 모바일 drawer 패턴 구현됨 (hamburger + overlay)
- Layout: `pt-20 md:pt-6` 모바일 top padding 이미 적용
- 대부분의 grid: `grid-cols-1 xl:grid-cols-2` 이미 반응형

**에러 상태 + 재연결**:
- WebSocketManager (`lib/websocket.ts`): exponential backoff 재연결 이미 구현
- `useEngineWs`: 재연결 로직 이미 구현
- 일부 컴포넌트에 에러 상태 UI 없음 (US-140~143 empty state로 대부분 해결)

**테스트 업데이트**:
- US-141~143 mock 제거에 따른 테스트 재작성 필요
- `PnLChart.test.tsx`: `genSeedData` 제거 반영
- `GlobalHeatmap.test.tsx`: `genMockGrid` 제거, `MOCK` 상태 표시 제거 반영
- `PositionTable.test.tsx`: `MOCK` 제거 반영
- `StrategyToggle.test.tsx`: 기존 패턴 유지 가능

**변경 사항**:

1. **TradeDetail 모바일 최적화**:
   - `w-80` -> `w-full sm:w-80` (모바일: full-width, 데스크탑: 320px)
   - 모바일에서 backdrop overlay 추가 (click으로 닫기)

2. **에러/재연결 UI 통일**:
   - 전체 페이지 공용 `ConnectionBanner` 컴포넌트 생성 (optional)
   - WebSocket 끊김 시 상단 배너: "Engine disconnected -- reconnecting..." (useEngineWs connected 상태 활용)
   - 또는 기존 Overview 페이지의 `● LIVE` / `● OFFLINE` 패턴을 다른 페이지에도 적용

3. **테스트 업데이트**:
   - Mock 제거된 컴포넌트 테스트 재작성 (empty state 테스트 추가)
   - API prefix 변경된 SWR key 반영
   - `npx tsc --noEmit` 0 errors 확인

4. **TypeScript 전체 검증**:
   - `npx tsc --noEmit` 0 errors
   - 모든 타입 정합성 확인

**대상 파일**:
- `dashboard/src/components/TradeDetail.tsx` (변경)
- `dashboard/src/__tests__/components/*.test.tsx` (변경)
- `dashboard/src/components/ConnectionBanner.tsx` (신규, optional)

**Acceptance Criteria**:
- [ ] TradeDetail 모바일: `w-full sm:w-80` (모바일 full-width)
- [ ] WebSocket 끊김 시 에러 UI 표시 (배너 또는 상태 표시)
- [ ] 전체 dashboard 테스트 PASS (`jest --passWithNoTests`)
- [ ] `npx tsc --noEmit` 0 errors
- [ ] 모든 페이지가 모바일에서 스크롤/조작 가능

---

## 6. 엔진 API 변경 (최소)

Phase S4에서 엔진 코드 변경은 1건만:

| 메서드 | 경로 | 인증 | 설명 | US | 상태 |
|--------|------|------|------|----|------|
| GET | `/api/v1/portfolio/daily-returns` | JWT | 일별 수익률 시계열 | US-143 | **신규** |

기존 엔드포인트 (이미 구현 완료):
- `GET /api/v1/system/containers` -- docker subprocess
- `GET /api/v1/system/resources` -- psutil
- `GET /api/v1/symbols` -- auto-discovery
- `GET /api/v1/spreads` -- signal generator snapshot

---

## 7. 전체 파일 변경 목록

### Dashboard (변경)

| 파일 | US | 변경 유형 |
|------|----|----------|
| `dashboard/src/lib/api.ts` | US-140 | SWR key 정합성 점검, `/health` 주석 추가 |
| `dashboard/src/app/system/page.tsx` | US-141 | `MOCK_CONTAINERS` 제거, `useApi` 연동 |
| `dashboard/src/components/GlobalHeatmap.tsx` | US-142 | `genMockGrid`, `FALLBACK_EXCHANGES`, mock refresh 제거 |
| `dashboard/src/components/OrderbookView.tsx` | US-142 | `FALLBACK_SYMBOLS`, `genBook`, mock refresh 제거 |
| `dashboard/src/components/OrderFlow.tsx` | US-142 | `genOrder`, 하드코딩 목록, mock 생성 제거 |
| `dashboard/src/components/StrategyPanel.tsx` | US-143 | `MOCK_STRATEGIES`, `mockStats` 제거 |
| `dashboard/src/components/PositionTable.tsx` | US-143 | `MOCK` positions 제거 |
| `dashboard/src/components/PnLChart.tsx` | US-143 | `genSeedData` 제거 |
| `dashboard/src/app/portfolio/page.tsx` | US-143 | Daily Returns placeholder -> 실 차트 |
| `dashboard/src/components/TradeDetail.tsx` | US-144 | 모바일 `w-80` -> `w-full sm:w-80` |
| `dashboard/src/__tests__/components/*.test.tsx` | US-144 | Mock 제거 반영 + empty state 테스트 |
| `dashboard/src/components/ConnectionBanner.tsx` | US-144 | **신규** -- WS 끊김 알림 (optional) |

### Engine (변경)

| 파일 | US | 변경 유형 |
|------|----|----------|
| `engine/src/api/routes/portfolio.py` | US-143 | `daily-returns` 엔드포인트 추가 |

---

## 8. IVE 팀 배정

| 에이전트 | 역할 | 담당 US | 대상 파일 |
|---------|------|---------|----------|
| Rei (designer) | Dashboard UI/UX | US-141, 142, 144 | `dashboard/src/app/`, `dashboard/src/components/` |
| Yujin (executor) | Engine API | US-143 | `engine/src/api/routes/portfolio.py` |
| Wonyoung (test-engineer) | 테스트 | US-144 | `dashboard/src/__tests__/` |
| Gaeul (executor) | Dashboard 로직 | US-140, 143 | `dashboard/src/lib/api.ts`, `dashboard/src/components/` |

---

## 9. 위험 요소 및 완화

| 위험 | 심각도 | 완화 |
|------|--------|------|
| Docker socket 접근 불가 (system/containers) | MEDIUM | 이미 subprocess fallback 구현. 컨테이너 내부 실행 시 docker.sock 볼륨 마운트 확인 |
| 심볼 175개 드롭다운 UX (OrderbookView) | LOW | 검색 필터 input 추가 (type-ahead filtering) |
| genMockGrid 제거 후 WS 미연결 시 빈 화면 | MEDIUM | REST `/api/v1/spreads` 폴링 5s fallback + loading skeleton |
| Daily Returns 데이터 부족 (Shadow 단기 운영) | LOW | "Insufficient data" 메시지, 데이터 수집 후 자동 표시 |
| OrderFlow WS 메시지 형식 미정의 | MEDIUM | trade_history REST `/api/v1/trades?limit=20` 폴링 fallback |
| StrategyPanel stats API 필드 부재 | MEDIUM | `/api/v1/strategy-metrics` 기존 엔드포인트 활용, 또는 strategies 응답에 metrics 병합 |

---

## 10. 실행 순서

```
Step 1: US-140 -- SWR key 감사 (30분)
  +-- api.ts SWR key 전수 점검 + PositionTable key 수정
  +-- /health 정책 주석 추가
  +-- npx tsc --noEmit 확인

Step 2: US-141 + US-142 + US-143 병렬 (각 1~2시간)
  +-- US-141: system/page.tsx MOCK_CONTAINERS 제거, useApi 연동
  +-- US-142: GlobalHeatmap/OrderbookView/OrderFlow mock 제거, API/WS 연동
  +-- US-143: StrategyPanel/PositionTable/PnLChart mock 제거 + portfolio daily-returns

Step 3: US-144 -- 테스트 + 모바일 (1시간)
  +-- TradeDetail 모바일 w-full sm:w-80
  +-- ConnectionBanner 에러 상태 UI (optional)
  +-- 테스트 재작성 (mock 제거 반영)
  +-- npx tsc --noEmit + jest 전체 PASS
```

---

## 11. 완료 기준

1. **Mock 데이터 제로**: `MOCK_CONTAINERS`, `MOCK_STRATEGIES`, `mockStats()`, `MOCK` positions, `genMockGrid()`, `genBook()`, `genOrder()`, `genSeedData()`, `FALLBACK_SYMBOLS`, `FALLBACK_EXCHANGES` 전부 제거
2. **API prefix 일관성**: `/health` 제외 모든 dashboard API 호출이 `/api/v1/` prefix 사용
3. **SWR key 정합성**: SWR key가 실제 API path와 일치
4. **TypeScript**: `npx tsc --noEmit` 0 errors
5. **테스트**: 전체 dashboard 테스트 PASS
6. **모바일**: TradeDetail 모바일 full-width, 전 페이지 모바일 사용 가능
7. **Empty State**: 모든 mock 제거 컴포넌트가 데이터 없을 때 graceful UI 표시
8. **Engine API**: `GET /api/v1/portfolio/daily-returns` 엔드포인트 동작 확인
9. **WS 재연결**: 끊김 시 에러 UI 표시 + 자동 재연결

---

## 12. Stage 진행 기준

| Stage | 팀 | 완료 기준 |
|-------|-----|----------|
| A (기획) | AESPA | 이 PLAN.md v2 승인 |
| B (개발) | IVE | US-140~144 구현 완료, `pytest` + `jest` PASS |
| C (검증) | BLACKPINK | 코드리뷰 + 보안리뷰 (JWT 인증 확인) |
| D (Shadow) | NewJeans | Shadow 10min+ 실행, 대시보드 데이터 실시간 반영 확인, 모바일 브라우저 검증 |
| E (정합성) | LE SSERAFIM | SSOT 업데이트, git commit + push, 텔레그램 알림 |
