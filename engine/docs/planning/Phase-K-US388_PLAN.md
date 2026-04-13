# Phase K — US-388: Paper force_enable + Bug Fix 3

> Stage A Plan | Created: 2026-04-04 | Author: leviathan-planner
> PRD: US-388 (passes:false) | Prerequisite: US-387 (passes:true)

---

## 1. Entry Gate Checklist

| # | Gate | Status | Evidence |
|---|------|--------|----------|
| 1 | SSOT §3 정합성 | PASS | server.py, shadow.py, signal.py — 기존 아키텍처 경계 내 수정 |
| 2 | PRD 연결 | PASS | US-388 in `.omc/prd.json` line 7356, passes:false |
| 3 | 선행 의존성 | PASS | US-387 passes:true (OHLCV 다운로드 완료) |
| 4 | 수학 모델 정합성 | N/A | 수학 공식 변경 없음 (PnL 계산 로직 버그 수정만) |
| 5 | 파일 경계 충돌 | PASS | 3개 파일 모두 다른 활성 US와 충돌 없음 |
| 6 | WIRING AC | N/A | 신규 컴포넌트 없음 — 기존 API 확장 + 버그 수정 |

**결정: Stage B 진입 승인**

---

## 2. AC별 구현 계획

### AC-1: POST /api/paper/start { force_enable: true, strategy_ids: [...] }

**현재 상태:**
- `engine/src/api/routes/paper.py:37-46` — `PaperStartRequest` 모델에 `force_enable` 필드 없음
- `engine/src/api/routes/paper.py:48-70` — `start_paper()` 핸들러가 ctx.paper_session에 저장만 하고 엔진 재시작 로직 없음

**수정 계획:**
1. `PaperStartRequest`에 `force_enable: bool = False` 필드 추가
2. `start_paper()` 핸들러에서 `force_enable=True`일 때:
   - `ctx.engine`의 shadow_mode에 strategy_filter 동적 설정
   - `ctx.paper_session`에 force_enable 상태 저장
3. `ctx.paper_session` 응답에 force_enable 반영

**파일:** `engine/src/api/routes/paper.py`

### AC-2: force_enable=true 시 strategy_activation.json 무시

**현재 상태:**
- `engine/src/main.py:1045-1057` — `strategy_activation.json`에서 `disabled_strategies` 로드
- `engine/src/modes/shadow.py:574-592` — `_disabled_strategies` set을 env에서 로드
- `engine/src/modes/shadow.py:2434-2437` — `set_disabled_strategies()` 메서드 존재

**수정 계획:**
1. `PaperMode`에 `force_enable_strategies(strategy_ids: list[str])` 메서드 추가:
   - `_disabled_strategies = set()` (전체 초기화)
   - `_strategy_filter = frozenset(strategy_ids)` (지정 전략만 허용)
2. API 핸들러 `start_paper()`에서 force_enable=True일 때 위 메서드 호출
3. strategy_activation.json 자체는 수정하지 않음 (런타임 오버라이드)

**파일:** `engine/src/modes/shadow.py`, `engine/src/api/routes/paper.py`

### AC-3: sigma 로그 수정 (signal.py:172)

**현재 상태 (signal.py:170-176):**
```python
sigma = Decimal(str(math.sqrt(float(variance))))
sigma = min(sigma, Decimal("0.10"))  # L171: clamp BEFORE log
logger.info(                          # L172: logs clamped value (OK)
    "signal.dynamic_sigma_computed symbol=%s sigma=%s history_len=%d",
    symbol, sigma, len(prices),
)
return max(sigma, Decimal("0.0001"))  # L176: floor AFTER log
```

**문제:** 로그가 `min()` 이후, `max()` 이전에 출력됨.
- sigma가 0.00001이면 -> min(0.00001, 0.10) = 0.00001 -> 로그 출력: 0.00001
- 이후 max(0.00001, 0.0001) = 0.0001이 실제 반환값
- **로그에 찍힌 sigma와 실제 사용되는 sigma가 다름**

**수정 계획:**
1. `max()` 적용 후로 logger.info 이동 (L176 이후)
2. 반환되는 최종 sigma를 로그에 출력하도록 변경

**파일:** `engine/src/core/signal.py` (L170-176)

### AC-4: total_pnl 세션 시작 시 리셋

**현재 상태 (shadow.py:647-654):**
```python
async def start(self) -> None:
    if self._running:
        return
    self._running = True
    self._stats = ShadowStats(start_time=time.monotonic())  # L654: 새 ShadowStats 생성
    await self._load_peak_equity_from_db()  # L656: DB에서 peak_pnl 복원
```

**문제:** L654에서 `ShadowStats` 새로 생성하면 total_pnl=0.0으로 초기화됨 (정상).
그러나 L656 `_load_peak_equity_from_db()`가 이전 세션의 `peak_pnl`을 DB에서 복원.
peak_pnl이 이전 세션 값(예: $14.21)인데 total_pnl=0.0이면:
- `_compute_drawdown()`에서 drawdown = peak_pnl(14.21) - total_pnl(0.0) = 14.21
- 즉시 MDD 100% 계산 -> 잘못된 drawdown alert 발생 가능

**수정 계획:**
1. `start()` 메서드에서 `_load_peak_equity_from_db()` 호출 제거 또는 조건부 실행
2. 새 세션 시작 시 peak_pnl=0.0 유지 (total_pnl과 동기)
3. 또는 `_load_peak_equity_from_db()` 호출 후 peak_pnl도 0.0으로 리셋하는 옵션 추가
4. `force_enable` 모드에서는 항상 clean session (peak_pnl=0, total_pnl=0)

**파일:** `engine/src/modes/shadow.py` (L647-656, L2340-2363)

### AC-5: net_pnl=0.0000 원인 분석 및 수정

**근본 원인 분석:**

cross_exchange 시그널 경로 (L1484-1488):
```python
net_pnl = (
    sell_notional - real_sell_fee
    - buy_notional - real_buy_fee
    - network_cost
)
```

BookWalkSlippage가 오더북 깊이를 워킹할 때, 실제 오더북 데이터가 sparse하면
buy_trade.price와 sell_trade.price가 원래 signal의 buy_price/sell_price와
거의 동일하게 반환됨. 이 경우:
- sell_notional ~= buy_notional (스프레드가 수수료보다 작음)
- net_pnl = (tiny spread) - fees - network = 음수 또는 거의 0

**그러나 net_pnl=0.0000 (정확히 0)이 반복되는 경우의 진짜 원인:**

multi-strategy 경로 `_execute_trade_request()` (L1799):
```python
net_pnl = Decimal("0")
for leg, trade in trades:
    ...
    if _is_cross_asset:
        continue  # <-- net_pnl 누적 건너뜀
    else:
        if leg.side == OrderSide.SELL:
            net_pnl += notional - fee
        else:
            net_pnl -= notional + fee
```

**_is_cross_asset 탐지 (L1796):**
```python
if not _is_cross_asset and len(_leg_symbols) > 1 and not _is_triangular:
    _is_cross_asset = True  # fallback
```

문제: spot_futures, futures_futures 같은 전략이 서로 다른 symbol을 가진 legs를
생성할 때 (예: BTC/USDT@binance + BTC/USDT@binance_futures), exchange suffix가
symbol에 포함되면 `_leg_symbols`가 2개로 판정 -> `_is_cross_asset=True` 오탐.

이후 L1824: `_expected = trade_request.expected_profit_usdt or Decimal("0")`
- expected_profit_usdt가 None이거나 0이면 -> net_pnl = 0 - total_fees = -fees
- expected_profit_usdt가 설정되지 않은 전략 -> net_pnl = -fees (0에 근접)

**수정 계획:**
1. `_is_cross_asset` 판정 로직 강화: symbol 비교 시 base pair 정규화
   - `BTC/USDT` (spot) == `BTC/USDT` (futures) -> same symbol -> not cross_asset
2. `expected_profit_usdt` 누락 시 fallback으로 실제 notional 차이 계산
3. 디버그 로그 추가: net_pnl=0 경우 원인 태그 출력

**파일:** `engine/src/modes/shadow.py` (L1792-1835)

### AC-6: pytest 5454+ passed

모든 수정 후 기존 테스트 깨지지 않음을 확인.
신규 테스트 추가:
- `test_paper_force_enable_api` — force_enable=true API 호출 테스트
- `test_sigma_log_after_max` — sigma 로그 값 == 반환값 확인
- `test_session_pnl_reset` — 세션 시작 시 total_pnl=0 확인
- `test_net_pnl_not_zero_for_same_symbol` — 동일 symbol legs의 cross_asset 오탐 방지

---

## 3. 파일 수정 범위

| 파일 | 변경 유형 | 예상 LOC |
|------|----------|---------|
| `engine/src/api/routes/paper.py` | AC-1,2: force_enable 파라미터 + 핸들러 로직 | +15 |
| `engine/src/modes/shadow.py` | AC-2: force_enable_strategies() 메서드 | +12 |
| `engine/src/modes/shadow.py` | AC-4: start() peak_pnl 리셋 | +5 |
| `engine/src/modes/shadow.py` | AC-5: _is_cross_asset 정규화 | +8 |
| `engine/src/core/signal.py` | AC-3: sigma 로그 이동 | +2/-2 |
| `engine/tests/unit/test_paper_api.py` | AC-6: force_enable 테스트 | +30 |
| `engine/tests/unit/test_shadow_mode.py` | AC-6: PnL/session 테스트 | +40 |

**총 예상:** +112 / -4 LOC

---

## 4. 태스크 시퀀스 (Stage B 실행 순서)

```
T1: AC-3 sigma 로그 수정 (signal.py) — 독립, 가장 간단
T2: AC-4 total_pnl 세션 리셋 (shadow.py start()) — 독립
T3: AC-5 net_pnl=0 수정 (shadow.py _execute_trade_request) — 독립
T4: AC-1+2 force_enable API (paper.py + shadow.py) — T3 이후 (같은 파일)
T5: AC-6 테스트 작성 + pytest 전체 실행 — T1~T4 완료 후
```

T1, T2, T3는 병렬 가능 (서로 다른 함수/파일).
T4는 shadow.py에 메서드 추가하므로 T3 이후.
T5는 최종 검증.

---

## 5. 리스크 및 주의사항

| 리스크 | 완화 |
|--------|------|
| force_enable이 기존 running 세션과 충돌 | start_paper()에서 이미 running이면 stop->재시작 또는 reject |
| _is_cross_asset 수정이 stat_arb에 영향 | stat_arb은 metadata에 `cross_asset=true` 명시 -> 정규화와 무관 |
| peak_pnl 리셋이 장기 MDD 추적 손실 | force_enable 모드만 리셋, 일반 시작은 DB 복원 유지 |
| sigma 로그 순서 변경이 모니터링 도구에 영향 | 로그 포맷 동일, 값만 최종값으로 변경 |

---

## 6. 검증 기준 (Stage B-Step 2 Shadow 확인)

1. `cd engine && python -m pytest tests/ -x --tb=short` — 5454+ passed
2. force_enable API: `curl -X POST /api/paper/start -d '{"force_enable":true,"strategy_ids":["cross_exchange_v1"]}'` -> 200
3. Shadow 10min 실행 -> net_pnl != 0.0000 for cross_exchange trades
4. 로그에서 `signal.dynamic_sigma_computed` sigma 값 == 실제 사용값 확인
5. 세션 재시작 시 total_pnl=0.0 확인 (이전 세션 이월 없음)
