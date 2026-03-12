# US-090: CEX-DEX Shadow 검증

## 변경 대상
1. `engine/tests/test_cex_dex_shadow.py` (NEW) — CEX-DEX shadow integration tests
2. `engine/src/strategies/cex_dex.py` (MODIFY, 최소) — shadow 호환성 확인

## 구현 상세

### 1. test_cex_dex_shadow.py — Shadow 통합 테스트
CEX-DEX 전략의 shadow mode 동작 검증:

#### 테스트 세트 A: Shadow 호환성
- CexDexStrategy가 ShadowMode와 호환 (on_signal → TradeRequest 흐름)
- PnL 추적: 거래 후 expected_profit_usdt 누적
- 가스비 정확도: DEXCostCalculator 비용이 metadata에 포함

#### 테스트 세트 B: 전략 동작 검증
- DEX 가격 > CEX 가격 → buy_cex_sell_dex 방향
- DEX 가격 < CEX 가격 → buy_dex_sell_cex 방향
- 스프레드 부족 시 시그널 필터링
- AMM slippage 반영 확인

#### 테스트 세트 C: 엔진 안정성
- DEX_RPC_URL 미설정 시 CexDex 미등록 (crash 없음)
- DEX_RPC_URL 설정 시 CexDex 등록 시도

### 2. cex_dex.py 수정 (최소)
- shadow mode에서 gas_cost 메타데이터 포함 확인
- 필요 시 on_fill에서 PnL 로깅 추가

## 테스트
- Shadow 호환성 5개
- 전략 동작 5개
- 엔진 안정성 2개
- 총 12개 이상
