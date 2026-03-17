# LEVIATHAN — Single Source of Truth (SSOT)

> **이 문서가 프로젝트의 유일한 설계 문서입니다. 다른 문서에 상태 정보를 기록하지 마세요.**
> 마지막 업데이트: 2026-03-18 (Phase S13 완료 — 기관급 전략 완전체) | 최신 커밋: aba84b6
> GAP 분석: `.claude/plans/modular-seeking-wreath.md` (6-관점 통합) | PRD: `.omc/prd.json` (228개 User Stories, 225 pass / 3 pending)
> **실행 순서**: A~M ✅ → **S1~S9** ✅ → TF QF 6차 ✅ → TF SF FAIL(2차) → Phase S13 ✅ → **Phase S14** (1 US) → TF QF → TF SF → TF Final → Live

---

## 1. 프로젝트 개요

**LEVIATHAN**은 글로벌 암호화폐 거래소 간 크로스 차익거래를 자동 실행하는 고빈도 거래 엔진이다.

| 항목 | 내용 |
|------|------|
| 엔진 | Python 3.12+ (AsyncIO) + Rust (PyO3 hot-path) |
| 대시보드 | Next.js 14 (App Router) + JWT 인증 + 실시간 WS 피드 |
| 거래소 | 10개 네이티브 WebSocket 어댑터 (7 spot + Binance/OKX/Bybit Futures, ccxt 미사용) |
| 전략 | 7개 (6개 기본 + CexDex 조건부, latency_arb는 US-194에서 cross_exchange로 병합) |
| 인프라 | Docker Compose 15 서비스, TimescaleDB + Redis + Prometheus + Grafana + Loki + Alertmanager + WAL백업 |
| 실행 모드 | Backtest → Paper → Shadow → Live |

**거래소 목록**: Binance, Binance Futures, Bybit, Bybit Futures, OKX, OKX Futures, Bitget, Upbit, Bithumb, Coinone (10개 네이티브 어댑터)

---

## 2. 현재 상태

> Machine-readable state: `.omc/state/leviathan-active-phase.json`
> TF verification status: `.omc/state/leviathan-tf-status.json`
> Current stage: `.omc/state/leviathan-current-stage.json`
> Team roster: `.omc/state/team-roster.json`

**Phase**: S14 (Auto-tuner Shadow 통합)
**Tests**: 4,783 passed / 0 failed / 12 skipped
**Coverage**: 86%
**TF Status**: QF 6차 PASS, SF 2차 FAIL → S13 회귀
**Next**: Phase S14 (US-234, 1개) → TF QF → TF SF → TF Final → Live

> 완료된 Phase S1-S12 상세: [`SSOT_COMPLETE.md`](SSOT_COMPLETE.md)

> Shadow 현실성 GAP 이력 (SG-1~SG-6 전부 RESOLVED): [`SSOT_COMPLETE.md`](SSOT_COMPLETE.md)

### 프로그레시브 Shadow 테스트 프로토콜

> 24H 단계적 검증으로 조기 문제 발견 (장기 안정성은 TF Final Canary 7일에서 실 자본 검증)

```
Stage 1: 1H  → 기본 동작 확인 (crash=0, 신호 흐름 정상)
Stage 2: 2H  → 승률/PnL 추세 안정성 (WR>60%, PnL 양수)
Stage 3: 6H  → 전략별 메트릭 분리 + 마찰력 정확도 검증
Stage 4: 12H → 메모리 누수/리소스 사용량 안정성
Stage 5: 24H → LiveGate 6-check + Sharpe>2.0, MDD<5%, 일일 PnL 양수 (최종)
```
각 Stage PASS 시 자동으로 다음 Stage 연장 (멈추지 않고 누적)

> Shadow 이력 아카이브 (Phase E-2, Phase 7.3h-i 등): [`SSOT_COMPLETE.md`](SSOT_COMPLETE.md)

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

### 워크플로우 자동화 레이어 (하이브리드 구조)
- **레이어 구분**: 기존 SSOT.md + leviathan.md + OMC는 그대로 유지, 순수 Python 보조 레이어 추가
- **체크포인팅**: `engine/src/workflow/checkpoint_engine.py` — Stage 전환 시 자동 스냅샷 (SQLite)
- **일관성 검사**: `engine/src/workflow/consistency.py` — SSOT↔PRD↔State 3-Way 자동 검증
- **CLI**: `python -m src.workflow.cli` — check_all, checkpoint save/restore/history
- **DB 분리**: TimescaleDB(Docker)=거래데이터, SQLite(로컬)=워크플로우 체크포인트

### 3.2 엔진 구조

```
Engine.run()
  ├── _init_config()           # Settings (env vars)
  ├── _init_infrastructure()   # EventBus (Redis/InMemory), DB, Telegram
  ├── _init_exchanges()        # Paper/Native 어댑터
  ├── _init_signal_pipeline()  # PriceHub → CostCalculator → SignalGenerator
  ├── _init_strategies()       # 7개 전략 등록 (latency_arb 병합됨, US-194)
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
| 7 | latency_arb | `strategies/latency_arb.py` | **병합됨(US-194)** | cross_exchange.latency_boost 모드로 통합, deprecated shim 유지 | (cross_exchange 포함) |
| 8 | cex_dex | `strategies/cex_dex.py` | 조건부 | GAP 8 (DEX stub, TF) | 10-50% |

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

**ETH 출금 비용 (네트워크별)**:

| 거래소 | ETH 비용 | 네트워크 | 비고 |
|--------|---------|---------|------|
| Binance | $0.06 | Arbitrum One | L2 최저경로 |
| Bybit | $0.19 | Arbitrum | L2 |
| OKX | $0.10 | Arbitrum | L2 |
| Bitget | $0.10 | Arbitrum | L2 |
| Upbit | $4.50 | Ethereum L1 | L2 미지원 |
| Bithumb | $2.50 | Ethereum L1 | L2 미지원 |
| Coinone | $2.50 | Ethereum L1 | L2 미지원 |

> **주의**: 글로벌 거래소 $0.06~$0.19 (Arbitrum L2) vs KRW 거래소 $2.50~$4.50 (L1 only). 상세: `engine/src/friction/fee_model.py` WITHDRAWAL_FEES_USD 참조.

### 4.3 리스크 모델

**KillSwitch (3-tier)**:
- Tier 1 (< 1ms): halt 플래그 설정 (threading.Event + Redis SET) → 즉시 신규 주문 차단
- Tier 2 (< 500ms): 전 거래소 미체결 주문 취소 (asyncio.gather, 2s timeout)
- Tier 3 (< 2000ms): 전 거래소 오픈 포지션 시장가 청산 (asyncio.gather, 3s timeout)

**CircuitBreaker**: CLOSED → OPEN → HALF_OPEN (고정 300s cooldown)

**RiskGuardian (11-check)**: #0 halt, #1 포지션한도, #2 드로다운, #3 익스포저, #4 서킷브레이커, #4e 넷익스포저(Amendment 7), #5 거래소건강도, #6 단일거래크기, #7 변동성, #8 롤백비용, #9 전략상관(log-only), #10 최대동시포지션(US-154)

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

### Docker Compose (15 서비스)

| 서비스 | 이미지 | 포트 | 역할 |
|--------|--------|------|------|
| engine | leviathan-engine:latest | 8000, 8001 | 거래 엔진 (REST + WS) |
| redis | redis:7.2-alpine | 6379 | 실시간 상태, Pub/Sub |
| redis-exporter | oliver006/redis_exporter:v1.58.0 | 9121 | Redis Prometheus 메트릭 |
| timescaledb | timescale/timescaledb:latest-pg16 | 5432 | 시계열 DB |
| dashboard | leviathan-dashboard:latest | 3000 | Next.js 대시보드 |
| prometheus | prom/prometheus:v2.50.1 | 9090 | 메트릭 수집 (30일 보관) |
| grafana | grafana/grafana:10.3.3 | 3001 | 메트릭 시각화 |
| alertmanager | prom/alertmanager:v0.26.0 | 9093 | 알림 라우팅 (Telegram/Slack) |
| nginx | nginx:alpine | 80, 443 | TLS 종단 + 역방향 프록시 |
| monitoring | leviathan-engine:latest | — | Telegram 인프라 모니터링 데몬 |
| auto-tuner | leviathan-engine:latest | — | Optuna 자동 튜닝 스케줄러 |
| db-backup | leviathan-engine:latest | — | TimescaleDB 자동 백업 |
| loki | grafana/loki | 3100 | 로그 집계 (크로스 컨테이너 검색) |
| promtail | grafana/promtail | — | 로그 수집 → Loki 전달 |
| wal-backup | leviathan-engine:latest | — | WAL 아카이빙 + PITR (RPO <1시간) |

### 모니터링 스택

- **Prometheus**: 엔진 메트릭 (`/metrics`), Redis exporter, 30일 보관
- **Grafana**: 대시보드 시각화 (admin/leviathan)
- **Telegram**: 일일 요약, 신호 알림, 긴급 알림
- **Nginx**: TLS 역방향 프록시, Rate Limiting, IP 화이트리스트, 보안 헤더

### DB 스키마 (TimescaleDB)

- `execution_log`: 거래 이력 (hypertable, 90일 retention)
- 시계열 저장: OHLCV, 스프레드 이력, 체결 데이터

---

## 7. 남은 작업 (`.omc/prd.json` 228개 User Stories, 225개 완료, 3개 미완)

> **실행 방식**: 3-Stage Sequential — Stage A(기획) → Stage B(구현+검증) → Stage C(리뷰+릴리스)
> **자동화**: `ralph autopilot` → prd.json Phase 단위 순회 → 각 Phase 자동 실행 (leviathan.md 참조)

> **Phase A~M 완료 상세 → [`SSOT_COMPLETE.md`](SSOT_COMPLETE.md)** (토큰 최적화로 아카이브 분리)

> Phase S1-S12 상세: [`SSOT_COMPLETE.md`](SSOT_COMPLETE.md)

#### Phase S13: 기관급 전략 완전체 — US-221~233, US-235~237 (← TF SF 2차 FAIL + 6명 전문가 리뷰)

> **목표**: 기관급 전략 완전체 구현. CRITICAL 버그 5개 수정 + 4계층 Stale 감지 + 전략별 CB + Auto-tuner 연동
> **회귀 사유**: TF SF 2차 Stage 2 FAIL — 2H45M PnL -$153.47, loss_capped 17건×-$50=-$850
> **전문가 리뷰**: Karina(아키텍트), Yeji(퀀트), Winter(비판), Wonyoung(테스트), Jisoo(보안), 디버거 + 외부 리서치 3팀
> **플랜**: `.claude/plans/snuggly-chasing-spark.md` (15 Part, 5라운드 검증)
> **진입 조건**: TF SF FAIL 확정

- [x] US-221: futures_futures 2차 freshness 검증 (← stale 17건 통과, threshold 3.0→1.5s + spread outlier)
- [x] US-222: per-strategy circuit breaker — 연속 손실 자동 쿨다운 (← -$50×4건 연속 8초)
- [x] US-223: spot_futures + funding_rate 비활성화 (← WR 42%, WR 6.7%)
- [x] US-224: loss_cap 전략별 차등 — futures $3, cross_exchange $7 (← $50×17건=-$850, $70 자본 기준 재설계)
- [x] US-225: futures spread outlier filter — >100bps WARNING, >200bps 블랙리스트 60s (← fake spread 진입)
- [x] US-226: CRITICAL 버그 5개 수정 — funding_rate=0.0 하드코딩, estimate_cost 슬리피지, AdaptiveThreshold 지연, RegimeDetector 미연결, Auto-tuner 검증
- [x] US-227: 4계층 Stale 감지 — 타임스탬프 분리 + 하트비트 EMA + 시퀀스 갭 + 스프레드 정상성
- [x] US-228: 전략별 서킷브레이커 상태머신 — ACTIVE/THROTTLED/HALTED/SUSPENDED + 복합 점수 + FIA 2024
- [x] US-229: spot_futures/funding_rate 시그널 레벨 사전 필터 강화 + cex_dex 명시적 비활성화
- [x] US-230: 스프레드 이상치 필터 — 적응형 롤링 중앙값 + 타임스탬프 교차검증 300ms
- [x] US-231: stat_arb z-score 하드스톱 3.5 + Kalman stale 가드 + 레짐 게이트 (학계 합의 Park 2026)
- [x] US-232: 전략 간 충돌 방지 — PositionRegistry 심볼 레벨 락 + 우선순위 계층
- [x] US-233: futures_futures 전용 강화 — min_spread 15bps + 호가 깊이 + 노셔널 캡
- [x] US-235: cross_exchange 미세 조정 — max_spread 100bps + min_book_depth 500
- [x] US-236: 엔진 Dead Wiring 전수 수정 — stat_arb Dead Code 연결, _position_manager 초기화, Redis 오타, PortfolioState 미연결
- [x] US-237: 대시보드 정합성 + 로그인 수정 — CORS/CSP, Alert API 경로, ParameterSlider, JWT 검증

#### Phase S14: Auto-tuner 완전 연동 — US-234

> **목표**: AdaptiveThreshold + RegimeDetector Shadow 통합 + Optuna 미니 튜너
> **진입 조건**: Phase S13 완료

- [ ] US-234: AdaptiveThreshold + RegimeDetector Shadow 통합 + Auto-tuner 미니 튜너

---

### TF Quarter-Final (QF): Development Verification — ✅ PASS 6차 (2026-03-17)

> **핵심 질문**: "코드가 올바르고, 빠진 것이 없는가?"
> **진입 가드**: 회귀 Phase 전부 완료 + pytest 0 fail + Docker healthy
> **FAIL 시**: 회귀 Phase 생성 → 3-Stage(A→B→C) → QF 재검증
> **PASS 기준**: CRITICAL 0, HIGH 0, MEDIUM ≤ 5 (자금 손실 경로 아님)
> **판정**: 6차 PASS (2026-03-17) — CRITICAL 0, HIGH 0, MEDIUM 6 + LOW 4
> **체크리스트**: `docs/checklists/tf-quarter-final_20260317.md`

**[단계 0] Smoke Test Gate**
- [x] 전체 pytest PASS (4,695 passed, 0 failed, 12 skipped)
- [x] Docker 전 컨테이너 healthy (15/15, promtail starting 비핵심)
- [x] 통합 Shadow 10min (crash=0, 4전략 신호 흐름, PnL=-$0.70, 2889 trades)

**[단계 1] 정합성 확인**
- [x] Karina: SSOT.md + prd.json + CLAUDE.md 3-way 정합성 확인
- [x] 누락 US/Phase 발견 시 새 Phase/US 생성

**[단계 2] 체크리스트 수립 (The Blueprint)**
- [x] Karina + 도메인 전문가 협의 → '완성 기준' 수립
- [x] 분야별 확인 체크리스트 문서 생성
- [x] Nayeon(TF 리더) 상용화 기준 부합 여부 최종 승인

**[단계 3] 교차 검증 (The Deep Dive)**
- [x] Jeongyeon(엔진): 초기화 체인, 전략 등록, 어댑터, RiskGuardian, KillSwitch, Shutdown, dead wiring
- [x] Momo(인프라): Docker, DB 스키마, Redis 인증, Nginx, .env 동기화, 포트, 리소스 제한, 백업
- [x] Dahyun(퀀트): 슬리피지 모델, 수수료 정합, 마찰력 공식, Sharpe, MDD 단위, 기본값 위험
- [x] Sana(데이터): Shadow 완전성, PnL 기록, WS 흐름, KRW 환율, 피드 연결 상태
- [x] Mina(UI/UX): 대시보드 4페이지 렌더링, 로그인, API 응답, 모바일 반응형, 콘솔 에러 0건
- [x] Jisoo(보안): JWT 인증, API 키 노출, CSP 헤더, IP whitelist, Redis commands, .gitignore
- [x] Karina 합동 점검: 실전적 질의응답

**[단계 3.5] 조립 검증 — 통합 검증 (Assembly Verification)** ✅ PASS (6차, 2026-03-17)
> "부품이 아니라, 조립된 완성품이 제대로 동작하는가?"
- [x] Sub-check 1: Init Chain non-None — 32개 서브시스템 전수 확인 PASS
- [x] Sub-check 2: Signal Flow E2E — 7전략 + 알림 경로 전수 확인 PASS
- [x] Sub-check 3: Config Flag Audit — 7개 플래그 전부 active PASS
- [x] Sub-check 4: Dead Wiring — PASS (MEDIUM 1 known: _position_manager + LOW 4)
> 5차에서 알림 서브시스템 Dead Wiring 누락 → 6차에서 수정 후 재검증:
> - metrics.py: KILL_SWITCH_ACTIVE + ROLLBACKS_TOTAL 추가
> - alerts.yml: engine_* → leviathan_* 전 규칙 통일 (10개 메트릭)
> - kill_switch.py: halt_local() → KILL_SWITCH_ACTIVE.set(1)
> - main.py: SmartTelegramAlerter start_flush_loop() 등록 + _kill_fn() send_kill_switch_event()

**[단계 4] 최종 확인 + 회귀 (The Feedback Loop)**
- [x] Karina → Nayeon 보고
- [x] Chaeyoung/Tzuyu QA 감사단 압박 면접
- [x] #1 FAIL → 회귀 Phase S1~S6 생성 → 3-Stage(A~C) 수정
- [x] #2 재검증 → 조건부 PASS (CRITICAL 0, HIGH 0)
- [x] #6 PASS (2026-03-17) → CRITICAL 0, HIGH 0, MEDIUM 6, LOW 4

#### 검증 이력

> **#1 FAIL (2026-03-13)**
> 판정: FAIL — CRITICAL 9, HIGH 12, MEDIUM 19, LOW 19 → 회귀 Phase S1~S6 생성
> 프로세스 상세: `docs/checklists/tf-semi-final-consolidated_20260313.md`
> 교차검증 보고서: `docs/checklists/tf-semi-final_20260313.md`
> TF 리더 판정문: `docs/checklists/tf-semi-final-verdict_20260313.md`

> **#2 조건부 PASS (2026-03-15)**
> 판정: **조건부 PASS** — 원본 59개 이슈 중 CRITICAL 9→0, HIGH 12→0 (91.5% 해소)
> 회귀 수정: S1~S6 (33/35 US PASS, 2개 Phase F 대기)
> 재검증 보고서: `docs/checklists/tf-semi-final-recheck_20260315.md`

> **#6 PASS (2026-03-17) ← CURRENT**
> 판정: **PASS** — CRITICAL 0, HIGH 0, MEDIUM 6, LOW 4 (자금 손실 경로 0건)
> 4695 tests, Shadow 10min crash=0, 조립 검증 4/4 PASS
> 체크리스트: `docs/checklists/tf-quarter-final_20260317.md`

### TF Semi-Final (SF): System Validation — ❌ Stage 2 FAIL (2차) → Phase S13 회귀

> **핵심 질문**: "24시간 동안 실제로 돈을 벌 수 있는가?"
> **진입 가드**: TF QF PASS
> **FAIL 시**: 회귀 Phase 생성 → 3-Stage(A→B→C) → SF 재검증 (QF 스킵, 구조적 결함 시 QF부터)
> **PASS 기준**: 24H+ 6-Stage ALL PASS + 전략별 WR>50% + E2E 10/10 + LiveGate 6-check
> **현재**: 2차 Stage 2 FAIL → Phase S13 회귀. S13 완료 후 TF QF → TF SF 재시작
> **체크리스트**: `docs/checklists/tf-semi-final_20260317.md`

#### 검증 이력

> **1차 [단계 1-A] ALL PASS (2026-03-15)**
> 판정: ALL PASS — CRITICAL 0, RISK 4, NOTE 3 → Phase S7 생성
> 보고서: `docs/checklists/tf-final-stage1_20260315.md`
>
> **1차 [단계 2] Stage 1 PASS → Stage 2 FAIL (2026-03-16)**
> Stage 1: 1H PnL +$18.18 PASS
> Stage 2: 2H PnL -$78.82 **FAIL** (stat_arb -$127, 전략 영역 겹침, Auto-tuner 미작동)
> **후속**: Phase S10 생성 → S10+S11+S12 완료 → TF QF 6차 PASS
>
> **2차 [단계 1] ALL PASS (2026-03-17)**
> 1-A Delta Check: PASS (QF 6차 이후 변경 0건 CRITICAL/HIGH)
> 1-B 전략별 독립검증: PASS (PnL +$59.80, 4전략, crash=0)
> 1-C 전략 상호작용: PASS (overlap=0, PnL 무결성 99.99%, 8555 trades)
>
> **2차 [단계 2] Stage 1 PASS → Stage 2 FAIL (2026-03-17) ← CURRENT**
> Stage 1: 1H crash=0, 4전략 활성, 10/10 거래소 → PASS
> Stage 2: 2H45M PnL **-$153.47**, WR 90.7%, loss_capped 17건(-$850) → **FAIL**
> 근본 원인: (1) futures_futures stale 진입 17건×-$50 (2) spot_futures WR 42% (3) funding_rate WR 6.7%
> **후속**: Phase S13 생성 (5 US: stale guard 강화, circuit breaker, 전략 비활성화, loss_cap 차등)

**[단계 1-A] 경량 재확인 (Delta Check)** ✅ ALL PASS (2026-03-15, 재검증 완료)
- [x] QF 이후 변경분 CRITICAL/HIGH 신규 0건 (S7 12 US + 3-Round 문서 변경분, HIGH 2건 발견→즉시 수정)
- [x] 전체 프로그램 응집도/결합도 점검 (8전략+10거래소+리스크+모니터링 — Karina/Jeongyeon 교차 확인)
- [x] 엔드투엔드 데이터 흐름 확인 (WS→PriceHub→Signal→Strategy→Executor→DB VERIFIED)

**[단계 1-B] 전략별 독립 검증 (Strategy Isolation)**
- [ ] 각 활성 전략 단독 10분 Shadow (P&L, WR, Sharpe, MDD 개별)
- [ ] 손실 전략 식별 → disabled_strategies 판단
- [ ] 전략 간 상관관계 분석

**[단계 1-C] 전략 상호작용 검증 (Strategy Interaction)** ← 신규
- [ ] 7개 전략 동시 10min Shadow → 합산 PnL vs 개별 PnL 합계 비교
- [ ] 합산 > 개별합 80% → PASS, < 50% → FAIL
- [ ] Strategy overlap 메트릭 = 0 확인

**[단계 2] Progressive Shadow (24H+) — 순차 OFF→ON 오토튜너 비교**
- [ ] Stage 1: 1H (튜너 OFF) → crash=0, PnL 기록
- [ ] Stage 2: 2H (튜너 OFF) → WR>60%, PnL>0, 전략별 리포트
- [ ] Stage 3: 2H (튜너 ON) → Stage 2 대비 비교 (PROVEN/NEUTRAL/HARMFUL/BUG 판정)
- [ ] Stage 4: 6H (최적 설정) → 각 전략 WR>50%, 마찰력 오차<20%
- [ ] Stage 5: 12H → 메모리<100MB증가, CPU<80%, WS 재연결
- [ ] Stage 6: 24H → LiveGate 6-check + Sharpe>2.0, MDD<5%, 일일 PnL 양수

**[단계 3-A] E2E 사용자 시나리오 (UAT)** ← 단계 2 Stage 2+ 통과 후 병렬 수행
- [ ] 로그인 → JWT 쿠키 → 리다이렉트
- [ ] Overview/Strategies/Portfolio/Settings 4페이지
- [ ] 모바일 반응형 (375px, 768px)
- [ ] WebSocket 실시간 갱신
- [ ] Kill Switch → Telegram < 5초
- [ ] API 전 엔드포인트 200 + 콘솔 에러 0건

**[단계 3-B] Master Inspection** ← 단계 2 Stage 1+ 통과 후 병렬 수행
- [ ] 코드 품질 (TODO/FIXME, dead code, 하드코딩)
- [ ] 로그 품질 + 설정 일관성

**[단계 3-C] 알림 체계 종합 검증** ← 단계 2 Stage 2+ 통과 후 병렬 수행
- [ ] Telegram 거래/워크플로우 알림 수신
- [ ] Kill Switch → 알림 → 거래 중단 < 5초
- [ ] Alertmanager 규칙 → 라우팅 → 수신

### TF Final (F): Operations Readiness — 미시작

> **핵심 질문**: "문제가 생기면 대응할 수 있는가? 실제 돈을 안전하게 운용할 준비가 되었는가?"
> **진입 가드**: TF SF 전 단계 PASS + 24H Shadow ALL PASS
> **PASS 기준**: ORR 완비 + DR 9/9 PASS (DR-1~6 인프라 + DR-7~9 전략) + Canary 7일 P&L>0 + 튜너 효과 판정 완료

**[단계 1] Operations Readiness Review (ORR)**
- [ ] 일일 점검 체크리스트 작성
- [ ] 장애 대응 절차 (IRP: P1/P2/P3)
- [ ] 에스컬레이션 경로 + 당직 체계

**[단계 2] Disaster Recovery (DR) 훈련**
- [ ] DR-1: 엔진 crash → 재시작 → 포지션 복구
- [ ] DR-2: DB 장애 → WAL/PITR 복구
- [ ] DR-3: 거래소 API 장애 → CircuitBreaker
- [ ] DR-4: Redis 장애 → 상태 복구
- [ ] DR-5: 네트워크 단절 → WS 재연결
- [ ] DR-6: 코드 롤백 → 안전 상태 복원
- [ ] DR-7: 전략 카니발리제이션 → 자동 감지 + 비활성화
- [ ] DR-8: 폭주 전략 → per-strategy circuit breaker 발동
- [ ] DR-9: 오토튜너 잘못된 파라미터 → 즉시 롤백

**[단계 3] Sandbox 실거래 테스트**
- [ ] Binance Testnet 주문 흐름 (생성→체결→취소)
- [ ] 비 testnet 거래소 API 조회 검증

**[단계 4] 자본/리스크 한도 확정**
- [ ] trading.json 운영 파라미터 확정 (사장님 승인)
- [ ] DynamicSizer 파라미터 검증

**[단계 5] Canary Deployment (1% 자본, 7일) — 오토튜너 최종 검증 포함**
- [ ] Alpha $70/exchange × 10 = $700, 7일 실거래
- [ ] 튜너 OFF 3일 → 튜너 ON 4일 → 순차 비교
- [ ] 일일 3-way 리콘실리에이션
- [ ] 슬리피지/수수료 실측 vs 예측 비교

**[단계 6] Live Kick-Off**
- [ ] Nayeon(TF 리더) 최종 서명
- [ ] Jisoo 보안 최종 점검
- [ ] 사장님 승인
- [ ] Alpha → Beta 확대 또는 Full Live

---

## 8. 결정 로그 → [`SSOT_COMPLETE.md`](SSOT_COMPLETE.md) (27개 결정, 전부 코드 반영 완료)

---

## 9. 알려진 이슈

> RESOLVED 이슈는 [`SSOT_COMPLETE.md`](SSOT_COMPLETE.md) §9로 이동 (취소선 처리)

> **RESOLVED 항목**: S8/S9에서 해소된 GAP 3/7/8 + HIGH 9건 + LOW 2건 → US-193 완료 시 SSOT_COMPLETE.md §9로 이관 예정.

### CRITICAL (3건 — Phase S13에서 해소)

| 이슈 | 설명 | 해결 US |
|------|------|---------|
| **전략 영역 겹침** | `_CROSS_EXCHANGE_CONSUMERS`가 stat_arb+latency_arb에 cross_exchange 신호 라우팅 → 중복 거래 | US-187, US-194 → **US-232** (PositionRegistry 심볼 레벨 락) |
| **stat_arb 구조적 결함** | 교차거래소 동일심볼 mean-reversion = cross_exchange 동일 영역, WFE=-1.03 | US-188 → **US-231** (z-score 하드스톱 3.5 + 레짐 게이트) |
| **AdaptiveThreshold WR 기반 피드백 루프** | WR 93.8%인데 손실 → WR>90%에서 edge 하향 = 손실 악화 | US-201 → **US-226** (첫 실행 지연 수정) + **US-234** (Shadow 통합) |

### HIGH (3건 — Phase S13에서 해소)

| 이슈 | 설명 | 해결 US |
|------|------|---------|
| **Auto-tuner 미작동** | ScheduledTuner 로그 미관찰, AdaptiveThreshold/RegimeDetector/ONNX 호출 미확인 | US-190, US-191 → **US-226** (로깅 강화) + **US-234** (Shadow 미니 튜너) |
| **전략 간 포지션 충돌** | 2개 전략이 동일 symbol 동시 거래 가능, 방지 메커니즘 없음 | US-195 → **US-232** (PositionRegistry) |
| **전략별 자본 할당 없음** | 7개 전략이 독립적으로 자본 사용, per-strategy 한도 없음 | US-196 → **US-224** (loss_cap 차등) + **US-228** (전략별 CB) |

### MEDIUM (1건)

| 이슈 | 설명 | 해결 US |
|------|------|---------|
| **cross_exchange MIN_EDGE 과소** | 5bps → 실제 round-trip 비용 32-65bps, 슬리피지 1건이 17건 이익 소멸 | US-189 |

### LOW (1건)

| 이슈 | 설명 | 해결 시점 |
|------|------|----------|
| **Phase D 대시보드 브라우저 미검증** | Chrome 렌더링, 모바일 반응형 미확인 | TF SF [단계 3-A] |

### RESOLVED (US-193 완료 — SSOT_COMPLETE.md §9로 이관됨)

> 14건 RESOLVED → [`SSOT_COMPLETE.md`](SSOT_COMPLETE.md) 참조
