# US-062: 거래소별 Rate Limit 시뮬레이션 (토큰 버킷)

## 개요
SG-6 해결: Shadow 모드에서 거래소별 주문 속도 제한 시뮬레이션.
기존 `engine/src/infra/exchange/rate_limiter.py`의 `TokenBucket` 재사용.

## 설계

### 1. TokenBucket에 try_acquire() 추가 (rate_limiter.py)
- 기존 `acquire()`는 blocking (async). Shadow에는 non-blocking 필요
- `try_acquire(tokens=1.0) → bool`: 토큰 있으면 소모+True, 없으면 False

### 2. ShadowRateLimiter 클래스 (shadow.py)
- 거래소별 order rate 기본값 (DEFAULT_RATE_LIMITS 기반):
  - binance: 5/s, bybit: 5/s, okx: 6/s, bitget: 10/s
  - upbit: 8/s, bithumb: 3/s, coinone: 2/s
- env var override: SHADOW_RATE_LIMIT_UPBIT, _BITHUMB, _DEFAULT
- `paper_`/`sandbox_` prefix 자동 제거

### 3. ShadowMode 통합
- `_execute_shadow_trade`: rate limit 체크 → balance 체크 순서 (rate limit 먼저)
- `_execute_shadow_trade_request`: 모든 leg exchange 체크, 하나라도 실패시 전체 skip
- ShadowStats에 `trades_rate_limited` 카운터 추가

### 4. 파일 변경
- `engine/src/infra/exchange/rate_limiter.py`: try_acquire() 추가 (5줄)
- `engine/src/modes/shadow.py`: ShadowRateLimiter + 통합 + stats
- `engine/tests/test_shadow_rate_limit.py`: 14개 테스트
