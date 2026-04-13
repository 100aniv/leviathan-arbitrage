# LEVIATHAN 라이브 테스트 배관(Plumbing) 검증 계획

> 작성: 2026-04-05 | 상태: DRAFT (사용자 확인 대기)
> 대상: API 키 보유 4개 거래소 x Live 가능 6개 전략 — 배관 end-to-end 검증
> 제외: 대시보드 (별도 Phase L 범위)

---

## 0. 코드베이스 조사 결과 요약

### 거래소 어댑터 현황 (place_order 구현 여부)

| 거래소 | 어댑터 종류 | place_order | 마켓 | API 키 |
|--------|------------|-------------|------|--------|
| Binance Spot | NativeAdapter | `_rest_place_order` | USDT | **보유** |
| Binance Futures | NativeAdapter | `_rest_place_order` | USDT | **보유** |
| Upbit | NativeAdapter | `_rest_place_order` | KRW | **보유** |
| Bithumb | NativeAdapter | `_rest_place_order` | KRW | **보유** |
| Coinone | **CCXTAdapter** | `ccxt.create_order` | KRW | **보유** |
| OKX/Bybit/Bitget | NativeAdapter | 구현됨 | - | **미보유** |

**위험 발견: Coinone은 유일한 CCXTAdapter 의존 거래소.** 나머지 3개는 native. ccxt 의존성이 런타임에 문제가 될 수 있음.

### 전략-거래소 매핑 (현재 API 키 기준)

| 전략 | 필요 조건 | 가능 거래소 조합 | 테스트 가능 |
|------|----------|-----------------|------------|
| **cross_exchange** | 2+ spot 거래소 | Binance+Upbit, Binance+Bithumb, Binance+Coinone, Upbit+Bithumb, Upbit+Coinone, Bithumb+Coinone | **YES (6 조합)** |
| **spot_futures** | 동일 거래소 spot+futures | Binance만 (spot+futures 둘 다 API 키 보유) | **YES (1 조합)** |
| **futures_futures** | 2+ futures 거래소 | Binance Futures만 보유 (1개) | **NO — 거래소 1개 부족** |
| **triangular** | 단일 거래소 3+ 페어 | Binance, Upbit, Bithumb, Coinone (각각) | **YES (4 조합)** |
| **funding_rate** | 2+ futures 거래소 | Binance Futures만 보유 (1개) | **NO — 거래소 1개 부족** |
| **statistical_arb** | 동일 거래소 2+ 페어 | Binance, Upbit, Bithumb, Coinone (각각) | **YES (4 조합)** |

### 실행 체인 (Signal → 실거래)

```
Engine.run()
  └─ _live_mode_loop()
       └─ LiveMode.__init__(execution_mode="live", exchanges=[...])
            ├─ LiveGate.evaluate() → 6-check AND
            ├─ approval_gate.request_live_approval() → Telegram /approve
            ├─ CollectorManager.start() → WS orderbook 수신
            ├─ SignalGenerator → cross_exchange 신호
            ├─ RealDataSignalProducer → 나머지 5 전략 신호
            ├─ StrategyManager.route_signal() → TradeRequest 생성
            ├─ RiskGuardian 11-check → 통과/차단
            └─ AtomicExecutor
                 ├─ execute_same_exchange() → 동일 거래소 2-leg 병렬
                 ├─ execute_cross_exchange() → 교차 거래소 순차 (Amendment 4)
                 └─ execute_multi_leg() → triangular 3-leg
                      └─ ExchangeAdapter.place_order(Order) → Trade
```

---

## 1. 테스트 매트릭스

### 1-A. 배관 검증 가능 케이스 (15개)

> 번호 = 실행 우선순위. 가장 간단한 것부터 복잡한 것 순서.

| # | 전략 | 거래소 | 실행 유형 | 예상 시간 | 전제 조건 |
|---|------|--------|----------|----------|----------|
| P-01 | **cross_exchange** | Binance + Upbit | cross_exchange (순차 2-leg) | 15min | Binance $20+, Upbit KRW 20,000+ |
| P-02 | **cross_exchange** | Binance + Bithumb | cross_exchange (순차 2-leg) | 15min | Binance $20+, Bithumb KRW 20,000+ |
| P-03 | **cross_exchange** | Binance + Coinone | cross_exchange (순차 2-leg) | 15min | Binance $20+, Coinone KRW 20,000+ |
| P-04 | **cross_exchange** | Upbit + Bithumb | cross_exchange (순차 2-leg) | 15min | Upbit+Bithumb KRW 20,000+ 각 |
| P-05 | **cross_exchange** | Upbit + Coinone | cross_exchange (순차 2-leg) | 15min | Upbit+Coinone KRW 20,000+ 각 |
| P-06 | **cross_exchange** | Bithumb + Coinone | cross_exchange (순차 2-leg) | 15min | Bithumb+Coinone KRW 20,000+ 각 |
| P-07 | **spot_futures** | Binance (spot+futures) | same_exchange (병렬 2-leg) | 15min | Binance spot $20+, futures $30+ |
| P-08 | **triangular** | Binance | multi_leg (3-leg 순차) | 20min | Binance $20+ |
| P-09 | **triangular** | Upbit | multi_leg (3-leg 순차) | 20min | Upbit KRW 30,000+ |
| P-10 | **triangular** | Bithumb | multi_leg (3-leg 순차) | 20min | Bithumb KRW 30,000+ |
| P-11 | **triangular** | Coinone | multi_leg (3-leg 순차) | 20min | Coinone KRW 30,000+ |
| P-12 | **statistical_arb** | Binance | same_exchange (병렬 2-leg) | 20min | Binance $20+ |
| P-13 | **statistical_arb** | Upbit | same_exchange (병렬 2-leg) | 20min | Upbit KRW 30,000+ |
| P-14 | **statistical_arb** | Bithumb | same_exchange (병렬 2-leg) | 20min | Bithumb KRW 30,000+ |
| P-15 | **statistical_arb** | Coinone | same_exchange (병렬 2-leg) | 20min | Coinone KRW 30,000+ |

### 1-B. 테스트 불가 케이스 (2개 전략 — 사유 문서화)

| 전략 | 사유 | 해결 방법 |
|------|------|----------|
| **futures_futures** | Binance Futures 1개만 보유. 최소 2개 futures 거래소 필요. `FuturesFuturesConfig.excluded_exchanges`에 KRW 거래소 이미 제외됨. | OKX 또는 Bybit Futures API 키 발급 |
| **funding_rate** | 동일 사유. funding rate 차이를 2개 futures 거래소에서 차익. | OKX 또는 Bybit Futures API 키 발급 |

### 1-C. 전략별 불가 거래소 조합 (명시적 배제)

| 전략 | 거래소 | 배제 사유 |
|------|--------|----------|
| spot_futures | Upbit/Bithumb/Coinone | KRW 거래소는 futures 미지원 |
| triangular | (없음 — 모두 가능) | 단, Bithumb stale WS data 주의 (fake spread 가드 있음) |
| cross_exchange | 동일 거래소 2번 | 교차 차익은 최소 2개 다른 거래소 필요 |

---

## 2. Pre-flight 체크리스트 (공통 — 모든 케이스 실행 전)

> US-055 LiveGate 10항목을 실제 명령어로 변환. 각 항목에 실행 명령어 + 성공 판정 기준.

### 체크리스트 파일: `.omc/state/live-preflight-checklist.json`

```
케이스 실행 전 아래 10항목 전부 PASS 확인. 1개라도 FAIL이면 해당 케이스 진행 불가.
```

| # | 항목 | 검증 명령어 | PASS 기준 |
|---|------|-----------|----------|
| PF-01 | TimescaleDB 연결 | `docker compose exec timescaledb pg_isready -U leviathan` | `accepting connections` 출력 |
| PF-02 | Redis 연결 | `docker compose exec redis redis-cli ping` | `PONG` 출력 |
| PF-03 | Exchange WS 연결 | `cd engine && timeout 30 python -c "import asyncio; from src.collectors.{exchange}_collector import *; print('OK')"` | `OK` 출력, import 에러 없음 |
| PF-04 | API 키 권한 (read) | `cd engine && python -c "import asyncio; from src.infra.exchange.native_{exchange} import *; a = ...; asyncio.run(a.connect()); b = asyncio.run(a.get_balances()); print(b)"` | 잔고 dict 출력, 에러 없음 |
| PF-05 | API 키 권한 (trade) | 소액 limit order 생성 후 즉시 취소 (아래 스크립트) | `order_id` 반환 + `cancel=True` |
| PF-06 | 잔고 확인 | PF-04 결과에서 잔고 확인 | 거래소별 최소 잔고 충족 |
| PF-07 | Kill Switch Clear | `cd engine && python -c "from src.risk.kill_switch import is_halted; print('HALTED' if is_halted() else 'CLEAR')"` | `CLEAR` 출력 |
| PF-08 | Circuit Breaker | `cd engine && python -c "from src.infra.metrics import CIRCUIT_BREAKER_STATE; print('CLOSED')"` | `CLOSED` |
| PF-09 | Adapter Health | 어댑터 connect 후 `health_score` 확인 | `> 0.95` |
| PF-10 | Telegram 연결 | `curl -s "https://api.telegram.org/bot${DEV_TELEGRAM_BOT_TOKEN}/getMe" \| jq .ok` | `true` |

### PF-05 소액 주문 테스트 스크립트 (거래소별)

```python
# engine/scripts/preflight_order_test.py (새로 작성 필요)
# 사용법: python scripts/preflight_order_test.py --exchange binance --symbol BTC/USDT
#
# 동작:
#   1. 현재가 조회
#   2. 현재가 * 0.5 (절대 체결 안 되는 가격) limit buy 주문 생성
#   3. order_id 반환 확인
#   4. 즉시 cancel_order 호출
#   5. cancel 성공 확인
#
# 성공 기준: order_id != None AND cancel == True
# 실패 시: API 키 권한 부족 또는 잔고 부족
```

---

## 3. 케이스별 검증 절차

> 각 케이스는 독립적. 하나 실패해도 나머지 진행 가능.
> 모든 케이스에 동일한 구조 적용: 설정 → 실행 → 증거 수집 → 판정.

### 3-1. 공통 실행 프레임워크

각 케이스(P-01 ~ P-15)에 대해:

**A단계: 환경 설정 (2min)**
```bash
# engine/.env 수정 (또는 환경변수 직접 설정)
export EXECUTION_MODE=live
export DATA_MODE=shadow
export LIVE_EXCHANGES="binance,upbit"       # 케이스별 변경
export LIVE_STRATEGIES="cross_exchange"      # 케이스별 변경
export MAX_POSITION_USD=10                   # 소액 고정
export DAILY_LOSS_LIMIT_USD=15              # 안전장치
```

**B단계: Paper 모드 사전 검증 (5min)**
```bash
# 먼저 Paper 모드로 동일 설정 실행 → 신호 발생 확인
cd engine && timeout 300 python -m src.main
# 로그에서 확인: "signal_detected strategy=cross_exchange" 1건 이상
```

**C단계: Live 모드 실행 (10min)**
```bash
cd engine && timeout 600 python -m src.main
# EXECUTION_MODE=live 상태에서 실행
```

**D단계: 증거 수집**
```bash
# 로그에서 추출
grep "place_order\|order_filled\|trade_executed\|execution_result" engine/logs/latest.log
# TimescaleDB에서 체결 기록
docker compose exec timescaledb psql -U leviathan -c \
  "SELECT * FROM trades WHERE created_at > NOW() - INTERVAL '15 minutes' ORDER BY created_at DESC LIMIT 10;"
```

**E단계: 판정**
- 각 증거 항목을 `.omc/state/plumbing-results.json`에 기록
- PASS/FAIL + 증거 로그 라인 복사

---

### 3-2. 케이스별 상세 (전략 유형별 그룹)

#### GROUP A: cross_exchange (P-01 ~ P-06)

**실행 체인**: Signal → AtomicExecutor.execute_cross_exchange() → leg1: ExchangeA.place_order() → leg2: ExchangeB.place_order()

**케이스 P-01: Binance + Upbit**
- 설정: `LIVE_EXCHANGES=binance,upbit`, `LIVE_STRATEGIES=cross_exchange`
- 심볼: `BTC/USDT` (Binance) ↔ `BTC/KRW` (Upbit) — KRW 자동 매핑
- 성공 로그:
  ```
  "execute_cross_exchange leg1=binance leg2=upbit status=success"
  "place_order exchange=binance symbol=BTC/USDT side=buy"
  "place_order exchange=upbit symbol=BTC/KRW side=sell"
  ```
- 실패 시: `order_rejected`, `insufficient_balance`, `api_key_invalid`, `rate_limit_exceeded`
- 특이사항: KRW→USDT 환율 변환 (`_krw_rate`) 정상 동작 확인 필수

**케이스 P-02: Binance + Bithumb**
- 설정: `LIVE_EXCHANGES=binance,bithumb`
- 특이사항: Bithumb delta orderbook + stale data 가드. `_delta_exchanges` 처리 확인

**케이스 P-03: Binance + Coinone**
- 설정: `LIVE_EXCHANGES=binance,coinone`
- **위험**: Coinone = CCXTAdapter. ccxt 의존성 런타임 로드 확인
- 특이사항: Coinone 수수료 0.02% (API 할인). FeeModel 정합성 확인

**케이스 P-04: Upbit + Bithumb**
- 설정: `LIVE_EXCHANGES=upbit,bithumb`
- 특이사항: 양쪽 모두 KRW. 환율 변환 없이 직접 KRW 비교. 스프레드 작을 수 있음

**케이스 P-05: Upbit + Coinone**
- 설정: `LIVE_EXCHANGES=upbit,coinone`
- 특이사항: KRW-KRW. Coinone ccxt 의존성

**케이스 P-06: Bithumb + Coinone**
- 설정: `LIVE_EXCHANGES=bithumb,coinone`
- 특이사항: Bithumb stale data + Coinone ccxt. 가장 위험한 조합

#### GROUP B: spot_futures (P-07)

**실행 체인**: Signal → AtomicExecutor.execute_same_exchange() → leg1: Binance Spot place_order() + leg2: Binance Futures place_order() (병렬)

**케이스 P-07: Binance Spot+Futures**
- 설정: `LIVE_EXCHANGES=binance,binance_futures`, `LIVE_STRATEGIES=spot_futures`
- 심볼: `BTC/USDT` (spot) ↔ `BTC/USDT:USDT` (futures perp)
- 성공 로그:
  ```
  "execute_same_exchange exchange=binance legs=2 status=success"
  "place_order exchange=binance symbol=BTC/USDT side=buy"
  "place_order exchange=binance_futures symbol=BTC/USDT:USDT side=sell"
  ```
- 전제: Binance spot $20+, futures $30+ (margin)
- 특이사항: basis_bps 계산 + funding_rate 메타데이터 확인

#### GROUP C: triangular (P-08 ~ P-11)

**실행 체인**: Signal → AtomicExecutor.execute_multi_leg() → 3-leg 순차 (같은 거래소)

**케이스 P-08: Binance Triangular**
- 설정: `LIVE_EXCHANGES=binance`, `LIVE_STRATEGIES=triangular`
- 경로 예시: USDT → BTC → ETH → USDT (3 pairs: BTC/USDT, ETH/BTC, ETH/USDT)
- 성공 로그:
  ```
  "execute_multi_leg exchange=binance legs=3 status=success"
  "place_order exchange=binance symbol=BTC/USDT side=buy"
  "place_order exchange=binance symbol=ETH/BTC side=buy"
  "place_order exchange=binance symbol=ETH/USDT side=sell"
  ```
- 특이사항: 3x taker fee (Binance 0.10% * 3 = 0.30%). min_profit_bps=8

**케이스 P-09: Upbit Triangular**
- 설정: `LIVE_EXCHANGES=upbit`, KRW 페어
- 경로: KRW → BTC → ETH → KRW
- 특이사항: Upbit taker 0.139% * 3 = 0.417%. 수익성 낮을 수 있음

**케이스 P-10: Bithumb Triangular**
- 설정: `LIVE_EXCHANGES=bithumb`
- **위험**: Bithumb 공개 WS stale data → fake spread (304만%). 가드가 유효 시그널 차단할 수 있음
- 특이사항: 이 케이스는 "가드가 정상 작동하여 REJECT하는 것"도 PASS

**케이스 P-11: Coinone Triangular**
- 설정: `LIVE_EXCHANGES=coinone`
- **위험**: CCXTAdapter + 3-leg = ccxt 3회 연속 호출. 레이턴시 확인
- 특이사항: Coinone 수수료 0.02% * 3 = 0.06% (가장 낮음)

#### GROUP D: statistical_arb (P-12 ~ P-15)

**실행 체인**: Signal → AtomicExecutor.execute_same_exchange() → 2-leg 병렬 (같은 거래소, 다른 심볼)

**케이스 P-12: Binance Stat Arb**
- 설정: `LIVE_EXCHANGES=binance`, `LIVE_STRATEGIES=statistical_arb`
- 페어: BTC/USDT - ETH/USDT (기본 설정)
- 성공 로그:
  ```
  "execute_same_exchange exchange=binance legs=2 status=success"
  "place_order exchange=binance symbol=BTC/USDT side=buy"
  "place_order exchange=binance symbol=ETH/USDT side=sell"
  ```
- 특이사항: Kalman hedge ratio + z-score. min_history=120 → 워밍업 10min+ 필요

**케이스 P-13 ~ P-15: KRW 거래소 Stat Arb**
- P-13: Upbit, P-14: Bithumb, P-15: Coinone
- 특이사항: KRW 페어간 cointegration. 데이터 수집 시간 길 수 있음

---

## 4. 누락 방지 전략

### 4-1. 체크리스트 파일 구조

```
.omc/state/plumbing-results.json
{
  "session_id": "plumbing-v1-2026-04-XX",
  "preflight": {
    "PF-01": {"status": "PASS", "evidence": "accepting connections", "timestamp": "..."},
    ...
  },
  "cases": {
    "P-01": {
      "status": "PASS|FAIL|SKIP|BLOCKED",
      "strategy": "cross_exchange",
      "exchanges": ["binance", "upbit"],
      "evidence": {
        "order_placed_log": "...",
        "trade_id": "...",
        "db_record": "...",
        "error": null
      },
      "timestamp": "...",
      "duration_seconds": 0
    },
    ...
  },
  "summary": {
    "total": 15,
    "pass": 0,
    "fail": 0,
    "skip": 0,
    "blocked": 0
  }
}
```

### 4-2. 증거 수집 자동화

각 케이스 완료 시 자동으로 수집할 증거 목록:

| 증거 유형 | 수집 방법 | 저장 위치 |
|----------|----------|----------|
| 엔진 로그 | `engine/logs/latest.log` 에서 grep | `plumbing-results.json` |
| DB 체결 기록 | TimescaleDB `trades` 테이블 쿼리 | `plumbing-results.json` |
| 거래소 API 응답 | 로그에서 `_rest_place_order response=` 추출 | 로그 원문 보존 |
| 잔고 변화 | 실행 전/후 `get_balances()` diff | `plumbing-results.json` |
| 에러/예외 | 로그에서 `ERROR\|FATAL\|Exception` 추출 | `plumbing-results.json` |

### 4-3. 게이트 체계

```
Pre-flight 10항목 전부 PASS
    ↓
케이스 P-01 (가장 간단: Binance+Upbit cross_exchange)
    ├─ PASS → P-02 진행
    └─ FAIL → 원인 분석 → 수정 → P-01 재시도 (다른 케이스는 대기)
         ↓
P-02 ~ P-06 (나머지 cross_exchange 조합)
    ↓ (cross_exchange 그룹 중 1개 이상 PASS)
P-07 (spot_futures — Binance only)
    ↓
P-08 ~ P-11 (triangular — 거래소별)
    ↓
P-12 ~ P-15 (statistical_arb — 거래소별)
    ↓
최종 보고서 → US-055/056 passes:true 판정
```

**게이트 규칙:**
- Pre-flight FAIL → 전체 중단 (환경 문제)
- GROUP A (P-01) FAIL → 기본 배관 문제. 나머지 전부 BLOCKED
- GROUP A 내 개별 FAIL → 해당 거래소 관련 다른 케이스도 BLOCKED (예: Coinone P-03 FAIL → P-05, P-06, P-11, P-15도 BLOCKED)
- GROUP B~D → GROUP A 최소 1건 PASS 후 진행

---

## 5. 우선순위 실행 순서

### 5-1. Phase 1: 최소 검증 (핵심 배관 3개, ~1시간)

**목표**: 가장 적은 자본으로 가장 넓은 범위의 배관 검증

| 순서 | 케이스 | 사유 |
|------|--------|------|
| 1 | **P-01** (Binance+Upbit CE) | 가장 안정적인 2개 거래소. 교차 거래소 배관 핵심 검증 |
| 2 | **P-07** (Binance spot+futures) | 유일한 spot_futures 가능 조합. same_exchange 배관 검증 |
| 3 | **P-08** (Binance triangular) | multi_leg 배관 검증. Binance가 가장 안정적 |

**이 3개 PASS 시**: 3가지 실행 유형 (cross/same/multi_leg) 모두 배관 검증 완료

### 5-2. Phase 2: KRW 거래소 확장 (4개, ~1시간)

| 순서 | 케이스 | 사유 |
|------|--------|------|
| 4 | **P-02** (Binance+Bithumb CE) | Bithumb delta orderbook 배관 확인 |
| 5 | **P-03** (Binance+Coinone CE) | Coinone CCXTAdapter 배관 확인 (가장 위험) |
| 6 | **P-09** (Upbit triangular) | KRW 단독 거래소 multi_leg 배관 |
| 7 | **P-12** (Binance stat_arb) | stat_arb 기본 배관 (Kalman + z-score) |

### 5-3. Phase 3: 완전 커버리지 (8개, ~2.5시간)

| 순서 | 케이스 | 사유 |
|------|--------|------|
| 8~15 | **P-04~06, P-10~11, P-13~15** | 나머지 조합 전부 |

### 5-4. 최소 자본 요구사항

| 거래소 | 최소 잔고 | 용도 |
|--------|----------|------|
| Binance Spot | $20 USDT | CE + triangular + stat_arb |
| Binance Futures | $30 USDT | spot_futures margin |
| Upbit | 30,000 KRW (~$22) | CE + triangular + stat_arb |
| Bithumb | 30,000 KRW (~$22) | CE + triangular + stat_arb |
| Coinone | 30,000 KRW (~$22) | CE + triangular + stat_arb |
| **합계** | **~$116** | 전 케이스 커버 |

Phase 1만 실행 시: Binance $50 (spot+futures) + Upbit 20,000 KRW = **~$65**

---

## 6. 알려진 위험 및 완화

| 위험 | 영향 | 완화 방법 |
|------|------|----------|
| Coinone CCXTAdapter → ccxt 런타임 의존성 | P-03/05/06/11/15 FAIL 가능 | Pre-flight에서 ccxt import 확인. FAIL 시 ccxt 설치/버전 확인 |
| Bithumb stale WS data | P-02/06/10/14에서 fake spread → 가드 reject | 가드 reject = 정상 동작. Live 인증 API 사용 시 해결 |
| triangular KRW 거래소 수익성 | 수수료 > 스프레드 → 신호 0건 | 배관 테스트이므로 Paper 모드로 신호 발생 확인 후 Live 진행. 신호 0건도 "배관은 정상, 수익성 부족" 판정 |
| cross_exchange KRW↔USDT 환율 변환 | 환율 오류 시 잘못된 스프레드 계산 | `_krw_rate` 로그 확인. CoinGecko/Upbit API에서 실시간 환율 정상 여부 |
| approval_gate Telegram 미설정 | 텔레그램 토큰 없으면 auto-approve (dev mode) | 배관 테스트에서는 auto-approve 허용. 실거래 전 반드시 Telegram 설정 |
| LiveGate 7일 데이터 부족 | evaluation_days=7 미충족 → LiveGate FAIL | `evaluation_days=1` 또는 `bypass=true` 설정 (배관 테스트 한정) |

---

## 7. PRD 매핑 (passes:false → passes:true 전환 조건)

| US | 전환 조건 | 필요 케이스 |
|----|----------|------------|
| US-055 | Pre-flight 10항목 PASS + LiveGate PASS | PF-01~10 전부 |
| US-056 | 실거래 체결 1건+ | P-01 PASS (최소) |
| US-373 | K-LT 전체 + 병렬 24H | Phase 3 전부 PASS + 24H 안정성 |
| US-425 | Binance 체결 1건+ MDD<5% | P-01 또는 P-07 또는 P-08 PASS |
| US-426 | Bitget 체결 1건+ | **불가 — API 키 미보유** |
| US-427 | Coinone 체결 1건+ | P-03 또는 P-05 또는 P-06 PASS |
| US-428 | Upbit 체결 1건+ | P-01 또는 P-04 또는 P-05 PASS |
| US-429 | Binance+Bitget CE 체결 | **불가 — Bitget API 키 미보유** |

> **US-426, US-429**: Bitget API 키 발급 후에만 테스트 가능. 현재 범위에서 제외.

---

## 8. 실행 전 필요 작업 (Executor가 수행할 사항)

1. **`engine/scripts/preflight_order_test.py` 작성** — PF-05 소액 주문 테스트 스크립트
2. **`.omc/state/plumbing-results.json` 초기화** — 결과 수집 파일
3. **engine/.env 백업** — 실행 전 현재 설정 백업
4. **거래소별 잔고 확인 + 입금** — 최소 자본 요구사항 충족
5. **LiveGate bypass 설정** — 배관 테스트용 임시 설정 (config/engine.json에 `live_gate.bypass=true` 또는 `evaluation_days=1`)

---

## Open Questions

- [ ] Coinone CCXTAdapter가 Live 실행에서 안정적인가? Native 어댑터로 마이그레이션 필요 여부 — Coinone 케이스 FAIL 시 즉시 판단 필요
- [ ] Bithumb triangular은 stale data 가드 때문에 유효 시그널 0건이 예상됨. "배관은 정상이나 데이터 품질 문제"를 PASS로 볼 것인가? — 사용자 판단 필요
- [ ] futures_futures/funding_rate를 테스트하려면 어느 거래소 API 키를 우선 발급할 것인가? (OKX vs Bybit) — 비용/편의성 고려
- [ ] 배관 테스트 중 실제 체결 시 발생하는 소액 손실(수수료+슬리피지)은 어느 범위까지 허용? — MAX_POSITION_USD=$10 기준 케이스당 최대 ~$0.50 손실 예상
- [ ] Phase L 완료 후 즉시 배관 테스트 진행? 아니면 Phase M 사이에 별도 배관 Phase 삽입?
