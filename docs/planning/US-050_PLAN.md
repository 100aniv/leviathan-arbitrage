# US-050: Inventory Rebalancer + Balance Tracker

## Acceptance Criteria
1. inventory_rebalancer.py: 매 4시간 잔고 체크, 편차 > 30% 시 이체 제안
2. balance_tracker.py: 매 5분 거래소별 잔고 폴링, 이력 저장
3. 잔고 부족 시 자동 거래 규모 축소
4. 임계치 이하 시 Telegram 경고

## 파일 변경
| 파일 | 변경 | 담당 |
|------|------|------|
| engine/src/core/balance_tracker.py | NEW — 거래소별 잔고 폴링 + 이력 | Jennie |
| engine/src/core/inventory_rebalancer.py | NEW — 잔고 편차 감지 + 이체 제안 | Jennie |
| engine/tests/unit/core/test_balance_tracker.py | NEW | Lisa |
| engine/tests/unit/core/test_inventory_rebalancer.py | NEW | Lisa |
