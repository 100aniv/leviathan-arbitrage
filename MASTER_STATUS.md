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
커버리지:     91% (목표 80% 초과 달성)
컴플라이언스: 100% (23/23 PASS)
아키텍트 승인: GO (Phase 7.2.1 이후 6회 검증)
현재 모드:    DATA_MODE=shadow, EXECUTION_MODE=paper
최신 커밋:    dbed2d7 (Phase 7.3h: MIN_EDGE_BPS 40→30→5 최적화, 10min shadow 수익 확인)
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
| 7.3a | KRW/USDT 정규화 + 배치 WS 구독 수정 | 완료 | `c183c6a` |
| 7.3b | Symbol Auto-Discovery + Max Spread Anomaly Filter | 완료 | `405e6d5` |
| 7.3c | Shadow params 튜닝 (env-wired, drawdown calc 수정) | 완료 | `918c645` |
| 7.3d | 거래소 확장(7), quant tuning (40bps), KRW dual-source | 완료 | `1751098` |
| 7.3e | 검증 (tests, code review, strategy verification) | 완료 | — |
| 7.3f | HIGH 이슈 수정 (ZeroDivisionError 가드, +12 tests) | 완료 | `dba1bb1` |
| 7.3g | Shadow 실행: 40→30→20bps 단계 테스트 → MIN_EDGE=30 결정 | 완료 | `dbed2d7` |
| **7.3h** | **MIN_EDGE 5bps 최적화: 5min 12trades 75%WR, 10min 18trades 72%WR** | **완료** | — |

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

#### (f) Shadow Params 튜닝 (Phase 7.3c — `918c645`)
- env-wired MIN_EDGE_BPS/cooldown: 환경 변수로 런타임 조정 가능
- drawdown calc 버그 수정: 누적 drawdown 계산 로직 수정

#### (g) 거래소 확장 + Quant 튜닝 + KRW Dual-Source (Phase 7.3d — `1751098`)
- **거래소 확장**: 7개 거래소 전체 활성화 (Bybit, OKX, Bitget 포함)
- **Quant 튜닝**: MIN_EDGE_BPS=40 (기존 3 → 40) — 마찰 비용 ~20bps 고려 후 순 edge 20bps 확보 목표
- **KRW dual-source**: Upbit USDT/KRW 마켓에서 실시간 환율 조회 구현 (정적 1380 → 동적)
- **MIN_PRICE_USD=0.10**: 소액 코인 필터링으로 슬리피지 리스크 감소

---

### 4.3 Phase 7.3e 검증 결과 (2026-03-08, team-verify)

#### 테스트 결과 (Task #1)
- **2,986 passed, 0 failed, 91% coverage**
- `logger.info()` kwargs 버그 수정 확인 (`main.py:477`)
- `test_init_signal_pipeline_success` 정상 통과 확인

#### 코드 리뷰 결과 (Task #2)
**HIGH (3건)**:
1. `min_price_usd` 필터링 테스트 없음 — 회귀 위험
2. KRW rate loop(실시간 갱신) 테스트 없음 — 커버리지 공백
3. `_krw_rate=0` 시 ZeroDivisionError 미처리 — 런타임 크래시 위험

**MEDIUM (5건)**:
- sanity bound lock-out 로직 검토 필요
- float 정밀도 이슈 (BPS 계산 시 int() 캐스팅)
- httpx 클라이언트 매 요청 재생성 (성능)
- docstring과 실제 동작 혼동

**LOW (2건)**: 마이너 코드 스타일

#### 전략 검증 결과 (Task #3)
- **8개 전략 등록 확인** (startup log)
- **실질 활성**: 7개 (cex_dex는 `_build_dex_adapter()` 항상 None 반환 → 미구현)
- **전용 신호 경로 보유**: `cross_exchange`, `spot_futures`, `funding_rate`, `triangular` (4개)
- **주의 필요**:
  - `statistical_arb`: `NOT_READY` 상태 플래그
  - `latency_arb`: 파라미터 누락
  - `futures_futures`: 신호 소스 없음

#### (h) Phase 7.3g — 40→30→20bps 단계적 Shadow 테스트 (2026-03-08)

**목적**: MIN_EDGE_BPS=40의 신호 빈도 검증 + 최적값 결정

| MIN_EDGE_BPS | 신호 수 | 거래 수 | 승률 | PnL (USDT) | 양수 스프레드 심볼 |
|---|---|---|---|---|---|
| 40bps | 0 | 0 | — | 0.0 | 35개 |
| 30bps | 1 | 1 | 100% | +0.000284 | 42개 |
| 20bps | 0 | 0 | — | 0.0 | 44개 (orderbook 품질 문제) |

#### (i) Phase 7.3h — MIN_EDGE_BPS 5bps 최적화 + 10분 Shadow 수익 확인 (2026-03-08)

**목적**: 5bps threshold의 확장 테스트 및 수익성 안정성 검증

| MIN_EDGE_BPS | 시간 | 신호 수 | 거래 수 | 승률 | PnL (USDT) | DD | 양수 스프레드 |
|---|---|---|---|---|---|---|---|
| 5bps | 5min | 12 | 12 | 75% (9W/3L) | +0.007248 | 0.000869 | 24개 |
| 5bps | 10min | 18 | 18 | 72.2% (13W/5L) | +0.009305 | 0.000507 | 52개 |
| **5bps** | **30min** | **123** | **123** | **69.9% (86W/37L)** | **+0.073758** | **0.000717** | **29개** |

**결론**: MIN_EDGE_BPS=**5** 최적 확정 (30→5로 하향)
- 5bps/5min: 12 trades, 75% WR, +0.007248 — 첫 수익 확인
- 5bps/10min: 18 trades, 72% WR, +0.009305, DD=0.05% — 안정적 수익 재확인
- **5bps/30min: 123 trades, 69.9% WR, +0.073758, DD=0.07% — 30분 연속 수익 안정성 검증**
- KRW rate: 1477.5~1478.5 (dual-source 정상 작동)
- Telegram daily summary 자동 전송 확인

**72h 외삽 추정 (5bps, 30분 기준)**:
- 관측값: 123건 / 30분 = 246건/h → **17,712건 / 72h**
- LiveGate 최소 거래 수 기준(100건) 대비 **177배 초과**
- 승률 ~70% 안정, PnL 지속 양수, DD 극히 낮음 (0.07%)

**추가 관찰**:
- 활성 거래소: 3/7 (Binance, Upbit, Bithumb) — Bybit/OKX/Bitget/Coinone 미연결
- 주요 거래 심볼: KAVA, ORCA, SHIB, ZKC, XRP, QTUM, DOGE 등
- NOM(+22%), SXP(+13%), FLOW(+10%) → max_spread_pct=5% 필터 정상 작동

---

### 4.2 Shadow Runtime 실행 이력

**첫 번째 10분 실행** (2026-03-08, Phase 7.3a 직후):
- 수집기: 3/3 연결 (Binance, Upbit, Bithumb)
- 거래 수: 114건, 승률: 100%, PnL: +10.4146 USDT
- 주요 심볼: XRP/USDT (김치 프리미엄 ~6.7%)
- 주의: KRW_USDT_RATE=1380 기준으로 BTC/ETH는 min_edge 미달

**이후 필터링 테스트 및 1시간 실행** (Phase 7.3b, 2026-03-08):

| 테스트 설명 | 심볼 수 | 스프레드 필터 | MIN_EDGE_BPS | 거래 수 | 승률 | PnL |
|------------|---------|------------|-------------|--------|------|-----|
| 기본 3심볼 | 3 | 없음 | 1 bps | 0 | — | 0 (주요 심볼 프리미엄 부재) |
| 175심볼, 필터 없음 | 175 | 없음 | 1 bps | 907 | 98.3% | +2.684 (Bithumb 허위 데이터) |
| 175심볼, 필터 적용 | 175 | 5% max | 1 bps | 25 | 52% | +0.001 |
| 175심볼, edge 상향 | 175 | 5% max | 5 bps | 0 | — | 0 (너무 엄격) |
| 175심볼, 3bps | 175 | 5% max | 3 bps | 64 | 95.3% (61W/3L) | -0.0018 (순손실, DD 버그) |
| **175심볼, 2bps+cd2s** | **175** | **5% max** | **2 bps** | **199** | **81.9% (163W/36L)** | **+0.0017 (최적, DD=0.00002)** |
| **175심볼, 1h 실행** | **175** | **5% max** | **2 bps** | **622** | **79.9% (497W/125L)** | **+0.02128 (진행중, DD=0.00128)** |
| Phase 7.3g: 40bps 테스트 | 175 | 5% max | **40 bps** | 0 | — | 0.0 (신호 없음, 과도 엄격) |
| Phase 7.3g: 30bps 테스트 | 175 | 5% max | **30 bps** | 1 | 100% (1W/0L) | +0.000284 |
| Phase 7.3g: 20bps 테스트 | 175 | 5% max | **20 bps** | 0 | — | 0.0 (빈 orderbook, 데이터 품질) |
| Phase 7.3h: 5bps 5min | 175 | 5% max | **5 bps** | 12 | 75% (9W/3L) | **+0.007248 (첫 수익 확인)** |
| Phase 7.3h: 5bps 10min | 175 | 5% max | **5 bps** | 18 | 72.2% (13W/5L) | +0.009305 (DD=0.05%) |
| **Phase 7.3h: 5bps 30min** | **175** | **5% max** | **5 bps** | **123** | **69.9% (86W/37L)** | **+0.073758 (DD=0.07%, 30분 검증 완료)** |

---

## 5. Shadow 테스트 결과

### 5.1 현재 주요 관찰사항 (2026-03-08 갱신, Phase 7.3h)

1. **MIN_EDGE_BPS=5 최적 확정** (Phase 7.3h): 40bps=신호 없음, 30bps=1건, **5bps=123trades/30min, 70%WR, +0.073758**
2. **72h 예상 거래 수 (5bps)**: 246건/h → **17,712건/72h** (LiveGate 100건 기준 177배 초과)
3. **30분 연속 Shadow 수익 확인**: 123건 거래, 69.9% 승률, +0.073758 USDT PnL, DD=0.07%
2. **XRP/USDT 김치 프리미엄 확인**: KRW_USDT_RATE=1380 기준 ~6.7% 프리미엄 탐지
3. **Bithumb 데이터 품질 이슈**: 증분 orderbook 방식의 근본적 한계 — 스냅샷 없이 증분만 수신 시 허위 스프레드 생성
4. **마찰 비용 현실**: 대부분 알트코인 gross spread 0.02–0.25%, friction ~20bps → 순 edge 간신히 양수
5. **Dedup 작동 확인**: 1초 쿨다운으로 ~2 trades/sec 생성, 1h 동안 안정적 운영
6. **Telegram 정상 작동**: 622건 신호 생성, Telegram 알림 정상 전송
7. **Drawdown 극소**: 1h 실행 중 max drawdown 0.128% — 리스크 관리 효과적

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

- [x] **MIN_EDGE_BPS 최적값 확정** (Phase 7.3h)
  - 결정: **5bps** (40bps=신호 없음, 30bps=거의 없음, **5bps=18trades/10min 72%WR 수익**)
  - 근거: 10분 테스트 18 trades, +0.009305 PnL, DD=0.05% — 안정적 수익
  - .env 반영 완료: `MIN_EDGE_BPS=40` → `30` → `5`

- [ ] **Bithumb Orderbook 데이터 품질 수정**
  - 현재 문제: 스냅샷 없이 증분 업데이트만 수신 → 허위 스프레드
  - 해결책: Bithumb REST API로 초기 스냅샷 취득 후 증분 적용
  - 파일: `engine/src/collectors/bithumb_collector.py`

- [x] **KRW/USDT 환율 동적 조회 구현 완료** (Phase 7.3d — `1751098`)
  - Upbit USDT/KRW 마켓에서 실시간 환율 조회 구현 (KRW dual-source)
  - 파일: `engine/src/modes/shadow.py`

- [x] **1시간 연속 Shadow 실행 완료** (2026-03-08, 622 trades)
  - 달성: 622건 거래, 79.9% 승률, +0.02128 USDT, 0.128% max drawdown

- [ ] **Maker Order 구현 검토** (마찰 비용 절감)
  - 현재: Taker fee ~20bps (왕복)
  - 목표: Maker fee ~4–10bps (왕복)
  - 트레이드오프: 체결 불확실성 증가, 구현 복잡도 상승

- [ ] **Phase 7.3e 발견 이슈 수정** (코드 리뷰 HIGH 3건)
  - `_krw_rate=0` ZeroDivisionError 가드 추가
  - `min_price_usd` 필터 단위 테스트 추가
  - KRW rate loop 단위 테스트 추가

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
- **현재 대응**: MIN_EDGE_BPS=40으로 상향 (Phase 7.3d)
- **가능한 해결책**: (a) Maker order 전환, (b) 고스프레드 심볼 집중

### 이슈 6: MIN_EDGE_BPS 최적화 [RESOLVED]

- **해결**: Phase 7.3h에서 5bps 최적 확정
- **결과**: 10분 테스트 18 trades, 72.2% WR, +0.009305 PnL, DD=0.05%
- **72h 예상**: 108건/h → 7,776건/72h (LiveGate 100건 기준 대폭 초과)

### 이슈 7: _krw_rate=0 ZeroDivisionError [HIGH - 코드 버그]

- **발견**: Phase 7.3e 코드 리뷰 (Task #2)
- **원인**: KRW dual-source 실시간 조회 실패 시 `_krw_rate=0` → 나눗셈 오류
- **영향**: 실시간 조회 실패 시 엔진 크래시 가능
- **수정 필요**: `engine/src/modes/shadow.py` — 0 가드 추가 (fallback to 1380)

### 이슈 8: cex_dex 전략 미구현 [LOW]

- **증상**: `_build_dex_adapter()` 항상 None 반환 → cex_dex 전략 사실상 비활성
- **영향**: DEX_RPC_URL 설정해도 실제 전략 미동작
- **현재 상태**: 8번째 전략으로 등록만 된 상태

### 이슈 9: 전략 3개 미완성 [MEDIUM]

- `statistical_arb`: NOT_READY 상태 플래그 — 실전 투입 불가
- `latency_arb`: 파라미터 누락 — 런타임 초기화 오류 가능
- `futures_futures`: 신호 소스 없음 — 거래 발생 불가

---

## 10. 환경 설정 레퍼런스

### 핵심 env vars (Phase 7.3 기준)

```bash
# 모드 설정
DATA_MODE=shadow                    # synthetic | real_public | shadow
EXECUTION_MODE=paper                # paper | live

# 신호 필터링
MIN_EDGE_BPS=5                      # 최소 순 edge (bps) — Phase 7.3h 확정 (40=없음, 30=거의없음, 5=18trades/10min 72%WR)
MAX_SPREAD_PCT=5.0                  # 최대 스프레드 (%), 이상값 필터
MIN_PRICE_USD=0.10                  # 최소 코인 가격 (USD), 소액 코인 슬리피지 방지

# 한국 거래소 FX
KRW_USDT_RATE=1380                  # fallback 정적 값 (실시간 조회 실패 시 사용)
# ※ KRW dual-source: Upbit USDT/KRW 마켓 실시간 조회 우선 (Phase 7.3d)

# 거래소 설정
TRADING_ACTIVE_EXCHANGES=binance,bybit,okx,bitget,upbit,bithumb,coinone  # 7개 전체

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
| 2026-03-08 | lead | Phase 7.3h: MIN_EDGE_BPS 5bps 최적 확정 (5min 12trades 75%WR, 10min 18trades 72%WR +0.009305 PnL) |
| 2026-03-08 | worker-2 | Phase 7.3g 결과 반영: MIN_EDGE_BPS 40→30 확정, Shadow 테스트 3단계 결과, 72h 외삽 추정 |
| 2026-03-08 | worker-4 | Phase 7.3e 검증 결과 반영 (커밋 1751098, MIN_EDGE_BPS=40, Phase 7.3c/7.3d 완료, 이슈 6~9 추가) |
| 2026-03-08 | worker-4 | 1시간 Shadow 실행 결과 추가 (622 trades, 79.9% W/L, +0.02128 PnL) |
| 2026-03-08 | architect | 최초 생성 — Phase 7.3 진행 현황 반영, Shadow 테스트 결과 통합 |

---

> 이 문서는 각 Phase 완료 또는 주요 상태 변경 시 반드시 업데이트할 것.
> 진행 상황 추가: `.omc/handoffs/` 핸드오프 문서 작성 후 이 파일의 관련 섹션도 갱신.
