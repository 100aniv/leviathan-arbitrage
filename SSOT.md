# LEVIATHAN — Single Source of Truth (SSOT)

> **이 문서가 프로젝트의 유일한 설계 문서입니다. 다른 문서에 상태 정보를 기록하지 마세요.**
> 마지막 업데이트: 2026-03-08 | 최신 커밋: `e273d9a`

---

## 1. 프로젝트 개요

**LEVIATHAN**은 글로벌 암호화폐 거래소 간 크로스 차익거래를 자동 실행하는 고빈도 거래 엔진이다.

| 항목 | 내용 |
|------|------|
| 엔진 | Python 3.12+ (AsyncIO) + Rust (PyO3 hot-path) |
| 대시보드 | Next.js 14 (App Router) + JWT 인증 + 실시간 WS 피드 |
| 거래소 | 7개 네이티브 WebSocket 어댑터 (ccxt 미사용) |
| 전략 | 8개 (7개 기본 + CexDex 조건부) |
| 인프라 | Docker Compose 8 컨테이너, TimescaleDB + Redis + Prometheus + Grafana |
| 실행 모드 | Backtest → Paper → Shadow → Live |

**거래소 목록**: Binance, Bybit, OKX, Bitget, Upbit, Bithumb, Coinone (7개 네이티브 어댑터)

---

## 2. 현재 상태

```
Phase:        7.3k (Shadow Runtime & Tuning)
테스트:       3,016 passed, 0 failed
커버리지:     88%
컴플라이언스: 100% (23/23 PASS)
현재 모드:    DATA_MODE=shadow, EXECUTION_MODE=paper
최신 커밋:    e273d9a (Phase 7.3i: 60min shadow)
```

### Shadow 최신 결과 (Phase 7.3k, 10min)

| 항목 | 값 |
|------|-----|
| 거래 수 | 3,110 |
| 승률 | 100% |
| PnL | +$21.10 |
| Max Drawdown | $0.00 |
| 심볼 수 | 175 |
| 활성 거래소 | 8 (MEMORY 기준) |
| MIN_EDGE_BPS | 5 |

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

### 3.3 전략 매트릭스

| # | 전략 | 파일 | 상태 | 비고 |
|---|------|------|------|------|
| 1 | cross_exchange | `strategies/cross_exchange.py` | **활성** | Shadow 주력 |
| 2 | spot_futures | `strategies/spot_futures.py` | 비활성 | Korean stale data |
| 3 | futures_futures | `strategies/futures_futures.py` | 비활성 | 신호 소스 없음 |
| 4 | triangular | `strategies/triangular.py` | 비활성 | |
| 5 | funding_rate | `strategies/funding_rate.py` | 비활성 | |
| 6 | statistical_arb | `strategies/stat_arb.py` | 비활성 | NOT_READY 플래그 |
| 7 | latency_arb | `strategies/latency_arb.py` | 비활성 | 파라미터 누락 |
| 8 | cex_dex | `strategies/cex_dex.py` | 조건부 | DEX_RPC_URL 필요, 미구현 |

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
기본값: k=1.0, gamma=0.5, base=0.001
근거: Almgren-Chriss (2000), sqrt 영향 모델
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
| Bybit | 0.01% | 0.06% | |
| OKX | 0.08% | 0.10% | |
| Bitget | 0.10% | 0.10% | |
| Upbit | 0.25% | 0.25% | KRW 마켓 |
| Bithumb | 0.25% | 0.25% | KRW 마켓 |
| Coinone | 0.20% | 0.20% | API 할인 시 0.02% |

### 4.3 리스크 모델

**KillSwitch (3-tier)**:
- Tier 1: 일일 누적 손실 > 임계값 → 전체 중단
- Tier 2: CB OPEN > 30min / 레이턴시 > 5s 연속 10회 → 자동 일시정지
- Tier 3: 수동 halt_local() → 즉시 중단

**CircuitBreaker**: CLOSED → OPEN → HALF_OPEN (지수 백오프 1s→60s cap)

**RiskGuardian (9-check)**: 자본, 마진, 스프레드, 포지션, 주문크기, 일일손실, 연속손실, 슬리피지, 롤백비용

### 4.4 이중 슬리피지 방지 규칙

> **절대 규칙**: PowerLawSlippage(k=5.0)는 ~100bps 왕복 영향 → PaperExecutor에 적용 금지.
> SignalGenerator의 CEXOrderbookSlippage가 유일한 슬리피지 소스.
> Shadow 모드에서 PaperExecutor는 ZERO slippage로 실행해야 이중 계산 방지.

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

### 7개 네이티브 WebSocket 어댑터 (ccxt 미사용)

| 거래소 | WS 엔드포인트 | 심볼 형식 | 상태 | 비고 |
|--------|-------------|---------|------|------|
| Binance | `wss://stream.binance.com:9443` | `BTC/USDT` | 연결됨 | 멀티스트림 지원 |
| Bybit | `wss://stream.bybit.com/v5/public/spot` | `BTC/USDT` | 준비 | |
| OKX | `wss://ws.okx.com:8443/ws/v5/public` | `BTC/USDT` | 준비 | |
| Bitget | `wss://ws.bitget.com/v2/ws/public` | `BTC/USDT` | 준비 | |
| Upbit | `wss://api.upbit.com/websocket/v1` | `BTC/KRW` | 연결됨 | 배치 구독 |
| Bithumb | `wss://pubwss.bithumb.com/pub/ws` | `BTC/KRW` | 연결됨 | 배치 구독 |
| Coinone | `wss://stream.coinone.co.kr` | `BTC/KRW` | 준비 | 30min PING |

### KRW 자동 매핑

- `CollectorManager.KOREAN_EXCHANGES = {"upbit", "bithumb", "coinone"}`
- `_get_exchange_symbols()`: `/USDT` → `/KRW` 자동 변환
- `ShadowMode._on_orderbook()`: KRW → USDT 역환산 (dual-source: Upbit+Bithumb API, 30s 갱신)
- Sanity: +/-10%, 120s staleness, 5-reject lockout escape

### Bithumb 데이터 품질 이슈

증분 orderbook에서 초기 스냅샷 없이 수신 → 소형코인(NOM +62%, SXP +12%)에서 허위 스프레드 발생.
현재 완화: `max_spread_pct=5.0` 필터. 근본 해결: REST 스냅샷 후 증분 적용 (미완료).

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

## 7. 남은 작업

### 즉시 실행

- [x] MIN_EDGE_BPS 최적화 → 5bps 확정 (Phase 7.3h)
- [x] KRW/USDT 동적 환율 (dual-source, Phase 7.3d)
- [x] 1시간 Shadow 검증 완료 (Phase 7.3i)
- [x] 마찰력 모델 5건 수정 (Phase 7.3j)
- [x] 10min 3110T 100%WR 검증 (Phase 7.3k)
- [ ] Bithumb REST 스냅샷 + 증분 적용
- [ ] Maker Order 구현 검토 (마찰 15bps→4-10bps)

### 전략 확장

- [ ] funding_rate 활성화 (8시간 주기 차익)
- [ ] spot_futures 활성화 (현물-선물 베이시스)
- [ ] triangular 활성화 (3-거래쌍 순환)
- [ ] 멀티 전략 동시 실행 + 자본 배분 최적화

### 성능

- [ ] 신호→주문 레이턴시 프로파일링 (목표 <10ms)
- [ ] 7/7 거래소 WS 재연결 안정성 검증

### 72h Shadow (핵심 게이트)

- [ ] 72h 무중단 Shadow 실행 (0 crash, Sharpe>1.0, DD<5%)
- [ ] 72h 후 Walk-Forward 파라미터 재튜닝
- [ ] Telegram 24h 요약 3회 수신 확인

### Live 배포

- [ ] 실 거래소 API 키 검증 (Binance, Upbit, Bithumb)
- [ ] LiveGate 6-check 전체 통과
- [ ] Testnet 소액 거래 검증

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

---

## 9. 알려진 이슈

### HIGH

| 이슈 | 설명 | 완화책 |
|------|------|--------|
| Bithumb 증분 Orderbook | 스냅샷 없이 증분만 수신 → 허위 스프레드 | max_spread_pct=5.0 필터 |
| 마찰 vs Gross Spread | 대부분 알트 spread 2-25bps, friction ~20bps | MIN_EDGE_BPS=5 + 고스프레드 심볼 집중 |

### MEDIUM

| 이슈 | 설명 | 상태 |
|------|------|------|
| 전략 3개 미완성 | stat_arb(NOT_READY), latency_arb(파라미터 누락), futures_futures(신호 없음) | 미해결 |
| httpx 클라이언트 재생성 | 매 요청마다 httpx.AsyncClient 재생성 → 성능 | 미해결 |

### LOW

| 이슈 | 설명 | 상태 |
|------|------|------|
| Coinone Rate Limit | 30min PING keepalive 유지 실패 가능 | 자동 재연결 구현됨 |
| 빈 Orderbook 경고 | 타이밍 레이스 (collector 전 신호 평가) | crash 없음, 신호 무시 |
| cex_dex 미구현 | _build_dex_adapter() 항상 None | DEX_RPC_URL 무시됨 |

### RESOLVED

| 이슈 | 해결 |
|------|------|
| MIN_EDGE_BPS 최적화 | 5bps 확정 (Phase 7.3h) |
| _krw_rate=0 ZeroDivisionError | fallback 1380 가드 추가 (Phase 7.3f) |
| KRW/USDT 정적 환율 | dual-source 동적 조회 구현 (Phase 7.3d) |
| 이중 슬리피지 | PaperExecutor ZERO slippage 적용 (Phase 7.3j) |
| PowerLawSlippage k/gamma 무시 | 실제 공식 적용 완료 (Phase 3.5) |
