# US-058: Shadow 부분체결(5%) + 주문거부(2%) 활성화

## 변경 범위
- **주 파일**: `engine/src/modes/shadow.py`
- **보조**: `engine/tests/` (신규 테스트)

## 구현 계획

### 1. PaperExecutor 초기화 변경 (shadow.py:202-205)
```python
self._paper_executor = paper_executor or PaperExecutor(
    slippage_model=PowerLawSlippage(k=0.0, gamma=0.5),
    fee_rate=Decimal("0"),
    partial_fill_rate=Decimal(os.environ.get("SHADOW_PARTIAL_FILL_RATE", "0.05")),
    rejection_rate=Decimal(os.environ.get("SHADOW_REJECTION_RATE", "0.02")),
)
```

### 2. ShadowStats + StrategyStats 카운터 추가
- `trades_rejected: int = 0`
- `trades_partial_fill: int = 0`

### 3. _execute_shadow_trade 개선
- OrderRejectedError 전용 catch → trades_rejected 증가
- 매수 체결 후 매도 수량 = buy_trade.amount (불일치 방지)
- 부분체결 감지 시 trades_partial_fill 증가

### 4. _execute_shadow_trade_request 개선
- OrderRejectedError 전용 catch → trades_rejected 증가
- N-leg에서 중간 실패 시 로그

### 5. _send_summary 수정
- 일일 요약에 rejection/partial_fill 카운트 포함

### 6. 테스트
- test_shadow_partial_fill: 부분체결 시 매도 수량 조정 검증
- test_shadow_rejection: 거부 시 stats.trades_rejected 증가 검증
- test_shadow_summary_includes_rejection_stats: 요약에 포함 확인
- random.seed로 deterministic 테스트

## 기존 테스트 영향
- 없음 (모든 기존 테스트가 mock/fixture PaperExecutor 사용)

## 완료 기준
1. partial_fill_rate=0.05 설정
2. rejection_rate=0.02 설정
3. Shadow 10min WR < 100%
4. pytest 전체 PASS
