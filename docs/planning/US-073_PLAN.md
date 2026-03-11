# US-073: Bithumb REST 스냅샷 → 증분 Orderbook 근본 해결

**작성자**: Architect (READ-ONLY 분석)
**대상 파일**: `engine/src/collectors/bithumb_collector.py`
**참조 파일**: `engine/src/modes/shadow.py`, `engine/src/core/stale_detector.py`

---

## Summary

Bithumb WebSocket은 `orderbookdepth` (증분 델타) 메시지를 전송하지만, 현재 `BithumbCollector._parse_message()`는 각 WS 메시지를 **완성된 orderbook 스냅샷처럼 처리**하여 소비자에게 전달한다. 누적된 in-memory book 상태가 없고, qty=0 삭제 처리도 없으므로, 소형 코인(NOM, SXP 등)에서 수신되는 희소(sparse) 델타가 실제 호가창과 2-10배 괴리된 가격을 만든다.

**근본 해결책**: WS 메시지를 in-memory 누적 book에 apply하고, qty=0 삭제 처리 + per-symbol 5초 스탈 감지 → 자동 re-sync를 추가한다.

---

## Analysis

### 1. WS 구독 타입 vs 처리 방식 불일치

**`bithumb_collector.py:133`**
```python
return [{"type": "orderbookdepth", "symbols": syms, "tickTypes": ["1H"]}]
```

- `orderbookdepth` + `tickTypes: ["1H"]` = **증분 델타** (변경된 호가 레벨만 전송)
- 반면 `orderbooksnapshot`은 전체 orderbook 스냅샷 전송
- `test_native_bithumb.py:143`에서 Native Adapter는 `orderbooksnapshot`을 사용함

**`bithumb_collector.py:144-195`** `_parse_message()` 분석:
```python
for entry in entries:
    if order_type == "bid":
        bids.append([price, qty])
    elif order_type == "ask":
        asks.append([price, qty])
# Sort and return
return symbol, bids, asks
```

- WS 메시지의 raw entry를 그대로 bids/asks로 반환 (누적 없음)
- qty=0인 삭제 신호를 처리하지 않음
- 각 WS 메시지는 전체 book이 아닌 변경된 레벨 몇 개만 포함함
- 결과: 소비자는 전체 book이 아닌 최근 변경 레벨 몇 개만 받음

### 2. 누적 in-memory Book 부재

현재 `BithumbCollector`에는 `self._orderbooks: dict` 형태의 누적 상태가 없다.

**`bithumb_collector.py:55-56`**:
```python
self._last_update: dict[str, float] = {}
self._snapshot_fetched = False
```

REST 스냅샷(`_fetch_initial_snapshots`, line 66)은 on_orderbook callback을 직접 호출하여 소비자에게 전달하지만, 이후 WS 델타를 이 초기 스냅샷에 **적용(apply)하는 코드가 없다**.

### 3. 주기적 Re-sync의 한계

**`shadow.py:995`**:
```python
interval = float(os.getenv("BITHUMB_REFRESH_INTERVAL_S", "60"))
```

**`bithumb_collector.py:197-205`** `refresh_snapshots()`:
```python
self._snapshot_fetched = False
await self._fetch_initial_snapshots()
```

`_fetch_initial_snapshots()`에서 심볼당 0.25초 sleep(`bithumb_collector.py:114`). 175 심볼 × 0.25초 = **43초** 소요. 60초 주기에서 실제 "신선한" 상태는 겨우 17초 — 1H Shadow 목표인 "freshness < 5초"를 달성 불가.

### 4. Stale 감지 방식이 사후 대응적(reactive)

**`stale_detector.py:73-138`** `check_cross_exchange()`:
- 신호 생성 시점에 다른 거래소와 중앙값 비교
- 이미 stale한 데이터로 신호가 생성된 **이후**에 걸러냄
- 소형 코인은 비교 거래소 데이터도 없어 `min_comparison_exchanges < 2` → 검사 통과

**`bithumb_collector.py:207-212`** `is_symbol_stale()`:
- 존재하지만 외부에서 능동적으로 호출하지 않음
- WS 메시지가 오면 무조건 `_last_update` 갱신 (line 193) — 스탈 여부와 무관하게

### 5. qty=0 삭제 처리 없음

Bithumb WS 프로토콜에서 qty=0은 해당 호가 레벨 삭제를 의미한다. 현재 코드는 이를 일반 엔트리와 동일하게 처리하여 qty=0 레벨이 book에 남는다.

---

## Root Cause

**두 가지 근본 원인이 중첩됨:**

1. **구조적 결함**: `orderbookdepth` (증분 델타)를 구독하면서 누적 in-memory book 없이 각 델타를 전체 스냅샷처럼 소비자에게 전달. 소형 코인의 드문 업데이트 시, 소비자가 받는 book은 실제 호가창의 극소 일부.

2. **운영 결함**: 주기적 REST re-sync(60초)가 너무 느리고(175심볼 × 0.25초 = 43초), per-symbol 스탈 자동 감지 + 즉시 re-sync 메커니즘이 없음.

---

## Recommendations

### Option A: In-memory 누적 Book (권장)

**전략**: `orderbookdepth` 구독 유지, 누적 dict 추가, qty=0 삭제 처리, per-symbol stale watcher.

#### 구현 단계

**Step 1: `__init__` — 누적 book dict 추가**

```python
# bithumb_collector.py:55 이후 추가
self._books: dict[str, dict[str, dict[str, str]]] = {}
# 구조: {symbol: {"bids": {price: qty}, "asks": {price: qty}}}
```

**Step 2: `_fetch_initial_snapshots` — book 초기화**

`bithumb_collector.py:108-110` 콜백 호출 직전:
```python
# book 상태 초기화
self._books[symbol] = {
    "bids": {b[0]: b[1] for b in bids},
    "asks": {a[0]: a[1] for a in asks},
}
```

**Step 3: `_parse_message` — 델타 apply + qty=0 삭제 + 누적 상태 반환**

`bithumb_collector.py:144-195` 교체:
```python
def _parse_message(self, data: dict) -> tuple[str, list, list] | None:
    # ... (기존 타입 체크, symbol 파싱 유지)

    # 누적 book이 없으면 초기화 (WS 선행 메시지 처리)
    if symbol not in self._books:
        self._books[symbol] = {"bids": {}, "asks": {}}

    book = self._books[symbol]
    for entry in entries:
        price = str(entry.get("price", "0"))
        qty = str(entry.get("quantity", "0"))
        side = "bids" if entry.get("orderType") == "bid" else "asks"

        if float(qty) == 0:
            book[side].pop(price, None)  # 삭제
        else:
            book[side][price] = qty      # 추가/갱신

    # 누적 상태에서 정렬된 top-N 추출
    bids = sorted([[p, q] for p, q in book["bids"].items()],
                  key=lambda x: float(x[0]), reverse=True)[:_SNAPSHOT_DEPTH]
    asks = sorted([[p, q] for p, q in book["asks"].items()],
                  key=lambda x: float(x[0]])[:_SNAPSHOT_DEPTH]

    self._last_update[symbol] = time.monotonic()
    return symbol, bids, asks
```

**Step 4: `refresh_snapshots` 개선 — 병렬 요청 + book 상태 초기화**

`bithumb_collector.py:197-205` 교체:
- `asyncio.gather()` 병렬 요청 (rate-limit: 세마포어 5 동시)
- 각 심볼 갱신 시 `self._books[symbol]` 초기화 후 콜백 호출
- 완료 후 `is_symbol_stale(s, max_age_s=5.0)` 기준 count 반환

**Step 5: `_stale_watch_loop` 추가 — per-symbol 능동 감지**

```python
async def _stale_watch_loop(self, check_interval_s: float = 1.0,
                             stale_threshold_s: float = 5.0) -> None:
    """심볼별 5초 이상 미갱신 시 즉시 REST re-sync 트리거."""
    while self._running:
        await asyncio.sleep(check_interval_s)
        stale_syms = [s for s in self.symbols
                      if self.is_symbol_stale(s, max_age_s=stale_threshold_s)]
        if stale_syms:
            await self._refresh_symbols(stale_syms)  # 개별 심볼 re-sync
```

**Step 6: `start()` — stale_watch_loop 시작**

```python
async def start(self) -> None:
    self._running = True
    await self._fetch_initial_snapshots()
    self._stale_task = asyncio.create_task(self._stale_watch_loop())
    await super().start()
```

**Step 7: shadow.py `_delta_refresh_loop` interval 조정**

`BITHUMB_REFRESH_INTERVAL_S` 기본값 60 → 300 (per-symbol stale watch가 5초 단위로 처리하므로 전체 re-sync는 덜 자주 실행해도 됨).

---

### Option B: `orderbooksnapshot` 타입으로 구독 변경

**전략**: WS 구독을 `orderbooksnapshot`으로 변경하여 전체 스냅샷 수신. 누적 book 불필요.

`bithumb_collector.py:133`:
```python
# 변경 전
return [{"type": "orderbookdepth", "symbols": syms, "tickTypes": ["1H"]}]
# 변경 후
return [{"type": "orderbooksnapshot", "symbols": syms, "tickTypes": ["1H"]}]
```

`_parse_message()` 파싱 형식도 `orderbooksnapshot` 포맷(bids/asks 배열 직접)으로 변경 필요.

---

## Trade-offs

| Option | Pros | Cons |
|--------|------|------|
| **A: In-memory 누적 Book** | 현재 구조 유지, 대역폭 최소, qty=0 삭제 정확 처리 | 구현 복잡도 높음, book 드리프트 위험 남음 |
| **B: orderbooksnapshot 변경** | 구현 단순, 항상 완전한 book, 드리프트 없음 | 대역폭 증가, 메시지 형식 변경(파서 수정 필요), Bithumb API 쿼터 영향 가능 |
| **현재 (유지)** | 변경 없음 | stale data 지속, 소형 코인 가격 2-10x 오차 유지 |

**권장**: Option A (in-memory 누적 + per-symbol stale watch). Option B는 스냅샷 형식 파서가 별도 확인 필요하며, NativeBithumbAdapter와의 일관성을 위해 A를 선택.

---

## Implementation Plan (Step-by-Step)

### Phase 1: 핵심 수정 (bithumb_collector.py)

| # | 항목 | 파일:라인 | 내용 |
|---|------|----------|------|
| 1 | `__init__` 확장 | `:55` | `self._books: dict` + `self._running: bool` 추가 |
| 2 | `_fetch_initial_snapshots` 수정 | `:66-125` | 콜백 전 `self._books[symbol]` 초기화 |
| 3 | `_parse_message` 교체 | `:144-195` | 델타 누적 apply + qty=0 삭제 + 누적 상태 반환 |
| 4 | `refresh_symbols` 추가 | 신규 | 특정 심볼 목록만 REST re-sync (병렬) |
| 5 | `refresh_snapshots` 개선 | `:197` | `refresh_symbols` 호출 + 병렬화 |
| 6 | `_stale_watch_loop` 추가 | 신규 | 1초 주기, 5초 stale 심볼 → `refresh_symbols` |
| 7 | `start()` 수정 | `:58` | stale_watch_loop task 시작 |
| 8 | `stop()` 추가 | 신규 | `_stale_task.cancel()` |

### Phase 2: Shadow 조정 (shadow.py)

| # | 항목 | 파일:라인 | 내용 |
|---|------|----------|------|
| 9 | `BITHUMB_REFRESH_INTERVAL_S` 기본값 변경 | `:995` | 60 → 300 (per-symbol watch 보완) |

### Phase 3: 테스트 (신규 + 기존 수정)

| # | 테스트 | 파일 |
|---|--------|------|
| 10 | qty=0 삭제 처리 확인 | `test_bithumb_snapshot.py` |
| 11 | 누적 book 정확성 (3개 델타 적용 후 최종 상태) | 신규 |
| 12 | stale_watch_loop → refresh_symbols 트리거 | 신규 |
| 13 | refresh_symbols 병렬 성능 (10심볼 < 2초) | 신규 |
| 14 | WS 연결 끊김 후 book 상태 보존 확인 | 신규 |
| 15 | `is_snapshot=True` 콜백 kwargs 확인 | 기존 수정 |

---

## Acceptance Criteria 매핑

| 기준 | 구현 항목 | 검증 방법 |
|------|----------|----------|
| REST 스냅샷 주기적 갱신 | Step 5-6 (stale watch) | 5초 미갱신 시 자동 re-sync 로그 확인 |
| 소형 코인 2-10x 가격 오차 해결 | Step 3 (qty=0 삭제, 누적 book) | SXP/NOM 심볼 테스트 |
| Stale 자동 감지 + 재스냅샷 | Step 5-6 | `_stale_watch_loop` 단위 테스트 |
| 1H Shadow freshness < 5초 | Step 5 (1초 주기 감시) | Shadow 실행 후 `_last_update` 타임스탬프 검사 |
| pytest 전체 PASS | Step 10-15 | `cd engine && python -m pytest tests/ -x --tb=short` |

---

## Critical File References

- `engine/src/collectors/bithumb_collector.py:133` — WS 구독 타입 (`orderbookdepth`)
- `engine/src/collectors/bithumb_collector.py:144-195` — `_parse_message`: 누적 없이 raw 델타 반환
- `engine/src/collectors/bithumb_collector.py:55-56` — 누락된 `self._books` 상태
- `engine/src/collectors/bithumb_collector.py:114` — 0.25s sleep × N심볼 = 병목
- `engine/src/collectors/bithumb_collector.py:197-205` — `refresh_snapshots`: 직렬, 느림
- `engine/src/modes/shadow.py:995` — 60초 전체 re-sync 간격
- `engine/src/core/stale_detector.py:73-138` — 사후 교차검증 (능동 감지 아님)
