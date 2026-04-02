# LEVIATHAN — Single Source of Truth (SSOT)

> **이 문서가 프로젝트의 유일한 설계 문서입니다. 다른 문서에 상태 정보를 기록하지 마세요.**
> 마지막 업데이트: 2026-04-02 (Phase K US-376 신규 등록 — DB mode 분리 배선 ID 충돌 해결) | PRD: `.omc/prd.json` (376개 US, 359 passes:true / 17 passes:false)
> GAP 분석: `.claude/plans/modular-seeking-wreath.md` (6-관점 통합) | 계획서: `.claude/plans/parallel-finding-sparrow.md` (7 Phase, 63 US) | **SIT-3 플랜: `.claude/plans/streamed-dazzling-music.md` (Canary 72H, 10팀 411 시나리오)**
> **Phase K 플랜**: `.claude/plans/radiant-cooking-forest.md` (Backtest→Paper→Live 종합 23케이스, 2026-04-02 v4)
> **실행 순서**: A~M ✅ → S1~S26 ✅ → SIT-0~2 ✅ → SIT-3 ✅ → Phase H ✅ → Phase I ✅ → J ✅ → **K** → L → M → N(TF Final → Live)

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

**Phase**: L (Phase K 완료 — Backtest 23케이스 + Paper 실행 + env 단일화 + 거래소 배선 검증, 2026-04-03)
**Tests**: 5,454 passed / 0 failed / 12 skipped
**Coverage**: 74%
**PRD**: 374/376 passes:true (passes:false 2개 — US-055/056, Live 실제 실행 증거 필요 → Phase L 이월)
**TF Status**: S1~S26 ✅ → SIT-0~2 ✅ → SIT-3 ✅ → Phase H ✅ → Phase I ✅ → Phase J ✅ → Phase K ✅ → **L** → M → N(TF Final → Live)
**Next**: Phase L — Live 모드 진입 (US-055 LiveGate 실행 + US-056 첫 실거래)
**모드 체계 (Phase I 확정)**: `backtest → paper → live` (shadow 명칭 폐기, EngineMode 단일 축)
**Live 설정**: max_position=$10, daily_loss=$15, exchanges=binance+binance_futures
**Live 파이프라인**: LiveMode 클래스 (직접 인-프로세스 라우팅, DI executor, KRW 정규화, circuit breaker, rate limiter)
**모니터링**: 텔레그램 TradeBot 알림 + 대시보드 + Shadow 병행
**계획서**: `.claude/plans/playful-booping-avalanche.md` (Phase H 플랜)
**Phase H 완료 (3 commits)**:
  - H-1: LiveMode 클래스 (1,163줄) + 코드리뷰 12/12 이슈 해결 + Architect 10/10 PASS
  - H-2: EngineMode 4-모드 단일 축 + config/engine.json + BacktestMode + conftest 격리
**API 키**: Binance ✅ Upbit ✅ Bithumb ✅ Coinone ✅ (OKX/Bybit/Bitget 미설정)

**전략 현황 (SIT-3 검증):**
- **funding_rate**: ✅ WR 100%, +$193 (USD sizing + carry sim)
- **spot_futures**: ✅ WR 40-63%, 정상 수익 (소액 — Live 시 포지션 확대로 개선)
- **futures_futures**: ✅ WR 87-93%, 정상
- **statistical_arb**: ✅ WR 100%, cap $10 (Shadow 구조 한계 — expected_profit 기반)
- **triangular**: ⚠️ Bithumb 공개 WS 데이터 품질 문제 (fake spread 304만%). 코드+가드 정상. Live 인증 API 사용 시 해결
- **cross_exchange**: ⚠️ 리테일 수수료(20bps) > 스프레드(0-3bps). 한국 IP 저수수료 거래소 없음 (MEXC 차단, Gate.io 20bps). **VIP 등급 달성이 유일한 해결책**. 코드 자체는 정상
- **cex_dex**: DEX 미연동. Live 후 Uniswap V3 연동 시 활성화
- **AutoTuner**: `TUNER_DATA_SOURCE=timescaledb` (실 데이터 26K+). synthetic 결과가 real params 덮어쓰기 방지

> 완료된 Phase S1-S12 상세: [`SSOT_COMPLETE.md`](SSOT_COMPLETE.md)

> Shadow 현실성 GAP 이력 (SG-1~SG-6 전부 RESOLVED): [`SSOT_COMPLETE.md`](SSOT_COMPLETE.md)

### 프로그레시브 Shadow 테스트 프로토콜

> 24H 단계적 검증으로 조기 문제 발견 (장기 안정성은 TF Final Canary 7일에서 실 자본 검증)

```
Stage 1: 1H  (튜너 OFF) → 기본 동작 확인 (crash=0, 신호 흐름 정상)
Stage 2: 2H  (튜너 OFF) → 승률/PnL 추세 안정성 (WR>60%, PnL 양수)
Stage 3: 2H  (튜너 ON)  → Stage 2 대비 오토튜너 비교 (PROVEN/NEUTRAL/HARMFUL/BUG)
Stage 4: 6H  (최적 설정) → 전략별 메트릭 분리 + 마찰력 정확도 검증
Stage 5: 12H → 메모리 누수/리소스 사용량 안정성
Stage 6: 24H → LiveGate 6-check + Sharpe>2.0, MDD<5%, 일일 PnL 양수 (최종)
```
각 Stage PASS 시 자동으로 다음 Stage 연장 (멈추지 않고 누적)

> Shadow 이력 아카이브 (Phase E-2, Phase 7.3h-i 등): [`SSOT_COMPLETE.md`](SSOT_COMPLETE.md)

### Shadow 통과 기준 (복합지표 — LiveGate 6-check 기반)

> 모든 Shadow 테스트에서 단순 PnL/WR이 아닌 복합지표 기준 적용 (사장님 지시)

**Stage B Shadow 10min (13항목 복합지표 — 시드 무관 절대 지표 포함):**

| # | 체크 | 임계값 | 유형 |
|---|------|--------|------|
| 1 | crash | = 0 | 시스템 |
| 2 | 무중단 실행 | >= 10분 | 시스템 |
| 3 | PnL | >= $0 | 기본 (참고용) |
| 4 | Max Drawdown | < 5% (자본 대비) | **절대 지표** |
| 5 | Profit Factor | > 1.0 (총이익/총손실) | **절대 지표** |
| 6 | 신호 수 | >= 100/day (외삽) | 활성도 |
| 7 | Kill Switch | Not halted | 방어 레이어 |
| 8 | Circuit Breaker | CLOSED | 방어 레이어 |
| 9 | 거래소 Health | >= 95% | 인프라 |
| 10 | loss_capped | = 0 | 리스크 |
| 11 | 전략별 trade | 모든 활성 전략 trade >= 1 | **통합 검증** |
| 12 | 방어 레이어 활성 | CB/StaleDetector/OutlierFilter 로그 >= 1건 | **통합 검증** |
| 13 | 결과 파일 | `.omc/state/shadow-result-latest.json` 존재 | 검증 증거 |

**TF SF Progressive Shadow 24H**: 위 기준 + Sharpe >= 2.0, Calmar > 0, 전략별 WR > 50%, Expected Edge > 0 bps

**TF Final Canary 7일**: 위 기준 + Sharpe >= 2.5, Profit Factor > 1.2, 리콘실리에이션 오차 < 1%

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
  │   └── 3-Bot Telegram      # TradeBot(20cmd) + InfraBot(7cmd) + DevBot(15cmd) poll_loop
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
| 1 | cross_exchange | `strategies/cross_exchange.py` | **활성 (기관급 25%)** | ~~GAP 1,2~~ RESOLVED | 5-25% |
| 2 | spot_futures | `strategies/spot_futures.py` | 대기 (기관급 15%) | 비용>basis (시장 조건), 신호 파이프라인 검증 완료 | 8-30% |
| 3 | futures_futures | `strategies/futures_futures.py` | **활성 (기관급 20%)** | OKX/Bybit futures 수집기 추가로 2+ 선물 거래소 확보 (US-075) | 5-15% |
| 4 | triangular | `strategies/triangular.py` | 대기 (수학오류 발견) | ~~GAP 7,4~~ RESOLVED, leg sizing 통화 불일치 S15에서 수정 | 2-10% |
| 5 | funding_rate | `strategies/funding_rate.py` | **검증됨 (기관급 30%)** | 4거래소×8심볼 수집 성공, diff<threshold 시 정상 필터 | 15-30% |
| 6 | statistical_arb | `strategies/statistical_arb.py` | **검증됨 (WFE -1.03, OOS 손실)** | ~~GAP 3~~ RESOLVED, regime_detector 미주입 S15에서 수정 | 11-16% |
| 7 | latency_arb | `strategies/latency_arb.py` | **병합됨(US-194)** | cross_exchange.latency_boost 모드로 통합, deprecated shim 유지 | (cross_exchange 포함) |
| 8 | cex_dex | `strategies/cex_dex.py` | 비활성 (DEX 비용 문제, 추후 검토) | GAP 8 (DEX stub, TF) | 10-50% |

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

## 7. 남은 작업 (`.omc/prd.json` 375개 User Stories, 359개 완료, 16개 미완)

> **실행 방식**: 3-Stage Sequential — Stage A(기획) → Stage B(구현+검증) → Stage C(리뷰+릴리스)
> **자동화**: `ralph autopilot` → prd.json Phase 단위 순회 → 각 Phase 자동 실행 (leviathan.md 참조)

> **Phase A~M 완료 상세 → [`SSOT_COMPLETE.md`](SSOT_COMPLETE.md)** (토큰 최적화로 아카이브 분리)

> Phase S1-S14 상세: [`SSOT_COMPLETE.md`](SSOT_COMPLETE.md)

#### Phase S15: CRITICAL 버그 + ML 파이프라인 연결 — US-245~259-a ✅ 완료 (2026-03-19, 11/17 US)

> **목표**: CRITICAL 버그 6개 수정 + 수학 오류 3개 수정 + ML 파이프라인 완전 연결 + 응집력 부족 모듈 통합
> **결과**: 11개 US VERIFIED (Shadow 런타임 증거 확인), 6개 US → S16~S21 이월
> **플랜**: `.claude/plans/parallel-finding-sparrow.md`

- [x] US-245: stat_arb regime_detector 주입 (← main.py:898, CRISIS 방어) — VERIFIED
- [x] US-246: LiveGate 실행 경로 강제 적용 (← is_live_eligible() 차단 동작) — VERIFIED
- [x] US-247: estimate_cost() → calculate() 통합 (← network_cost 포함 full cost) — FIXED
- [ ] US-248: ADV/sigma 동적 계산 → **S16~S21 이월**
- [x] US-249: 삼각 차익 leg별 통화 크기 보정 (← triangular.py:134-145) — FIXED
- [ ] US-250: 포지션 리커버리 + 리콘실러 통합 → **S16~S21 이월**
- [x] US-250-a: ComplianceChecker 시작 시 실행 (← infra/compliance.py 23항목) — VERIFIED
- [x] US-251: HMM Trainer 학습 루프 연결 (← main.py _hmm_training_loop()) — FIXED
- [x] US-252: XGBoost Trainer 학습 루프 + ONNX export — FIXED
- [ ] US-253: Feature Pipeline → ONNX Scorer 연결 → **S16~S21 이월**
- [x] US-254: RegimeDetector 전 전략 연결 (← 6개 전략 CRISIS→거부) — VERIFIED
- [x] US-255: AdaptiveThreshold 전략별 분리 + wiring 주입 — FIXED
- [ ] US-256: peak_equity 실시간 갱신 + DB 영속화 → **S16~S21 이월**
- [x] US-257: profit_factor 계산 버그 수정 (금액 비율) — VERIFIED
- [x] US-258-a: ShadowMiniTuner 활성화 — VERIFIED
- [ ] US-258-b: 전략 warm-up 상태 추적 → **S16~S21 이월**
- [ ] US-259-a: S15 통합 Shadow 10min 검증 → **S16~S21 이월**

#### Phase S16: 동적 임계치 + 적응형 파라미터 + S15 이월 — US-248/250/253/256/258-b/259-a + US-260~265 ✅ 완료 (2026-03-20, 12 US)

> **목표**: S15 이월 6개 US 완료 + 정적 임계치 → 롤링 백분위수 + 변동성 가중치 기반 동적 임계치. 기관급 핵심 차별점
> **결과**: 12개 US VERIFIED (Shadow 11min, +$379.44 PnL, 0 crashes, 4962 tests PASS)
> **플랜**: `engine/docs/planning/Phase-S16_PLAN.md`
> **리뷰**: `engine/docs/review/Phase-S16_REVIEW.md` (CRITICAL 0, HIGH 0, MEDIUM 2 non-blocking)

**S15 이월 항목 (6개):**
- [x] US-248: ADV/sigma 동적 계산 (← S15 이월, 114K+ dynamic_sigma_computed 로그) — VERIFIED
- [x] US-250: 포지션 리커버리 + 리콘실러 통합 (← S15 이월, PositionRecovery initialized) — VERIFIED
- [x] US-253: Feature Pipeline → ONNX Scorer 연결 (← S15 이월, MLFeaturePipeline 20-feature initialized) — VERIFIED
- [x] US-256: peak_equity 실시간 갱신 + DB 영속화 (← S15 이월, pool.acquire() 버그 수정, DB 영속화 동작) — VERIFIED
- [x] US-258-b: 전략 warm-up 상태 추적 (← S15 이월, warmup_excluded/crisis_excluded 플래그 활성) — VERIFIED
- [x] US-259-a: S15 통합 Shadow 10min 검증 (← S15 이월, 13항목 복합지표 12/13 PASS) — VERIFIED

**S16 고유 항목 (6개):**
- [x] US-260: 롤링 백분위수 + 변동성 가중치 (cross_exchange, futures_futures) (← AdaptiveThreshold 12K+ signals) — VERIFIED
- [x] US-261: 롤링 백분위수 + 변동성 가중치 (spot_futures basis) (← basis signals processed) — VERIFIED
- [x] US-262: Funding Rate 동적 임계치 (← z-score filter active, 8H rolling history) — VERIFIED
- [x] US-263: Regime별 파라미터 매트릭스 (← REGIME_PARAM_MATRIX + get_regime_params() 테스트 PASS) — VERIFIED
- [x] US-264: CorrelationMonitor 강제 적용 (← Guardian check #9 실제 position scaling) — VERIFIED
- [x] US-265: S16 통합 Shadow 10min 검증 (← 11min, +$379.44, crash=0, 22 new tests) — VERIFIED

#### Phase S17: 전략별 고급 기능 + 실행 안전장치 — US-266~276 ✅ 완료 (2026-03-20, 12 US, 4991 tests)

> **목표**: GPT/Gemini 공통 지적 + 사장님 추가 피드백 반영. 전략별 고급 기법 + Atomic Fallback
> **진입 조건**: Phase S16 완료
> **플랜**: `.claude/plans/parallel-finding-sparrow.md`

- [x] US-266: Bellman-Ford + 비용 통합 (triangular) (← fee weight 수식 `-log(1-fee)` 수정, float underflow guard) — VERIFIED
- [x] US-267: 삼각 차익 Latency Budget 500ms (triangular) (← signal_timestamp_ms 메타데이터 주입 + 소비자 검증) — VERIFIED
- [x] US-268: OU Process Funding Rate 예측 (funding_rate) (← OU 파라미터 추정, 다음 funding 예측 사전 진입, Half-life < Execution Latency 시 차단) — VERIFIED
- [x] US-269: Funding Rate 다중 거래소 스캐너 (funding_rate) (← 3거래소 스캔, settlement 정규화, 최적 쌍 선택) — VERIFIED
- [x] US-270: spot_futures OU Basis Modeling (← OU half-life 적용, signed basis_bps, predict horizon=3600s) — VERIFIED
- [x] US-271: spot_futures max_holding_hours 강제 (← 양레그 동시 청산, futures_symbol/exchange 추적, on_fill startswith 매칭) — VERIFIED
- [x] US-272: futures_futures Funding Convergence (← funding_diff_bps 메타데이터 주입 + ±500bps 클램핑) — VERIFIED
- [x] US-273: futures_futures Stale Guard (← enable_stale_guard default=False, book_age_ms float 변환) — VERIFIED
- [x] US-274: stat_arb Z-score 거래비용 조정 (← z-score 진입에 왕복 비용 반영, 비용 > 예상수익 시 스킵) — VERIFIED
- [x] US-275: Atomic Fallback (Partial Fill 대응) (← 한쪽 leg만 체결 시 수치 기준 손절, 델타 불균형 X초 이상 또는 Y% 이상 시 시장가 청산) — VERIFIED
- [x] US-275-a: DepthAnalyzer 주문 사이징 연결 (← core/depth_analyzer.py VWAP/유동성 미사용 → AtomicExecutor 대형 주문 depth 기반 사이징) — VERIFIED
- [x] US-276: S17 통합 Shadow 10min 검증 (← 멀티모델 감사 Quorum MUST FIX 5건 해결, 4991 tests PASS) — VERIFIED

#### Phase S18: 포트폴리오 리스크 + 평가 체계 + Slippage Feedback — US-277~285 ✅ 완료 (2026-03-20, 11 US, 5080 tests)

> **목표**: 개별 전략 완성 후 포트폴리오 리스크 관리 + 실제 Slippage Feedback Loop + Market Impact
> **진입 조건**: Phase S17 완료
> **플랜**: `.claude/plans/parallel-finding-sparrow.md`

- [x] US-277: portfolio_risk.py 신규 생성 (← 전략간 PnL 상관 행렬 30min rolling, 상관>0.7 합산 포지션 제한, 포트폴리오 VaR) ✅
- [x] US-278: 포트폴리오 MDD 관리 (← 전체 MDD 3% → 신규 진입 차단, 5% → 전체 청산) ✅
- [x] US-279: Regime-Aware 자본 배분 (← CALM→공격적, VOLATILE→보수적, CRISIS→방어적, 전략별 max_position 동적 조정) ✅
- [x] US-280: LiveGate Enforcer 상시화 (← 모든 Shadow/TF에서 6-check 자동 계산+로깅, 미달 시 자동 FAIL, 24H 주기 재평가) ✅
- [x] US-281: Sharpe/Calmar/Sortino + Consistency 실시간 계산 (← 1분 PnL → 1H/1D/7D 롤링, 수익 일관성=양수 PnL 비율, 이상치 필터링) ✅
- [x] US-282: 전략별 Attribution 분석 (← 수익 기여도, 드로다운 기여도, 신호 품질 분해) ✅
- [x] US-283: Slippage Feedback Loop (← 실제 체결가 vs 주문 시점 Orderbook 차이 DB 기록 → CostCalculator 실시간 피드백) ✅
- [x] US-284: Market Impact Cost 모델 (← 주문 크기 대비 호가 잠식, Temporary+Permanent impact 분리, 대형 주문 분할 실행) ✅
- [x] US-284-a: CapitalAllocator(Kelly) 연결 (← core/capital_allocator.py 미사용 → 전략별 edge/WR 기반 자본 배분, 거래 이력 30+ 후 활성화) ✅
- [x] US-284-b: Attribution context 연결 (← main.py:502 생성하지만 EngineContext 미설정, API 매 요청마다 새 인스턴스 생성 문제 수정) ✅
- [x] US-285: S18 통합 Shadow 10min 검증 (← MDD 관리, 상관관계 제한, Slippage feedback, CapitalAllocator 동작 확인) ✅

#### Phase S19: 데이터 품질 통합 — DataQualityManager — US-286~290-a ✅ 완료 (2026-03-21, 6 US, 5120 tests)

> **목표**: StaleDetector/HealthChecker 개별 존재 → DataQualityManager 단일 진입점 통합. stale 진입 0건
> **진입 조건**: Phase S15 완료 (S16/S17/S18과 병렬 가능)
> **플랜**: `.claude/plans/parallel-finding-sparrow.md`

- [x] US-286: DataQualityManager 중앙 관리 객체 (← StaleOrderbookDetector + HealthChecker 단일 통합, update_orderbook()/get_health_scores()/is_blacklisted()/cleanup(), RiskGuardian/LiveGate 주입, 블랙리스트 TTL 관리) ✅
- [x] US-287: 전략별/거래소별 차등 Freshness Threshold (← CEX-CEX: 500ms, Korean: 1s, 기본: 2s, DataQualityManager 설정 관리) ✅
- [x] US-288: Exchange Health Score 실시간 계산 (← DataQualityManager.get_health_scores() 통합, WS+메시지빈도+지연 → 0-100점, <80 비활성) ✅
- [x] US-289: Anomaly Detection (← 30초 롤링 평균 대비 ±5% → 3초 격리 후 재확인, DataQualityManager 레이어) ✅
- [x] US-290: Bithumb 증분 Orderbook Stale 특화 (← 소형코인 2-10x 가격 오차 패턴 탐지 → fake spread 거부) ✅
- [x] US-290-a: S19 통합 Shadow 10min 검증 (← DataQualityManager 동작, stale 거부 건수, health score 모니터링) ✅

#### Phase S20: 사용자 편의성 + 모니터링 — US-291~296 ✅ 완료 (2026-03-21, 7 US, 5150 tests)

> **목표**: 기관급 알고리즘 + 개인 사용자 편의성. 상용 봇(Bitsgap/Pionex) 수준 원클릭 편의성
> **진입 조건**: Phase S15 완료 (S16/S17/S18과 병렬 가능)
> **플랜**: `.claude/plans/parallel-finding-sparrow.md`

- [x] US-291: Prometheus 계측 완성 (← 전략별 trades/signals/latency, 포트폴리오 PnL/MDD, 거래소 health, 신호→체결 Execution Latency) ✅
- [x] US-292: Grafana 대시보드 4개 (← Overview/전략별상세/거래소상태/ML모델성능, 개인 사용자 빠른 상태 파악) ✅
- [x] US-293: Alertmanager → 텔레그램 알림 강화 + Kill Switch 버튼 (← MDD>3% WARNING, MDD>5% CRITICAL, 텔레그램 한국어+인라인 버튼 Kill Switch) ✅
- [x] US-294: 원클릭 시작/중지 CLI (← python -m src.main start/stop/status, .env 자동 검증) ✅
- [x] US-295: 일일 요약 리포트 (텔레그램) (← 매일 09:00 KST 전일 PnL+전략별 성과+Sharpe+MDD+주요 이벤트 자동 발송) ✅
- [x] US-295-a: MonitorDaemon 백그라운드 시작 (← infra/monitor_daemon.py 5분 주기 Redis/DB/API 헬스체크, main.py 백그라운드 태스크 연결) ✅
- [x] US-296: S20 통합 Shadow 10min 검증 (← Grafana 실시간 확인, 텔레그램 알림 트리거, CLI 동작, MonitorDaemon 테스트) ✅

#### Phase S20-B: 3-Bot 버그 수정 + 원격 제어 강화 — US-302~308 ✅ 완료 (2026-03-21, 7 US, 5200 tests)

> **목표**: TradeBot 무응답 수정, Docker monitoring 통합, DevBot 원격 제어 15개, TradeBot 기관급 20개, InfraBot 리소스 모니터링
> **진입 조건**: Phase S20 완료
> **플랜**: `.claude/plans/effervescent-whistling-bird.md`

- [x] US-302: TelegramBotBase HTML fallback + 에러 응답 (← send_message() HTML 실패 시 plain text fallback, _handle_message() 에러 시 사용자 응답) ✅
- [x] US-303: Docker monitoring INFRA 토큰 전환 + MonitorDaemon Engine 분리 (← docker-compose.yml INFRA_TELEGRAM_BOT_TOKEN 매핑, main.py MonitorDaemon run() 제거) ✅
- [x] US-304: Alertmanager 3봇 토큰 sed 치환 (← INFRA/TRADE/DEV 토큰 + chat_id sed 치환, alertmanager.yml placeholder 매칭) ✅
- [x] US-305: DevBot 원격 제어 확장 4→15 (← /session /cmd /test /shadow /git /deploy /logs /approve /reject /progress /env, /cmd 화이트리스트, /deploy 2단계 확인) ✅
- [x] US-306: TradeBot 기관급 기능 12→20 (← /positions /fills /strategy on|off /exchanges /whitelist /blacklist /params /report, send_fill_enhanced() 기관급 체결 알림) ✅
- [x] US-307: InfraBot 시스템 리소스 4→7 (← /resources psutil CPU/메모리/디스크/네트워크/Top프로세스, /metrics Prometheus, /restart 2단계 확인) ✅
- [x] US-308: S20-B SSOT/prd.json/CLAUDE.md 동기화 + Stage C 검증 ✅

#### Phase S21: 전략 포트폴리오 최적화 + Live 준비 — US-297~300 ✅

> **목표**: S15~S20 전체 완성 후 포트폴리오 최종 최적화. 실데이터 WFE + 통합 1H Shadow
> **진입 조건**: Phase S18 + S19 + S20 전부 완료
> **플랜**: `.claude/plans/parallel-finding-sparrow.md`

- [x] US-297: stat_arb WFE 음수 해결 ✅
- [x] US-298: 실데이터 WFE 백테스트 ✅
- [x] US-299: 전략별 독립 Shadow 30min ✅
- [x] US-300: 포트폴리오 통합 Shadow 1H ✅

#### Phase S22: AdaptiveThreshold 이상치 필터 + 전략 이중 게이트 정리 — US-316~320 ✅ 완료 (2026-03-22, 5 US)
- [x] US-316~320: TF QF 9차 회귀 수정 (이중friction, book_depth, config 경로, DB PW, rate limiter)

#### Phase S23: LoginRateLimitMiddleware + spot_futures 역매핑 — US-321~322 ✅ 완료 (2026-03-22, 2 US)
- [x] US-321: _counts IP cleanup 메커니즘
- [x] US-322: spot_futures on_fill 심볼 키 불일치 수정

#### Phase S24: SignalGenerator regime override 조건부화 — US-323 ✅ 완료 (2026-03-22, 1 US)
- [x] US-323: MIN_EDGE_BPS=0 시 regime override 비활성화

#### Phase S25: 텔레그램 알림 전체 한글화 — US-324 ✅ 완료 (2026-03-22, 1 US)
- [x] US-324: send_alert 20곳 한글화

#### Phase S26: 전략 리서치 + Shadow Live급 강화 — US-325~335 ✅ 완료 (2026-03-24, 9/11 US, 5244 tests)
> TF SF FAIL(Sharpe 0.53) → 전략 리서치 + 파라미터 재설계 + Shadow 강화
- [x] US-325: 전략 수익성 벤치마크 리서치 (exa.ai)
- [x] US-326: 파라미터 근본 재설계 (slippage_buffer, active_hours)
- [x] US-327: 전략별 활성화/비활성화 기준 재설정
- [x] US-328: Shadow 포지션 크기 Live급 상향 (DEPTH_FRACTION=1.0)
- [x] US-329: TCA 로깅 강화 (Arrival Price, Timing, 전략별)
- [x] US-330: Shadow vs Virtual Live 비교 리포터
- [x] US-331: Leg Risk 감지 + 메트릭
- [ ] US-332: SF 24H Progressive Shadow 재실행 — **런타임 실행 필요**
- [x] US-333: TCA 기반 min_profitability 재보정
- [ ] US-334: 소액 Live 전환 기준 + Sandbox Testnet 검증 — **런타임 실행 필요**
- [x] US-335: 일일 3-Way 리콘실리에이션 리포터

#### Phase SIT-1: 완전체 구축 (한글화 + 모드UI) — US-336~337 ✅ 완료 (2026-03-24, 2 US)
- [x] US-336: 텔레그램 send_alert_kr 구조화 양식 전환 (15곳)
- [x] US-337: 대시보드 Settings 모드별 설정 UI

#### Phase SIT-2: 클로즈 베타 — US-338 ✅
- [x] US-338: 클로즈 베타 체크리스트 10항목 통과

#### Phase SIT-3: 종합테스트 — US-339 ✅
- [x] US-339: 종합테스트 410/410 GREEN, CP1~CP5 PASS (2026-03-29)

#### Phase I: 배관 정리 + 거래소 기반 완성 — US-344~350 ✅ 완료 (2026-04-01)

> **목표**: 설정 통합 + Dead Wiring 제거 + EngineMode 단순화 + 거래소 기반 정리
> **결과**: 5,348 tests PASS, crash 0, Step 0 완료 (Redis 초기화 버그 수정 + DB mode 컬럼 + PaperMode 리네임)
> **커밋**: `486419b feat: Phase I 완료 — 배관정리 + 거래소 기반 완성`

- [x] US-344: Claude Code 인프라 설정 (hooks + env) ✅
- [x] US-345: 설정 통합 (5 진입점 → 2개) ✅
- [x] US-346: EngineMode 3개 단순화 (SHADOW 삭제) ✅
- [x] US-347: ShadowMode/LiveMode 중복 제거 (PaperMode 리네임, shadow.py class → PaperMode) ✅
- [x] US-348: Dead Wiring 수정 (ExposureTracker/TCAAnalyzer/BookWalkSlippage) ✅
- [x] US-349: AutoTuner 실 데이터 활성화 ✅
- [x] US-350: 거래소 확장 (Gate.io/Bitget/OKX API + BingX/LBank/OrangeX 어댑터) ✅

**Step 0 완료 항목 (2026-04-01):**
- [x] Redis 초기화 버그 수정 (main.py:467)
- [x] DB mode 컬럼 추가 (execution_log, migration 002_add_mode_column.sql)
- [x] MarketRecorder mode 파라미터 추가
- [x] PaperMode 리네임 (shadow.py class → PaperMode, paper.py 정식 경로)
- [x] 전체 테스트 5,348 passed / 0 failed / 12 skipped

---

#### Phase J: Backtest 검증 — US-351~357 ✅ 완료 (2026-04-02)

> **목표**: BacktestMode wiring + WFA 6전략 루프 + ML A/B 연결 + Sharpe sqrt(8760) 통일 + orderbook retention 30일
> **결과**: 5,379 tests PASS, Shadow 13/13 PASS (PnL=+$13,243, PF=8.75, CB CLOSED, 13.77min), crash 0
> **커밋**: `feat: Phase J — BacktestMode WFA + ML A/B + Sharpe sqrt(8760) + coinone CB fix`
> **플랜**: `/Users/100aniv/.claude/plans/fancy-strolling-pine.md`

- [x] US-351: BacktestMode wiring (_backtest_mode_task + EngineMode.BACKTEST) ✅
- [x] US-352: Sharpe sqrt(8760) 3곳 통일 ✅
- [x] US-353: WFA 6전략 루프 ✅
- [x] US-354: ML A/B MLSignalBacktester.ab_test() 연결 ✅
- [x] US-355: BacktestResult 3중 정의 통합 ✅
- [x] US-356: EngineMode.BACKTEST config ✅
- [x] US-357: orderbook retention 30일 ✅

**Shadow 13항목 복합지표 (2026-04-02):**
- PnL: +$13,243.52 (257 trades), PF: 8.75, MDD: 0.0%, CB: CLOSED
- Assembly Gate 5/5 PASS, 코드리뷰 PASS (HIGH 2건 수정 완료)

#### Phase K: 종합 테스트 (Backtest → Paper → Live) — 진행중

> **목표**: 15개 거래소 × 7전략 모든 케이스를 Backtest→Paper→Live 전 사이클로 체계적 실증
> **진입 조건**: Phase J 완료 ✅
> **플랜**: `.claude/plans/radiant-cooking-forest.md` (v4, 2026-04-02)
> **핵심 배경**: Phase J 백테스트 결과에 어떤 전략/기간/시드로 실행했는지 브리핑 없음, 사용자 파라미터 선택 UI도 없음 → Phase K에서 전 사이클 재설계
> **자본 기준**: Spot $20 (글로벌) / ₩28,000 (KRW) / Futures $30 (글로벌만). max_position_pct=5%
> **총 케이스**: 23개 (Batch1~3: 16개 + Batch4 Tier4 WS전용: 7개)
> **모드 명칭**: backtest / paper / live (shadow 명칭 폐기 — Phase I 확정)
> **실행 순서**: K-0-ENV(US-375) → K-0(US-334/365) → K-1A/C/D(US-359/364/366/367/360) → K-6/K-7(US-361/362) → K-2-B(US-368~371, Batch2+3 병렬) → K-2-P(US-332/372) → K-4(US-055) → K-2-L(US-056) → K-2-ALL(US-373)

**거래소 아키텍처 (15개)**

| 티어 | 거래소 | 거래 어댑터 | API 키 | Phase K 작업 |
|------|--------|-----------|--------|------------|
| Tier 1 Native Spot | Binance / Bybit / OKX | 완성 | Binance ✅ / 나머지 ❌ | — |
| Tier 1 Native Spot | Bitget / Upbit / Bithumb | 완성 | 모두 ✅ | K-1A: config.py 필드 이미 추가(US-359) |
| Tier 2 Native Futures | Binance Fut / Bybit Fut / OKX Fut | 완성 | Binance ✅ / 나머지 ❌ | — |
| Tier 3 CCXT | Coinone | CCXT 경유 | ✅ | K-1A: config.py 필드 이미 추가(US-359) |
| Tier 4 WS전용 | MEXC / Gate.io / BingX / LBank / OrangeX | US-360 ✅ 완성 | ❌ 미발급 | API 발급 즉시 Live 가능 |

**K-0-ENV: .env 단일화 (모든 K 단계 최우선 선행)**
- [ ] US-375: engine/.env 삭제 + config.py 절대경로 수정 + 드리프트 4개 해소 (EXECUTION_MODE=paper, MAX_DAILY_LOSS_USD=15, SHADOW_DISABLED_STRATEGIES 제거, ALLOWED_IPS 통합) + preflight.py _check_env_sync() 삭제 + engine.json shadow/live_gate/tuner 섹션 추가

**K-0: 선행 완료**
- [ ] US-334: engine.json capital.tiers.alpha 설정 + Testnet 주문 1건 (Binance Testnet)
- [ ] US-365: DB mode 분리 배선 — walk_forward mode='backtest' 필터 + migration 006 + /trades?mode= 파라미터
- [ ] US-376: DB mode 분리 배선 세부 배선 — walk_forward/attribution mode 필터 (US-375 의존)

**K-1: 전체 거래소 배선 현황 + 검증 (15개)**
- [x] US-359: config.py API 키 필드 18개 추가 (Bitget 4 + Upbit 2 + Bithumb 2 + Coinone 2 + Tier4 10) ✅
- [ ] US-364: Telegram 승인 게이트 구현 (imessage_gate.py + live.py 주입, DevBot /approve, fail-closed) — K-1C
- [ ] US-366: engine/.env 표준화 — K-0-ENV 완료로 자동 충족 대기 (K-1B → K-0-ENV 흡수)
- [ ] US-367: 거래소별 배선 검증 — API 보유 7개 Paper 1H (crash=0) + Bybit/OKX WS 연결 확인 (K-1C)
- [x] US-360: Tier4 거래 어댑터 5개 (MEXC/Gate.io/BingX/LBank/OrangeX, Bitget 패턴) ✅

**K-선행 (K-6/K-7 — 백테스트 실행 전 완료 필수)**
- [x] US-361: POST /api/backtest/start + BacktestResult meta 5필드 (전략/기간/시드/거래소 브리핑) ✅
- [x] US-362: OHLCV 다운로더 — Binance 1H→합성 오더북→TimescaleDB ✅
- [x] US-358: LiveMode record_execution(mode='live') 호출 추가 ✅
- [x] US-363: POST /api/paper/start 엔드포인트 구현 ✅

**K-2-B: 백테스트 단계 (23케이스, PASS 기준: Sharpe>0.5, MDD<20%, trades>=5, PnL>0)**
- [ ] US-368: Batch1 — Binance 4케이스 B-01~B-04 (funding_rate/triangular/stat_arb/spot_futures) — 병렬 실행
- [ ] US-369: Batch2 — Bitget+KRW 7케이스 B-05~B-11 — Batch3과 병렬 실행
- [ ] US-370: Batch3 — 멀티거래소 5케이스 B-12~B-16 — Batch2와 병렬 실행
- [ ] US-371: Batch4 — Tier4 WS전용 7케이스 B-17~B-23 (K-1D 완료 후, Binance proxy)

**K-2-P: 페이퍼 테스트 단계 (백테스트 PASS 조합만, 2H~4H. 누적 ≥24H → US-332 자동 충족)**
- [ ] US-332: Paper 무중단 24H (crash=0, Sharpe>=2.0) — K-2-P 23케이스 누적으로 자동 충족
- [ ] US-372: P-01~P-23 페이퍼 실행 전체 (crash=0, trade>=1. Tier4는 K-1D 완료 후)

**K-4: LiveGate 통과**
- [ ] US-055: Preflight 10항목 통과 (TimescaleDB/WS+REST/API키/잔고/KillSwitch/CB/LiveGate/Telegram/AdapterHealth/Paper72H)

**K-2-L: 라이브 테스트 단계 (페이퍼 PASS + 라이브 가능 조합)**
- [ ] US-056: 첫 Live 체결 1건+ (L-01 BN-FR 최우선 → L-02 CN-Tri → L-03 BN-Stat → L-04 BG-FR → L-05 BN-Tri → L-06 BN-BG-CE → L-07a~d)
  - Live 불가 케이스: BT-Tri(WS 품질), BN-CN-CE/BN-UP-CE(L1 전송비 $2.56~$4.56), BNF-BGF-FF(Phase L)

**K-2-ALL: 전체 병렬 운영 (Day 15~21)**
- [ ] US-373: 검증 완료 4조합 동시 24H (Binance FR + Bitget FR + BN-BG CE + Coinone Tri, crash=0, 전략별 trade>=1, MDD<5%)

**K-8: Notion 실시간 플랜 공유**
- [ ] US-374: NotionReporter — Phase K 플랜 페이지 + 단계별 실시간 업데이트

**백테스트 전략×거래소 매트릭스 (23케이스)**

| ID | 거래소 | 전략 | B | P | L | 제약 |
|----|--------|------|---|---|---|------|
| B-01/P-01/L-01 | Binance | funding_rate | Batch1 | 2H | 1순위 | SIT-3 기검증 |
| B-02/P-02/L-05 | Binance | triangular | Batch1 | 4H | 5순위 | min_edge_bps 확인 |
| B-03/P-03/L-03 | Binance | statistical_arb | Batch1 | 4H | 3순위 | pos_usd cap 주의 |
| B-04/P-04/L-07a | Binance | spot_futures | Batch1 | 4H | 7a순위 | basis 거래 |
| B-05/P-05/L-04 | Bitget | funding_rate | Batch2 | 4H | 4순위 | 배선 완료 후 |
| B-06/P-06 | Bitget | triangular | Batch2 | 4H | — | |
| B-07/P-07 | Bitget | statistical_arb | Batch2 | 4H | — | |
| B-08/P-08/L-02 | Coinone | triangular | Batch2 | 4H | 2순위 | 수수료 0.06% 최저 |
| B-09/P-09/L-07b | Coinone | statistical_arb | Batch2 | 4H | 7b순위 | |
| B-10/P-10/L-07c | Upbit | triangular | Batch2 | 4H | 7c순위 | 수수료 0.43% |
| B-11/P-11 | Bithumb | triangular | Batch2 | 4H | Paper only | WS 데이터 품질 |
| B-12/P-12/L-06 | Binance↔Bitget | cross_exchange | Batch3 | 4H | 6순위 | L2 전송비 낮음 |
| B-13/P-13/L-07d | Binance+Bitget | funding_rate(양) | Batch3 | 4H | 7d순위 | delta-neutral |
| B-14/P-14 | Binance↔Coinone | cross_exchange | Batch3 | 4H | Paper only | L1 전송비 $2.56 |
| B-15/P-15 | Binance↔Upbit | cross_exchange | Batch3 | 4H | Paper only | L1 전송비 $4.56 |
| B-16/P-16 | BinanceFut↔BitgetFut | futures_futures | Batch3 | 4H | Phase L | Bitget Fut 미구현 |
| B-17/P-17 | MEXC | triangular | Batch4 | 4H | API 미발급 | K-1D 후 |
| B-18/P-18 | MEXC | statistical_arb | Batch4 | 4H | API 미발급 | K-1D 후 |
| B-19/P-19 | Gate.io | triangular | Batch4 | 4H | API 미발급 | K-1D 후 |
| B-20/P-20 | Gate.io | statistical_arb | Batch4 | 4H | API 미발급 | K-1D 후 |
| B-21/P-21 | BingX | triangular | Batch4 | 4H | API 미발급 | K-1D 후 |
| B-22/P-22 | LBank | triangular | Batch4 | 4H | API 미발급 | 소규모, 유동성 낮음 |
| B-23/P-23 | OrangeX | statistical_arb | Batch4 | 4H | API 미발급 | 파생상품 특화 |

**Phase K 완료 기준**:
- Tests 5,379+ 유지 (pytest 0 failures)
- US-375 K-0-ENV 완료 (engine/.env 삭제, config.py 절대경로, 드리프트 4개 해소)
- US-332 Paper 24H PASS (K-2-P 누적) + US-365 DB mode 분리
- 전체 거래소 배선: API 보유 7개 Paper 1H crash=0 + Bybit/OKX WS 연결
- 백테스트 23케이스 결과 브리핑 (전략/기간/시드/거래소 지표 포함)
- 페이퍼 23케이스 crash=0, trade>=1
- US-055 Preflight 10/10 PASS + US-056 첫 Live 체결 1건+
- K-2-ALL 4전략 동시 24H crash=0, MDD<5%
- US-374 Notion 플랜 페이지 생성
- check_all 9/9 OK + git push

#### Phase L: 대시보드 재설계 + 운영 안정화 — 미시작

> **목표**: 토스증권/업비트 UX 기반 전면 재설계 + 운영 인프라 안정화
> **진입 조건**: Phase K 2주 완료

- [ ] L-1: 대시보드 UX 전면 재설계 (토스증권/업비트 참조)
- [ ] L-2: Settings hot-reload (재시작 없이 파라미터 변경)
- [ ] L-3: OpenTelemetry 통합 (분산 트레이싱)
- [ ] L-4: Zero-downtime 배포 (Blue-Green 또는 Rolling)
- [ ] L-5: 운영 Runbook + 장애 대응 절차 (IRP: P1/P2/P3) 문서화
- [ ] L-6: Phase L Shadow 검증 + 대시보드 브라우저 E2E (UAT)

#### Phase M: 전략 성숙 — 미시작

> **목표**: 수익 전략 성능 고도화 + 미활성 전략 재활성화
> **진입 조건**: Phase L 완료

- [ ] M-1: spot_futures WR 75%+ 달성 (OU Basis 파라미터 최적화)
- [ ] M-2: Bithumb 인증 API 연동 (공개 WS stale data 해결 → triangular 재활성화)
- [ ] M-3: triangular 재활성화 (Bithumb 인증 API 적용 후 fake spread 해소 검증)
- [ ] M-4: cross_exchange VIP 수수료 협상 또는 저수수료 거래소 추가
- [ ] M-5: cex_dex — Uniswap V3 연동 (leviathan-quant + dex-specialist)
- [ ] M-6: Phase M Shadow 24H 검증 (전 활성 전략 WR>50%, Sharpe>2.0)

#### Phase N: TF Final → 상용화 — 미시작

> **목표**: 최종 TF 4-Round 통과 → 전체 자본 Live 전환
> **진입 조건**: Phase M 완료 + TF SF 24H ALL PASS
> **완료 기준**: Sharpe > 2.0, MDD < 5%, 72H 무중단, TF 4-Round PASS

- [ ] N-1: TF QF 재검증 (코드 정합성 최종 확인)
- [ ] N-2: TF SF 24H Progressive Shadow (Sharpe>2.0, MDD<5%, 전략별 WR>50%)
- [ ] N-3: TF PF 코드 구조 최종 점검 (기능 변경 0)
- [ ] N-4: TF Final ORR + DR 9/9 PASS
- [ ] N-5: Canary 7일 실거래 (Alpha $70/exchange × 10 = $700)
- [ ] N-6: 사장님 최종 승인 → Full Live 전환

---

### TF Quarter-Final (QF): Development Verification — 11차 PASS + Re-Validation PASS (2026-03-22)

> **핵심 질문**: "코드가 올바르고, 빠진 것이 없는가?"
> **진입 가드**: 회귀 Phase 전부 완료 + pytest 0 fail + Docker healthy
> **FAIL 시**: 회귀 Phase 생성 → 3-Stage(A→B→C) → QF 재검증
> **PASS 기준**: CRITICAL 0, HIGH 0, MEDIUM ≤ 5 (자금 손실 경로 아님)
> **판정**: 11차 PASS (2026-03-22) — CRITICAL 0, HIGH 0, MEDIUM 0 (S23 회귀 + 멀티모델 3종 재검증 완료)
> **체크리스트**: `docs/checklists/tf-quarter-final_20260318.md`

**[단계 0] Smoke Test Gate**
- [x] 전체 pytest PASS (4,843 passed, 0 failed, 12 skipped)
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

> **#6 PASS (2026-03-17)**
> 판정: **PASS** — CRITICAL 0, HIGH 0, MEDIUM 6, LOW 4 (자금 손실 경로 0건)
> 4695 tests, Shadow 10min crash=0, 조립 검증 4/4 PASS
> 체크리스트: `docs/checklists/tf-quarter-final_20260317.md`

> **#7 PASS (2026-03-18)**
> 판정: **PASS** — CRITICAL 0, HIGH 0, MEDIUM 4
> 4,843 tests, 7개 전략 로직 개선 (stat_arb routing, hedge-ratio, spot_futures direction, funding_rate phantom fix)
> 체크리스트: `docs/checklists/tf-quarter-final_20260318.md`
>
> **#8 INVALIDATED (2026-03-22)**
> 판정: **무효** — Shadow trades=0 (DQM HealthChecker Paper 모드 health=0.45 → RiskGuardian 차단), 멀티모델 감사 미실행, MEDIUM 6건 override 무단
> 근본 원인: HealthChecker.is_connected=False (Paper 어댑터가 record_ws_connect() 미호출)
> 수정: DQM always_healthy 분기 추가 (fc22995)
>
> **#9 IN_PROGRESS (2026-03-22) ← CURRENT**
> Phase 1-2 완료 (Shadow DQM 수정 + sync CLI + FSM + consistency 9항목), Phase 3 완료 (leviathan.md L88 + watchdog + CLAUDE.md)
> 재검증 대기: Shadow trades>0 확인 후 QF 9차 305줄+ 체크리스트 실행 예정

### TF Semi-Final (SF): System Validation — 3차 Stage 2 PASS (2026-03-18)

> **핵심 질문**: "24시간 동안 실제로 돈을 벌 수 있는가?"
> **진입 가드**: TF QF PASS
> **FAIL 시**: 회귀 Phase 생성 → 3-Stage(A→B→C) → SF 재검증 (QF 스킵, 구조적 결함 시 QF부터)
> **PASS 기준**: 24H+ 6-Stage ALL PASS + 전략별 WR>50% + E2E 10/10 + LiveGate 6-check
> **현재**: 3차 Stage 2 PASS (2H PnL +$3,312.08) → Stage 3~6 진행 예정
> **체크리스트**: `docs/checklists/tf-semi-final_20260318.md`

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
> **2차 [단계 2] Stage 1 PASS → Stage 2 FAIL (2026-03-17)**
> Stage 1: 1H crash=0, 4전략 활성, 10/10 거래소 → PASS
> Stage 2: 2H45M PnL **-$153.47**, WR 90.7%, loss_capped 17건(-$850) → **FAIL**
> 근본 원인: (1) futures_futures stale 진입 17건×-$50 (2) spot_futures WR 42% (3) funding_rate WR 6.7%
> **후속**: Phase S13 생성 (stale guard 강화, circuit breaker, 전략 비활성화, loss_cap 차등)
>
> **3차 [단계 2] Stage 2 PASS (2026-03-18) ← CURRENT**
> Stage 1: 1H crash=0, 5전략 활성 → PASS
> Stage 2: 2H PnL **+$3,312.08**, WR 72.7%, 6,902 trades → **PASS**
> 전략별: spot_futures 4,508 / triangular 1,292 / futures_futures 958 / stat_arb 139 / funding_rate 5
> loss_capped: 11건 (cap $1~$5), regime_check: 119, adaptive_threshold: 24
> 이전 2차 FAIL(-$153.47) 대비 완전 반전 (+$3,465.55 개선)

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
- [x] Stage 1: 1H (튜너 OFF) → crash=0, PnL 기록 ✅ PASS
- [x] Stage 2: 2H (튜너 OFF) → WR>60%, PnL>0, 전략별 리포트 ✅ PASS (PnL +$3,312.08, WR 72.7%)
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

### TF Pre-Final (PF): Code Structure — 미시작

> **핵심 질문**: "상용급 코드 구조인가? 기능 변경 없이 구조를 개선할 수 있는가?"
> **진입 가드**: TF SF 24H ALL PASS
> **PASS 기준**: Shadow baseline 동일 + pytest 0 fail + Assembly 4-check + 멀티모델 "기능 변경 0" 합의
> **회귀**: git rollback (prd.json 비관여). 최대 2회 재시도. 실패 시 PF 스킵 → Final.

**[PF-1]** Baseline 확보 (pytest + Shadow 10min + git tag pf-baseline)
**[PF-2]** Settings 통합 (35개 env → EngineConfig dataclass)
**[PF-3]** Init Chain 모듈화 (_init_*() 11개 → EngineBootstrap)
**[PF-4]** Loop Manager 추출 (13개 루프 → LoopManager)
**[PF-5]** 타입 강화 (Any → Protocol/TypeVar)
**[PF-6]** 멀티모델 리팩토링 감사 (기능 변경 0 검증)
**[PF-7]** 재검증 (baseline vs post 비교)

> 상세 절차: `leviathan-tf.md` §PF 참조

### TF Final (F): Operations Readiness — 미시작

> **핵심 질문**: "문제가 생기면 대응할 수 있는가? 실제 돈을 안전하게 운용할 준비가 되었는가?"
> **진입 가드**: TF PF PASS (또는 PF 스킵) + 24H Shadow ALL PASS
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

### CRITICAL (6건 — 전부 S15에서 해결 ✅)

| 이슈 | 설명 | 해결 US |
|------|------|---------|
| ~~**stat_arb regime_detector 미주입**~~ | ~~`main.py:898`에서 regime_detector 미전달~~ | ~~US-245~~ ✅ S15 |
| ~~**LiveGate 차단 미동작**~~ | ~~`is_live_eligible()` 미호출~~ | ~~US-246~~ ✅ S15 |
| ~~**profit_factor 계산 버그**~~ | ~~건수 비율 → 금액 비율~~ | ~~US-257~~ ✅ S15 |
| ~~**estimate_cost() 비용 과소계산**~~ | ~~network_cost=0 → full cost~~ | ~~US-247~~ ✅ S15 |
| ~~**ADV/sigma 하드코딩**~~ | ~~동적 계산으로 수정~~ | ~~US-248~~ ✅ S16 |
| ~~**삼각 leg sizing 통화 불일치**~~ | ~~leg별 통화 크기 보정~~ | ~~US-249~~ ✅ S15 |

### HIGH (5건 — 전부 S15~S19에서 해결 ✅)

| 이슈 | 설명 | 해결 US |
|------|------|---------|
| ~~**PositionReconciler 미연결**~~ | ~~PositionRecovery + Reconciler 통합~~ | ~~US-250~~ ✅ S16 |
| ~~**AdaptiveThreshold 글로벌 단일**~~ | ~~전략별 분리~~ | ~~US-255~~ ✅ S15 |
| ~~**HealthChecker 피드 미호출**~~ | ~~DQM 통합으로 해결~~ | ~~US-286~~ ✅ S19 |
| ~~**CapitalAllocator(Kelly) 미연결**~~ | ~~전략별 자본 배분 연결~~ | ~~US-284-a~~ ✅ S18 |
| ~~**Attribution context 미설정**~~ | ~~EngineContext 주입 완료~~ | ~~US-284-b~~ ✅ S18 |

### MEDIUM (3건 — 전부 S15~S20에서 해결 ✅)

| 이슈 | 설명 | 해결 US |
|------|------|---------|
| ~~**MonitorDaemon 미시작**~~ | ~~백그라운드 태스크 등록 완료~~ | ~~US-295-a~~ ✅ S20 |
| ~~**ShadowMiniTuner 데드코드**~~ | ~~활성화 완료~~ | ~~US-258-a~~ ✅ S15 |
| ~~**전략 warm-up 추적 없음**~~ | ~~플래그 추가~~ | ~~US-258-b~~ ✅ S16 |

### LOW (2건)

| 이슈 | 설명 | 해결 시점 |
|------|------|----------|
| **Phase D 대시보드 브라우저 미검증** | Chrome 렌더링, 모바일 반응형 미확인 | TF SF [단계 3-A] |
| **send_fill_kr/send_fill_enhanced dead code** | telegram_trade_bot.py:617,622 정의만 있고 런타임 호출 0건 (테스트만 호출). US-306 passes:true이지만 dead code → 와이어링 필요 | QF 9차 전 |

### RESOLVED (S13/S14에서 해소 — SSOT_COMPLETE.md §9로 이관)

> S13 CRITICAL 3건 (전략 영역 겹침, stat_arb 구조적 결함, AdaptiveThreshold 피드백 루프) + HIGH 3건 (Auto-tuner, 포지션 충돌, 자본 할당) + MEDIUM 1건 (MIN_EDGE 과소) → [`SSOT_COMPLETE.md`](SSOT_COMPLETE.md) 참조
> 기존 14건 RESOLVED → [`SSOT_COMPLETE.md`](SSOT_COMPLETE.md) 참조
