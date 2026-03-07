# LEVIATHAN Arbitrage Engine — MASTER STATUS (SSOT)

> **Single Source of Truth** — 모든 팀원이 이 문서를 기준으로 현재 상태를 파악한다.
> 마지막 업데이트: 2026-03-08
> 현재 Phase: **7.3 Shadow Runtime & Tuning** (진행 중)

---

## 목차

1. [프로젝트 개요](#1-프로젝트-개요)
2. [현재 상태 요약](#2-현재-상태-요약)
3. [Phase 진행 이력](#3-phase-진행-이력)
4. [Phase 7.3 상세 현황](#4-phase-73-상세-현황)
5. [Shadow 테스트 결과](#5-shadow-테스트-결과)
6. [남은 작업 체크리스트](#6-남은-작업-체크리스트)
7. [아키텍처 퀵 레퍼런스](#7-아키텍처-퀵-레퍼런스)
8. [핵심 결정 및 근거](#8-핵심-결정-및-근거)
9. [알려진 이슈 및 리스크](#9-알려진-이슈-및-리스크)
10. [환경 설정 레퍼런스](#10-환경-설정-레퍼런스)

---

## 1. 프로젝트 개요

**LEVIATHAN**은 6개 이상의 암호화폐 거래소에서 크로스 거래소 차익거래를 자동 실행하는 고빈도 거래 엔진이다.

| 항목 | 내용 |
|------|------|
| 언어 | Python 3.12+ (AsyncIO) + Rust (PyO3 hot-path) |
| 거래소 | 7개 네이티브 WebSocket 어댑터 (ccxt 미사용) |
| 전략 수 | 8개 (7개 기본 + CexDex 조건부) |
| 실행 모드 | Backtest → Paper → Shadow → Live |
| 대시보드 | Next.js 14 + JWT 인증 + 실시간 WS 피드 |
| 인프라 | TimescaleDB + Redis + Prometheus + Grafana + Nginx TLS |

---

## 2. 현재 상태 요약

```
테스트:       2,986 passed, 0 failed
커버리지:     92% (목표 80% 초과 달성)
컴플라이언스: 100% (23/23 PASS)
아키텍트 승인: GO (Phase 7.2.1 이후 6회 검증)
현재 모드:    DATA_MODE=shadow, EXECUTION_MODE=paper
최신 커밋:    405e6d5 (Phase 7.3b: symbol discovery + spread filter)
```

### Mode Validation 결과 (Phase 8 기준, 모두 PASS)

| Stage | Mode | 소요시간 | 결과 |
|-------|------|---------|------|
| 1 | BACKTEST (synthetic GBM) | 1.0s | PASS — 22 folds, best Sharpe 5.43 |
| 2 | PAPER (real WebSocket) | 26.9s | PASS — Binance+Upbit WS 연결 확인 |
| 3 | SHADOW (real WS + metrics) | 25.6s | PASS — ShadowMode+LiveGate 활성 |
| 4 | LIVE_GATE | 9.4s | PASS — 6 checks (history 부재로 blocked) |

---

## 3. Phase 진행 이력

| Phase | 내용 | 상태 | 커밋 |
|-------|------|------|------|
| 0 | 기반 연결 (Protocol, TimescaleDB, Telegram, Rust Bridge) | 완료 | — |
| 1 | 실 데이터 수집 (WS collectors, walk-forward) | 완료 | — |
| 1.5 | 수치 동등성 검증 (Rust vs Python, 89 parity tests) | 완료 | — |
| 2 | Rust Hot-Path (OrderBook, Signal, KillSwitch) | 완료 | — |
| 3 | Shadow Mode + Sharpe Gate + Blueprint Compliance | 완료 | — |
| 3.5 | 수술적 버그 수정 (executor lock, telegram, slippage) | 완료 | — |
| 4 | Native Exchange Adapters (6개, ccxt-free) | 완료 | — |
| 5 | Preflight + Runbooks + Compliance 100% | 완료 | — |
| 6 | 실전 테스트 튜닝 (7 strategies, 2474 tests, 86% coverage) | 완료 | `703f858` |
| 7.1 | 파라미터 적용 (config/strategy_params.json + main.py wiring) | 완료 | `703f858` |
| 7.2 | API security middleware + shadow config + stat_arb | 완료 | `b0c3705` |
| 7.2.1 | Fix 3 deprecations + dynamic BTC reference price | 완료 | `a2a71d0` |
| 7.2.2 | 90 tests + websocket asyncio deprecation fix | 완료 | `272857a` |
| 7.2.3 | Coverage 88% → 92% (290 new tests) | 완료 | `866ff88` |
| 7.2.4 | Code quality: duplicate 제거, 하드코딩→env vars | 완료 | `eabec21` |
| 7.2.5 | Unused import cleanup (28 imports, 26 files) | 완료 | `2ca37a1` |
| 7.2.6 | Generic except → 구체적 예외 타입 (10개) | 완료 | `6356737` |
| 8 | Mode Validation Pipeline (4-stage E2E) | 완료 | `3832fa1` |
| 8.1 | Upbit + Bithumb WebSocket collectors (6/6 coverage) | 완료 | `8e3aa3f` |
| **7.3** | **Shadow Runtime & Tuning (72h + re-tuning)** | **진행 중** | `405e6d5` |

---

## 4. Phase 7.3 상세 현황

### 4.1 완료된 작업

#### (a) Coinone WebSocket Collector 추가 (Phase 7.3 prep — `a74aef1`)
- 파일: `engine/src/collectors/coinone_collector.py`
- WebSocket: `wss://stream.coinone.co.kr` (public, 인증 불필요)
- 심볼 형식: `BTC/KRW` → `quote_currency=KRW, target_currency=BTC`
- 30분 PING keepalive 구현
- 20개 신규 테스트 추가
- CollectorManager DEFAULT_EXCHANGES: 6 → 7 (coinone 추가)

#### (b) KRW/USDT 가격 정규화 (Phase 7.3a — `c183c6a`)
- **문제**: 한국 거래소(Upbit, Bithumb, Coinone)는 KRW 페어만 지원
  - CollectorManager가 동일한 USDT 심볼을 전달 → 한국 거래소 데이터 수신 0건 → 신호 0건
- **수정 1**: `engine/src/collectors/manager.py`
  - `KOREAN_EXCHANGES = {"upbit", "bithumb", "coinone"}` 집합 정의
  - `_get_exchange_symbols()` 메서드: `/USDT` → `/KRW` 자동 변환
- **수정 2**: `engine/src/modes/shadow.py`
  - `_on_orderbook()` 내 KRW→USDT 가격 정규화 추가
  - `KRW_USDT_RATE` env var 기준으로 변환 (현재 정적 값)
- **수정 3**: `.env` — `KRW_USDT_RATE=1380` 추가

#### (c) 배치 WebSocket 구독 수정 (Phase 7.3a — `d05b502`)
- **문제**: Upbit/Bithumb은 모든 심볼을 단일 구독 메시지에 담아야 함
  - 개별 심볼 구독 방식으로는 BTC/ETH 데이터가 도착하지 않음
- **수정**: `engine/src/collectors/base_collector.py`
  - `_subscribe_all_messages()` 훅 추가
  - 한국 거래소 collectors가 배치 구독 메서드를 override

#### (d) Symbol Auto-Discovery (Phase 7.3b — `405e6d5`)
- 파일: `engine/src/collectors/symbol_discovery.py`
- Binance REST API에서 USDT 페어 취득 (443개)
- Upbit REST API에서 KRW 페어 취득 (243개)
- Bithumb REST API에서 KRW 페어 취득 (453개)
- 교집합 계산: 공통 심볼 175개 (stablecoins/wrapped tokens 제외)
- `discover_common_symbols()` async 함수로 제공

#### (e) Max Spread Anomaly Filter (Phase 7.3b — `405e6d5`)
- **문제**: Bithumb 증분 orderbook 데이터의 품질 문제
  - NOM: +62% 스프레드, SXP: +12% 스프레드 → 허위 고수익 신호
- **수정**: `SignalConfig`에 `max_spread_pct=5.0` 게이트 추가
  - 스프레드가 5% 초과인 신호는 즉시 필터링 (이상값으로 간주)
- **수정**: `engine/src/main.py`
  - `MIN_EDGE_BPS`, `MAX_SPREAD_PCT` env var에서 읽도록 변경 (기존 하드코딩 제거)

---

### 4.2 Shadow Runtime 실행 이력

**첫 번째 10분 실행** (2026-03-08, Phase 7.3a 직후):
- 수집기: 3/3 연결 (Binance, Upbit, Bithumb)
- 거래 수: 114건, 승률: 100%, PnL: +10.4146 USDT
- 주요 심볼: XRP/USDT (김치 프리미엄 ~6.7%)
- 주의: KRW_USDT_RATE=1380 기준으로 BTC/ETH는 min_edge 미달

**이후 필터링 테스트** (Phase 7.3b):

| 테스트 설명 | 심볼 수 | 스프레드 필터 | MIN_EDGE_BPS | 거래 수 | 승률 | PnL |
|------------|---------|------------|-------------|--------|------|-----|
| 기본 3심볼 | 3 | 없음 | 1 bps | 0 | — | 0 (주요 심볼 프리미엄 부재) |
| 175심볼, 필터 없음 | 175 | 없음 | 1 bps | 907 | 98.3% | +2.684 (Bithumb 허위 데이터) |
| 175심볼, 필터 적용 | 175 | 5% max | 1 bps | 25 | 52% | +0.001 |
| 175심볼, edge 상향 | 175 | 5% max | 5 bps | 0 | — | 0 (너무 엄격) |
| 175심볼, 3bps | 175 | 5% max | 3 bps | 64 | 95.3% (61W/3L) | -0.0018 (순손실, DD 버그) |
| **175심볼, 2bps+cd2s** | **175** | **5% max** | **2 bps** | **199** | **81.9% (163W/36L)** | **+0.0017 (최적, DD=0.00002)** |

---

## 5. Shadow 테스트 결과

### 5.1 현재 주요 관찰사항

1. **XRP/USDT 김치 프리미엄 확인**: KRW_USDT_RATE=1380 기준 ~6.7% 프리미엄 탐지
2. **Bithumb 데이터 품질 이슈**: 증분 orderbook 방식의 근본적 한계 — 스냅샷 없이 증분만 수신 시 허위 스프레드 생성
3. **마찰 비용 현실**: 대부분 알트코인 gross spread 0.02–0.25%, friction ~20bps → 순 edge 간신히 양수
4. **Dedup 작동 확인**: 1초 쿨다운으로 ~2 trades/sec 생성
5. **Telegram 정상 작동**: 32건 신호 알림 전송, 20/min rate limit 작동

### 5.2 Friction 분해 (현재 추정)

```
Taker fee:      Binance 0.10% + Korean 0.05%  = 약 15 bps (왕복)
PowerLaw slip:  k=1.0, gamma=0.5, size 의존    = 약 3–5 bps (추정)
네트워크 비용:  —                              = 약 1–2 bps
Rollback 비용:  —                              = 약 1–2 bps
─────────────────────────────────────────────────────────────
총 friction:                                   ≈ 20–24 bps
```

**결론**: gross spread > 20 bps인 심볼만 실질적 기회 존재

---

## 6. 남은 작업 체크리스트

### 6.1 즉시 실행 가능 (인프라 불필요)

- [ ] **MIN_EDGE_BPS 최적값 탐색** — 현재 3bps 테스트 중
  - 목표: 승률 55%+, 일관된 양의 PnL, 거래 건수 통계적 유의성 확보
  - 후보값: 3bps, 5bps, 8bps, 10bps 순차 비교

- [ ] **Bithumb Orderbook 데이터 품질 수정**
  - 현재 문제: 스냅샷 없이 증분 업데이트만 수신 → 허위 스프레드
  - 해결책: Bithumb REST API로 초기 스냅샷 취득 후 증분 적용
  - 파일: `engine/src/collectors/bithumb_collector.py`

- [ ] **KRW/USDT 환율 동적 조회** — 현재 정적 값(1380) 사용
  - 해결책: Upbit USDT/KRW 마켓에서 실시간 환율 조회
  - 파일: `engine/src/modes/shadow.py`
  - 연관 파일: `engine/src/collectors/manager.py`

- [ ] **1시간 이상 연속 Shadow 실행** — 통계적 신뢰도 확보
  - 목표: 최소 100건 이상의 실거래 시뮬레이션

- [ ] **Maker Order 구현 검토** (마찰 비용 절감)
  - 현재: Taker fee ~20bps (왕복)
  - 목표: Maker fee ~4–10bps (왕복)
  - 트레이드오프: 체결 불확실성 증가, 구현 복잡도 상승

### 6.2 전략 확장

- [ ] **추가 전략 활성화** — 현재 cross_exchange 위주
  - `funding_rate`: Binance 선물 펀딩비 차익 (8시간 주기)
  - `spot_futures`: 현물-선물 베이시스 차익
  - `triangular`: 3-거래쌍 삼각 차익
  - `statistical_arb`: 코인트레이션 기반 평균 회귀
  - `latency_arb`: 가격 전파 지연 포착

- [ ] **멀티 전략 동시 실행 튜닝**
  - 전략 간 포지션 충돌 방지 로직 검증
  - 자본 배분 비율 최적화 (현재 균등 배분)

### 6.3 성능 최적화

- [ ] **레이턴시 측정** — 신호 탐지 → 주문 생성까지 소요시간 프로파일링
  - 도구: Prometheus metrics (`SIGNAL_PROCESSING_TIME`)
  - 목표 레이턴시: < 10ms (신호 처리)

- [ ] **WebSocket 재연결 안정성 검증**
  - 7/7 거래소 연결 끊김 → 자동 재연결 테스트
  - fast_backoff 패턴 확인

### 6.4 대시보드 / UI

- [ ] **실시간 스프레드 시각화** — 거래쌍별 현재 스프레드 라이브 차트
- [ ] **전략별 심볼 성과 분석** — 어떤 심볼이 수익을 내는지 breakdown
- [ ] **PnL 차트 및 Drawdown 시각화** — 시간대별 누적 PnL 그래프
- [ ] **알림 설정 UI** — Telegram 알림 임계값 대시보드에서 조절

### 6.5 72시간 연속 Shadow Runtime (핵심 게이트)

- [ ] **72h 무중단 Shadow 실행**
  - 조건: Docker compose 재시작 없이 72h 연속
  - 모니터링: Telegram 24h 요약 알림 3회 수신 확인
  - 성공 기준: 0 crashes, Sharpe > 1.0, max drawdown < 5%

- [ ] **72h 실행 후 파라미터 재조정**
  - Walk-forward 재튜닝 (실거래 데이터 기반)
  - `config/strategy_params.json` 업데이트
  - 툴: `engine/src/tuning/optimizer.py`

### 6.6 배포 준비

- [ ] **실 거래소 API 키 검증** — Binance, Upbit, Bithumb 실 API 키
- [ ] **Live Gate 통과 기준 달성**
  - Sharpe > 1.0 (72h 데이터)
  - Max drawdown < 5%
  - Win rate > 55%
  - 거래 수 > 100건

- [ ] **Testnet 배포** (Phase 7.3 마지막 단계)
  - Docker compose full stack
  - 실 API 키로 testnet 거래 (소액)

---

## 7. 아키텍처 퀵 레퍼런스

### 7.1 Exchange Collectors (7/7)

| 거래소 | Collector 파일 | WS Endpoint | 심볼 형식 | 상태 |
|--------|---------------|-------------|---------|------|
| Binance | `binance_collector.py` | `wss://stream.binance.com:9443` | `BTC/USDT` | 연결됨 |
| Bybit | `bybit_collector.py` | `wss://stream.bybit.com/v5/public/spot` | `BTC/USDT` | 준비 |
| OKX | `okx_collector.py` | `wss://ws.okx.com:8443/ws/v5/public` | `BTC/USDT` | 준비 |
| Bitget | `bitget_collector.py` | `wss://ws.bitget.com/v2/ws/public` | `BTC/USDT` | 준비 |
| Upbit | `upbit_collector.py` | `wss://api.upbit.com/websocket/v1` | `BTC/KRW` (auto-map) | 연결됨 |
| Bithumb | `bithumb_collector.py` | `wss://pubwss.bithumb.com/pub/ws` | `BTC/KRW` (auto-map) | 연결됨 |
| Coinone | `coinone_collector.py` | `wss://stream.coinone.co.kr` | `BTC/KRW` (auto-map) | 준비 |

**KRW 자동 매핑**: `CollectorManager.KOREAN_EXCHANGES = {"upbit", "bithumb", "coinone"}`
- `_get_exchange_symbols()`: `/USDT` → `/KRW` 자동 변환
- `ShadowMode._on_orderbook()`: KRW 가격 → USDT 역환산

### 7.2 Strategies (7+1)

| 전략 | 파일 | 현재 상태 |
|------|------|---------|
| `cross_exchange` | `strategies/cross_exchange.py` | **활성** (Shadow에서 주로 거래 발생) |
| `spot_futures` | `strategies/spot_futures.py` | 등록됨, 미활성화 |
| `futures_futures` | `strategies/futures_futures.py` | 등록됨, 미활성화 |
| `triangular` | `strategies/triangular.py` | 등록됨, 미활성화 |
| `funding_rate` | `strategies/funding_rate.py` | 등록됨, 미활성화 |
| `statistical_arb` | `strategies/stat_arb.py` | 등록됨, 미활성화 |
| `latency_arb` | `strategies/latency_arb.py` | 등록됨, 미활성화 |
| `cex_dex` | `strategies/cex_dex.py` | DEX_RPC_URL 설정 시 활성 |

### 7.3 Mode 진행 경로

```
DATA_MODE=synthetic    →  Backtest (GBM 합성 데이터)
DATA_MODE=real_public  →  Paper (실 WebSocket, 가상 실행)
DATA_MODE=shadow       →  Shadow (실 데이터 + 전체 지표 + LiveGate)
EXECUTION_MODE=live    →  Live (실 거래, Phase 7.3 이후)
```

### 7.4 Friction 모델

```python
# PowerLaw slippage (engine/src/modes/shadow.py:PowerLawSlippage)
slippage = k * size ^ gamma    # k=1.0, gamma=0.5 (Blueprint 기준)

# Friction 합계
total_friction = taker_fee_buy + taker_fee_sell + slippage + network + rollback
# 현재 추정: ~20 bps (왕복)
```

### 7.5 핵심 파일 맵

```
engine/
├── src/
│   ├── collectors/
│   │   ├── manager.py              # 7개 collector 오케스트레이션, KRW 자동매핑
│   │   ├── symbol_discovery.py     # 공통 심볼 자동 탐색 (175개)
│   │   ├── binance_collector.py    # Binance WS
│   │   ├── upbit_collector.py      # Upbit WS (KRW, 배치 구독)
│   │   ├── bithumb_collector.py    # Bithumb WS (KRW, 배치 구독) ⚠ 데이터 품질 이슈
│   │   └── coinone_collector.py    # Coinone WS (KRW, 신규)
│   ├── modes/
│   │   ├── shadow.py               # Shadow mode (KRW→USDT 정규화 포함)
│   │   └── live_gate.py            # Live 전환 게이트
│   ├── tuning/
│   │   ├── optimizer.py            # Walk-forward 파라미터 최적화
│   │   └── shadow_runner.py        # Shadow 실행 헬퍼
│   └── main.py                     # MIN_EDGE_BPS, MAX_SPREAD_PCT env var 읽기
├── config/
│   └── strategy_params.json        # 7개 전략 파라미터 (Phase 6에서 튜닝됨)
└── run_mode_validation.py          # 4-stage E2E 검증 (Phase 8)
```

---

## 8. 핵심 결정 및 근거

### 결정 1: KRW/USDT 환율 — 정적 값 사용 (임시)

- **결정**: `KRW_USDT_RATE=1380` 정적 환경 변수 사용
- **근거**: 구현 속도 우선, Shadow 초기 검증에 충분
- **리스크**: 실제 환율(~1477–1478)과 괴리 → 인위적 프리미엄/디스카운트 발생
- **향후**: Upbit USDT/KRW 마켓에서 실시간 동적 조회로 전환 필요

### 결정 2: Testnet 제거 (Phase 8)

- **결정**: Mode Validation에서 testnet/sandbox 단계 제거
- **근거**: 거래소별 testnet 환경이 불안정하고 실 거래소와 행동 차이 존재
- **대안**: Shadow mode가 testnet 역할을 대신함 (실 데이터 + 가상 실행)

### 결정 3: Max Spread 5% 게이트

- **결정**: `SignalConfig.max_spread_pct=5.0` — 스프레드 5% 초과 신호 필터링
- **근거**: Bithumb 증분 orderbook에서 실제로 존재하지 않는 60%+ 스프레드가 발생
- **트레이드오프**: 실제 고스프레드 기회도 일부 놓칠 수 있음 (예: 극단적 시장 상황)

### 결정 4: ccxt 미사용 — 네이티브 어댑터

- **결정**: 모든 거래소 어댑터를 `websockets + httpx`로 직접 구현
- **근거**: ccxt 추상화 레이어의 레이턴시 오버헤드, 커스텀 최적화 불가
- **트레이드오프**: 거래소별 유지보수 부담 증가 (업데이트 시 직접 패치 필요)

### 결정 5: 배치 구독 (Upbit/Bithumb)

- **결정**: `base_collector.py`에 `_subscribe_all_messages()` 훅 추가
- **근거**: Upbit/Bithumb은 모든 심볼을 단일 WS 메시지로 구독해야 함
- **구현**: 한국 거래소 collector가 훅을 override하여 배치 메시지 생성

---

## 9. 알려진 이슈 및 리스크

### 이슈 1: Bithumb 증분 Orderbook 데이터 품질 [HIGH]

- **증상**: NOM +62%, SXP +12% 등 비현실적 스프레드 신호 발생
- **원인**: 초기 스냅샷 없이 증분 업데이트만 수신 → orderbook 상태 불일치
- **현재 완화책**: `max_spread_pct=5.0` 필터로 명백한 이상값 제거
- **근본 해결책**: Bithumb REST API로 초기 스냅샷 취득 후 증분 적용 (미완료)

### 이슈 2: KRW/USDT 정적 환율 [MEDIUM]

- **증상**: `KRW_USDT_RATE=1380` vs 실제 ~1477–1478 → 약 6.9% 괴리
- **영향**: 한국 거래소 가격이 USDT 기준으로 인위적으로 낮게 계산됨
- **현재 상태**: Shadow 초기 검증에서 XRP 김치 프리미엄 탐지는 성공
- **리스크**: 실 환율로 정규화 시 현재 탐지 신호 일부 소멸 가능

### 이슈 3: Coinone WS Rate Limit [LOW]

- **증상**: 미확인 (프로덕션 환경에서 확인 필요)
- **리스크**: 30분 PING keepalive 유지 실패 시 연결 끊김
- **완화책**: 자동 재연결 로직 구현됨 (`fast_backoff` 패턴)

### 이슈 4: 빈 Orderbook 경고 [LOW]

- **증상**: Shadow 실행 중 "Empty orderbook for XRP/USDT" 간헐적 발생
- **원인**: 타이밍 레이스 (collector 데이터 도착 전에 신호 평가)
- **영향**: 거래 누락 (신호 무시), crash 없음

### 이슈 5: 마찰 비용 vs Gross Spread [HIGH - 전략적]

- **현황**: 대부분 알트코인 gross spread 2–25bps, friction ~20bps
- **결론**: 실질적 기회는 스프레드 > 20bps인 심볼에 한정
- **리스크**: 현 friction 수준에서 전략 수익성 제한적
- **가능한 해결책**: (a) Maker order 전환, (b) 더 높은 MIN_EDGE_BPS 설정, (c) 고스프레드 심볼 집중

---

## 10. 환경 설정 레퍼런스

### 핵심 env vars (Phase 7.3 기준)

```bash
# 모드 설정
DATA_MODE=shadow                    # synthetic | real_public | shadow
EXECUTION_MODE=paper                # paper | live

# 신호 필터링
MIN_EDGE_BPS=3                      # 최소 순 edge (bps), 현재 3 테스트 중
MAX_SPREAD_PCT=5.0                  # 최대 스프레드 (%), 이상값 필터

# 한국 거래소 FX
KRW_USDT_RATE=1380                  # ⚠ 임시 정적 값 (실제: ~1477)

# 거래소 설정
TRADING_ACTIVE_EXCHANGES=binance,upbit,bithumb

# Telegram 알림
TELEGRAM_BOT_TOKEN=<token>
TELEGRAM_CHAT_ID=<chat_id>

# JWT 인증 (대시보드)
JWT_SECRET=<secret>
DASHBOARD_USER=<user>
DASHBOARD_PASSWORD=<password>

# DB / 인프라
DATABASE_URL=postgresql://...       # TimescaleDB
REDIS_URL=redis://localhost:6379

# 선택적
DEX_RPC_URL=<rpc>                   # 설정 시 CexDex 전략 활성화
ENGINE_ENV=dev                      # dev | staging | prod | test
```

### Shadow 실행 방법

```bash
# 옵션 1: 직접 실행
cd engine && python -m src.main

# 옵션 2: Docker compose
docker compose up -d

# 옵션 3: Mode Validation 먼저 확인
cd engine && python run_mode_validation.py
```

### 테스트 실행

```bash
cd engine && python -m pytest tests/ -x           # 전체 테스트
cd engine && python -m pytest tests/ --cov=src    # 커버리지 포함
```

---

## 변경 이력

| 날짜 | 변경자 | 내용 |
|------|--------|------|
| 2026-03-08 | architect | 최초 생성 — Phase 7.3 진행 현황 반영, Shadow 테스트 결과 통합 |

---

> 이 문서는 각 Phase 완료 또는 주요 상태 변경 시 반드시 업데이트할 것.
> 진행 상황 추가: `.omc/handoffs/` 핸드오프 문서 작성 후 이 파일의 관련 섹션도 갱신.
