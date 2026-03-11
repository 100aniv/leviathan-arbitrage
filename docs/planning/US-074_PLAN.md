# US-074: Coinone WebSocket 안정성 강화 + 재연결 로직 개선 — 구현 계획

> Phase I | Priority: 77 | Architect: Claude Opus 4.6
> 작성일: 2026-03-11

---

## 1. 현재 상태 분석

### 1.1 코드 구조 현황

`CoinoneCollector` (`engine/src/collectors/coinone_collector.py:26-109`)는 84줄의 얇은 래퍼.
모든 재연결/백오프/WS 로직이 `BaseCollector` (`base_collector.py`)에 위임된다.

```
CoinoneCollector (110줄)
├── __init__()     : ping_interval=1800, ping_timeout=30 설정
├── _ws_url()      : "wss://stream.coinone.co.kr"
├── _subscribe_message() : {"request_type": "SUBSCRIBE", ...}
└── _parse_message()     : DATA + ORDERBOOK 만 처리

BaseCollector (191줄)  ← 실질적인 연결/재연결 로직
├── start()        : while loop + _connect_and_listen() + _backoff()
├── _connect_and_listen() : websockets.connect() + async for raw in ws
├── _backoff()     : exponential (1→2→4→...→60초) — 지터 없음 ⚠️
└── stats          : _last_message_time 추적만, 감시 없음 ⚠️
```

### 1.2 진단된 결함 (file:line 증거)

| # | 결함 | 위치 | 심각도 |
|---|------|------|--------|
| D1 | **지터 없는 지수 백오프** — 다수 collector 동시 끊김 시 thundering herd | `base_collector.py:144-148` | HIGH |
| D2 | **데이터 갭 감시자 없음** — TCP 연결은 살아있지만 메시지가 오지 않는 좀비 연결 시 `async for raw in ws:`에서 영구 대기 | `base_collector.py:119`, `base_collector.py:59` | HIGH |
| D3 | **애플리케이션 레벨 PING 미구현** — 도컴에 "30-minute PING keepalive required"라고 명시됐지만, `ping_interval=1800`은 WebSocket 프로토콜 레벨 ping (RFC 6455)이고 Coinone이 기대하는 JSON PING이 아닐 수 있음 | `coinone_collector.py:42-49` | MEDIUM |
| D4 | **심볼별 stale 감지 없음** — BithumbCollector는 `is_symbol_stale()` 보유 (`bithumb_collector.py:207-212`), CoinoneCollector는 미보유 | `coinone_collector.py` 전체 | LOW |
| D5 | **PING/PONG 프레임 무음 폐기** — `_parse_message()`가 `response_type != DATA`를 `None` 반환으로 버림. 로그/메트릭 없음 | `coinone_collector.py:83-84` | LOW |

### 1.3 다른 수집기와의 비교

| 기능 | Binance | Upbit | Bithumb | Coinone |
|------|---------|-------|---------|---------|
| 지터 백오프 | ❌ (BaseCollector 공통) | ❌ | ❌ | ❌ |
| 데이터 갭 감시자 | ❌ | ❌ | ❌ | ❌ |
| 심볼 stale 감지 | ❌ | ❌ | ✅ `is_symbol_stale()` | ❌ |
| REST 스냅샷 복구 | ❌ | ❌ | ✅ `refresh_snapshots()` | ❌ |
| app-level PING | ❌ | ❌ | ❌ | 필요 (미구현) |

**결론**: D1(지터)과 D2(갭 감시자)는 BaseCollector 공통 개선으로 모든 수집기에 혜택을 줄 수 있음. D3~D5는 Coinone 특화.

---

## 2. 아키텍처 설계

### 2.1 D1 수정: 지터 추가 (BaseCollector 공통)

**현재 코드** (`base_collector.py:144-148`):
```python
async def _backoff(self) -> None:
    logger.info("collector_reconnecting", ...)
    await asyncio.sleep(self._reconnect_delay)
    self._reconnect_delay = min(self._reconnect_delay * 2, self.MAX_RECONNECT_DELAY)
```

**변경 후**:
```python
import random

async def _backoff(self) -> None:
    jitter = random.uniform(0.75, 1.25)          # ±25% 랜덤 지터
    delay = self._reconnect_delay * jitter
    logger.info("collector_reconnecting", exchange=self.exchange_id,
                delay_s=round(delay, 2), jitter=round(jitter, 3))
    await asyncio.sleep(delay)
    self._reconnect_delay = min(self._reconnect_delay * 2, self.MAX_RECONNECT_DELAY)
```

**트레이드오프**: BaseCollector 수정이므로 모든 8개 수집기에 영향.
- 장점: 전체 시스템의 thundering herd 방지
- 단점: 테스트에서 `random.uniform`을 mock해야 함
- **결정: BaseCollector 공통 수정** (범위가 명확하고 위험 낮음)

### 2.2 D2 수정: 데이터 갭 감시자 (CoinoneCollector 특화)

CoinoneCollector에서 `_connect_and_listen()`을 오버라이드해 감시자 태스크를 병렬로 실행:

```
_connect_and_listen() 오버라이드:
├── asyncio.create_task(_data_gap_watchdog())  ← 신규
│   ├── 30초마다 _last_message_time 확인
│   ├── 120초 이상 무메시지 → ws.close() 강제 호출
│   └── _connect_and_listen() 종료 시 자동 취소
├── asyncio.create_task(_application_ping_loop())  ← 신규 (D3)
│   ├── 25분마다 {"request_type": "PING"} 전송
│   └── _connect_and_listen() 종료 시 자동 취소
└── super()._connect_and_listen() 호출 (기존 WS 수신 루프)
```

**갭 감지 임계값**:
- Coinone orderbook 업데이트 주기: ~100-500ms (활성 심볼 기준)
- 감시 주기: 30초 (`GAP_CHECK_INTERVAL_S = 30`)
- 재연결 임계값: 120초 무메시지 (`GAP_RECONNECT_THRESHOLD_S = 120`)
- 이유: 거래량 낮은 심볼은 60~90초 업데이트 간격 가능; 120초는 합리적 안전 마진

### 2.3 D3 수정: 애플리케이션 레벨 PING/PONG

Coinone 공식 문서에서 확인 필요하지만, 도컴에 "30-minute PING keepalive required" 명시.

현재: `ping_interval=1800`은 WebSocket 프로토콜 레벨 ping 프레임 → Coinone 서버가 이를 응답하면 충분.

그러나 일부 거래소는 애플리케이션 레벨 JSON PING을 별도로 요구함:
```json
// 전송
{"request_type": "PING"}
// 기대 응답
{"response_type": "PONG"}
```

**구현 계획**:
1. `_parse_message()`에서 `response_type=PONG` 처리 추가 (수신 로그)
2. `_application_ping_loop()`: 25분마다 JSON PING 전송 (30분 타임아웃 5분 전)
3. websockets 내장 `ping_interval=1800`은 **유지** (이중 보호)

### 2.4 D4 수정: 심볼별 stale 감지

BithumbCollector 패턴(`bithumb_collector.py:207-212`)을 복사:

```python
def __init__(self, ...):
    ...
    self._last_symbol_time: dict[str, float] = {}  # 신규

def _parse_message(self, data):
    ...
    symbol = _denormalize_symbol(...)
    self._last_symbol_time[symbol] = time.monotonic()  # 심볼별 갱신
    return symbol, bids, asks

def is_symbol_stale(self, symbol: str, max_age_s: float = 300.0) -> bool:
    last = self._last_symbol_time.get(symbol)
    if last is None:
        return True
    return (time.monotonic() - last) > max_age_s
```

---

## 3. 변경 파일 및 구현 상세

### 3.1 파일: `engine/src/collectors/base_collector.py`

**변경 범위**: `_backoff()` 메서드만 (`base_collector.py:144-148`, +3줄)

```python
# 추가 import
import random

# _backoff() 수정
async def _backoff(self) -> None:
    """Exponential backoff with ±25% jitter between reconnection attempts."""
    jitter = random.uniform(0.75, 1.25)
    delay = self._reconnect_delay * jitter
    logger.info("collector_reconnecting", exchange=self.exchange_id,
                delay_s=round(delay, 2))
    await asyncio.sleep(delay)
    self._reconnect_delay = min(self._reconnect_delay * 2, self.MAX_RECONNECT_DELAY)
```

### 3.2 파일: `engine/src/collectors/coinone_collector.py`

**추가 구성 상수**:
```python
_GAP_CHECK_INTERVAL_S = 30       # 갭 감시 주기 (초)
_GAP_RECONNECT_THRESHOLD_S = 120 # 재연결 임계값 (초)
_APP_PING_INTERVAL_S = 1500      # 애플리케이션 PING 주기 (25분)
```

**추가 메서드**:

1. `_connect_and_listen()` 오버라이드:
```python
async def _connect_and_listen(self) -> None:
    import websockets, json as _json, asyncio as _asyncio
    url = self._ws_url()
    logger.info("collector_connecting", exchange=self.exchange_id, url=url)

    async with websockets.connect(
        url,
        ping_interval=self.ping_interval,
        ping_timeout=self.ping_timeout,
    ) as ws:
        self._ws = ws
        self._connected = True
        self._reconnect_delay = self.INITIAL_RECONNECT_DELAY
        logger.info("collector_connected", exchange=self.exchange_id)

        # 구독 메시지 전송
        for symbol in self.symbols:
            msg = self._subscribe_message(symbol)
            await ws.send(_json.dumps(msg) if isinstance(msg, dict) else msg)
            logger.info("collector_subscribed", exchange=self.exchange_id, symbol=symbol)

        # 병렬 태스크 실행
        watchdog_task = _asyncio.create_task(self._data_gap_watchdog(ws))
        ping_task = _asyncio.create_task(self._application_ping_loop(ws))

        try:
            async for raw in ws:
                if not self._running:
                    break
                self._last_message_time = time.monotonic()
                self._message_count += 1
                try:
                    await self._handle_message(raw)
                except Exception as exc:
                    logger.warning("collector_parse_error",
                                   exchange=self.exchange_id, error=str(exc))
        finally:
            watchdog_task.cancel()
            ping_task.cancel()
            await _asyncio.gather(watchdog_task, ping_task, return_exceptions=True)

    self._connected = False
```

2. `_data_gap_watchdog()`:
```python
async def _data_gap_watchdog(self, ws) -> None:
    """Monitor for data gaps; close WS if no messages for threshold seconds."""
    import asyncio
    while True:
        await asyncio.sleep(_GAP_CHECK_INTERVAL_S)
        if self._last_message_time == 0.0:
            continue  # 아직 메시지 미수신 (연결 직후)
        age = time.monotonic() - self._last_message_time
        if age > _GAP_RECONNECT_THRESHOLD_S:
            logger.warning(
                "coinone_data_gap_detected",
                exchange=self.exchange_id,
                gap_seconds=round(age, 1),
            )
            await ws.close()
            return
```

3. `_application_ping_loop()`:
```python
async def _application_ping_loop(self, ws) -> None:
    """Send application-level JSON PING every 25 minutes."""
    import asyncio, json as _json
    while True:
        await asyncio.sleep(_APP_PING_INTERVAL_S)
        try:
            await ws.send(_json.dumps({"request_type": "PING"}))
            logger.debug("coinone_app_ping_sent", exchange=self.exchange_id)
        except Exception as exc:
            logger.warning("coinone_app_ping_failed",
                           exchange=self.exchange_id, error=str(exc))
            return
```

4. `_parse_message()` 확장 (PONG 처리):
```python
def _parse_message(self, data: dict) -> tuple[str, list, list] | None:
    response_type = data.get("response_type")

    # 애플리케이션 레벨 PONG 응답 처리
    if response_type == "PONG":
        logger.debug("coinone_pong_received", exchange=self.exchange_id)
        return None

    if response_type != "DATA":
        return None
    # ... 기존 로직 동일
```

5. `is_symbol_stale()` 추가 (D4):
```python
def is_symbol_stale(self, symbol: str, max_age_s: float = 300.0) -> bool:
    """Check if a symbol's data is stale (no update in max_age_s seconds)."""
    last = self._last_symbol_time.get(symbol)
    if last is None:
        return True
    return (time.monotonic() - last) > max_age_s
```

---

## 4. 테스트 계획

### 4.1 신규 테스트 파일: `engine/tests/unit/test_coinone_stability.py`

| # | 테스트 클래스 | 테스트명 | 검증 내용 |
|---|-------------|---------|----------|
| 1 | `TestBackoffJitter` | `test_backoff_delay_has_jitter` | random.uniform mock → 지터 적용 확인 |
| 2 | `TestBackoffJitter` | `test_backoff_stays_within_bounds` | delay ≤ MAX_RECONNECT_DELAY 확인 |
| 3 | `TestBackoffJitter` | `test_backoff_increases_exponentially` | 연속 호출 시 delay 증가 패턴 확인 |
| 4 | `TestDataGapWatchdog` | `test_watchdog_closes_ws_on_gap` | 120초 초과 시 ws.close() 호출 |
| 5 | `TestDataGapWatchdog` | `test_watchdog_does_not_trigger_within_threshold` | 60초 갭 → ws.close() 미호출 |
| 6 | `TestDataGapWatchdog` | `test_watchdog_skips_when_no_messages_yet` | `_last_message_time=0` → 무시 |
| 7 | `TestAppPingLoop` | `test_ping_loop_sends_json_ping` | 25분 슬립 후 JSON PING 전송 확인 |
| 8 | `TestAppPingLoop` | `test_ping_loop_handles_send_failure` | ws.send 예외 → 로그 + 종료 |
| 9 | `TestParseMessage` | `test_parse_pong_returns_none` | `response_type=PONG` → None 반환 |
| 10 | `TestParseMessage` | `test_parse_unknown_type_returns_none` | 기타 타입 → None 반환 |
| 11 | `TestParseMessage` | `test_parse_data_orderbook_valid` | 정상 DATA/ORDERBOOK → (symbol, bids, asks) |
| 12 | `TestSymbolStale` | `test_is_symbol_stale_no_data` | 미수신 심볼 → stale=True |
| 13 | `TestSymbolStale` | `test_is_symbol_stale_fresh_data` | 최근 수신 → stale=False |
| 14 | `TestSymbolStale` | `test_is_symbol_stale_old_data` | 300초 초과 → stale=True |
| 15 | `TestSymbolStale` | `test_last_symbol_time_updated_on_parse` | `_parse_message()` 호출 → `_last_symbol_time` 갱신 |

**예상 신규 테스트 수: 15개**

### 4.2 기존 테스트 업데이트: `engine/tests/unit/test_collectors_exchange.py`

- `TestCoinoneParseMessage` 클래스에 PONG 처리 케이스 추가 (+2 테스트)

### 4.3 BaseCollector 지터 테스트: `engine/tests/unit/test_base_collector.py`

- `test_backoff_applies_jitter_via_random_mock` (+1 테스트)

**예상 총 추가 테스트: 18개**

---

## 5. 변경 파일 목록

| 파일 | 변경 유형 | 변경 범위 |
|------|----------|----------|
| `engine/src/collectors/base_collector.py` | 수정 | `_backoff()` 지터 추가 (+5줄, `import random` 포함) |
| `engine/src/collectors/coinone_collector.py` | 수정 | `_connect_and_listen()` 오버라이드, `_data_gap_watchdog()`, `_application_ping_loop()`, `_parse_message()` PONG 처리, `is_symbol_stale()` 추가 (+80줄) |
| `engine/tests/unit/test_coinone_stability.py` | **신규** | 15개 테스트 |
| `engine/tests/unit/test_collectors_exchange.py` | 수정 | CoinoneCollector PONG 테스트 +2개 |
| `engine/tests/unit/test_base_collector.py` (있으면) | 수정 | 지터 테스트 +1개 |

**예상 총 변경량: ~150줄** (신규 ~120 + 수정 ~30)

---

## 6. 구현 순서 (Step-by-Step)

```
Step 1: base_collector.py _backoff() 지터 추가
        → 단순 수정, 5줄 변경

Step 2: coinone_collector.py 확장
  2a. 상수 추가 (_GAP_CHECK_INTERVAL_S 등)
  2b. __init__() 에 _last_symbol_time: dict 추가
  2c. _parse_message() PONG 처리 + _last_symbol_time 갱신
  2d. is_symbol_stale() 추가
  2e. _data_gap_watchdog() 추가
  2f. _application_ping_loop() 추가
  2g. _connect_and_listen() 오버라이드

Step 3: test_coinone_stability.py 작성 + pytest 통과
Step 4: 기존 test_collectors_exchange.py 업데이트
Step 5: (Step 2.5 통합검증) 전체 pytest 통과 확인
        → cd engine && python -m pytest tests/ -x --tb=short
```

---

## 7. 수용 기준 매핑

| AC | 구현 | 검증 |
|----|------|------|
| Coinone WS 재연결 로직 강화 (exponential backoff + jitter) | `base_collector.py:_backoff()` 지터 추가 | `test_backoff_applies_jitter_via_random_mock` |
| 연결 끊김 시 자동 복구 | 기존 BaseCollector 루프 + 지터 | 기존 reconnect 테스트 + D1 |
| 데이터 갭 감지 | `_data_gap_watchdog()` — 120초 무메시지 → ws.close() | `test_watchdog_closes_ws_on_gap` |
| Heartbeat/ping-pong 프레임 정상 처리 | `_parse_message()` PONG 처리 + `_application_ping_loop()` | `test_parse_pong_returns_none`, `test_ping_loop_sends_json_ping` |
| 24H 연속 실행 안정성 | 감시자 + 지터 + 앱 PING 조합 | Shadow 10분 실행 PASS (Phase C 검증) |

---

## 8. 리스크 및 트레이드오프

### 8.1 트레이드오프 표

| 옵션 | 장점 | 단점 |
|------|------|------|
| **A. BaseCollector에 지터 추가 (선택)** | 8개 모든 수집기에 즉시 혜택, 단일 수정 | 기존 test에서 sleep mock 수정 필요 가능성 |
| B. CoinoneCollector에만 지터 오버라이드 | 범위 최소화, 다른 수집기 영향 없음 | 중복 코드, 향후 다른 수집기도 별도 수정 필요 |
| **C. _connect_and_listen() 오버라이드로 감시자 추가 (선택)** | Coinone 특화 로직 분리, BaseCollector 단순 유지 | `super()`가 subscribe 중복 전송 가능 → 직접 구현 필요 |
| D. BaseCollector에 감시자 추가 | 공통 혜택 | BaseCollector 복잡도 증가, 거래소마다 타임아웃 설정 달라야 함 |

### 8.2 주요 리스크

| 리스크 | 완화책 |
|--------|--------|
| 감시자가 정상 저 트래픽 심볼에서 오탐 | `GAP_RECONNECT_THRESHOLD_S=120`은 보수적. 필요 시 환경변수로 조정 가능하게 설계 |
| `_connect_and_listen()` 오버라이드 시 구독 로직 중복 | `super()` 미호출하고 직접 구독 + 수신 루프 작성 (BinanceCollector 패턴 참조: `binance_collector.py:94-122`) |
| 애플리케이션 PING이 Coinone에서 실제 필요 없을 수 있음 | 실패해도 ws.close() 없이 로그만 → 무해; websockets 프로토콜 ping이 fallback |
| asyncio.gather 예외 전파 | `return_exceptions=True`로 안전 수집 |

---

## 9. 아키텍처 노트

- **BinanceCollector 오버라이드 패턴 참조**: `binance_collector.py:94-122` — `super()` 없이 `_connect_and_listen()` 완전 오버라이드. 동일 패턴 적용.
- **BithumbCollector stale 패턴 참조**: `bithumb_collector.py:207-212` — `is_symbol_stale()` 구현 동일 복사.
- **`_last_message_time` 접근**: BaseCollector의 `_last_message_time`은 `async for` 루프에서 갱신 (`base_collector.py:122`). 오버라이드된 루프에서도 동일하게 갱신 필수.
- **24H 테스트 대리**: Shadow 10분 실행으로 대체 (Phase C). 실제 24H는 프로덕션 환경 모니터링으로 확인.
- **`docs/planning/` 경로**: 기존 US-072_PLAN.md 형식 준수.
