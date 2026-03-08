# US-023: ShadowMode에 StrategyManager 주입 + 라우팅 (GAP-1 해결)

**날짜**: 2026-03-08
**Phase**: B-4 (GAP-1 해결)
**모드**: RALPLAN --deliberate
**복잡도**: MEDIUM (3 수정 파일, ~130 LOC 생산 코드 + ~220 LOC 테스트)
**선행 조건**: Phase B-3 완료 (GAP 7,3,2 RESOLVED -- commit 1286999)
**리비전**: v2 (Critic ITERATE 반영 -- 4건 수정)

---

## 1. 현재 상태 분석 (코드 기반, 2026-03-08)

### 이미 구현된 항목 (AC 충족)

| 항목 | 파일:라인 | 상태 |
|------|-----------|------|
| `strategy_manager: Any \| None = None` 파라미터 | `shadow.py:161` | DONE |
| `self._strategy_manager` 저장 | `shadow.py:188` | DONE |
| `_route_signal_to_strategies(signal)` 메서드 | `shadow.py:866-890` | DONE (단, 결함 있음) |
| `_execute_shadow_trade_request(trade_request)` N-leg 실행 | `shadow.py:892-1002` | DONE |
| `_on_orderbook` 분기: strategy_manager 유무에 따라 라우팅 | `shadow.py:531-536` | DONE |
| `_evaluate_multi_strategies` 분기 | `shadow.py:591-594` | DONE |
| `_funding_rate_loop` 분기 | `shadow.py:662-665` | DONE |
| `main.py`에서 `strategy_manager=self._strategy_manager` 전달 | `main.py:1009` | DONE |
| 전략별 `shadow_mode=True` 설정 + `start_strategy()` 호출 | `main.py:1013-1022` | DONE |
| `ShadowStats.by_strategy` 전략별 메트릭 추적 | `shadow.py:958-964` | DONE |
| Prometheus 전략별 라벨링 | `shadow.py:979-991` | DONE |

### 미해결 결함 (5건)

#### 결함 0: futures_futures signal_id vs STRATEGY_TYPE 불일치 (CRITICAL)

**위치**: `multi_signal.py:368` + `futures_futures.py:37`

**문제**: `MultiStrategySignalProducer`가 생성하는 시그널의 `strategy_id="futures_futures_spread"` (multi_signal.py:368)와 `FuturesFuturesStrategy.STRATEGY_TYPE="futures_futures_cross"` (futures_futures.py:37)는 양방향 substring 매칭에 **모두 실패**함.

- `"futures_futures_cross" in "futures_futures_spread"` = **False**
- `"futures_futures_spread" in "futures_futures_cross"` = **False**

`StrategyManager._should_route()` (manager.py:211-216)는 양방향 substring 매칭을 수행하므로, `route_signal()` 도입 시 futures_futures 전략이 **영구적으로 시그널을 수신하지 못함**.

**영향**: Step 1-2 적용 후 futures_futures 전략이 완전히 비활성화됨.

#### 결함 1: 타입 기반 시그널 매칭 누락 (CRITICAL)

**위치**: `shadow.py:866-890` (`_route_signal_to_strategies`)

**문제**: 현재 구현은 모든 활성 전략에 모든 시그널을 무차별 전달함.
```python
# 현재 코드 (shadow.py:876-882)
for sid in self._strategy_manager.list_strategies():
    strategy = self._strategy_manager.get_strategy(sid)
    if strategy is None or not strategy.is_active:
        continue
    # _should_route() 호출 없음 -- 모든 전략이 모든 시그널을 받음
    result: TradeRequest | None = await strategy.on_signal(signal)
```

`StrategyManager._should_route()` (manager.py:201-218)는 `STRATEGY_TYPE` 접두어 매칭을 수행하지만, `_route_signal_to_strategies`는 이 로직을 전혀 사용하지 않음.

**영향**:
- `cross_exchange_spot` 시그널이 `funding_rate_arb`, `triangular` 등 무관한 전략에도 전달됨
- 전략 내부 `on_signal()`이 부적합한 시그널을 필터링해야 하므로 불필요한 오버헤드 발생
- `StrategyMetrics.signals_received`가 과다 집계됨 (실제 관련 시그널 수 대비 7배)

#### 결함 2: 이중 전달 (Double-Delivery) 위험 (HIGH)

**위치**: `main.py:747` + `shadow.py:531-534`

**문제**: `main.py:_start_background_tasks()`에서 `_strategy_manager_loop()`이 **모든 모드**에서 시작됨 (line 747). 이 루프는 `StrategyManager.start()`를 호출하여 Redis Streams `leviathan:signals`를 구독함.

동시에 Shadow 모드에서:
1. `SignalGenerator.on_orderbook_update()`가 시그널을 Redis Stream에 퍼블리시 (signal.py:212-213)
2. `StrategyManager._consume_loop()`가 Redis에서 읽어서 `strategy.on_signal()` 호출 (경로 A)
3. `ShadowMode._route_signal_to_strategies()`가 직접 `strategy.on_signal()` 호출 (경로 B)

**결과**: 동일 시그널이 각 전략에 **2회** 전달될 수 있음.

**참고**: `MultiStrategySignalProducer._publish()`도 Redis Stream에 퍼블리시 (multi_signal.py:396-407), `RealDataSignalProducer`를 통한 시그널도 동일한 이중 전달 위험이 있음.

#### 결함 3: 라우팅 결정 로깅/메트릭 부재 (MEDIUM)

**위치**: `shadow.py:866-890`

**문제**: 어떤 전략이 어떤 시그널을 수신했는지, 거부했는지에 대한 로깅이 없음. 디버깅 및 운영 모니터링이 불가능.

현재 로깅:
- `strategy_on_signal_error`: 예외 발생 시만 로깅
- 정상 라우팅, 필터링 거부, 매칭/미스매칭에 대한 로깅 없음
- **폴백 발동 시 Prometheus 카운터 없음** -- 경고 로그만으로는 폴백 빈도 모니터링 불가

#### 결함 4: 추가 컨텍스트 미전달 (LOW)

**위치**: `shadow.py:866-890`

**문제**: `_route_signal_to_strategies`가 시그널만 전달. 일부 전략은 `futures_books`, `funding_rates` 등의 추가 데이터가 필요할 수 있음.

**현재 영향**: 낮음. 현재 등록된 7개 전략의 `on_signal(Signal)` 인터페이스는 Signal 객체만 받으며, 추가 데이터는 전략 내부에서 별도 관리. Phase B-5 이후 필요 시 확장.

---

## 2. RALPLAN-DR 요약

### 원칙 (Principles, 5개)

1. **하위 호환성 보전**: `strategy_manager=None` 기본값으로 기존 동작 100% 유지
2. **단일 책임 라우팅**: 시그널 라우팅은 한 경로만 존재해야 함 (Redis OR 직접, 동시 아님)
3. **기존 로직 재사용**: `StrategyManager._should_route()` 로직을 복제하지 않고 위임 사용
4. **방어적 실패 처리**: 라우팅 실패 시 기존 `_execute_shadow_trade()`로 폴백, 폴백 발동은 Prometheus 카운터로 추적
5. **관측 가능성**: 라우팅 결정(매칭/거부/에러/폴백)을 구조화 로그 + Prometheus 메트릭으로 추적

### 결정 동인 (Decision Drivers, 3개)

1. **이중 전달 방지**: Shadow 모드에서 동일 시그널이 2번 처리되면 PnL 과다 집계, 메트릭 왜곡 발생
2. **타입 매칭 정확성**: 무관한 전략에 시그널이 전달되면 불필요한 연산 + 잘못된 TradeRequest 생성 위험
3. **최소 변경 범위**: 이미 90% 구현된 상태에서 나머지 결함만 정밀 수정 (과도한 리팩터링 금지)

### 선택지 (Viable Options, 2개)

#### Option A: StrategyManager.route_signal() 위임 (권장)

`_route_signal_to_strategies`를 `StrategyManager.route_signal()` 새 메서드로 교체. Shadow 모드에서는 Redis _consume_loop을 시작하지 않음.

| 장점 | 단점 |
|------|------|
| `_should_route()` 로직 재사용 (DRY) | StrategyManager에 새 public 메서드 추가 |
| Shadow/Live 동일한 매칭 동작 보장 | route_signal()이 shadow_mode를 알아야 함 |
| 이중 전달 완전 차단 (Redis loop 미시작) | main.py 수정 필요 (strategy_manager.start() 조건부) |
| 테스트 용이 (route_signal 단위 테스트) | - |

#### Option B: 인라인 _should_route 복제

Shadow의 `_route_signal_to_strategies`에 `_should_route` 로직을 직접 복제. Redis loop은 별도 처리.

| 장점 | 단점 |
|------|------|
| StrategyManager 수정 불필요 | _should_route 로직 복제 (DRY 위반) |
| 변경 범위가 shadow.py에 한정 | 매칭 로직 분기 시 두 곳 동시 수정 필요 |
| - | 이중 전달 방지를 별도로 해결해야 함 |
| - | 장기 유지보수 비용 증가 |

#### Option B 기각 사유

- DRY 위반으로 `_should_route()` 로직이 manager.py와 shadow.py 두 곳에 존재하게 됨
- `_should_route()` 변경 시 shadow.py 동기화를 잊으면 Live와 Shadow의 라우팅 동작이 달라지는 사일런트 버그 발생
- 이중 전달 문제를 별도 메커니즘으로 해결해야 하므로 총 변경량이 Option A보다 많아짐

**선택: Option A**

---

## 3. 구현 단계 (Implementation Steps)

### Step 0: futures_futures signal_id / STRATEGY_TYPE 정렬 (CRITICAL -- Critic v2 추가)

**파일**: `engine/src/strategies/futures_futures.py`
**위치**: line 37

**문제 재확인**:
- `multi_signal.py:368` -- `strategy_id="futures_futures_spread"`
- `futures_futures.py:37` -- `STRATEGY_TYPE = "futures_futures_cross"`
- `_should_route()` (manager.py:215) 양방향 substring 매칭:
  - `"futures_futures_cross" in "futures_futures_spread"` = **False**
  - `"futures_futures_spread" in "futures_futures_cross"` = **False**
- **결론**: 매칭 실패. `route_signal()` 도입 시 futures_futures 전략이 시그널을 영구적으로 수신 불가.

**수정 방향**: 가드레일에 `RealDataSignalProducer 변경 금지`가 있고, `MultiStrategySignalProducer`도 시그널 소스이므로 변경 최소화를 위해 **전략 클래스 측**의 `STRATEGY_TYPE`을 변경한다.

**변경 내용**:
```python
# 변경 전 (futures_futures.py:37)
STRATEGY_TYPE = "futures_futures_cross"

# 변경 후
STRATEGY_TYPE = "futures_futures"
```

**매칭 검증 (변경 후)**:
- `multi_signal.py:368`의 `strategy_id="futures_futures_spread"`:
  - `"futures_futures" in "futures_futures_spread"` = **True** (substring 매칭 성공)
- `RealDataSignalProducer`가 `strategy_id="futures_futures_xxx"` 형태 시그널을 생성하는 경우에도 `"futures_futures"` prefix로 매칭됨

**전체 매칭 테이블 (Step 0 적용 후)**:

| signal.strategy_id (multi_signal.py) | STRATEGY_TYPE (변경 후) | 매칭 결과 |
|---|---|---|
| `"futures_futures_spread"` (line 368) | `"futures_futures"` | `"futures_futures" in "futures_futures_spread"` = **True** |
| `"spot_futures_basis"` (line 153) | `"spot_futures_basis"` | 정확 매칭 = **True** |
| `"funding_rate_arb"` (line 195) | `"funding_rate_arb"` | 정확 매칭 = **True** |
| `"triangular"` (line 234) | `"triangular"` | 정확 매칭 = **True** |
| `"statistical_arb_zscore"` (line 277) | `"statistical_arb"` | `"statistical_arb" in "statistical_arb_zscore"` = **True** |
| `"latency_arb"` (line 325) | `"latency_arb"` | 정확 매칭 = **True** |
| `"cross_exchange_spot"` (signal.py) | `"cross_exchange_spot"` | 정확 매칭 = **True** |

**수용 기준**:
- [ ] `futures_futures.py:37`의 `STRATEGY_TYPE`이 `"futures_futures"`로 변경됨
- [ ] `"futures_futures" in "futures_futures_spread"` = True 확인 (Python REPL 또는 단위 테스트)
- [ ] 기존 `futures_futures` 관련 테스트 전수 통과 (`pytest tests/ -k futures_futures -v`)
- [ ] `multi_signal.py`는 **변경하지 않음** (가드레일 준수)
- [ ] `RealDataSignalProducer`는 **변경하지 않음** (가드레일 준수)

### Step 1: StrategyManager.route_signal() 추가

**파일**: `engine/src/strategies/manager.py`
**위치**: line 219 이후 (기존 `_emit_trade_request` 다음)

```python
async def route_signal(self, signal: Signal) -> list[TradeRequest]:
    """Route signal directly to matching active strategies (no Redis).

    Used by ShadowMode for in-process signal routing.
    Returns list of TradeRequests from strategies that accepted the signal.
    Reuses _should_route() for consistent matching with _dispatch().
    """
    results: list[TradeRequest] = []
    for strategy in self._strategies.values():
        if not strategy.is_active:
            continue
        if not self._should_route(strategy, signal):
            logger.debug(
                "route_signal: skip %s for signal %s",
                strategy.strategy_id, signal.strategy_id,
            )
            continue
        try:
            strategy._metrics.signals_received += 1
            request = await strategy.on_signal(signal)
            if request is not None:
                strategy._metrics.trade_requests_generated += 1
                results.append(request)
            else:
                strategy._metrics.signals_filtered += 1
        except Exception as exc:
            logger.error(
                "Strategy %s raised on route_signal: %s",
                strategy.strategy_id, exc, exc_info=True,
            )
    if not results:
        logger.debug("route_signal: no strategy accepted signal %s", signal.strategy_id)
    return results
```

**수용 기준**:
- [ ] `route_signal(signal)` -> `list[TradeRequest]` 반환
- [ ] `_should_route()` 재사용으로 타입 매칭 수행
- [ ] 비활성 전략 건너뛰기
- [ ] 전략별 `StrategyMetrics` 업데이트 (signals_received, trade_requests_generated, signals_filtered)
- [ ] 전략 예외 시 다른 전략 계속 실행
- [ ] 기존 메서드 (_dispatch, _consume_loop 등) 미변경

### Step 2: Shadow의 _route_signal_to_strategies를 route_signal() 위임으로 교체 + Prometheus 폴백 카운터

**파일**: `engine/src/modes/shadow.py`
**위치**: line 866-890 (`_route_signal_to_strategies` 메서드) + 파일 상단 import/메트릭 정의

#### 2-a: Prometheus 폴백 카운터 정의

`shadow.py` 상단 import 영역 (line 36-46 부근, 기존 `from src.infra.metrics import (...)` 블록 다음)에 추가:

```python
from prometheus_client import Counter as PromCounter

ROUTING_FALLBACK_TOTAL = PromCounter(
    "shadow_routing_fallback_total",
    "Number of times signal routing fell back to direct execution",
    ["reason"],
)
```

> **참고**: `Counter`를 `PromCounter`로 alias하여 `collections.Counter`와 이름 충돌 방지. 또는 기존 `src.infra.metrics`에 추가해도 됨 -- executor 판단에 위임.

#### 2-b: _route_signal_to_strategies 교체

기존 코드:
```python
async def _route_signal_to_strategies(self, signal: Signal) -> None:
    if self._strategy_manager is None:
        return
    for sid in self._strategy_manager.list_strategies():
        strategy = self._strategy_manager.get_strategy(sid)
        if strategy is None or not strategy.is_active:
            continue
        try:
            result: TradeRequest | None = await strategy.on_signal(signal)
            if result is not None:
                await self._execute_shadow_trade_request(result)
        except Exception as exc:
            logger.warning(...)
```

교체 코드:
```python
async def _route_signal_to_strategies(self, signal: Signal) -> None:
    """Route signal to matching strategies via StrategyManager.route_signal().

    Delegates type-based matching to StrategyManager._should_route().
    Falls back to _execute_shadow_trade() on routing failure.

    빈 리스트 반환 vs 폴백 구분:
    - route_signal()이 빈 리스트를 반환한 경우: 모든 전략이 시그널을 정상적으로
      필터링한 것이므로 폴백을 실행하지 않음 (의도된 동작).
    - route_signal() 호출 자체가 예외를 발생시킨 경우: 라우팅 메커니즘 실패이므로
      기존 _execute_shadow_trade()로 폴백하여 시그널 유실을 방지.
    """
    if self._strategy_manager is None:
        return

    try:
        trade_requests = await self._strategy_manager.route_signal(signal)
        for request in trade_requests:
            await self._execute_shadow_trade_request(request)

        logger.debug(
            "shadow_mode.signal_routed",
            signal_strategy=signal.strategy_id,
            symbol=signal.symbol,
            requests_generated=len(trade_requests),
        )
    except Exception as exc:
        ROUTING_FALLBACK_TOTAL.labels(reason="routing_exception").inc()
        logger.warning(
            "shadow_mode.strategy_routing_failed",
            signal_strategy=signal.strategy_id,
            error=str(exc),
        )
        # 폴백: 라우팅 메커니즘 실패 시 기존 직접 실행으로 시그널 유실 방지
        await self._execute_shadow_trade(signal)
```

**수용 기준**:
- [ ] `_should_route()` 타입 매칭이 적용됨
- [ ] `cross_exchange_spot` 시그널이 `CrossExchangeStrategy`에만 전달됨
- [ ] `funding_rate_arb` 시그널이 `FundingRateStrategy`에만 전달됨
- [ ] `futures_futures_spread` 시그널이 `FuturesFuturesStrategy`에만 전달됨 (Step 0 적용 후)
- [ ] **빈 리스트 반환 시 폴백 미실행** (의도된 동작 -- 전략이 정상 필터링한 경우)
- [ ] **예외 발생 시 폴백 실행** + `ROUTING_FALLBACK_TOTAL` Prometheus 카운터 증가
- [ ] 라우팅 결과 구조화 로그 출력 (signal_strategy, symbol, requests_generated)
- [ ] `ROUTING_FALLBACK_TOTAL` 카운터가 `reason` 라벨로 정의됨

### Step 3: Shadow 모드에서 StrategyManager Redis _consume_loop 비활성화

**파일**: `engine/src/main.py`
**위치**: line 746-748 (background tasks 생성)

현재 코드 (문제):
```python
tasks = [
    asyncio.create_task(self._strategy_manager_loop(), name="strategy_mgr"),  # 모든 모드에서 시작
    ...
]
```

수정 코드:
```python
tasks = [
    asyncio.create_task(self._trade_consumer_loop(), name="trade_consumer"),
    asyncio.create_task(self._health_check_loop(), name="health_check"),
    asyncio.create_task(self._reconcile_loop(), name="reconcile"),
    asyncio.create_task(self._heartbeat_loop(), name="ws_heartbeat"),
    asyncio.create_task(self._dashboard_feed_loop(), name="dashboard_feed"),
]

# Shadow 모드: route_signal()로 직접 라우팅하므로 Redis consume loop 불필요
# Live/Paper 모드: Redis Streams로 시그널 소비
if self._data_mode != DataMode.SHADOW:
    tasks.append(
        asyncio.create_task(self._strategy_manager_loop(), name="strategy_mgr")
    )
else:
    logger.info("Shadow mode: StrategyManager Redis consume loop skipped (using direct routing)")
```

**수용 기준**:
- [ ] Shadow 모드에서 `StrategyManager._consume_loop()` 미실행
- [ ] Shadow 모드에서 `StrategyManager._running == False`
- [ ] Live/Paper 모드에서 기존대로 Redis 소비 루프 실행
- [ ] 로그로 스킵 사유 명시

### Step 4: 라우팅 결정 로깅 강화

**파일**: `engine/src/modes/shadow.py`
**위치**: Step 2에서 교체한 `_route_signal_to_strategies` 내부 (이미 포함)

추가로 `_on_orderbook` 분기점에 라우팅 경로 로그 추가:

```python
# shadow.py line 531-536 수정
if signal is not None:
    if self._telegram is not None:
        try:
            await self._telegram.send_signal_found(signal)
        except Exception as exc:
            logger.warning(...)

    if self._strategy_manager is not None:
        logger.debug(
            "shadow_mode.routing_via_strategy_manager",
            signal_strategy=signal.strategy_id,
            symbol=signal.symbol,
        )
        await self._route_signal_to_strategies(signal)
    else:
        await self._execute_shadow_trade(signal)
```

**수용 기준**:
- [ ] 라우팅 경로 (StrategyManager vs 직접 실행) 로그 출력
- [ ] 시그널별 매칭/비매칭 전략 debug 로그 출력
- [ ] 에러 발생 시 warning 로그 + 폴백 경로 로그

---

## 4. Pre-Mortem (3 시나리오) -- deliberate 모드

### 시나리오 1: "모든 시그널이 필터링되어 거래 0건"

**발생 조건**: `route_signal()` 적용 후 `_should_route()`의 `STRATEGY_TYPE` 매칭이 현재 시그널의 `strategy_id`와 불일치.

**근본 원인 분석**:

Step 0 적용 전, `futures_futures_spread` vs `futures_futures_cross` 불일치가 존재했음. Step 0에서 `STRATEGY_TYPE`을 `"futures_futures"`로 변경하여 해결.

나머지 6개 전략의 매칭 테이블 (Section 3, Step 0 참조):
- `cross_exchange_spot` -> 정확 매칭 (True)
- `spot_futures_basis` -> 정확 매칭 (True)
- `funding_rate_arb` -> 정확 매칭 (True)
- `triangular` -> 정확 매칭 (True)
- `statistical_arb_zscore` -> substring 매칭 (True)
- `latency_arb` -> 정확 매칭 (True)

**위험 지점**: `RealDataSignalProducer`가 생성하는 시그널의 `strategy_id`가 `STRATEGY_TYPE`과 불일치할 수 있음.

**완화 조치**:
1. Step 0에서 전체 매칭 테이블을 검증하여 모든 signal_id-STRATEGY_TYPE 쌍이 매칭됨을 확인
2. 매칭 실패 시 `route_signal()`이 debug 로그를 남기므로 즉시 발견 가능
3. 폴백으로 `_execute_shadow_trade()` 실행되므로 최악의 경우에도 기존 동작 유지
4. **빈 리스트 반환은 폴백을 트리거하지 않음** -- 이는 모든 전략이 시그널을 정상적으로 필터링한 정상 동작

### 시나리오 2: "StrategyManager.route_signal() 도입 후 성능 저하"

**발생 조건**: `route_signal()`이 7개 전략 모두를 순회하면서 `on_signal()` 호출이 느려짐. 특히 `StatisticalArbStrategy.on_signal()`이 무거운 계산 수행.

**정량 분석**:
- 현재: 시그널당 1회 `_execute_shadow_trade()` 호출 (~1ms)
- 변경 후: 시그널당 최대 7회 `_should_route()` + 1-2회 `on_signal()` + 1회 `_execute_shadow_trade_request()`
- `_should_route()`는 문자열 비교만 수행 (~0.01ms)
- 예상 오버헤드: ~0.1ms/시그널 (무시 가능)

**완화 조치**:
1. `stat_arb`, `latency_arb` 등 비활성 전략은 `is_active=False`로 즉시 스킵
2. `route_signal()` elapsed time을 Prometheus histogram으로 추적 (향후 필요 시)

### 시나리오 3: "이중 전달 방지 실패 -- main.py 수정 누락"

**발생 조건**: Step 3 (main.py 수정)이 누락되어 `_strategy_manager_loop()`이 여전히 Shadow 모드에서 시작됨. Redis consume loop이 `strategy.on_signal()`을 호출하면서 `_route_signal_to_strategies()`와 이중 실행.

**영향**:
- 전략별 `signals_received`가 2배로 집계
- `TradeRequest`가 2번 생성되어 PnL 과다 집계
- Shadow 10min 실행 시 trades 수가 비정상적으로 높음

**완화 조치**:
1. Step 3을 반드시 Step 2와 동일 커밋에 포함 (atomic change)
2. 통합 테스트에서 `StrategyManager._running == False` 검증 (US-026)
3. Shadow 10min 실행 후 trades 수가 기존 대비 급증하지 않음을 확인

---

## 5. 확장 테스트 계획 -- deliberate 모드

### 5.1 단위 테스트 (Unit)

**파일**: `engine/tests/unit/strategies/test_manager.py` (기존 파일에 추가)

| # | 테스트명 | 검증 내용 |
|---|---------|-----------|
| 1 | `test_route_signal_dispatches_to_matching_strategy` | `_should_route()` 매칭되는 전략에만 `on_signal()` 호출 |
| 2 | `test_route_signal_returns_trade_requests` | 수락한 전략의 TradeRequest 리스트 반환 |
| 3 | `test_route_signal_skips_inactive_strategies` | `is_active=False` 전략 스킵 |
| 4 | `test_route_signal_skips_non_matching_type` | `STRATEGY_TYPE` 미스매칭 전략 스킵 |
| 5 | `test_route_signal_returns_empty_when_all_filtered` | 모든 전략 거부 시 빈 리스트 반환 (폴백 미트리거 검증) |
| 6 | `test_route_signal_handles_strategy_exception` | 한 전략 예외 시 나머지 계속 |
| 7 | `test_route_signal_updates_metrics_signals_received` | `signals_received` 증가 |
| 8 | `test_route_signal_updates_metrics_trade_requests_generated` | TradeRequest 생성 시 카운트 |
| 9 | `test_route_signal_updates_metrics_signals_filtered` | None 반환 시 `signals_filtered` 증가 |

### 5.2 통합 테스트 (Integration)

**파일**: `engine/tests/integration/test_shadow_strategy_integration.py` (신규)

| # | 테스트명 | 검증 내용 |
|---|---------|-----------|
| 10 | `test_shadow_routes_cross_exchange_signal_via_manager` | SignalGenerator 시그널 -> StrategyManager -> CrossExchangeStrategy -> TradeRequest -> _execute_shadow_trade_request |
| 11 | `test_shadow_routes_multi_strategy_signals_via_manager` | RealDataSignalProducer 시그널이 올바른 전략에만 라우팅 |
| 12 | `test_shadow_fallback_without_strategy_manager` | `strategy_manager=None`일 때 기존 `_execute_shadow_trade()` 직접 호출 |
| 13 | `test_shadow_fallback_on_routing_exception` | 라우팅 예외 시 `_execute_shadow_trade()` 폴백 + `ROUTING_FALLBACK_TOTAL` 카운터 증가 확인 |
| 14 | `test_strategy_manager_redis_loop_not_started_in_shadow` | Shadow 모드에서 `StrategyManager._running == False` |
| 15 | `test_per_strategy_metrics_populated_after_routing` | 시그널 라우팅 후 `ShadowStats.by_strategy`에 전략별 데이터 존재 |
| 16 | `test_signal_type_matching_cross_exchange` | `strategy_id="cross_exchange_spot"` -> `CrossExchangeStrategy`만 수신 |
| 17 | `test_signal_type_matching_funding_rate` | `strategy_id="funding_rate_arb"` -> `FundingRateStrategy`만 수신 |
| 18 | `test_signal_type_matching_futures_futures` | `strategy_id="futures_futures_spread"` -> `FuturesFuturesStrategy` (STRATEGY_TYPE="futures_futures") 매칭 검증. Step 0 적용 후 `"futures_futures" in "futures_futures_spread"` = True 확인 |
| 19 | `test_empty_route_result_no_fallback` | `route_signal()` 빈 리스트 반환 시 `_execute_shadow_trade()` 미호출 + `ROUTING_FALLBACK_TOTAL` 미증가 확인 |

### 5.3 E2E 테스트 (End-to-End)

| # | 테스트명 | 검증 내용 |
|---|---------|-----------|
| 20 | `test_shadow_10min_with_strategy_manager` | Shadow 10min 실행, trades > 0, 전략별 메트릭 존재 (수동/CI) |
| 21 | `test_no_double_delivery_in_shadow_mode` | Shadow 10min 실행, 동일 시그널 ID가 전략에 1회만 전달됨 확인 |

### 5.4 관측가능성 (Observability)

| 항목 | 위치 | 검증 방법 |
|------|------|-----------|
| 라우팅 결정 로그 | `shadow_mode.signal_routed` | structlog에서 `requests_generated` 필드 확인 |
| 매칭 스킵 로그 | `route_signal: skip` | debug 레벨에서 미스매칭 전략 확인 |
| 폴백 발생 로그 | `shadow_mode.strategy_routing_failed` | warning 레벨 모니터링 |
| **폴백 Prometheus** | `shadow_routing_fallback_total{reason="routing_exception"}` | Prometheus 쿼리로 폴백 빈도 모니터링 |
| Prometheus 메트릭 | `TRADES_TOTAL{strategy=...}` | 전략별 라벨이 올바른 전략 ID로 집계됨 |
| StrategyMetrics | `StrategyManager.get_all_metrics_summary()` | `signals_received > 0` for 관련 전략 |

---

## 6. ADR (Architecture Decision Record)

### Decision

Shadow 모드에서 시그널 라우팅을 `StrategyManager.route_signal()` 새 메서드에 위임하고, Redis `_consume_loop`은 Shadow 모드에서 비활성화한다. `FuturesFuturesStrategy.STRATEGY_TYPE`을 `"futures_futures"`로 변경하여 signal_id 매칭 불일치를 해결한다.

### Drivers

1. 이중 전달 방지: Redis consume loop + 직접 호출이 동시 실행되면 PnL 과다 집계
2. 타입 매칭 일관성: Live 모드와 Shadow 모드가 동일한 `_should_route()` 로직 사용
3. 최소 변경 원칙: 이미 90% 구현된 코드에서 결함 5건만 정밀 수정
4. signal_id 매칭 정합성: `futures_futures_spread` <-> `futures_futures` 공통 prefix로 substring 매칭 보장

### Alternatives Considered

| 대안 | 기각 사유 |
|------|-----------|
| Option B (인라인 _should_route 복제) | DRY 위반, 장기 유지보수 비용, Live/Shadow 라우팅 분기 위험 |
| Redis consume loop 유지 + 중복 제거 | 복잡도 증가 (dedup key 관리), Redis 왕복 지연, Shadow에서 불필요 |
| 전략별 Redis Stream 분리 | 과도한 아키텍처 변경, Phase B-4 범위 초과 |
| multi_signal.py의 signal_id 변경 | 가드레일 위반 (RealDataSignalProducer/MultiStrategySignalProducer 변경 금지), 다른 소비자에 영향 가능 |

### Why Chosen

- `route_signal()`은 기존 `_dispatch()`와 거의 동일한 로직이지만 Redis 없이 동기적으로 동작
- `_should_route()` 재사용으로 매칭 로직 단일 소스
- Shadow에서 Redis loop 비활성화로 이중 전달 완전 차단
- 기존 코드의 90%를 유지하면서 5건의 결함만 수정
- `STRATEGY_TYPE` 변경은 전략 클래스 1줄 수정으로 최소 영향

### Consequences

**긍정적**:
- Shadow와 Live의 라우팅 매칭 로직 일관성 보장
- 이중 전달 위험 제거
- 전략별 메트릭이 정확해짐 (signals_received가 관련 시그널만 카운트)
- 라우팅 결정에 대한 관측가능성 확보 (로그 + Prometheus 폴백 카운터)
- futures_futures 전략이 route_signal() 도입 후에도 정상 동작

**부정적**:
- `StrategyManager`에 public 메서드 1개 추가 (API surface 증가)
- Shadow 모드의 Redis 미사용으로 Redis 기반 디버깅/모니터링 불가 (SignalGenerator의 Redis publish는 유지되므로 관측은 가능)
- `FuturesFuturesStrategy.STRATEGY_TYPE` 변경으로 기존에 `"futures_futures_cross"` 문자열을 하드코딩한 코드가 있으면 영향 (executor가 grep 확인 필요)

### Follow-ups

1. **US-024**: 전략별 메트릭 추적 강화 (per-strategy PnL, win_rate Prometheus gauge)
2. **US-026**: 통합 테스트 작성 (이 플랜의 Section 5)
3. **Phase B-5 (GAP-4)**: N-leg TradeRequest 실행 지원 (`_execute_shadow_trade_request`는 이미 존재하나 3-leg 이상 검증 필요)
4. 결함 4 (추가 컨텍스트 전달)은 Phase B-5에서 전략 인터페이스 확장 시 함께 해결

---

## 7. 가드레일

### Must Have
- Step 0 (STRATEGY_TYPE 정렬)이 Step 1보다 먼저 실행됨
- `StrategyManager.route_signal()`이 기존 `_should_route()` 로직 재사용 (복제 금지)
- `strategy_manager=None` 시 기존 동작 100% 유지
- Shadow 모드에서 `StrategyManager._consume_loop` 미실행
- 라우팅 실패 시 `_execute_shadow_trade()` 폴백 + `ROUTING_FALLBACK_TOTAL` Prometheus 카운터 증가
- **빈 리스트 반환 시 폴백 미실행** (정상 필터링 동작)
- 기존 테스트 3,016건 전수 통과

### Must NOT Have
- `BaseStrategy` 또는 개별 전략 클래스 변경 (**예외: `futures_futures.py:37` STRATEGY_TYPE만 허용**)
- `SignalGenerator` 또는 `RealDataSignalProducer` 변경
- `MultiStrategySignalProducer` 변경
- `StrategyManager._consume_loop`, `_dispatch` 변경
- 비활성 전략 (stat_arb, latency_arb) 강제 활성화
- 새 외부 의존성 추가
- `_execute_shadow_trade()` 메서드 제거 (폴백 용도로 유지)

---

## 8. 실행 순서 (Executor 지침)

```
Step 0: engine/src/strategies/futures_futures.py -- STRATEGY_TYPE 변경 ("futures_futures_cross" -> "futures_futures")
        grep -r "futures_futures_cross" engine/ 로 다른 참조 확인 후 필요 시 업데이트
        pytest tests/ -k futures_futures -v

Step 1: engine/src/strategies/manager.py -- route_signal() 추가
        pytest tests/unit/strategies/test_manager.py -v

Step 2: engine/src/modes/shadow.py -- _route_signal_to_strategies 교체 + ROUTING_FALLBACK_TOTAL 카운터 추가
        pytest tests/unit/test_shadow_mode.py -v  (기존 테스트 하위호환)

Step 3: engine/src/main.py -- Shadow 모드 Redis loop 조건부 비활성화
        pytest tests/unit/test_main_engine.py -v

Step 4: engine/src/modes/shadow.py -- 라우팅 로그 강화 (Step 2에 포함 가능)
        pytest tests/ -x --tb=short  (전체 회귀)

Step 5: tests/unit/strategies/test_manager.py -- route_signal 단위 테스트 9건
        tests/integration/test_shadow_strategy_integration.py -- 통합 테스트 10건 (futures_futures 매칭 + 빈 리스트 검증 포함)
        pytest tests/ -x --tb=short  (전체)
```

---

## 9. 성공 기준

1. `pytest tests/ -x --tb=short` -- 기존 3,016 + 신규 ~19건 = ~3,035건 전수 통과
2. `FuturesFuturesStrategy.STRATEGY_TYPE == "futures_futures"` -- `"futures_futures" in "futures_futures_spread"` = True
3. `StrategyManager.route_signal(signal)` -- 매칭 전략에만 디스패치, `list[TradeRequest]` 반환
4. Shadow `_on_orderbook` -- StrategyManager 있을 때 `route_signal()` 경유, 없으면 직접 실행
5. Shadow 모드에서 `StrategyManager._running == False` (Redis consume loop 미실행)
6. 라우팅 결정이 구조화 로그로 추적 가능 (`shadow_mode.signal_routed`)
7. **라우팅 폴백 시 `ROUTING_FALLBACK_TOTAL` Prometheus 카운터 증가**
8. **`route_signal()` 빈 리스트 반환 시 폴백 미실행** (정상 동작)
9. **`futures_futures_spread` 시그널이 `FuturesFuturesStrategy`에 정상 라우팅**
10. Shadow 10min 실행 시 최소 1개 전략에서 `signals_received > 0`
11. 기존 cross_exchange 동작 유지 (PnL 양수, trades > 0)

---

## 변경 이력

| 버전 | 날짜 | 변경 사항 |
|------|------|-----------|
| v1 | 2026-03-08 | 초안 작성 (Planner + Architect 합의) |
| v2 | 2026-03-08 | Critic ITERATE 반영: Step 0 추가 (futures_futures STRATEGY_TYPE 정렬), Step 2에 ROUTING_FALLBACK_TOTAL Prometheus 카운터 추가, 빈 리스트 반환 동작 명시, test_signal_type_matching_futures_futures + test_empty_route_result_no_fallback 테스트 추가 |
