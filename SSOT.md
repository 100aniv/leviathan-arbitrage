# LEVIATHAN — Single Source of Truth (SSOT)

> **이 문서가 프로젝트의 유일한 설계 문서입니다. 다른 문서에 상태 정보를 기록하지 마세요.**
> 마지막 업데이트: 2026-03-12 (Phase J-EXT Wave 3 Batch 1: US-114~119 완료) | 최신 커밋: 0a5302e
> 실행 플랜: `.claude/plans/smooth-tickling-giraffe.md` (강화 계획) | GAP 분석: `.claude/plans/modular-seeking-wreath.md` (6-관점 통합) | PRD: `.omc/prd.json` (113개 User Stories)
> **실행 순서**: J-EXT Wave1(보안) → Wave2(UX) → Wave3(엔진) → K(Regime) → L(DEX) → M(ML) → Wave4(인프라) → F(LAST)

---

## 1. 프로젝트 개요

**LEVIATHAN**은 글로벌 암호화폐 거래소 간 크로스 차익거래를 자동 실행하는 고빈도 거래 엔진이다.

| 항목 | 내용 |
|------|------|
| 엔진 | Python 3.12+ (AsyncIO) + Rust (PyO3 hot-path) |
| 대시보드 | Next.js 14 (App Router) + JWT 인증 + 실시간 WS 피드 |
| 거래소 | 10개 네이티브 WebSocket 어댑터 (7 spot + Binance/OKX/Bybit Futures, ccxt 미사용) |
| 전략 | 8개 (7개 기본 + CexDex 조건부) |
| 인프라 | Docker Compose 8 컨테이너, TimescaleDB + Redis + Prometheus + Grafana |
| 실행 모드 | Backtest → Paper → Shadow → Live |

**거래소 목록**: Binance, Binance Futures, Bybit, Bybit Futures, OKX, OKX Futures, Bitget, Upbit, Bithumb, Coinone (10개 네이티브 어댑터)

---

## 2. 현재 상태

```
Phase:        M (ML 시그널 파이프라인) ← CURRENT  [J-EXT Wave1~3 ✅, Phase K ✅, Phase L ✅]
테스트:       4,145+ passed, 0 failed (US-092: XGBoost trainer +17 tests)
커버리지:     88%
컴플라이언스: 100% (23/23 PASS)
현재 모드:    DATA_MODE=shadow, EXECUTION_MODE=paper
최신 커밋:    (pending)
다음 작업:    Phase M — US-093 (ONNX 내보내기)
완료된 US:    US-065~076, US-105~120, US-081~092
Collectors:   10/10 (Binance, BinanceFutures, Bybit, BybitFutures, OKX, OKXFutures, Bitget, Upbit, Bithumb, Coinone)
```

### Shadow 현실성 GAP (Phase SR)

> **핵심 발견**: 신호 필터링(CostCalculator)은 상용급이나 실행 시뮬레이션(PaperExecutor)은 데모급.
> 100% 승률 / +$39K PnL은 실거래 성능을 반영하지 않음.

| ID | 심각도 | 현재 상태 | 필요한 상태 |
|----|--------|----------|------------|
| SG-1 | ~~치명~~ | ~~partial_fill_rate=0.0, rejection_rate=0.0~~ | 0.05 / 0.02 활성화 (RESOLVED) |
| SG-2 | ~~치명~~ | ~~매수+매도 레그 동기 실행, 0ms 지연~~ | 50-300ms 랜덤 지연 활성 (RESOLVED) |
| SG-3 | ~~높음~~ | ~~PowerLawSlippage(k=0) — 오더북 깊이 미반영~~ | BookWalkSlippage VWAP 체결 활성 (RESOLVED) |
| SG-4 | ~~높음~~ | ~~무한 가상 잔고, 소진 추적 없음~~ | VirtualBalanceTracker + 리밸런스 (RESOLVED) |
| SG-5 | ~~높음~~ | ~~trade_size=Decimal("1") 하드코딩~~ | compute_depth_trade_size(L1깊이×0.10) (RESOLVED) |
| SG-6 | ~~중간~~ | ~~Rate limit 시뮬레이션 없음~~ | 거래소별 토큰 버킷 (RESOLVED) |

### 프로그레시브 Shadow 테스트 프로토콜

> 72H 단일 게이트 대신 단계적 검증으로 조기 문제 발견

```
Stage 1: 1H  → 기본 동작 확인 (crash=0, 신호 흐름 정상)
Stage 2: 2H  → 승률/PnL 추세 안정성 (WR>50%, PnL 양수)
Stage 3: 6H  → 전략별 메트릭 분리 + 마찰력 정확도 검증
Stage 4: 12H → 메모리 누수/리소스 사용량 안정성
Stage 5: 24H → Sharpe>2.0, MDD<5%, 일일 PnL 양수
Stage 6: 72H → LiveGate 6-check 전체 PASS → Live 전환 승인
```
각 Stage PASS 시 자동으로 다음 Stage 연장 (멈추지 않고 누적)

### Shadow 최신 결과 (Phase E-2 US-047, 10min)

| 항목 | 값 |
|------|-----|
| 거래 수 | 2,325 (cross:1155, latency:1142, spot_futures:28) |
| 승률 | 96.5% (2243W / 82L) |
| PnL | +$39,733.58 |
| Crash | 0 (Traceback=0, CRITICAL=0) |
| 활성 전략 | 7개 등록+시작 |
| MIN_EDGE_BPS | 3 (SignalGenerator) |

### Shadow 이력 요약 (Phase 7.3h-i, MIN_EDGE_BPS=5)

| 시간 | 거래 수 | 승률 | PnL (USDT) | DD |
|------|---------|------|------------|-----|
| 5min | 12 | 75% | +0.007 | 0.09% |
| 10min | 18 | 72% | +0.009 | 0.05% |
| 30min | 123 | 70% | +0.074 | 0.07% |
| 60min | 55 | 89% | +0.045 | ~0% |

---

## 3. 아키텍처

### 3.1 모드 전환 경로

```
DATA_MODE=synthetic     → Backtest (GBM 합성 데이터)
DATA_MODE=real_public   → Paper   (실 WebSocket, 가상 실행)
DATA_MODE=shadow        → Shadow  (실 데이터 + 전체 지표 + LiveGate)
EXECUTION_MODE=live     → Live    (실 거래, LiveGate 통과 후)
```

### Paper vs Shadow 차이 (핵심)

| 구분 | Paper | Shadow |
|------|-------|--------|
| **목적** | 파이프라인 기능 검증 ("작동하는가?") | 수익성 검증 ("돈이 되는가?") |
| **데이터** | 실 WebSocket (real_public) | 실 WebSocket (shadow) |
| **실행** | PaperExecutor (가상) | PaperExecutor (가상) |
| **지표** | 없음 | Prometheus + TimescaleDB 전체 기록 |
| **LiveGate** | 없음 | 6-check 게이트 평가 |
| **Telegram** | 없음 | 일일 요약 + 알림 |
| **엔진 코드** | 동일 | 동일 (DATA_MODE env var만 다름) |

### LiveGate 전환 기준 (6-check AND)

| # | 체크 | 임계값 |
|---|------|--------|
| 1 | Sharpe (7일 롤링) | >= 2.5 |
| 2 | Max Drawdown | < 5% |
| 3 | 일일 신호 수 | >= 100/day |
| 4 | Kill Switch | Not halted |
| 5 | Circuit Breaker | CLOSED |
| 6 | 거래소 Health | >= 95% |

### 3.2 엔진 구조

```
Engine.run()
  ├── _init_config()           # Settings (env vars)
  ├── _init_infrastructure()   # EventBus (Redis/InMemory), DB, Telegram
  ├── _init_exchanges()        # Paper/Native 어댑터
  ├── _init_signal_pipeline()  # PriceHub → CostCalculator → SignalGenerator
  ├── _init_strategies()       # 8개 전략 등록
  ├── _init_risk()             # Guardian, CircuitBreaker, KillSwitch
  ├── _init_execution()        # AtomicExecutor, TradeRequestConsumer
  ├── _start_background_tasks()# Health, Reconcile, Heartbeat, Shadow, LiveGate
  └── await shutdown signal
```

### 3.4 대시보드 컴포넌트 & API

**신규 UI 컴포넌트** (US-069/070/071):

| 컴포넌트 | 페이지 | 용도 | 상태 |
|---------|--------|------|------|
| **PortfolioSummary** | Overview | 포트폴리오 상태 (4 KPI: 총자산, 일일 PnL, 누적 PnL, 활성 포지션) + 거래소별 연결 상태 바 | ✅ 완성 |
| **RiskGauge** | Overview | 리스크 지표 (Max Drawdown 게이지 + Kill Switch/Circuit Breaker 상태) | ✅ 완성 |
| **PerformanceTrend** | Overview | 세션 성과 추세 (시간대별 PnL 꺾은선 그래프) | ✅ 완성 |
| **EventFeed** | Overview | 거래/신호/경고 실시간 피드 (WebSocket 브로드캐스트) | ✅ 완성 |
| **GlobalHeatmap** | Overview | 거래소×심볼 스프레드 히트맵 (Major 8/Top 20/All/Custom 드롭다운, 로컬 저장) | ✅ 개선 (US-110) |
| **OrderbookView** | Overview | 실시간 오더북 (Binance spot 기본, 거래소 선택 가능) | ✅ 개선 |
| **ModeSwitch** | Overview 헤더 | 실행 모드 전환 UI (시뮬레이션/연습/실거래 한글 명칭, Live 전환 시 LiveGate 다이얼로그) | ✅ 신규 (US-107) |
| **EquityCurve** | Portfolio 탭 | 자본 곡선 그래프 + Sharpe/MDD/Calmar 리스크 메트릭스 | ✅ 신규 (US-108) |
| Attribution | Attribution 페이지 | 전략별/거래소별 수익 기여도 분석 | ✅ 완성 |
| Funding Rate | Funding 페이지 | 펀딩레이트 수익 추적 + 거래소별 비교 | ✅ 완성 |
| System Health | System 페이지 | 인프라 상태 (Docker, DB, Redis, Prometheus) | ✅ 완성 |

**API 엔드포인트**:

| 메서드 | 경로 | 인증 | 설명 |
|--------|------|------|------|
| GET | `/api/v1/shadow/stats` | JWT | Shadow 실시간 메트릭 (PnL, WR, MDD, 전략별 breakdown) |
| GET | `/api/v1/portfolio-summary` | JWT | 총자산 + 거래소별 잔고 (VirtualBalanceTracker 기반, exchange_status fallback) |
| GET | `/api/v1/portfolio/equity-curve` | JWT | 자본 곡선 시계열 데이터 (US-108) |
| GET | `/api/v1/portfolio/metrics` | JWT | Sharpe/MDD/Calmar 리스크 메트릭스 (US-108) |
| PATCH | `/api/v1/settings/mode` | JWT | 실행 모드 전환 (shadow/paper/live, LiveGate 체크 포함) (US-107) |
| GET | `/exchanges` | JWT | 거래소 연결 상태 + 잔고 (balance.USDT) |
| GET | `/risk/metrics` | JWT | Kill Switch/Circuit Breaker 상태 + MDD |
| WS | `/ws` | JWT (query/cookie) | 실시간 state_update 브로드캐스트 (1s 간격, shadow_stats 포함) |
| WS | `/ws/feed` | JWT (query/cookie) | 실시간 이벤트 피드 (거래/신호/경고 브로드캐스트) |

**Shadow 데이터 흐름**: `ShadowMode._metrics` → `get_snapshot()` (thread-safe dict) → `EngineContext.shadow_mode` → `/api/v1/shadow/stats` (REST) + WS `_dashboard_feed_loop` (1s 간격) → `ShadowPanel.tsx` 렌더링

**ShadowStats 타입** (`dashboard/src/types/index.ts`):
- `total_pnl`, `win_rate`, `total_trades`, `max_drawdown`
- `by_strategy`: `ShadowStrategyBreakdown[]` (strategy_id, pnl, trades, win_rate)

### 3.3 전략 매트릭스

| # | 전략 | 파일 | 상태 | 차단 GAP | 예상 연간수익 |
|---|------|------|------|---------|------------|
| 1 | cross_exchange | `strategies/cross_exchange.py` | **활성** | ~~GAP 1,2~~ RESOLVED | 5-25% |
| 2 | spot_futures | `strategies/spot_futures.py` | 대기(CONDITIONAL) | 비용>basis (시장 조건), 신호 파이프라인 검증 완료 | 8-30% |
| 3 | futures_futures | `strategies/futures_futures.py` | **활성** | OKX/Bybit futures 수집기 추가로 2+ 선물 거래소 확보 (US-075) | 5-15% |
| 4 | triangular | `strategies/triangular.py` | 대기(CONDITIONAL) | ~~GAP 7,4~~ RESOLVED, 실시장 cycle 희소 | 2-10% |
| 5 | funding_rate | `strategies/funding_rate.py` | **검증됨** | 4거래소×8심볼 수집 성공, diff<threshold 시 정상 필터 | 15-30% |
| 6 | statistical_arb | `strategies/statistical_arb.py` | **검증됨** | ~~GAP 3~~ RESOLVED, 2 trades 실행, WFE 음수 주의 | 11-16% |
| 7 | latency_arb | `strategies/latency_arb.py` | **활성** | cross_exchange 신호 라우팅 추가, 82 trades in 10min | 20-100%+ |
| 8 | cex_dex | `strategies/cex_dex.py` | 조건부 | GAP 8 (DEX stub, Phase F) | 10-50% |

> **GAP 의존성 순서**: GAP9→10→(5,6,7 병렬)→3→(1,2)→4 — 상세: §9 참조

---

## 4. 수학 모델

### 4.1 슬리피지 모델 (3종)

**Base SlippageModel** (`execution/paper.py`)
```
slippage_pct = base_slippage_pct * (1 + random(0, 0.5) * volatility_factor)
fill_price = base_price * (1 +/- slippage_pct)
기본값: base_slippage_pct=0.001 (0.1%), volatility_factor=1.0
용도: 유닛 테스트, 기본 Paper 모드
```

**PowerLawSlippage** (`modes/shadow.py`)
```
impact = k * size^gamma
slippage = base_slippage_pct * impact * random(0.5, 1.5)
fill_price = base_price * (1 +/- slippage)
기본값: k=0.0, gamma=0.5, base=0.001
근거: SignalGenerator가 CEXOrderbookSlippage로 사전 필터.
      PaperExecutor에서 추가 슬리피지 적용 시 이중 계산.
      k=0으로 PaperExecutor 슬리피지 제거 (Phase C 확정).
용도: Shadow 모드 전용
```

**CEXOrderbookSlippage** (`friction/slippage_model.py`)
```
impact_fraction = sigma * k * sqrt(size / ADV)
expected_abs = impact_fraction * mid_price
CI: size/ADV <= 1.0 -> +/-20%, 1-3 -> +/-50%, 3-10 -> +/-100%, >10 -> DO NOT TRADE
Impact_decay(t) = Impact_0 * (1 + t/t_0)^(-gamma)  [t_0=60s, gamma=0.5]
용도: SignalGenerator 필터 (Phase 4+)
```

### 4.2 마찰력 모델 (`friction/cost_calculator.py`)

```
Net_Profit = Gross_Spread
           - Fee_Buy - Fee_Sell
           - Slippage_Buy - Slippage_Sell
           - Network_Cost - Funding_Cost - Opportunity_Cost
           - E[Rollback_Cost]

E[Rollback_Cost] = P(rollback) * Avg_Rollback_Cost
P(rollback): 30-trade 롤링 윈도우, cold-start 5%
```

거래소별 수수료 (Tier 0, Taker):

| 거래소 | Maker | Taker | 비고 |
|--------|-------|-------|------|
| Binance | 0.10% | 0.10% | |
| Bybit | 0.10% | 0.10% | Spot VIP0 |
| OKX | 0.08% | 0.10% | |
| Bitget | 0.10% | 0.10% | |
| Upbit | 0.05% | 0.139% | KRW 마켓 |
| Bithumb | 0.25% | 0.25% | KRW 마켓 |
| Coinone | 0.02% | 0.02% | API 할인 적용 (기본 0.20%) |

### 4.3 리스크 모델

**KillSwitch (3-tier)**:
- Tier 1: 일일 누적 손실 > 임계값 → 전체 중단
- Tier 2: CB OPEN > 30min / 레이턴시 > 5s 연속 10회 → 자동 일시정지
- Tier 3: 수동 halt_local() → 즉시 중단

**CircuitBreaker**: CLOSED → OPEN → HALF_OPEN (지수 백오프 1s→60s cap)

**RiskGuardian (9-check)**: 자본, 마진, 스프레드, 포지션, 주문크기, 일일손실, 연속손실, 슬리피지, 롤백비용

### 4.4 슬리피지 계층 규칙

> **사전 필터**: SignalGenerator의 CEXOrderbookSlippage — 통계적 시장 영향 추정 (sigma * k * sqrt(size/ADV)). 신호 허용/차단 기준으로만 사용. fill_price에 미반영.
> **실행 시뮬레이션**: BookWalkSlippage (US-060) — 실제 오더북 깊이 워킹 VWAP 체결가 산출. fill_price를 결정하는 실행 계층.
> **이중 계산 아님**: 두 계층은 서로 다른 질문에 답함 (필터 vs 체결가). PnL 계산에서 더해지지 않음.
> **금지**: PowerLawSlippage(k>0)를 PaperExecutor에 적용하는 것은 여전히 금지 (통계 모델 + 통계 모델 = 이중계산).
> BookWalkSlippage는 실제 오더북 레벨을 워킹하므로 통계 모델이 아닌 실행 시뮬레이션.

### 4.5 Sharpe 비율 (연간화)

```
Sharpe = (mu - rf) / sigma * sqrt(periods_per_year)
mu = mean(hourly_returns), sigma = std(hourly_returns)
periods_per_year = 8760 (1시간 윈도우)
```

### 4.6 Maximum Drawdown

```
MDD = max_t { (Peak_t - Cumulative_PnL_t) / Peak_t }
```

---

## 5. 거래소 어댑터

### 10개 네이티브 WebSocket 어댑터 (ccxt 미사용)

| 거래소 | WS 엔드포인트 | 심볼 형식 | 상태 | 비고 |
|--------|-------------|---------|------|------|
| Binance | `wss://stream.binance.com:9443` | `BTC/USDT` | 연결됨 | 멀티스트림 지원 |
| Binance Futures | `wss://fstream.binance.com` | `BTCUSDT` | 연결됨 | futures_futures 활성 |
| Bybit | `wss://stream.bybit.com/v5/public/spot` | `BTC/USDT` | 준비 | |
| Bybit Futures | `wss://stream.bybit.com/v5/public/linear` | `BTCUSDT` | 준비 | US-075, futures_futures 활성 |
| OKX | `wss://ws.okx.com:8443/ws/v5/public` | `BTC/USDT` | 준비 | |
| OKX Futures | `wss://ws.okx.com:8443/ws/v5/public` | `BTC-USDT-SWAP` | 준비 | US-075, -SWAP 접미사 |
| Bitget | `wss://ws.bitget.com/v2/ws/public` | `BTC/USDT` | 준비 | |
| Upbit | `wss://api.upbit.com/websocket/v1` | `BTC/KRW` | 연결됨 | 배치 구독 |
| Bithumb | `wss://pubwss.bithumb.com/pub/ws` | `BTC/KRW` | 연결됨 | 누적 orderbook + REST re-sync (US-073) |
| Coinone | `wss://stream.coinone.co.kr` | `BTC/KRW` | 준비 | watchdog 120s + app PING 25min (US-074) |

### KRW 자동 매핑

- `CollectorManager.KOREAN_EXCHANGES = {"upbit", "bithumb", "coinone"}`
- `_get_exchange_symbols()`: `/USDT` → `/KRW` 자동 변환
- `ShadowMode._on_orderbook()`: KRW → USDT 역환산 (dual-source: Upbit+Bithumb API, 30s 갱신)
- Sanity: +/-10%, 120s staleness, 5-reject lockout escape

### Bithumb 데이터 품질 이슈 (US-073 근본 해결)

증분 orderbook에서 초기 스냅샷 없이 수신 → 소형코인(NOM +62%, SXP +12%)에서 허위 스프레드 발생.
**근본 해결 완료 (US-073)**: REST 스냅샷 후 증분 적용 (누적 book). stale 5초 감지 + parallel re-sync. `max_spread_pct=5.0` 필터 유지.

---

## 6. 인프라

### Docker Compose (8 컨테이너)

| 서비스 | 이미지 | 포트 | 역할 |
|--------|--------|------|------|
| engine | leviathan-engine:latest | 8000, 8001 | 거래 엔진 (REST + WS) |
| redis | redis:7.2-alpine | 6379 | 실시간 상태, Pub/Sub |
| redis-exporter | oliver006/redis_exporter:v1.58.0 | 9121 | Redis Prometheus 메트릭 |
| timescaledb | timescale/timescaledb:latest-pg16 | 5432 | 시계열 DB |
| dashboard | leviathan-dashboard:latest | 3000 | Next.js 대시보드 |
| prometheus | prom/prometheus:v2.50.1 | 9090 | 메트릭 수집 (30일 보관) |
| grafana | grafana/grafana:10.3.3 | 3001 | 메트릭 시각화 |
| nginx | nginx:alpine | 80, 443 | TLS 종단 + 역방향 프록시 |

### 모니터링 스택

- **Prometheus**: 엔진 메트릭 (`/metrics`), Redis exporter, 30일 보관
- **Grafana**: 대시보드 시각화 (admin/leviathan)
- **Telegram**: 일일 요약, 신호 알림, 긴급 알림
- **Nginx**: TLS 역방향 프록시, Rate Limiting, IP 화이트리스트, 보안 헤더

### DB 스키마 (TimescaleDB)

- `execution_log`: 거래 이력 (hypertable, 90일 retention)
- 시계열 저장: OHLCV, 스프레드 이력, 체결 데이터

---

## 7. 남은 작업 (`.omc/prd.json` 96개 User Stories)

> **실행 방식**: 3-Phase Sequential — Phase A(기획/OMC) → Phase B(개발/Agent Teams) → Phase C(검증/OMC)
> **자동화**: `ralph autopilot` → prd.json 순회 → 각 US 자동 실행

### Phase A: 인프라 재정비 (US-001~009) — ☑ ALL PASS

- [x] US-001: OMC State 초기화 + project-memory 수정
- [x] US-002: SSOT.md 생성 (기존 문서 통합)
- [x] US-003: 커스텀 에이전트 정의 파일 생성
- [x] US-004: settings.local.json 권한 완화
- [x] US-005: CLAUDE.md 업데이트
- [x] US-006: prd.json 생성
- [x] US-007: notepad.md 생성
- [x] US-008: 기존 문서 아카이브
- [x] US-009: Phase A 통합 검증

### Phase B-1: Foundation (GAP 9,10) — US-010~013 — ☑ ALL PASS

- [x] US-010: okx/bitget futures 수수료 추가 (DEFAULT_FEES + WITHDRAWAL_FEES)
- [x] US-011: Unknown exchange fallback (ValueError → 0.25% + logging)
- [x] US-012: estimate_cost() Protocol 브릿지 (CostCalculator)
- [x] US-013: 7개 전략 통합 테스트 (+47 tests, 3063 total)

### Phase B-2: Futures Infrastructure (GAP 5,6) — US-014~018 ✅ ALL PASS

- [x] US-014: BinanceFuturesCollector 검증 (17 unit tests)
- [x] US-015: CollectorManager futures 등록 + Shadow 분리 검증 (13 unit tests)
- [x] US-016: FundingRateCollector 구현 — 4 거래소 REST (23 unit tests)
- [x] US-017: Engine.run()에 FundingRateCollector 연결 (shadow.py + main.py)
- [x] US-018: Futures + FundingRate 통합 테스트 (19 integration tests)

### Phase B-3: Signal Production (GAP 7,3,2) — US-019~022 ✅

- [x] US-019: TriangularScanner (Bellman-Ford) — 18 unit tests
- [x] US-020: RealDataSignalProducer (실 데이터 신호) — 10 unit tests
- [x] US-021: Shadow mode에 RealDataSignalProducer 연결 — 384줄 인라인 삭제
- [x] US-022: 4종 신호 타입 통합 테스트 — 17 integration tests

### Phase B-4: Shadow Integration (GAP 1) — US-023~026 ✅ ALL PASS

- [x] US-023: ShadowMode에 StrategyManager 주입 + route_signal() (16 tests)
- [x] US-024: 전략별 메트릭 추적 (by_strategy + Prometheus + Telegram breakdown)
- [x] US-025: main.py Shadow mode에 StrategyManager 전달 + start_strategy()
- [x] US-026: Shadow 전략 통합 테스트 (10 integration tests)

### Phase B-5: Multi-Leg Executor (GAP 4) — US-027~029 ✅ ALL PASS

- [x] US-027: ExecutionResult N-leg 확장 (legs:list[LegResult] + compat properties)
- [x] US-028: execute_multi_leg() + 역순 rollback + TradeRequestConsumer 라우팅
- [x] US-029: 3-leg triangular 실행 테스트 (14 unit + 4 integration)

### Phase C: Strategy Validation — US-030~036 ✅ ALL PASS

- [x] US-030: cross_exchange 전략 객체 경유 Shadow 10min (132T, 100%WR, +$34.97)
- [x] US-031: spot_futures (CONDITIONAL: 신호 생성 확인, 비용>basis로 정상 필터)
- [x] US-032: futures_futures (CONDITIONAL: 선물 거래소 1개, 코드 검증 완료)
- [x] US-033: funding_rate (PASS: 4거래소×8심볼 수집, 0 failures)
- [x] US-034: triangular (CONDITIONAL: scanner 검증 완료, 실시장 cycle 미감지)
- [x] US-035: statistical_arb (PASS: z-score 계산, 2 trades 실행)
- [x] US-036: 전체 통합 (PASS: 7 전략 동시, PnL 분리, crash 0)

### Phase D: Dashboard UX — US-037~041 — ☑ ALL PASS (코드 레벨, Chrome 검증은 D-verify)

- [x] US-037: Trade History + Alerts 페이지
- [x] US-038: Settings 페이지 + Logout 기능
- [x] US-039: Strategy Analytics + Funding Rate 모니터
- [x] US-040: Exchange Status 대시보드
- [x] US-041: Mobile Responsive + 전략별 API endpoint

### Phase D-verify: 브라우저 검증 — US-063~064 ☑ ALL PASS

- [x] US-063: 대시보드 Chrome 브라우저 검증 — 핵심 4페이지 (Overview, Trades, Settings, Login)
- [x] US-064: 대시보드 모바일 반응형 + Settings/Alerts 페이지 검증

### Phase E-1: Production Monitoring — US-042~044 — ☑ ALL PASS

- [x] US-042: Telegram 인프라 모니터링 daemon
- [x] US-043: Grafana 대시보드 프리셋
- [x] US-044: 자동 알림 규칙

### Phase E-2: Auto-Tuning Pipeline — US-045~048 — ☑ ALL PASS

- [x] US-045: Scheduled Offline Tuner (Docker)
- [x] US-046: Shadow Runner 자동 적용 + TimescaleDB 데이터
- [x] US-047: Adaptive Threshold + Regime Detector (28 tests, 4 MEDIUM fixes)
- [x] US-048: 3-Layer 튜닝 통합 테스트 (17 integration tests)

### Phase E-3: Production Readiness — US-049~053 — ☑ ALL PASS

- [x] US-049: Capital Allocator (Kelly Criterion, Half-Kelly, 19 tests)
- [x] US-050: Inventory Rebalancer + Balance Tracker (27 tests)
- [x] US-051: Performance Attribution Engine (13 tests)
- [x] US-052: TimescaleDB 자동 백업 + Position Recovery (12 tests)
- [x] US-053: Dashboard Attribution 페이지

### Phase SR: Shadow 현실성 강화 — US-058~062 ☑ ALL PASS

- [x] US-058: PaperExecutor 부분체결(5%) + 주문거부(2%) 활성화 — 12 tests, 3484 total PASS
- [x] US-059: Shadow 레그 간 실행 지연(50-300ms) 추가 — 8 tests, 3492 total PASS
- [x] US-060: BookWalkSlippage — 오더북 깊이별 VWAP 체결 — 15 tests, 3507 total PASS
- [x] US-061: VirtualBalanceTracker + 깊이 기반 주문 크기 제한 (15 tests, 3522 total PASS)
- [x] US-062: 거래소별 Rate Limit 시뮬레이션 (11 tests, 3533 total PASS)

### Phase G: 전략 수익성 복원 — US-066~068

- [x] US-066: Stale Orderbook 감지 + 블랙리스트 + 손실 제한 — StaleOrderbookDetector 4계층 방어, 34 tests, 3609 total PASS
- [x] US-067: 전략별 개별 1H Shadow 검증 — StrategyValidationOrchestrator 구현, STRATEGY_SIGNAL_ID_MAP 기반 격리, 18 tests, 3627 total PASS
- [x] US-068: Shadow 기반 파라미터 재최적화 — Optuna 파이프라인 latency_arb 추가, TimescaleDB/activation 필터 연동, param_bridge 키 정규화, 13 tests, 3640 total PASS

### Phase H: 대시보드/프론트 통합 완성 — US-065, US-069~072 ☑ ALL PASS

- [x] US-065: Shadow→Dashboard 데이터 브리지 — ShadowMode.get_snapshot() + /api/v1/shadow/stats REST + ShadowPanel 컴포넌트 + WS feed shadow_stats 통합. 3,656 tests PASS
- [x] US-069: Overview 종합 상황판 리디자인 — PortfolioSummary(4 KPI + 거래소 상태바) + RiskGauge(MDD 게이지) + PerformanceTrend(PnL 추세) + EventFeed(실시간 피드). 81 tests PASS
- [x] US-070: Attribution/Funding/System 빈 페이지 완성 — 3개 페이지 실 컨텐츠 구현 (전략별 수익 분석, 펀딩레이트 추적, 인프라 상태). 81 tests PASS
- [x] US-071: GlobalHeatmap + OrderbookView 실 데이터 연결 — REST polling fallback 구현, 거래소 선택 기능. 81 tests PASS
- [x] US-072: 계좌 정보/총자산/거래소별 잔고 표시 — GET /api/v1/portfolio-summary + VirtualBalanceTracker 기반 거래소별 잔고 + PortfolioSummary.tsx 컴포넌트. 12 tests, 3,668 total PASS

### Phase I: 거래소/전략 완성도 — US-073~076

- [x] US-073: Bithumb REST 스냅샷 → 증분 orderbook 근본 해결 (누적 book, stale 5초 감지, parallel re-sync)
- [x] US-074: Coinone WS 안정성 강화 (지터 백오프, watchdog 120초, app PING 25분, symbol stale 감지)
- [x] US-075: futures_futures 전략 활성화 (OKX/Bybit futures 수집기, DEFAULT_EXCHANGES 8→10)
- [x] US-076: 전략/거래소 완성도 전수 감사

### Phase J-EXT: 보안+UX+엔진+인프라 강화 (6-관점 GAP 분석) — US-105~122

> 6개 관점(UX, 퀀트, DevOps, PM, 보안, 경쟁분석) 통합 GAP 분석 결과.
> 상세: `.claude/plans/modular-seeking-wreath.md`

**Wave 1 — 보안 (간단, 먼저)**
- [x] US-105: JWT 시크릿 기본값 제거 + bcrypt 비밀번호 해싱 (기본값 fallback 제거 → 미설정 시 서버 거부, 평문→bcrypt) ✅
- [x] US-106: WebSocket 피드 JWT 인증 (WS 핸드셰이크 시 토큰 검증) ✅

**Wave 2 — 대시보드 UX**
- [x] US-107: 모드 전환 UI 연결 + 친화적 명칭 (ModeSwitch.tsx 신규, PATCH /api/v1/settings/mode, shadow→"시뮬레이션"/paper→"연습"/live→"실거래", Live 전환 시 LiveGate 확인 다이얼로그) ✅
- [x] US-108: 포트폴리오 별도 탭 (portfolio/page.tsx + EquityCurve.tsx 신규, GET /portfolio/equity-curve + /portfolio/metrics, Sharpe/MDD/Calmar 리스크 메트릭스, 자산배분 바 차트) ✅
- [x] US-109: 오버뷰 개선 (ROI%, 시스템 성능 위젯, "Shadow Monitor"→현재 모드명 동적 변경) ✅
- [x] US-110: 히트맵 심볼 확장 (GlobalHeatmap.tsx Major 8/Top 20/All/Custom 드롭다운, All 시 엔진 전체 심볼 표시, Custom 드롭다운 로컬 저장) ✅
- [x] US-111: 거래 설명 기능 ("왜 이 거래를?" — GET /trades/{id} + TradeDetail 사이드 패널, reason/spread_bps/fee_usd/net_pnl)
- [x] US-112: 트레이드 필터링 + CSV 내보내기 (날짜/전략/거래소/심볼 필터 + RFC 4180 CSV 다운로드)
- [x] US-113: 용어 친화화 + 툴팁 ("War Room"→"대시보드", "MIN_EDGE_BPS"→"최소 수익 기준" + info 아이콘) ✅

**Wave 3 — 엔진 강화**
- [x] US-114: 동적 포지션 사이징 (신뢰도(edge) × 레짐(RegimeDetector) × 유동성(DepthAnalyzer) 기반, CRISIS 25%, LOW vol 150%) ✅
- [x] US-115: 슬리피지 피드백 루프 (실제 체결가 vs 예상가 비교 → EMA로 모델 파라미터 자동 조정) ✅
- [x] US-116: TCA 모듈 + 실행 레이턴시 위젯 (TCAAnalyzer + PercentileTracker, GET /api/v1/tca/summary JWT, TCAWidget.tsx System 탭) ✅
- [x] US-117: 텔레그램 양방향 명령어 (/status, /kill, /mode, /balance — 단일 봇으로 통합) ✅
- [x] US-118: 전략 간 상관관계 모니터링 (30-trade 롤링 상관계수, >0.7 시 소규모 전략 50% 축소) ✅
- [x] US-119: IOC 주문 타입 (IOC 리밋 우선 → 타임아웃 시 마켓 폴백) ✅
- [x] US-120: 인벤토리 리밸런싱 통합 확인 (main.py wiring + _rebalancer_loop 4h 주기 + Telegram CRITICAL/WARNING 알람) ✅

### Phase K: Regime Detection 기반 구축 — US-081~085

- [x] US-081: ML 의존성 + HMM 3-regime 설계 (hmmlearn/sklearn [ml] dep, MarketRegime CALM/NORMAL/VOLATILE 확장, HMMRegimeDetector 클래스) ✅
- [x] US-082: 레짐 피처 엔지니어링 (RegimeFeaturePipeline 10-feature: vol×3, spread×2, volume×2, momentum×2, order_flow×1 + normalize + fill_missing) ✅
- [x] US-083: HMM 학습 파이프라인 (HMMTrainer: fetch→extract→fit→캐시, 주간 배치 스케줄러, predict <2ms) ✅
- [x] US-084: 레짐→시그널 통합 (REGIME_MIN_EDGE: CALM:3bps, NORMAL:5bps, VOLATILE:8bps, CRISIS:15bps + SignalGenerator regime_detector 파라미터) ✅
- [x] US-085: Walk-forward 레짐 검증 (RegimeWalkForwardAnalyzer: 레짐-성과 상관분석, regime-adaptive vs fixed PnL 비교, walk-forward PASS 검증) ✅

### Phase L: DEX 실시간 + 가스비 통합 — US-086~090

- [x] US-086: 실시간 가스비 오라클 (GasOracle: 6 chains, 30초 캐시, RPC→fallback, [dex] optional dep) ✅
- [ ] US-087: CostCalculator DEX 확장 (LP fee + gas + MEV 추정 + bridge cost)
- [ ] US-088: Uniswap V3 실시간 가격/슬리피지 (slot0 → 가격, liquidity → VWAP)
- [ ] US-089: CEX-DEX 스프레드 스캐너 (net spread 가스비 차감 후)
- [ ] US-090: CEX-DEX Shadow 검증

### Phase M: 로컬 ML 시그널 파이프라인 — US-091~096

- [ ] US-091: ML 피처 파이프라인 (orderbook/volatility/volume/regime/execution)
- [ ] US-092: XGBoost 학습 루프 (주간 배치, optuna HPO)
- [ ] US-093: ONNX 내보내기 + 버전관리 (onnxmltools, opset 관리)
- [ ] US-094: ONNX Runtime 추론 통합 (<1ms 보장, SignalGenerator 연동)
- [ ] US-095: ML 시그널 백테스트 (walk-forward A/B 비교)
- [ ] US-096: Production Canary (Paper→Shadow ML 시그널 검증)

### Phase J-EXT Wave 4 — 인프라 (K/L/M 완료 후 실행) — US-121~122

- [ ] US-121: Loki + Promtail 로그 집계 (Grafana 연동, 크로스 컨테이너 검색)
- [ ] US-122: WAL 백업 + PITR (RPO <1시간, 주간 복원 검증)

### Phase F: 최종 검수 — US-054~057, US-077, US-079~080 (LAST — 전 Phase 완료 후 진입)

- [ ] US-054: Progressive Shadow 재실행 (J-EXT/K/L/M 포함된 최신 엔진, 72H 전 단계) — 기존: 580T/67.2%WR/+$2203.92
- [ ] US-077: 문서 정합성 최종 감사 (SSOT↔PRD↔구현 전수 조사)
- [ ] US-057: 운영 문서 최종화
- [ ] US-079: 운영 Runbook 업데이트 + 장애 대응 매뉴얼
- [ ] US-055: LiveGate 자동 평가 확인
- [ ] US-056: Live 모드 전환 (사용자 승인)
- [ ] US-080: 종합 검사지 기반 최종 감사 (178항목 체크리스트)

---

## 8. 결정 로그

| 날짜 | 결정 | 근거 |
|------|------|------|
| Phase 4 | ccxt 미사용, 네이티브 어댑터 | ccxt 레이턴시 오버헤드, 커스텀 최적화 불가 |
| Phase 8 | Testnet 단계 제거 | 거래소별 testnet 불안정, Shadow가 대체 |
| 7.3b | max_spread_pct=5.0 게이트 | Bithumb 허위 스프레드 60%+ 방지 |
| 7.3b | 배치 WS 구독 훅 추가 | Upbit/Bithumb 단일 구독 메시지 요구 |
| 7.3d | KRW dual-source 실시간 환율 | 정적 1380 vs 실제 1477 괴리 해소 |
| 7.3d | MIN_PRICE_USD=0.10 | 소액 코인 슬리피지 리스크 감소 |
| 7.3h | MIN_EDGE_BPS=5 확정 | 40=없음, 30=거의없음, 5=모든 시간대 수익 |
| 7.3j | PaperExecutor ZERO slippage | 이중 슬리피지 계산 방지 (CEXOrderbook이 유일 소스) |
| 7.3k | transfer_coin 동적 할당 | network cost: BTC=$1.39, ETH=$5.60, XRP=$0.40 등 |
| SR | Docker Shadow 필수화 | Shadow 테스트 중 Docker 미실행 발견. graceful degradation으로 거래 로직 유효하나 TimescaleDB/Redis 미저장. 향후 Shadow 실행 전 `docker compose up -d` 필수 |
| SR | Phase D 브라우저 테스트 필수화 | US-037~041, US-053 대시보드 US가 `npm run build`만으로 passes:true 처리됨. 실제 Chrome 렌더링/API 연동/WebSocket 피드 검증 미완. Phase D 완료 기준에 Chrome 브라우저 테스트 추가 |
| SR | Shadow 현실성 6개 GAP 식별 (SG-1~SG-6) | PaperExecutor가 데모급(100% fill, 0ms delay, 무한잔고). 상용급 전환 위해 부분체결/지연/깊이VWAP/가상잔고/Rate Limit 추가 필요 |
| Phase G | 4계층 stale 방어 (cross-validation + periodic refresh + update_count gate + loss cap) | 1H Shadow -$1,937 fat-tail 방지, defense-in-depth |
| Phase G | 단일 ShadowMode 인스턴스 재사용 + 동적 _disabled_strategies 전환 | WS 재연결 비용 절감 (7×30s 절약), VirtualBalanceTracker/RateLimiter/StaleDetector reset으로 전략 간 격리 보장 |
| Phase G | latency_arb Optuna 튜닝 파이프라인 추가 | US-068: statistical_arb/cex_dex 제외, activation filter 결과 연동, TimescaleDB 데이터 기반 최적화 |
| Phase G | param_bridge 키 정규화 (max_position_size_usdt) | strategy_params.json의 '최대_포지션_크기_usdt'를 'max_position_size_usdt'로 정규화하여 Optuna 반환값과 일치 |
| Phase H US-065 | ShadowMode.get_snapshot() 공개 메서드 + EngineContext.shadow_mode 필드 | Shadow 메트릭을 REST/WS 양방향으로 대시보드에 노출. shadow_router 별도 마운트로 관심사 분리. ShadowPanel 조건부 렌더링으로 Shadow 모드 비활성 시 UI 숨김 |
| Phase H US-069 | 4개 신규 컴포넌트 + Overview 페이지 리디자인 | PortfolioSummary(상태 배지 + 4 KPI), RiskGauge(SVG 게이지), PerformanceTrend(선 그래프), EventFeed(피드). grid 반응형 레이아웃 (1-col mobile, 2-col xl+). useEngineWs() + useApi() hook으로 실시간 데이터 연동 |
| Phase H US-070 | Attribution/Funding/System 페이지 실 컨텐츠 구현 | 3개 빈 페이지를 함수형 컴포넌트로 변환. REST API 연동 (getAttributionData, getFundingMetrics, getSystemHealth). 테스트 통합 (81 total) |
| Phase H US-071 | GlobalHeatmap + OrderbookView REST polling fallback | API 장애 시 mock 데이터로 fallback. 거래소 드롭다운 선택기 추가. 재시도 로직 (exponential backoff) |
| Phase H US-072 | VirtualBalanceTracker 기반 포트폴리오 요약 API | /api/v1/portfolio-summary 신설. Shadow 모드에서 VirtualBalanceTracker 직접 조회, 비Shadow 시 exchange_status fallback. PortfolioSummary.tsx로 거래소별 잔고 breakdown + mode badge 표시 |
| Phase I US-073 | Bithumb 누적 orderbook 방식 채택 | REST 스냅샷 후 증분 적용(full_snapshot→updates). 허위 스프레드 근본 해결. stale 5초 감지 + parallel re-sync로 데이터 신뢰성 확보 |
| Phase I US-074 | 지터 백오프 공통화 + Coinone watchdog | 재연결 지터(jitter backoff) 패턴 공통화. Coinone watchdog 120초, app PING 25분으로 장시간 연결 안정성 확보. symbol stale 감지 추가 |
| Phase I US-075 | OKX/Bybit futures -SWAP 접미사 + DEFAULT_EXCHANGES 8→10 | okx_futures/bybit_futures 수집기 신설. 심볼 형식: BTC-USDT-SWAP (OKX), BTCUSDT (Bybit). futures_futures 전략 활성화 조건(2+ 선물 거래소) 충족 |
| Phase J-EXT US-106 | WS JWT: query param ?token= 우선 + cookie leviathan_token fallback | 브라우저 WebSocket API가 커스텀 헤더 미지원. 업계 표준(Socket.IO, Slack). accept()→close(4003) 패턴으로 미인증 연결 즉시 거부 |
| Phase J-EXT US-107 | ModeSwitch: PATCH /api/v1/settings/mode + LiveGate 확인 다이얼로그 | Live 전환 시 6-check LiveGate 통과 여부 사전 확인. 한글 명칭(시뮬레이션/연습/실거래)으로 비개발자 친화적 UX 개선 |
| Phase J-EXT US-108 | 포트폴리오 탭 신설: equity-curve + metrics 별도 API | Overview 과부하 방지. EquityCurve.tsx + 자산배분 바 차트로 수익성 가시화. Sharpe/MDD/Calmar 3종 리스크 지표 통합 |
| Phase J-EXT US-110 | GlobalHeatmap 심볼 필터: Major 8/Top 20/All/Custom + 로컬 저장 | All 모드에서 엔진 전체 175심볼 렌더링. Custom 설정은 localStorage 저장으로 세션 유지 |
| Phase J-EXT W3-B1 | Telegram fail-closed auth: 빈 allowed_chat_ids → 전부 차단 | TelegramCommandHandler 요청 시 미설정 상황 fail-closed 원칙 적용. 인가된 chat_ids 없으면 명령어 거부 |
| Phase J-EXT W3-B1 | CorrelationMonitor → Guardian check() #9 통합 | 5개 모듈(DynamicSizer, SlippageFeedbackLoop, CorrelationMonitor, TelegramCommandHandler, AtomicOrderExecutor) main.py wiring 필수화. CorrelationMonitor 결과는 Guardian check #9로 로그만 기록, DynamicSizer가 실제 포지션 축소 담당 |

---

## 9. 알려진 이슈

### CRITICAL — Architecture GAPs (10건)

> 전체 플랜: `.claude/plans/jazzy-wishing-avalanche.md` Part 11 참조
> 의존성 순서: GAP9→10→(5,6,7 병렬)→3→(1,2)→4

| GAP | 설명 | 파일:라인 | 해결 Phase | 크기 |
|-----|------|----------|-----------|------|
| **1** | ~~Shadow가 Strategy 객체 우회 — StrategyManager.route_signal() 도입~~ | `shadow.py:878-909` | ~~B-4~~ **RESOLVED** | L |
| **2** | ~~SignalGenerator가 cross_exchange 신호만 생산 — RealDataSignalProducer 도입~~ | `core/real_signal.py` | ~~B-3~~ **RESOLVED** | M |
| **3** | MultiStrategySignalProducer Paper 모드에서만 동작 | `main.py:842-873` | B-3 | L |
| **4** | ~~AtomicExecutor 2-Leg만 지원 — execute_multi_leg() N-leg 도입~~ | `executor.py:298-380` | ~~B-5~~ **RESOLVED** | L |
| **5** | ~~Futures 데이터 파이프라인 부재~~ | `collectors/binance_futures_collector.py` | ~~B-2~~ **RESOLVED** | L |
| **6** | ~~Funding Rate Collector 부재~~ | `collectors/funding_rate_collector.py` | ~~B-2~~ **RESOLVED** | M |
| **7** | Triangular Scanner 부재 | `triangular.py:63-68` | B-3 | M |
| **8** | DEX Adapter 완전 Stub | `cex_dex.py:30-68` | F (미래) | XL |
| **9** | ~~Fee Model에 okx/bitget futures 누락 + ValueError~~ | `fee_model.py` | ~~B-1~~ **RESOLVED** | S |
| **10** | ~~CostCalculator Protocol 불일치 (estimate_cost 없음)~~ | `cost_calculator.py` | ~~B-1~~ **RESOLVED** | M |

### CRITICAL — Shadow Realism GAPs (6건, Phase SR)

> 상세: `.claude/plans/jazzy-wishing-avalanche.md` Part 23 참조
> PaperExecutor가 데모급 → 상용급 전환 필요

| GAP | 설명 | 현재 상태 | 해결 US |
|-----|------|----------|---------|
| **SG-1** | ~~partial_fill_rate=0.0, rejection_rate=0.0~~ | partial_fill=0.05, rejection=0.02 활성 | ~~US-058~~ **RESOLVED** |
| **SG-2** | ~~레그 간 0ms 동기 실행~~ | 50-300ms 랜덤 지연 활성 | ~~US-059~~ **RESOLVED** |
| **SG-3** | ~~PowerLawSlippage(k=1.0) — 오더북 깊이 미반영~~ | BookWalkSlippage VWAP 활성 | ~~US-060~~ **RESOLVED** |
| **SG-4** | ~~무한 가상 잔고, 소진 추적 없음~~ | VirtualBalanceTracker 활성 ($10M/exchange, 리밸런스 경고) | ~~US-061~~ **RESOLVED** |
| **SG-5** | ~~trade_size=Decimal("1") 하드코딩~~ | compute_depth_trade_size (L1×0.10, [0.001,10]) | ~~US-061~~ **RESOLVED** |
| **SG-6** | ~~Rate limit 시뮬레이션 없음~~ | ShadowRateLimiter 토큰 버킷 활성 (거래소별 order rate) | ~~US-062~~ **RESOLVED** |

### HIGH

| 이슈 | 설명 | 완화책 |
|------|------|--------|
| **Phase D 대시보드 브라우저 미검증** | US-037~041, US-053 코드 레벨만 검증(`npm run build`). Chrome 렌더링, API 연동, WebSocket 실시간 피드, 모바일 반응형 미확인 | Phase D 재검증 US 추가 (Chrome 브라우저 테스트 필수) |
| **Shadow 실행 시 Docker 미실행 위험** | Docker 없이 Shadow 실행 시 거래 로직은 유효하나 TimescaleDB/Redis 미저장 → 메트릭 유실 | Shadow 실행 전 `docker compose up -d` 필수 (leviathan.md에 명시) |
| **AtomicOrderExecutor: place_ioc_limit() 미구현** | Native Adapter에서 IOC limit 주문 지원 부재 → 모든 거래소에서 US-119 IOC fallback(limit→market) 동작만 수행 | Phase K/L에서 거래소별 실제 API 연동 시 추가 (현재는 코드만 검증) |
| ~~Bithumb 증분 Orderbook~~ | ~~스냅샷 없이 증분만 수신 → 허위 스프레드~~ | **RESOLVED (US-073)**: 누적 orderbook + REST re-sync + stale 5초 감지 |
| 마찰 vs Gross Spread | 대부분 알트 spread 2-25bps, friction ~20bps | MIN_EDGE_BPS=5 + 고스프레드 심볼 집중 |

### MEDIUM

| 이슈 | 설명 | 상태 |
|------|------|------|
| 전략 6개 비활성 | GAP 1-7로 cross_exchange만 Shadow 동작 | Phase B-1~B-5에서 해결 예정 |
| httpx 클라이언트 재생성 | 매 요청마다 httpx.AsyncClient 재생성 → 성능 | 미해결 |

### LOW

| 이슈 | 설명 | 상태 |
|------|------|------|
| Coinone Rate Limit | 30min PING keepalive 유지 실패 가능 | 자동 재연결 구현됨 |
| 빈 Orderbook 경고 | 타이밍 레이스 (collector 전 신호 평가) | crash 없음, 신호 무시 |
| cex_dex 미구현 | _build_dex_adapter() 항상 None | Phase F (GAP 8) |

### RESOLVED

| 이슈 | 해결 |
|------|------|
| GAP 5: Futures 데이터 파이프라인 | BinanceFuturesCollector 검증 + Shadow futures_books 분리 (Phase B-2) |
| GAP 6: Funding Rate Collector | 4 거래소 REST collector + Engine wiring (Phase B-2) |
| MIN_EDGE_BPS 최적화 | 5bps 확정 (Phase 7.3h) |
| _krw_rate=0 ZeroDivisionError | fallback 1380 가드 추가 (Phase 7.3f) |
| KRW/USDT 정적 환율 | dual-source 동적 조회 구현 (Phase 7.3d) |
| 이중 슬리피지 | PaperExecutor ZERO slippage 적용 (Phase 7.3j) |
| PowerLawSlippage k/gamma 무시 | 실제 공식 적용 완료 (Phase 3.5) |
| Stale Orderbook fat-tail loss | StaleOrderbookDetector 4계층 방어 + loss cap $50 (Phase G US-066) |
