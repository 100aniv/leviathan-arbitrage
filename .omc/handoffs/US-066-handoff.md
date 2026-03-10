# US-066 Handoff: 전략 수익성 수정 — Stale Orderbook 감지 + 전략 블랙리스트 + 손실 제한

> **Plan**: `.omc/plans/US-066_PLAN.md`
> **Phase**: G (전략 수익성 복원) | **작성일**: 2026-03-10 | **상태**: 구현 준비 완료

---

## Executive Summary

1H Progressive Shadow에서 -$1,937 손실의 근본 원인인 Korean exchange stale orderbook 문제를 3계층 방어로 해결:
1. 가격 변동 기반 staleness 감지 (OrderBook + SignalGenerator)
2. 전략 블랙리스트 (ShadowMode)
3. Per-trade max loss guard (ShadowMode)

## 1H Progressive Shadow 분석 (배경)

- PID 80286, 2026-03-10 18:34 KST
- **2,554 trades**, 75.6% WR, **PnL = -$1,937**, MaxDD = $2,423
- 전략별: spot_futures -$1,127 / cross_exchange -$497 / latency_arb -$67 / stat_arb -$3
- 근본 원인: Korean exchange(upbit, bithumb, coinone) stale orderbook → 가짜 스프레드 → fat-tail 단건 -$249

---

## 수정 대상 파일 (5개 소스 + 3개 테스트)

### 1. `engine/src/core/order_book.py`
- **추가**: `last_price_change_time: float = 0.0` 필드 (line 27 근처)
- **변경**: `apply_snapshot()` — 항상 `last_price_change_time = time.monotonic()` 설정
- **변경**: `apply_delta()` — 가격/수량 실제 변동 시에만 갱신. 동일 값 반복 수신 시 미갱신
  - 핵심 로직: 기존 `self.bids[p] = q` 직접 설정 전에 `old_qty = self.bids.get(p)` 보존 → 업데이트 후 `old_qty != q` 비교
  - asks도 동일 패턴
  - `price_changed = True`일 때만 `self.last_price_change_time = time.monotonic()`
- **주의**: 기존 `last_update_time`은 변경하지 않음 (WS 수신 시각 기준 유지, 모든 delta에서 갱신)

### 2. `engine/src/core/signal.py`
- **추가**: `SignalConfig.max_price_stale_seconds: float = 60.0` (line 52 근처)
- **추가**: `on_orderbook_update()` staleness gate 블록(line 156-165)에 가격 staleness 체크 추가
  - 위치: 기존 `max_book_age_seconds` 체크 직후
  - 조건: `ob.last_price_change_time > 0 and (now_mono - ob.last_price_change_time) > max_price_stale`
  - `last_price_change_time == 0`(미초기화)인 경우 기존 `max_book_age_seconds` gate에만 의존 (하위 호환)
  - 로그: `stale_price_rejected symbol=%s exchange=%s price_age=%.1fs`

### 3. `engine/src/modes/shadow.py`
- **추가 (init, line 456 근처)**:
  - `_disabled_strategies: set[str]` — `SHADOW_DISABLED_STRATEGIES` env var 파싱 (쉼표 구분)
  - `_max_loss_per_trade_usd: Decimal` — `MAX_LOSS_PER_TRADE_USD` env var (기본 50)

- **추가 (_execute_shadow_trade, line 933)**:
  - 메서드 상단 (negative spread 체크 직후): 전략 블랙리스트 체크
    ```
    sid = signal.strategy_id or self.STRATEGY_ID
    if sid in self._disabled_strategies: return
    ```
  - rate limit 체크 후, balance deduct 전: max loss guard
    ```
    gross_profit_est = (sell_price - buy_price) * volume
    est_fee = buy_price * volume * Decimal("0.005")  # 0.25% * 2 보수적
    est_pnl = gross_profit_est - est_fee - Decimal("5.0")
    if est_pnl < -self._max_loss_per_trade_usd: reject + log + return
    ```

- **추가 (_execute_shadow_trade_request, line 1199)**:
  - 동일한 블랙리스트 + max loss guard
  - N-leg: 모든 leg 합산 예상 PnL로 판단

### 4. `engine/src/core/real_signal_producer.py`
- **변경**: `on_orderbook_update()` (line 72-104)
  - 기존: spot_futures만 Korean exchange 비활성 (line 92)
  - 변경: Korean exchange에서 triangular만 허용, 나머지 전략 전면 차단
  - 근거: cross_exchange(-$497), latency_arb(-$67) 모두 Korean stale가 원인
  - triangular은 단일 거래소 내 arb이므로 stale 영향 없음
  ```python
  KOREAN_EXCHANGES = {"upbit", "bithumb", "coinone"}
  if exchange_id in KOREAN_EXCHANGES:
      signals.extend(await self._evaluate_triangular(exchange_id, symbol, book))
      return signals  # 나머지 전략 차단
  ```

### 5. `engine/src/main.py`
- **변경**: `_init_signal_pipeline()` (line 464 근처)
  - `STALE_PRICE_SECONDS` env var → `SignalConfig.max_price_stale_seconds`
  - 시작 로그에 `stale_price_sec` 값 출력

### 6-8. 테스트 파일 (신규 3개, 총 12개+ 테스트)

**`engine/tests/unit/test_stale_orderbook.py`** (5개):
1. apply_delta 가격 변동 시 last_price_change_time 갱신
2. 동일 가격/수량 반복 시 미갱신
3. apply_snapshot 항상 갱신
4. SignalGenerator stale price 차단 (60초 초과)
5. SignalGenerator fresh price 허용 (60초 이내)

**`engine/tests/unit/test_shadow_strategy_blacklist.py`** (4개):
6. 블랙리스트 전략 거래 0건
7. 빈 블랙리스트 시 기존 동작
8. 복수 전략 비활성
9. Korean exchange cross/latency 차단

**`engine/tests/unit/test_shadow_max_loss_guard.py`** (3개+):
10. est_pnl < -$50 거부
11. est_pnl > -$50 허용
12. N-leg trade request 거부

---

## Env Vars (신규 3개)

| Env Var | 기본값 | 설명 |
|---------|--------|------|
| `STALE_PRICE_SECONDS` | `60` | 가격 변동 없이 N초 경과 시 stale 판정 (초) |
| `SHADOW_DISABLED_STRATEGIES` | (빈 문자열) | 쉼표 구분 전략 ID 블랙리스트 (예: `spot_futures_v1,latency_arb_v1`) |
| `MAX_LOSS_PER_TRADE_USD` | `50` | 단건 예상 손실 임계값 (USD) |

---

## 구현 순서 (권장)

1. OrderBook `last_price_change_time` 추가 + `apply_delta` 변동 감지 로직
2. SignalGenerator `max_price_stale_seconds` gate 추가
3. main.py env var 연결
4. ShadowMode 블랙리스트 + max loss guard
5. RealDataSignalProducer Korean 전면 차단
6. 테스트 작성 (12개+)
7. `cd engine && python -m pytest tests/ -x` 전체 통과 확인

---

## 핵심 주의사항

1. **apply_delta 가격 변동 감지**: 기존 코드가 `self.bids[p] = q`를 직접 설정하므로, 변동 비교를 위해 **이전 값 보존 후 비교** 필요
2. **이중 슬리피지 금지**: PaperExecutor의 BookWalkSlippage(k=0)는 절대 변경하지 않음
3. **KOREAN_EXCHANGES 재활용**: `{"upbit", "bithumb", "coinone"}` set 하드코딩 추가 금지, CollectorManager 상수 참조 또는 동일 set 사용
4. **max loss guard 위치**: balance deduct 전에 배치. 잔고 차감 후 거부하면 잔고 소모됨
5. **last_price_change_time 기본값 0.0**: "아직 가격 변동 없음" 의미. gate에서 `> 0` 조건으로 미초기화 book은 기존 gate에만 의존

---

## 의존성

- **선행**: 없음 (독립적 수정)
- **후행**: US-065 (Dashboard 연동) → US-054 재테스트 → US-055 (LiveGate)
- **관련**: US-067 (Bithumb REST 스냅샷 근본 해결, 미래 Phase)

## 검증 기준

- [ ] `cd engine && python -m pytest tests/ -x` — 0 failures
- [ ] 10분 Shadow — 거래 건수 1,500건 이상 (기존 3,110 대비 50%)
- [ ] 1H Shadow — PnL > 0, fat-tail 단건 -$50 이하
- [ ] Korean exchange stale 로그 확인 (`stale_price_rejected` 로그 발생)
- [ ] `SHADOW_DISABLED_STRATEGIES=spot_futures_v1` 설정 시 해당 전략 거래 0건
