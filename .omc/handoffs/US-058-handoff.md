# US-058 Handoff: Shadow 부분체결 + 주문거부 활성화

## 목표
PaperExecutor에 partial_fill_rate=0.05, rejection_rate=0.02 설정하여 Shadow 모드 현실성 강화 (SG-1)

## 변경 파일
- `engine/src/modes/shadow.py` (유일한 소스 변경)

## 구현 상세

### 1. PaperExecutor 초기화 (shadow.py:202-205)
env var로 설정: SHADOW_PARTIAL_FILL_RATE (기본 0.05), SHADOW_REJECTION_RATE (기본 0.02)

### 2. ShadowStats 카운터 추가 (shadow.py:117-131)
- `trades_rejected: int = 0`
- `trades_partial_fill: int = 0`
StrategyStats에도 동일 추가 (shadow.py:107-115)

### 3. _execute_shadow_trade 수정 (shadow.py:698-817)
- `from src.execution.paper import OrderRejectedError` import 추가
- buy_trade 실행 후 sell_order.amount = buy_trade.amount (매수 체결량에 매도 맞춤)
- 부분체결 감지: buy_trade.amount < signal.volume → stats.trades_partial_fill += 1
- OrderRejectedError 전용 catch 추가 (generic Exception 전에) → stats.trades_rejected += 1

### 4. _execute_shadow_trade_request 수정 (shadow.py:912-1005)
- OrderRejectedError 전용 catch → stats.trades_rejected += 1
- 부분체결 감지 → stats.trades_partial_fill += 1

### 5. _send_summary 수정
- rejection/partial_fill 카운트를 일일 요약 메시지에 포함

## Acceptance Criteria
1. PaperExecutor partial_fill_rate=0.05 설정 ✓
2. PaperExecutor rejection_rate=0.02 설정 ✓
3. Shadow 10min 승률 100% 미만으로 하락
4. pytest 전체 PASS

## 주의사항
- 이중 슬리피지 금지: k=0.0 유지 (SignalGenerator가 유일한 슬리피지 소스)
- fee_rate=Decimal("0") 유지 (FeeModel이 별도 처리)
- 기존 테스트 영향 없음 (mock/fixture 사용)
