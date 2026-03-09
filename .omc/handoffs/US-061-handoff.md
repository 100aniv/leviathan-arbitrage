# US-061 Handoff: VirtualBalanceTracker + Depth-Based Sizing

## 구현 대상
- **SG-4**: 무한 가상 잔고 → 거래소별 VirtualBalanceTracker
- **SG-5**: trade_size=Decimal("1") 하드코딩 → min(L1_depth * 0.10, max_size)

## 파일 경계
- **Jennie (Backend)**: `engine/src/modes/shadow.py`, `engine/src/core/signal.py`
- **Lisa (QA)**: `engine/tests/test_virtual_balance.py`

## 구현 지침

### 1. VirtualBalanceTracker 클래스 (shadow.py, BookWalkSlippage 아래)
- `__init__(initial_balance_usdt)`: env SHADOW_INITIAL_BALANCE_USDT (default 10000)
- `get_balance(exchange_id)` → Decimal: lazy-init per exchange
- `deduct(exchange_id, amount_usdt)` → bool: 잔고 부족 시 False + structlog warning
- `credit(exchange_id, amount_usdt)` → None
- `reset()` → None
- `summary()` → dict[str, str]
- 리밸런스 경고: balance < initial * SHADOW_REBALANCE_THRESHOLD_PCT (default 0.10)

### 2. ShadowMode 통합 (shadow.py)
- `__init__`에 `self._balance_tracker = VirtualBalanceTracker()` 추가
- `_execute_shadow_trade`에서:
  - BUY 전: `if not self._balance_tracker.deduct(buy_exchange, buy_price * signal.volume): return` (skip trade)
  - SELL 후: `self._balance_tracker.credit(sell_exchange, sell_price * sell_trade.amount)`
- `_log_summary`에서 balance summary 포함

### 3. Depth-Based Sizing (signal.py)
- `on_orderbook_update` 내부에서:
  ```python
  depth_fraction = Decimal(os.getenv("SHADOW_DEPTH_FRACTION", "0.10"))
  max_trade = Decimal(os.getenv("SHADOW_MAX_TRADE_SIZE", "10"))
  buy_depth = buy_book.volume_at_price(best_ask.price, "ask")
  sell_depth = sell_book.volume_at_price(best_bid.price, "bid")
  depth_size = min(buy_depth, sell_depth) * depth_fraction
  trade_size = max(Decimal("0.001"), min(depth_size, max_trade))
  ```
- 기존 `trade_size` 파라미터 시그니처 유지 (하위 호환)

### 4. 주의사항
- VirtualBalanceTracker는 PnL 계산과 독립 (기존 fee/slippage 체인 불변)
- structlog 경고 키: `shadow_mode.insufficient_balance`, `shadow_mode.rebalance_needed`
- deduct/credit 단위: USDT (가격 × 수량)
- 이중 슬리피지 금지: BookWalkSlippage/CEXOrderbookSlippage 체인 변경 없음
