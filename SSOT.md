# LEVIATHAN — Single Source of Truth (SSOT)

> **이 문서가 프로젝트의 유일한 설계 문서입니다. 다른 문서에 상태 정보를 기록하지 마세요.**
> 마지막 업데이트: 2026-03-16 (Phase S10 계획 수립 — 전략 영역 분리 + stat_arb 재설계 + Auto-Tuner) | 최신 커밋: 63f31fc
> GAP 분석: `.claude/plans/modular-seeking-wreath.md` (6-관점 통합) | PRD: `.omc/prd.json` (220개 User Stories, 175 pass / 45 pending)
> **실행 순서**: A~M ✅ → 회귀 **S1~S9** ✅ → TF QF ✅ → TF SF FAIL → **Phase S10** 🔧 (16 US) → TF QF 재실행(단계 3.5) → Phase S11(UI/UX) → TF SF(순차 OFF→ON) → Phase S12 → TF Final → Live

---

## 1. 프로젝트 개요

**LEVIATHAN**은 글로벌 암호화폐 거래소 간 크로스 차익거래를 자동 실행하는 고빈도 거래 엔진이다.

| 항목 | 내용 |
|------|------|
| 엔진 | Python 3.12+ (AsyncIO) + Rust (PyO3 hot-path) |
| 대시보드 | Next.js 14 (App Router) + JWT 인증 + 실시간 WS 피드 |
| 거래소 | 10개 네이티브 WebSocket 어댑터 (7 spot + Binance/OKX/Bybit Futures, ccxt 미사용) |
| 전략 | 8개 (7개 기본 + CexDex 조건부) |
| 인프라 | Docker Compose 15 서비스, TimescaleDB + Redis + Prometheus + Grafana + Loki + Alertmanager + WAL백업 |
| 실행 모드 | Backtest → Paper → Shadow → Live |

**거래소 목록**: Binance, Binance Futures, Bybit, Bybit Futures, OKX, OKX Futures, Bitget, Upbit, Bithumb, Coinone (10개 네이티브 어댑터)

---

## 2. 현재 상태

```
Phase:        Phase S10 Strategy Architecture Hardening 🔧 (2026-03-16)
테스트:       4,588 passed, 0 failed, 12 skipped
커버리지:     86%
컴플라이언스: 100% (23/23 PASS)
현재 모드:    DATA_MODE=shadow, EXECUTION_MODE=paper
최신 커밋:    63f31fc
다음 작업:    Phase S10 (US-187~202, 16 US) → TF QF 재실행 → Phase S11 (UI/UX) → TF SF → Phase S12 → TF Final → Live
완료된 US:    175/220 (Phase S10 0/16 진행 중, S11 0/10 + S12 0/8 pending, prd.json 175 pass / 45 pending)
TF QF:        ✅ PASS (2026-03-16) — CRITICAL 0, HIGH 0 (단계 3.5 조립 검증 추가 예정)
              1차(2026-03-13): FAIL → 회귀 S1~S7
              2차(2026-03-15): 조건부 PASS → S8 후 재실행
              3차(2026-03-16): FAIL — 8/8 전략 중 4개 비활성화 → Phase S9 회귀
              4차(2026-03-16): **PASS** — S9에서 4개 전략 evaluator 구현 완료
TF SF:        ❌ Stage 2 FAIL (2H Shadow PnL -$78.82) → Phase S10 회귀
              Stage 1: 1H PnL +$18.18 PASS
              Stage 2: 2H PnL -$78.82 FAIL (stat_arb -$127, 전략 영역 겹침, Auto-tuner 미작동)
              S10+S11 완료 후 TF QF 재실행 → TF SF Stage 1부터 재시작
Phase S7:     ALL PASS (2026-03-15) — US-157~168 12개 US 전부 완료
Phase S8:     완료 (2026-03-15) — US-169~180 12개 US, CRITICAL 2 + HIGH 3 수정
Phase S9:     완료 (2026-03-16) — US-181~186 6개 US, 8개 전략 전체 활성화
Phase S10:    🔧 진행 중 — US-187~202 16개 US, 전략 아키텍처 하드닝 (latency_arb 병합, stat_arb cross-asset, AdaptiveThreshold PnL전환)
              회귀 사유: TF SF Stage 2 FAIL (stat_arb -$127, 전략 신호 겹침, Auto-tuner 미작동)
              근본 원인 4가지: ① 전략 영역 겹침 (_CROSS_EXCHANGE_CONSUMERS) ② stat_arb = cross_exchange 동일 영역 ③ Auto-tuner/ML 미작동 ④ AdaptiveThreshold WR→PnL 전환 필요
인프라:       Loki+Promtail 로그집계, WAL 아카이빙+PITR, Alertmanager, Docker 15 services ✅ 7/8 HEALTHY
Collectors:   10/10 (Binance, BinanceFutures, Bybit, BybitFutures, OKX, OKXFutures, Bitget, Upbit, Bithumb, Coinone)
```

### Phase S1 완료 현황 (2026-03-14)

| US | 제목 | 상태 | 결과 |
|----|------|------|------|
| US-152 | API 키 로테이션 + .gitignore 강화 + pre-commit hook | ✅ PASS | .env REDIS_PASSWORD 인라인 주석 버그 수정 |
| US-123 | 전 엔드포인트 JWT 인증 강제 | ✅ PASS | JWT 미들웨어 적용, 13 새로운 테스트 추가 |
| US-124 | JWT 시크릿 강화 + prod fail-fast | ✅ PASS | bcrypt 비밀번호 해싱 + DASHBOARD_PASSWORD 필수 |
| US-125 | Nginx IP whitelist + X-Forwarded-For 신뢰 | ✅ PASS | Nginx 설정: set_real_ip_from, trusted_proxies |
| US-126 | Redis 인증 + dangerous commands 비활성화 | ✅ PASS | Redis --requirepass CLI, COMMAND DISABLE |
| US-127 | CSP 헤더 강화 (Nginx + Next.js) | ✅ PASS | CSP: default-src 'self', script-src 'self' (no unsafe-eval) |
| US-128 | pytest backoff 테스트 수정 | ✅ PASS | Coinone 지터 백오프 테스트 jitter 구간 조정 |

**Shadow 검증 결과** (Stage D):
- **10분 Shadow**: uptime=613.1s, PnL=+$38.21, WR=93.75% (30/32), crash=0
- **QA 보안**: 8/8 PASS (JWT auth, Redis AUTH, CONFIG disabled, health/metrics public)
- **Docker**: .env REDIS_PASSWORD 인라인 주석 버그 수정, TimescaleDB WAL 파라미터 개선

**추가 수정사항**:
- `.env REDIS_PASSWORD` 인라인 주석 (# 포함) → `leviathan-redis-secret` 치환
- `docker-compose.yml` TimescaleDB `include_dir` 제거 → 개별 `-c` WAL 파라미터 적용

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

> 24H 단계적 검증으로 조기 문제 발견 (장기 안정성은 TF Final Canary 7일에서 실 자본 검증)

```
Stage 1: 1H  → 기본 동작 확인 (crash=0, 신호 흐름 정상)
Stage 2: 2H  → 승률/PnL 추세 안정성 (WR>60%, PnL 양수)
Stage 3: 6H  → 전략별 메트릭 분리 + 마찰력 정확도 검증
Stage 4: 12H → 메모리 누수/리소스 사용량 안정성
Stage 5: 24H → LiveGate 6-check + Sharpe>2.0, MDD<5%, 일일 PnL 양수 (최종)
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

## 7. 남은 작업 (`.omc/prd.json` 220개 User Stories, 175개 완료, 45개 미완)

> **실행 방식**: 3-Stage Sequential — Stage A(기획) → Stage B(구현+검증) → Stage C(리뷰+릴리스)
> **자동화**: `ralph autopilot` → prd.json Phase 단위 순회 → 각 Phase 자동 실행 (leviathan.md 참조)

> **Phase A~M 완료 상세 → [`SSOT_COMPLETE.md`](SSOT_COMPLETE.md)** (토큰 최적화로 아카이브 분리)

### 회귀 Phase S1~S6 (TF QF 발견 → 원본 Phase 보완)

> **S1~S6은 새로운 Phase가 아닌, TF Quarter-Final(QF) 검증에서 발견된 원본 Phase(A~M)의 미비점 회귀 수정.**
> TF는 검사 프로세스이며, 실패 시 원본 Phase로 회귀하여 보완한다. S1~S6 완료 후 TF 재검증.
> 상세 매핑 (원본 Phase↔회귀 US 역추적): `docs/checklists/tf-semi-final-consolidated_20260313.md`
> 의존성: S1 → (S2 ∥ S3) → S4 → S5 → S6

#### Phase S1: Security Hardening — US-123~128, US-152 ✅ ALL PASS (← J-EXT W1, E-1, D, I 보완)

- [x] US-152: **API 키 로테이션** + .gitignore 강화 + pre-commit hook (← J-EXT US-105,106 누락) ✅
- [x] US-123: 전 엔드포인트 JWT 인증 강제 (← J-EXT US-105,106 인증 범위 미완) ✅
- [x] US-124: JWT 시크릿 강화 + prod fail-fast (← J-EXT US-105 bcrypt fallback) ✅
- [x] US-125: Nginx IP whitelist + X-Forwarded-For (← Phase I US-075 인프라 보안) ✅
- [x] US-126: Redis 인증 + dangerous commands (← Phase E-1 US-042~044 모니터링 보안) ✅
- [x] US-127: CSP 헤더 강화 (← Phase D US-037~041 대시보드 보안) ✅
- [x] US-128: pytest backoff jitter 테스트 수정 (← Phase I US-074 Coinone 백오프) ✅

#### Phase S2: Engine Wiring Completion — US-129~134, US-153~155 ✅ ALL PASS (← E-3, J-EXT W3, K, M, B-5 보완)

- [x] US-129: RiskGuardian PortfolioState 실제 값 주입 (← Phase E-3 US-049 무력화) ✅
- [x] US-130: DynamicSizer 실행 경로 연결 (← J-EXT W3 US-114 미연결) ✅
- [x] US-131: RegimeDetector + ONNX Scorer main.py 주입 (← Phase K US-084 + Phase M US-094 미연결) ✅
- [x] US-132: SlippageFeedbackLoop LegResult 필드 수정 (← J-EXT W3 US-115 필드 불일치) ✅
- [x] US-133: AtomicOrderExecutor(IOC) main.py 연결 (← J-EXT W3 US-119 미연결) ✅
- [x] US-134: TCA/Correlation ExecutionResult 필드 통일 (← J-EXT W3 US-116,118 필드 불일치) ✅
- [x] US-153: **주문 중복 방지** Idempotency Key (← Phase B-5 US-027~029 누락) ✅
- [x] US-154: RiskGuardian max_concurrent_positions (← Phase E-3 US-049 체크 누락) ✅
- [x] US-155: Graceful shutdown 오픈 포지션 정리 (← Phase E-3 US-049~050 누락) ✅

**Shadow 검증 결과** (Stage D):
- **10분 Shadow**: uptime=912.5s, PnL=+$419.40, WR=85% (324/381), crash=0
- **코드리뷰**: 0 CRITICAL, 0 HIGH, 9 fixes applied
- **L0 수정**: _peak_equity None guard (main.py), blacklist re-registration loop fix (stale_detector.py)

#### Phase S3: Infrastructure Hardening — US-135~139 ✅ ALL PASS (← A, E-1, E-2, SR 보완)

- [x] US-135: DB 스키마 통합 + 자동 마이그레이션 (docker/init.sql 통합, migration_runner.py with advisory lock + transaction) ✅ PASS
- [x] US-136: .env MIN_EDGE_BPS 동기화 + PowerLaw k (_check_env_sync in preflight.py, main.py에서 호출) ✅ PASS
- [x] US-137: Nginx WS 포트 + 백업 자동재시작 (docker-compose.yml backup services, restart:"no") ✅ PASS
- [x] US-138: Alertmanager 연결 + Grafana datasource (sed-based env var substitution, Telegram webhook) ✅ PASS
- [x] US-139: Docker 리소스 제한 + healthcheck (datasources.yml 프로비저닝, mem_limit 설정) ✅ PASS

**Shadow 검증 결과** (Stage D):
- **12분 Shadow**: uptime=720s, PnL=+$0.0069, WR=100% (4/4), crash=0
- **DB Migration**: advisory lock + transaction 적용, auto_ddl 검증 완료
- **인프라**: Docker 15 services ALL HEALTHY, Alertmanager→Telegram webhook 연결 완료

#### Phase S4: Dashboard Completion — US-140~144 (← D, H, J-EXT W2 보완)

- [x] US-140: API prefix 통일 + SWR key — `/api/v1/` 통일, kill-switch/strategies/status 경로 수정
- [x] US-141: System 페이지 실데이터 연동 — system.py NEW (Docker containers + psutil resources), asyncio.to_thread
- [x] US-142: Heatmap/OrderbookView 175 심볼 연동 — symbols/spreads 엔드포인트, SpreadItem[] 변환
- [x] US-143: Strategy/Portfolio/EquityCurve mock 제거 — 전 컴포넌트 실데이터 연결, MOCK→OFFLINE
- [x] US-144: 대시보드 테스트 SWR v2 + 모바일 — isValidating 수정, TradeDetail 반응형 (w-full sm:w-80)

**Stage C 코드리뷰**: CRITICAL 3 + HIGH 5 → fix loop 12건 적용 → 전부 해결. 보안 CRITICAL 0.
**Shadow 검증 결과** (Stage D):
- **18.5h Shadow**: 289 trades, WR=90.7%, PnL=-$7.86 (stat_arb_v1 -$7.61 원인), crash=0
- **API QA**: 13/13 PASS — containers/resources/symbols/spreads 엔드포인트 + 인증 + 엣지케이스
- **stat_arb 손실 문제**: US-156 신규 생성 (SSOT §9 HIGH 등록). SHADOW_DISABLED_STRATEGIES .env 미설정이 원인.
- **pytest**: 4,360 passed, 0 failed, 6 skipped | tsc: 0 errors

#### Phase S5: Data Pipeline & Auto-Tuner — US-145~148, US-156 ✅ ALL PASS (← E-2, E-3, SR 보완 + S4 Shadow 발견)

- [x] US-145: Auto-Tuner TimescaleDB async loader ✅ PASS
- [x] US-146: ScheduledTuner main.py 연결 ✅ PASS
- [x] US-147: Attribution TimescaleDB + materialized views ✅ PASS
- [x] US-148: Shadow MDD 비율 + Rebalancer balance feed ✅ PASS
- [x] US-156: Shadow 손실 전략 비활성화 — SHADOW_DISABLED_STRATEGIES .env 설정 ✅ PASS

**Shadow 검증 결과** (Stage D):
- **12분 Shadow**: uptime=721s, PnL=+0.2671 USDT, WR=95.3% (148/155), crash=0
- **Auto-Tuner**: TimescaleDB async loader 동작, ScheduledTuner 매주 실행 확인
- **Attribution**: 과거 거래 이력 TimescaleDB 조회 가능, materialized views 생성 완료
- **손실 전략 비활성화**: SHADOW_DISABLED_STRATEGIES 설정으로 stat_arb/spot_futures/latency_arb 비활성, Shadow PnL 양수 전환
- **pytest**: 4,474 passed, 0 failed, 6 skipped | tsc: 0 errors

#### Phase S6: Documentation Sync — US-149~151 ✅ ALL PASS (← A 보완, 최후 실행)

- [x] US-149: prd.json 파일 경로 검증 — 0 mismatches 확인, total_stories=147 정합 ✅
- [x] US-150: CLAUDE.md 현행화 — Tests 4,460, PRD 145/2, 다음작업 TF재검증 ✅
- [x] US-151: SSOT.md 수식/체크 코드 동기화 — §4.3 이미 정합 확인, §4.2 ETH L2 비용 테이블 추가 ✅

#### Phase S7: Pre-Live Hardening — US-157~168 ALL PASS (2026-03-15)

- [x] US-157: Config 아키텍처 분리 — engine/config/trading.json 생성, config.py 로더 ✅
- [x] US-158: okx_futures + bybit_futures 활성 거래소 추가 — trading.json active_exchanges ✅
- [x] US-159: _reconcile_loop 실제 구현 — shadow.py 60s 주기 잔고 비교 ✅
- [x] US-160: InMemoryEventBus 큐 크기 제한 — maxsize=10000, drop oldest ✅
- [x] US-161: KRW stale rate 거래 중단 로직 — _krw_stale 플래그 필터링 ✅
- [x] US-162: Auto-discovery 거래량 필터 — min_volume_usd in SignalConfig ✅
- [x] US-163: Dashboard 로그인 수정 — login page + next.config.js rewrites ✅
- [x] US-164: Shadow PnL 단일 손실 방어 — strategy temp disable (기본 0s, prod 설정 가능) ✅
- [x] US-165: Redis 연결 명시적 close — Engine.stop()에서 disconnect() ✅
- [x] US-166: 모니터링 가이드 문서 작성 — docs/operations/monitoring-guide.md ✅
- [x] US-167: Docker 리소스 제한 — Redis cpus:0.5, TimescaleDB cpus:1.0 ✅
- [x] US-168: httpx AsyncClient 재사용 — telegram.py, telegram_bot.py, bithumb_collector.py ✅

**Shadow 검증 결과** (Stage D):
- **10분 Shadow**: 2,230 trades, WR=93.3%, PnL=+$0.464, MaxDD=$0.222, crash=0
- **코드리뷰**: CRITICAL 1 + HIGH 2 발견 → 즉시 수정 (get_all_balances→summary, telegram close, min_volume_usd env)
- **테스트**: 4,471 passed, 0 failed, 6 skipped

#### Phase S8: System Integration Hardening — US-169~180 ✅ ALL PASS (2026-03-15)

> **완료**: 구현되었으나 엔진 미연결 기능 12개 main.py 초기화 체인 연결 완료.
> CRITICAL 2건 수정 (OKX IOC ordType, KRW KillSwitch→soft-block) + HIGH 3건 수정
> Shadow 35min: PnL +$1.85, WR 92.2%, crash=0 | 4,587 tests passed

**CRITICAL (3건)**
- [x] US-169 (S8-1): MultiStrategySignalProducer LIVE 모드 연결 — Paper/Shadow만 동작, LIVE에서 5/8 전략 신호 0건
- [x] US-170 (S8-2): Triangular Scanner 구현 — 전략 코드만 존재, 신호 생성 Scanner 부재 (GAP 7 해소)
- [x] US-171 (S8-3): KRW Staleness → soft-block 활성화 — 120초 stale 시 경고만 → 거래 신호 필터링

**HIGH (5건)**
- [x] US-172 (S8-4): ONNX ML Scorer 신호 필터링 연결 — 로드만 하고 signal.py에서 호출 안 함
- [x] US-173 (S8-5): HMM RegimeDetector 신호 파이프라인 연결 — 초기화만, predict() 미호출 → 레짐 항상 NORMAL
- [x] US-174 (S8-6): AdaptiveThreshold 엔진 연결 — 94줄 구현 완료이나 main.py 미인스턴스화
- [x] US-175 (S8-7): ExposureTracker 인스턴스화 + RiskGuardian 연결 — 코드 존재하나 미생성
- [x] US-176 (S8-8): CorrelationMonitor → DynamicSizer 포지션 축소 연결 — Check #9 로그만

**MEDIUM (4건)**
- [x] US-177 (S8-9): DEX 실연결 — _build_dex_adapter() 항상 None (GAP 8 해소)
- [x] US-178 (S8-10): IOC Limit Order 주요 거래소 구현 — Binance/Bybit/OKX native adapter
- [x] US-179 (S8-11): ScheduledTuner 핫리로드 + 기본 활성화 — 파라미터 재시작 없이 반영
- [x] US-180 (S8-12): InMemoryEventBus 큐 크기 제한 — maxsize 강제 + drop oldest

**추가 설정/환경 갭 수정 (S8 내 포함)**
- [x] TRADING_ACTIVE_EXCHANGES .env 동기화 (okx_futures, bybit_futures 추가)
- [x] strategy_activation.json Paper/Live에서도 적용
- [x] _reconcile_loop 거래소 API 잔고 대조 구현

#### Phase S9: Strategy Activation — US-181~186 ✅ ALL PASS (2026-03-16)

> **완료**: TF QF 3차 FAIL(8/8 전략 중 4개 비활성화) 회귀. 4개 전략 evaluator 구현 + 전체 활성화.
> Shadow 10min: 7/7 전략 등록, 6/7 시그널 생산, 10/10 거래소, crash=0 | 4,588 tests passed

**CRITICAL (4건 — TF QF FAIL 사유)**
- [x] US-181 (S9-1): RealDataSignalProducer statistical_arb evaluator 구현 — rolling z-score(z=8.0, 200samples, 300s cooldown), Korean exchange 제외
- [x] US-182 (S9-2): RealDataSignalProducer latency_arb evaluator 구현 — LatencyTracker.lead_lag_pairs() + StaleDetector 교차검증
- [x] US-183 (S9-3): spot_futures/stat_arb/latency_arb disabled_strategies 해제 — trading.json `disabled_strategies: []`, Korean guard 유지
- [x] US-184 (S9-4): futures_futures stale spread 방어 — StaleDetector + 500bps 이상치 필터

**HIGH (1건)**
- [x] US-185 (S9-5): StrategyValidation insufficient_data→unverified 분류 — ScheduledTuner cascade-disable 방지

**검증 (1건)**
- [x] US-186 (S9-6): 8개 전략 전체 Shadow 통합 검증 — 7/7 등록, 10/10 거래소, crash=0

**TF QF 중 추가 수정**
- [x] TRADING_ACTIVE_EXCHANGES 8→10개 (bybit_futures, okx_futures 추가)
- [x] InventoryRebalancer balance_feed NOT_CONNECTED → CONNECTED (connect_exchange_feeds 호출)
- [x] /health 엔드포인트 내부 상태(engine_running, kill_switch_active) 노출 제거 (보안)
- [x] SSOT.md/CLAUDE.md 테스트 수 4,587→4,589 동기화
- [x] spot_futures evaluator Korean exchange guard 추가 (upbit, bithumb, coinone 제외)

#### Phase S10: Strategy Architecture Hardening — US-187~202 🔧 진행 중 (2026-03-16)

> **회귀 사유**: TF SF Stage 2 (2H Shadow) PnL -$78.82 FAIL
> **근본 원인**: ① 전략 영역 겹침 (_CROSS_EXCHANGE_CONSUMERS) ② stat_arb = cross_exchange 동일 영역 ③ Auto-tuner/ML 미작동 ④ AdaptiveThreshold WR→PnL 전환 필요
> **회귀 후 경로**: S10 완료 → TF QF 재실행(단계 3.5 조립 검증 추가) → Phase S11(UI/UX) → TF SF 재시작

**CRITICAL (4건)**
- [ ] US-187 (S10-1): `_CROSS_EXCHANGE_CONSUMERS` 제거 + 신호 흐름 검증 — manager.py frozenset 제거, stat_arb/latency_arb RealDataSignalProducer 신호 수신 확인
- [ ] US-188 (S10-2): stat_arb cross-asset pair 재설계 (2-3일) — BTC-ETH/ETH-SOL/BTC-BNB 고정 3쌍, Signal.metadata["symbol2"], _is_cointegrated fail-closed 수정
- [ ] US-194 (S10-3): latency_arb → cross_exchange 병합 — LatencyArbStrategy 삭제, latency_boost 모드 통합, 전략 8→7개
- [ ] US-201 (S10-4): AdaptiveThreshold WR→복합 지표(Expected Edge bps + Profit Factor) 기반 전환 — expected_edge_bps + PF 기반 조정, WR은 보조 지표

**HIGH (5건)**
- [ ] US-189 (S10-5): cross_exchange min_spread_bps 5→10 복원 — latency_boost 모드일 때 5bps 허용
- [ ] US-195 (S10-6): 전략 간 포지션 충돌 방지 — (symbol, exchange_pair) 10초 윈도우 중복 체크, asyncio.Lock
- [ ] US-196 (S10-7): 전략별 자본 할당 — trading.json capital_allocation_pct, RiskGuardian check #11
- [ ] US-197 (S10-8): stat_arb ScheduledTuner EXCLUDED 제거 — US-188 완료 후 적용
- [ ] US-199 (S10-9): 전략 overlap 감지 메트릭 — Prometheus counter, 10초 윈도우 감지

**MEDIUM (4건)**
- [ ] US-190 (S10-10): ScheduledTuner 작동 확인 — optuna/apscheduler import, 수동 트리거 --run-once
- [ ] US-198 (S10-11): Korean exchange 필터 보강 — latency_boost + stat_arb cross-asset Korean 제외
- [ ] US-191 (S10-12): ML/Tuning 컴포넌트 작동 로그 — AdaptiveThreshold PnL 로그, RegimeDetector 레짐 로그, ONNX 카운터
- [ ] US-192 (S10-13): ExposureTracker Redis 연결 확인

**LOW (1건)**
- [ ] US-200 (S10-14): 오토튜너 백테스트 리플레이 A/B 인프라 — event-level 데이터 저장, deterministic replay

**검증 (1건)**
- [ ] US-202 (S10-15): 7개 전략 전체 Shadow 2H 재검증 — 총합 PnL>$0, 개별 PnL>=-$5, overlap=0, crash=0

**SSOT 정비 (1건)**
- [ ] US-193 (S10-16): §9 RESOLVED 이슈 → SSOT_COMPLETE.md 이관

---

### TF Quarter-Final (QF): Development Verification — ✅ PASS

> **핵심 질문**: "코드가 올바르고, 빠진 것이 없는가?"
> **진입 가드**: 회귀 Phase 전부 완료 + pytest 0 fail + Docker healthy
> **FAIL 시**: 회귀 Phase 생성 → 3-Stage(A→B→C) → QF 재검증
> **PASS 기준**: CRITICAL 0, HIGH 0, MEDIUM ≤ 5 (자금 손실 경로 아님)

**[단계 0] Smoke Test Gate**
- [x] 전체 pytest PASS (4,471 passed, 0 failed)
- [x] Docker 전 컨테이너 healthy (7/8, promtail 비핵심)
- [x] 통합 Shadow 10min (crash=0, 전략 신호 흐름 정상, PnL 기록 확인)

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

**[단계 3.5] 조립 검증 — 통합 검증 (Assembly Verification)** ← 신규
> "부품이 아니라, 조립된 완성품이 제대로 동작하는가?"
- [ ] Init Chain Audit: verify_assembly.py — main.py 서브시스템 non-None 확인
- [ ] Signal Flow E2E: 7개 전략 각각 on_signal() 호출 > 0
- [ ] Config Flag Audit: 모든 feature flag 활성화 상태 확인
- [ ] Dead Wiring Detection: 구현되었으나 미연결 코드 0건

**[단계 4] 최종 확인 + 회귀 (The Feedback Loop)**
- [x] Karina → Nayeon 보고
- [x] Chaeyoung/Tzuyu QA 감사단 압박 면접
- [x] #1 FAIL → 회귀 Phase S1~S6 생성 → 3-Stage(A~C) 수정
- [x] #2 재검증 → 조건부 PASS (CRITICAL 0, HIGH 0)

#### 검증 이력

> **#1 FAIL (2026-03-13)**
> 판정: FAIL — CRITICAL 9, HIGH 12, MEDIUM 19, LOW 19 → 회귀 Phase S1~S6 생성
> 프로세스 상세: `docs/checklists/tf-semi-final-consolidated_20260313.md`
> 교차검증 보고서: `docs/checklists/tf-semi-final_20260313.md`
> TF 리더 판정문: `docs/checklists/tf-semi-final-verdict_20260313.md`

> **#2 조건부 PASS (2026-03-15) ← CURRENT**
> 판정: **조건부 PASS** — 원본 59개 이슈 중 CRITICAL 9→0, HIGH 12→0 (91.5% 해소)
> 회귀 수정: S1~S6 (33/35 US PASS, 2개 Phase F 대기)
> 재검증 보고서: `docs/checklists/tf-semi-final-recheck_20260315.md`

### TF Semi-Final (SF): System Validation — ❌ Stage 2 FAIL → Phase S10 회귀

> **핵심 질문**: "24시간 동안 실제로 돈을 벌 수 있는가?"
> **진입 가드**: TF QF PASS
> **FAIL 시**: 회귀 Phase 생성 → 3-Stage(A→B→C) → SF 재검증 (QF 스킵, 구조적 결함 시 QF부터)
> **PASS 기준**: 24H+ 6-Stage ALL PASS + 전략별 WR>50% + E2E 10/10 (단계 2 병렬) + LiveGate 6-check + 오토튜너 효과 판정
> **현재**: Stage 2 FAIL → Phase S10 회귀. S10 완료 후 TF QF 재실행 → TF SF Stage 1부터 재시작

#### 검증 이력

> **[단계 1-A] ALL PASS (2026-03-15)**
> 판정: ALL PASS — CRITICAL 0, RISK 4 (Live 전환 시 주의), NOTE 3 → Phase S7 생성
> 보고서: `docs/checklists/tf-final-stage1_20260315.md`
> 전문가: Karina(ALL PASS), Jeongyeon(ALL PASS), Dahyun(ALL PASS), Momo(CONDITIONAL), Chaeyoung(0 CRITICAL, 4 RISK), Tzuyu(APPROVE)
>
> **[단계 2] Stage 1 PASS (8h28m)**
> Progressive Shadow Stage 1: 8h28m, 502 trades, 91.8% WR, crash=0
> **후속**: Phase S7 (Pre-Live Hardening) 완료 → 3-Round 체계 강화로 [단계 1-A]부터 재검증
>
> **[단계 2] Stage 1 재시작 PASS (2026-03-16, 1H)**
> Progressive Shadow Stage 1: 1H, PnL +$18.18, PASS
>
> **[단계 2] Stage 2 FAIL (2026-03-16, 2H)**
> Progressive Shadow Stage 2: 2H, PnL -$78.82, **FAIL**
> 근본 원인: stat_arb -$127 (cross_exchange 영역 중복), 전략 신호 겹침, Auto-tuner 미작동
> stat_arb 제외 시 +$48 → stat_arb가 유일한 손실원
> **후속**: Phase S10 생성 (전략 영역 분리 + stat_arb cross-asset 재설계 + Auto-Tuner 확인)

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

> **RESOLVED 정리**: GAP 3/7/8 + HIGH 9건 + LOW 2건 = S8/S9에서 해소 완료 → US-193 완료 시 SSOT_COMPLETE.md §9로 이관.
> 아래는 **현재 미해결 이슈만** 표시.

### RESOLVED (S8/S9에서 해소 — US-193 완료 시 SSOT_COMPLETE.md 이관)

<details>
<summary>RESOLVED 항목 보기 (접기)</summary>

**CRITICAL GAP 3건**: ~~GAP 3 (MultiStrategy LIVE)~~, ~~GAP 7 (Triangular Scanner)~~, ~~GAP 8 (DEX Adapter)~~ → S8 US-169/170/177
**HIGH 9건**: ~~ONNX~~, ~~HMM~~, ~~AdaptiveThreshold~~, ~~ExposureTracker~~, ~~CorrelationMonitor~~, ~~Docker pre-flight~~, ~~IOC~~, ~~마찰 vs Spread~~ → S8 US-172~178
**LOW 2건**: ~~Coinone Rate Limit~~ (자동 재연결), ~~빈 Orderbook~~ (crash 없음)

</details>

### 현재 미해결 이슈 (Phase S10에서 해소 예정)

| # | 심각도 | 이슈 | 설명 | 해결 US |
|---|--------|------|------|---------|
| 1 | **CRITICAL** | 전략 영역 겹침 | _CROSS_EXCHANGE_CONSUMERS가 stat_arb+latency_arb에 cross_exchange 신호 라우팅 → 중복 거래 | US-187, US-194 |
| 2 | **CRITICAL** | stat_arb 구조적 결함 | 교차거래소 동일심볼 mean-reversion = cross_exchange 동일 영역, WFE=-1.03 | US-188 |
| 3 | **CRITICAL** | AdaptiveThreshold WR 기반 | WR 93.8%인데 손실 → WR>90%에서 edge 하향 = 손실 악화 피드백 루프 | US-201 |
| 4 | **HIGH** | Auto-tuner 미작동 | ScheduledTuner 로그 미관찰, AdaptiveThreshold/RegimeDetector/ONNX 호출 미확인 | US-190, US-191 |
| 5 | **HIGH** | 전략 간 포지션 충돌 | 2개 전략이 동일 symbol 동시 거래 가능, 방지 메커니즘 없음 | US-195 |
| 6 | **HIGH** | 전략별 자본 할당 없음 | 7개 전략이 독립적으로 자본 사용, per-strategy 한도 없음 | US-196 |
| 7 | **MEDIUM** | cross_exchange MIN_EDGE 과소 | 5bps → 실제 round-trip 비용 32-65bps, 슬리피지 1건이 17건 이익 소멸 | US-189 |
| 8 | **LOW** | Phase D 대시보드 브라우저 미검증 | Chrome 렌더링, 모바일 반응형 미확인 | TF SF [단계 3-A] |
