# US-061: VirtualBalanceTracker + 깊이 기반 주문 크기 제한

## 1. 요약

SG-4(무한 가상 잔고) + SG-5(trade_size=1 하드코딩) 해결.

## 2. 변경 범위

### 2.1 `engine/src/modes/shadow.py`

**VirtualBalanceTracker 클래스 추가** (BookWalkSlippage 아래):

```python
class VirtualBalanceTracker:
    """Per-exchange virtual balance tracker for shadow mode."""

    def __init__(self, initial_balance_usdt: Decimal = None):
        self._initial = initial_balance_usdt or Decimal(
            os.getenv("SHADOW_INITIAL_BALANCE_USDT", "10000")
        )
        self._balances: dict[str, Decimal] = {}  # exchange_id -> USDT balance
        self._rebalance_threshold = Decimal(
            os.getenv("SHADOW_REBALANCE_THRESHOLD_PCT", "0.10")
        )

    def get_balance(self, exchange_id: str) -> Decimal:
        if exchange_id not in self._balances:
            self._balances[exchange_id] = self._initial
        return self._balances[exchange_id]

    def deduct(self, exchange_id: str, amount_usdt: Decimal) -> bool:
        bal = self.get_balance(exchange_id)
        if bal < amount_usdt:
            logger.warning("shadow_mode.insufficient_balance",
                exchange=exchange_id, balance=str(bal), required=str(amount_usdt))
            return False
        self._balances[exchange_id] = bal - amount_usdt
        # Rebalance alert
        if self._balances[exchange_id] < self._initial * self._rebalance_threshold:
            logger.warning("shadow_mode.rebalance_needed",
                exchange=exchange_id,
                remaining=str(self._balances[exchange_id]),
                threshold=str(self._initial * self._rebalance_threshold))
        return True

    def credit(self, exchange_id: str, amount_usdt: Decimal) -> None:
        self.get_balance(exchange_id)  # ensure initialized
        self._balances[exchange_id] += amount_usdt

    def reset(self) -> None:
        self._balances.clear()

    def summary(self) -> dict[str, str]:
        return {ex: str(bal) for ex, bal in self._balances.items()}
```

**ShadowMode.__init__에 통합:**
```python
self._balance_tracker = VirtualBalanceTracker()
```

**_execute_shadow_trade에서 잔고 체크/차감:**
- BUY 전: `deduct(buy_exchange, buy_price * volume)`
- SELL 후: `credit(sell_exchange, sell_price * sell_trade.amount)`
- 잔고 부족 시 trade skip + 로그

### 2.2 `engine/src/core/signal.py`

**on_orderbook_update에서 depth-based sizing:**

현재: `trade_size: Decimal = Decimal("1")` (파라미터 기본값)

변경: 호출부(shadow.py)에서 동적 계산:
```python
# L1 depth의 10% = 시장 영향 최소화
buy_depth = buy_book.asks.get(best_ask.price, Decimal("0"))
sell_depth = sell_book.bids.get(best_bid.price, Decimal("0"))
depth_size = min(buy_depth, sell_depth) * Decimal("0.10")
trade_size = max(Decimal("0.001"), min(depth_size, Decimal("10")))
```

**변경 위치**: signal.py의 `on_orderbook_update` 내부에서 자체 계산.
- 기존 `trade_size` 파라미터 유지 (외부 오버라이드 가능)
- `trade_size` 파라미터가 기본값(Decimal("1"))이면 depth-based 계산 적용
- 명시적으로 전달되면 그 값 사용

## 3. 수식

```
depth_trade_size = min(L1_ask_qty, L1_bid_qty) × 0.10
clamped_size = clamp(depth_trade_size, 0.001, 10.0)
effective_size = min(clamped_size, balance_usdt / buy_price)
```

## 4. 환경 변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| SHADOW_INITIAL_BALANCE_USDT | 10000 | 거래소별 초기 가상 잔고 |
| SHADOW_REBALANCE_THRESHOLD_PCT | 0.10 | 리밸런스 경고 비율 (10%) |
| SHADOW_MAX_TRADE_SIZE | 10 | 최대 주문 크기 (base asset) |
| SHADOW_DEPTH_FRACTION | 0.10 | L1 깊이 대비 주문 비율 |

## 5. 테스트 계획

1. VirtualBalanceTracker 초기화 (기본 10000 USDT)
2. deduct 성공 → 잔고 감소 확인
3. deduct 실패 → 잔고 부족 시 False 반환
4. credit → 잔고 증가 확인
5. rebalance 경고 트리거 (잔고 < 10%)
6. reset → 모든 잔고 초기화
7. depth_based sizing → min(L1_ask, L1_bid) * 0.10 계산
8. 크기 clamp → [0.001, 10] 범위 확인
9. 깊이 없음 → fallback to 최소 크기
10. _execute_shadow_trade 잔고 차감 통합
11. 잔고 부족 시 trade skip
12. pytest 전체 PASS

## 6. 이중 계산 방지

- VirtualBalanceTracker는 PnL 계산에 영향 없음 (독립 추적)
- depth_based sizing은 SignalGenerator의 friction calculation과 일관 (같은 trade_size 사용)
- 기존 CEXOrderbookSlippage → BookWalkSlippage 체인 변경 없음
