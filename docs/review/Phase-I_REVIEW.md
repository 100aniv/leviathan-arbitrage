# Phase I Code Review — US-073 / US-074 / US-075

**Reviewer**: code-reviewer
**Date**: 2026-03-11
**Files Reviewed**: 8
**Total Issues**: 6 (0 CRITICAL, 0 HIGH, 4 MEDIUM, 2 LOW)

---

## Stage 1 — Spec Compliance ✅

| US | 요구사항 | 구현 | 판정 |
|----|---------|------|------|
| US-073 | Bithumb 누적 orderbook + stale 감지 + parallel refresh | `_books` dict 누적 + `_stale_watch_loop` + `refresh_symbols(sem=5)` | ✅ PASS |
| US-074 | Coinone watchdog + app-level PING + stale 감지 | `_data_gap_watchdog` + `_application_ping_loop` + `is_symbol_stale` | ✅ PASS |
| US-075 | OKX/Bybit futures 수집기 신규 추가 + factory/set 등록 | `okx_futures_collector.py` + `bybit_futures_collector.py` + manager/shadow/real_signal_producer 등록 | ✅ PASS |

---

## Stage 2 — LSP Diagnostics

모든 수정 파일에서 **타입 오류 0건** 확인:
- `bithumb_collector.py` — clean
- `coinone_collector.py` — clean
- `okx_futures_collector.py` — clean
- `bybit_futures_collector.py` — clean
- `base_collector.py` — clean

---

## 이중 슬리피지 검증 ✅

`shadow.py:416-436` 확인:
```python
# k=0: zero slippage in PaperExecutor — SignalGenerator already applies
# CEXOrderbookSlippage, so PaperExecutor must NOT add more (double-count).
self._paper_executor = paper_executor or PaperExecutor(
    slippage_model=BookWalkSlippage(books=self._books),  # ← PowerLaw 아님
    fee_rate=Decimal("0"),
    ...
)
```
- PaperExecutor는 `BookWalkSlippage` 사용 (PowerLaw k=0 유지)
- `fee_model.py`에 `okx_futures`, `bybit_futures` 수수료 테이블 정상 등록
- **이중 슬리피지 위험 없음**

---

## 프로토콜 엔드포인트 검증 ✅

| 거래소 | 설정값 | 판정 |
|--------|--------|------|
| OKX futures | `wss://ws.okx.com:8443/ws/v5/public` + `-SWAP` suffix | ✅ 올바름 |
| Bybit futures | `wss://stream.bybit.com/v5/public/linear` | ✅ 올바름 |
| OKX instId | `BTC/USDT` → `BTC-USDT-SWAP` | ✅ 올바름 |
| Bybit topic | `orderbook.50.BTCUSDT` | ✅ 올바름 |

---

## Issues

### [MEDIUM] refresh_symbols 가격 sanity check 누락

**File**: `engine/src/collectors/bithumb_collector.py:242-264`

`_fetch_initial_snapshots`(L109-115)에는 `top_bid/top_ask > 10 or < 0.1` sanity guard가 있지만, `refresh_symbols`(stale 재동기화 경로)에는 이 검사가 없다. Bithumb 소형코인 stale 후 REST에서 이상 가격이 내려오면 `_books`에 그대로 기록되어 fake spread를 유발한다.

**Fix**: `refresh_symbols`의 `_fetch_one` 내부에 동일한 sanity check 추가:
```python
top_bid = float(bids[0][0]) if bids else 0
top_ask = float(asks[0][0]) if asks else 0
if top_ask > 0 and (top_bid / top_ask > 10 or top_bid / top_ask < 0.1):
    logger.warning("bithumb_refresh_price_insane", symbol=symbol, bid=top_bid, ask=top_ask)
    return False
```

---

### [MEDIUM] Coinone watchdog — _last_message_time 미리셋 시 즉시 재연결

**File**: `engine/src/collectors/coinone_collector.py:127-141`

`_last_message_time`은 `BaseCollector.__init__`에서 `0.0`으로 초기화되며, 연결이 끊어져도 리셋되지 않는다. 컬렉터가 >120초 단절 후 재연결하면 첫 watchdog 체크(30초 후)에서 `age = time.monotonic() - self._last_message_time > 120`이 즉시 참이 되어 방금 연결된 WS를 강제 close한다. 이 경우 연결→watchdog 트리거→재연결 루프가 발생할 수 있다.

**Fix**: `_connect_and_listen` 진입 직후 `_last_message_time` 리셋:
```python
async with websockets.connect(...) as ws:
    self._ws = ws
    self._connected = True
    self._last_message_time = 0.0  # ← watchdog이 새 세션 기준으로 계산하도록
    self._reconnect_delay = self.INITIAL_RECONNECT_DELAY
```

---

### [MEDIUM] Bithumb stale_watch_loop — 175 심볼 동시 refresh 시 API rate limit 위험

**File**: `engine/src/collectors/bithumb_collector.py:284-293`

`stale_threshold_s=5.0`(기본값)로 1초마다 체크. WS 재연결 소요 시간(2-5초)과 겹칠 경우 `self.symbols` 전체가 stale로 판정되어 `refresh_symbols(all_symbols)`가 호출된다. 175 심볼을 semaphore(5)로 나누면 35 배치가 순차 실행되며 Bithumb 공개 API를 빠르게 소진할 수 있다.

**Fix**: stale 재동기화에 쿨다운 추가 또는 threshold를 최소 WS 재연결 최대 시간(MAX_RECONNECT_DELAY=60s)보다 크게 설정:
```python
async def _stale_watch_loop(
    self, check_interval_s: float = 30.0, stale_threshold_s: float = 30.0
) -> None:
```
혹은 직전 `refresh_symbols` 호출 시각을 추적해 최소 60초 쿨다운 보장.

---

### [MEDIUM] Bybit/OKX futures — delta를 full replace로 처리 시 top-of-book 누락 가능

**File**: `engine/src/collectors/bybit_futures_collector.py:66-82`
**File**: `engine/src/collectors/okx_futures_collector.py:64-94`

Bybit V5 `delta` 메시지와 OKX `update` 메시지는 변경된 레벨만 포함한다. 이를 full book로 취급하면 delta 배열의 길이가 2-3 레벨에 불과할 때 `bids[0]`가 실제 best bid가 아닌 내부 레벨이 될 수 있다.

docstring에 "safe for arbitrage use"라고 명시했지만, snapshot 이후 delta가 심층 레벨만 업데이트할 경우 `bids/asks`가 1개짜리 리스트가 되어 `_on_orderbook` 콜백에 잘못된 BBO가 전달된다.

**Fix 옵션 A** (권장): Bithumb처럼 `_books` dict로 누적 유지.
**Fix 옵션 B**: delta 프레임에서 bids/asks가 비어있으면 return None하여 마지막 snapshot 상태 유지:
```python
if msg_type == "delta":
    if not bids and not asks:
        return None  # 빈 delta — 이전 snapshot 유지
```

---

### [LOW] manager.py docstring 오기

**File**: `engine/src/collectors/manager.py:47`

```python
# Args:
#     exchanges: Exchange IDs to enable (default all 7)
```
DEFAULT_EXCHANGES는 이제 10개 항목. "all 7" → "all 10"으로 수정 필요.

---

### [LOW] BybitFuturesCollector — 폐기된 quote asset 포함

**File**: `engine/src/collectors/bybit_futures_collector.py:20`

```python
quotes = ["USDT", "BUSD", "USDC", "BTC", "ETH", "BNB", "TUSD", "USD"]
```
Bybit linear futures는 USDT만 사용. BUSD(2023년 폐기), TUSD, BNB, USD는 Bybit linear futures에 존재하지 않는다. 불필요한 항목이 `_denormalize_symbol` 파싱 오류를 숨길 수 있다. Bybit linear futures 전용이라면:
```python
quotes = ["USDT", "USDC"]
```

---

## 검증 항목 체크리스트

- [x] 이중 슬리피지 없음 (PowerLaw k=0 유지, BookWalkSlippage만 사용)
- [x] OKX -SWAP 접미사 올바름
- [x] Bybit /linear 엔드포인트 올바름
- [x] base_collector jitter (±25%)는 기존 수집기 테스트에 영향 없음 (INITIAL=1.0s << 10s threshold)
- [x] Bithumb `_books` dict는 `__init__` 시 symbols 고정 → 심볼 삭제 없음 → 메모리 누수 없음
- [x] `stop()` 에서 `_stale_task.cancel()` + `await` 정상 처리
- [x] Coinone watchdog `finally` 블록에서 task 정리 정상
- [x] fee_model.py에 okx_futures, bybit_futures 수수료 테이블 존재 확인

---

## 결론

```
VERDICT: REQUEST CHANGES
```

4개의 MEDIUM 이슈가 있음. 특히:
1. **MEDIUM-1** (sanity check 누락): stale 경로에서 Bithumb 이상 가격이 그대로 전달될 수 있어 fake spread 유발 가능.
2. **MEDIUM-2** (watchdog 즉시 트리거): 재연결 시 즉시 close 루프로 Coinone이 영구 disconnected 상태에 빠질 수 있음.

이 두 건은 프로덕션 안정성에 직접 영향을 주므로 수정 후 재검토 요청.
