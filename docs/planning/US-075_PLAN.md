# US-075 구현 계획: futures_futures 전략 활성화 (OKX/Bybit Futures 수집기)

> **Architect 분석** — READ-ONLY 분석 결과. 코드 변경은 executor가 담당.

---

## 요약

`futures_futures` 전략이 현재 `binance_futures` 단일 거래소만 `futures_books`에 등록되어 신호를 생성할 수 없는 상태 (`len(fut_books) < 2`로 항상 early return). OKX futures와 Bybit futures WebSocket 수집기를 추가하고, 이를 `futures_books` 라우팅 파이프라인에 연결하면 즉시 활성화된다.

**핵심 발견**: `fee_model.py`에 `okx_futures`/`bybit_futures` 수수료가 이미 구성되어 있어 수수료 모델 변경은 불필요.

---

## 분석

### 현재 futures_books 데이터 흐름

```
shadow.py._futures_exchanges = {"binance_futures"}  (line 491)
         ↓ on_orderbook
shadow.py:849  if exchange_id in self._futures_exchanges:
                   futures_books[symbol][exchange_id] = book
         ↓
RealDataSignalProducer._evaluate_futures_futures(symbol, futures_books)
         ↓ (line 223)
if len(fut_books) < 2: return []  ← 항상 여기서 종료 (거래소 1개뿐)
```

### OKX Futures WS API 명세

- **엔드포인트**: `wss://ws.okx.com:8443/ws/v5/public` (**spot과 동일**)
- **채널**: `books50-l2-tbt` (spot과 동일)
- **instId 형식**: `BTC-USDT-SWAP` (spot은 `BTC-USDT`, `-SWAP` 접미사 추가)
- **구독 메시지**: 기존 `OKXCollector._subscribe_message()`와 동일한 형태, instId만 다름
- **응답 파싱**: 기존 `OKXCollector._parse_message()`와 동일, 단 instId에서 `-SWAP` 제거 후 `/` 변환

**근거**: `engine/src/collectors/okx_collector.py:36-96` 참조

### Bybit Futures WS API 명세

- **엔드포인트**: `wss://stream.bybit.com/v5/public/linear` (**spot과 다름! spot은 `/spot`**)
- **채널/토픽**: `orderbook.{depth}.{symbol}` (spot과 동일한 형식)
- **심볼 형식**: `BTCUSDT` (spot과 동일)
- **구독/파싱**: 기존 `BybitCollector`와 완전히 동일, URL만 다름

**근거**: `engine/src/collectors/bybit_collector.py:41-85` 참조, Bybit 공식 문서 확인 (linear vs spot 엔드포인트 분리)

### 수수료 모델 현황

`engine/src/friction/fee_model.py:65-71, 134-138` — `bybit_futures`/`okx_futures` 이미 등록:
```
"bybit_futures": VIP0 maker=0.02%, taker=0.055%
"okx_futures":   VIP0 maker=0.02%, taker=0.05%
"*_futures":     withdrawal_fee = $0 (내부 이전 무료)
```

→ **수수료 모델 변경 불필요**

---

## 루트 코스 (변경 없으면 신호가 0인 이유)

`real_signal_producer.py:222-223`:
```python
fut_books = futures_books.get(symbol, {})
if len(fut_books) < 2:
    return signals  # ← binance_futures 하나뿐이라 항상 여기서 탈출
```

`shadow.py:491`:
```python
self._futures_exchanges: set[str] = {"binance_futures"}  # ← 두 신규 거래소 없음
```

---

## 구현 계획

### Step 1: OKX Futures 수집기 신규 생성
**파일**: `engine/src/collectors/okx_futures_collector.py`

참조: `engine/src/collectors/okx_collector.py` (거의 동일, 2개 차이점만)

```python
class OKXFuturesCollector(BaseCollector):
    _WS_URL = "wss://ws.okx.com:8443/ws/v5/public"  # spot과 동일
    _CHANNEL = "books50-l2-tbt"

    def __init__(self, symbols, on_orderbook=None):
        super().__init__(exchange_id="okx_futures", symbols=symbols, on_orderbook=on_orderbook)

    def _normalize_symbol(self, symbol: str) -> str:
        # "BTC/USDT" → "BTC-USDT-SWAP"
        return symbol.replace("/", "-") + "-SWAP"

    def _denormalize_symbol(self, inst_id: str) -> str:
        # "BTC-USDT-SWAP" → "BTC/USDT"
        return inst_id.removesuffix("-SWAP").replace("-", "/")

    def _subscribe_message(self, symbol):
        return {
            "op": "subscribe",
            "args": [{"channel": self._CHANNEL, "instId": self._normalize_symbol(symbol)}]
        }

    def _parse_message(self, data):
        # OKXCollector._parse_message()와 동일 로직,
        # _denormalize_symbol()에서 -SWAP 제거
        ...
```

**trade-off**: 동일 WS 연결을 spot과 공유하지 않는다 (별도 연결). 심플함 우선.

### Step 2: Bybit Futures 수집기 신규 생성
**파일**: `engine/src/collectors/bybit_futures_collector.py`

참조: `engine/src/collectors/bybit_collector.py` (URL 하나만 다름)

```python
class BybitFuturesCollector(BaseCollector):
    _WS_URL = "wss://stream.bybit.com/v5/public/linear"  # ← spot은 /spot
    _DEPTH = 50

    def __init__(self, symbols, on_orderbook=None):
        super().__init__(exchange_id="bybit_futures", symbols=symbols, on_orderbook=on_orderbook)

    # _subscribe_message, _parse_message → BybitCollector와 100% 동일
```

**단, `_parse_message`에서 symbol denormalize 로직은 동일** (`BTCUSDT → BTC/USDT`)

### Step 3: CollectorManager 업데이트
**파일**: `engine/src/collectors/manager.py`

변경점:
1. import 추가 (lines 17-18 이후):
   ```python
   from src.collectors.okx_futures_collector import OKXFuturesCollector
   from src.collectors.bybit_futures_collector import BybitFuturesCollector
   ```
2. `DEFAULT_EXCHANGES` (line 30) 에 `"okx_futures"`, `"bybit_futures"` 추가
3. `_create_collector()` factory dict (lines 61-69)에 두 항목 추가:
   ```python
   "okx_futures": OKXFuturesCollector,
   "bybit_futures": BybitFuturesCollector,
   ```

**주의**: `KOREAN_EXCHANGES` 에는 추가 안 함 (KRW 매핑 불필요, futures는 USDT 페어)

### Step 4: ShadowMode futures_exchanges 업데이트
**파일**: `engine/src/modes/shadow.py:491`

```python
# Before:
self._futures_exchanges: set[str] = {"binance_futures"}
# After:
self._futures_exchanges: set[str] = {"binance_futures", "okx_futures", "bybit_futures"}
```

이 변경으로 `shadow.py:849`의 라우팅 조건이 세 거래소 모두를 `futures_books`에 저장하게 됨.

### Step 5: RealDataSignalProducer 기본값 업데이트
**파일**: `engine/src/core/real_signal_producer.py:66`

```python
# Before:
self._futures_exchanges: set[str] = futures_exchanges or {"binance_futures"}
# After:
self._futures_exchanges: set[str] = futures_exchanges or {"binance_futures", "okx_futures", "bybit_futures"}
```

Shadow mode는 이미 `futures_exchanges=self._futures_exchanges`를 전달(`shadow.py:499`)하므로, 이 변경은 비-shadow 호출 경로(테스트 등)를 위한 방어적 fallback.

### Step 6: 테스트 작성
**신규 파일**:
- `engine/tests/collectors/test_okx_futures_collector.py`
- `engine/tests/collectors/test_bybit_futures_collector.py`

**테스트 항목** (각 수집기):
1. `test_ws_url()` — URL 값 검증 (`/linear`, `/public`)
2. `test_subscribe_message()` — BTC/USDT → BTC-USDT-SWAP 변환 확인
3. `test_parse_snapshot()` — snapshot 메시지 파싱 → (symbol, bids, asks) 반환
4. `test_parse_ack_returns_none()` — event ack 메시지 → None 반환
5. `test_symbol_denormalize()` — BTC-USDT-SWAP → BTC/USDT 변환
6. `test_on_orderbook_callback()` — 콜백 호출 확인 (AsyncMock)

**수정 파일**:
- `engine/tests/collectors/test_manager.py` (신규 생성 필요): okx_futures/bybit_futures가 DEFAULT_EXCHANGES에 있는지 확인

---

## 파일 변경 요약

| # | 파일 | 작업 | 크기 |
|---|------|------|------|
| 1 | `engine/src/collectors/okx_futures_collector.py` | 신규 생성 | ~70줄 |
| 2 | `engine/src/collectors/bybit_futures_collector.py` | 신규 생성 | ~65줄 |
| 3 | `engine/src/collectors/manager.py` | 수정 (import+factory+defaults) | +6줄 |
| 4 | `engine/src/modes/shadow.py:491` | 수정 (1줄) | +2 exchange IDs |
| 5 | `engine/src/core/real_signal_producer.py:66` | 수정 (1줄) | +2 exchange IDs |
| 6 | `engine/tests/collectors/test_okx_futures_collector.py` | 신규 생성 | ~80줄 |
| 7 | `engine/tests/collectors/test_bybit_futures_collector.py` | 신규 생성 | ~70줄 |
| 8 | `engine/tests/collectors/test_manager_futures.py` | 신규 생성 (or 기존 확장) | ~30줄 |

---

## Trade-offs

| 옵션 | 장점 | 단점 |
|------|------|------|
| 별도 WS 연결 (채택) | 단순, 독립 재연결, 기존 패턴 일관성 | 연결 수 증가 (OKX spot과 별도) |
| OKX spot과 WS 공유 | 연결 1개로 절약 | Collector 리팩토링 필요, 복잡도 증가 |
| `instType=SWAP` 필터 추가 | OKX 공식 권장 방식 | `books50-l2-tbt`에서는 instId로 충분 |
| Bybit `/linear` 엔드포인트 | 공식 선형 선물 전용 | 별도 연결 필요 (spot `/spot`과 분리) |

---

## Acceptance Criteria 검증 방법

| 조건 | 검증 방법 |
|------|-----------|
| OKX futures WS 수집기 구현 | `pytest tests/collectors/test_okx_futures_collector.py` |
| Bybit futures WS 수집기 구현 | `pytest tests/collectors/test_bybit_futures_collector.py` |
| CollectorManager 2+ futures 등록 | `"okx_futures" in CollectorManager.DEFAULT_EXCHANGES` |
| futures_futures 전략 2개 거래소 연결 | `len(futures_books[symbol]) >= 2` 확인 |
| 1H Shadow에서 신호 생성 | Shadow 실행 후 `real_signal_producer.futures_futures_signal` 로그 확인 |

---

## 의존성 확인

- **수수료 모델**: ✅ 이미 구성됨 (`fee_model.py:65-71`)
- **신호 생성 로직**: ✅ 변경 불필요 (`_evaluate_futures_futures`는 2개 이상이면 동작)
- **FuturesFuturesStrategy**: ✅ 변경 불필요 (`on_signal` 로직 완비)
- **websockets 라이브러리**: ✅ 이미 사용 중 (`BaseCollector._connect_and_listen`)

---

## 레퍼런스

- `engine/src/collectors/binance_futures_collector.py` — 참조 구현 (전체)
- `engine/src/collectors/okx_collector.py:36-96` — OKX spot 패턴 (futures의 기반)
- `engine/src/collectors/bybit_collector.py:41-85` — Bybit spot 패턴 (futures의 기반)
- `engine/src/collectors/manager.py:30,61-79` — 팩토리 패턴 및 DEFAULT_EXCHANGES
- `engine/src/modes/shadow.py:491,849` — futures_exchanges 라우팅
- `engine/src/core/real_signal_producer.py:62-66,222-223` — futures_books 처리
- `engine/src/friction/fee_model.py:65-71,134-138` — 수수료 이미 등록됨
