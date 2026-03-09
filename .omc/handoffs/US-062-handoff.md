# US-062 Handoff: Rate Limit 시뮬레이션 (토큰 버킷)

## 구현 대상
- **SG-6**: Rate limit 시뮬레이션 없음 → 거래소별 토큰 버킷

## 파일 경계
- **Jennie (Backend)**: `engine/src/infra/exchange/rate_limiter.py`, `engine/src/modes/shadow.py`
- **Lisa (QA)**: `engine/tests/test_shadow_rate_limit.py`

## 구현 지침

### 1. TokenBucket.try_acquire() 추가 (rate_limiter.py)

기존 TokenBucket 클래스에 메서드 추가:
```python
def try_acquire(self, tokens: float = 1.0) -> bool:
    """Non-blocking acquire. Returns True if tokens consumed, False if insufficient."""
    self._refill()
    if self._tokens >= tokens:
        self._tokens -= tokens
        return True
    return False
```

### 2. ShadowRateLimiter 클래스 (shadow.py, VirtualBalanceTracker 아래)

```python
class ShadowRateLimiter:
    EXCHANGE_ORDER_RATES: dict[str, tuple[float, int]] = {
        "binance": (5.0, 10),
        "binance_futures": (5.0, 10),
        "bybit": (5.0, 10),
        "okx": (6.0, 12),
        "bitget": (10.0, 20),
        "upbit": (8.0, 8),
        "bithumb": (3.0, 5),
        "coinone": (2.0, 3),
    }
```

- `__init__`: env var override 파싱 (SHADOW_RATE_LIMIT_UPBIT 등)
- `_get_bucket(exchange_id)`: lazy-init, `paper_`/`sandbox_` prefix 제거
- `try_acquire(exchange_id) → bool`: bucket.try_acquire() 위임
- `summary() → dict`: 진단용

### 3. ShadowMode 통합

**__init__** (self._balance_tracker 아래):
```python
self._rate_limiter = ShadowRateLimiter()
```

**_execute_shadow_trade** (balance 체크 **이전**에 삽입):
```python
if not self._rate_limiter.try_acquire(signal.buy_exchange):
    self._stats.trades_rate_limited += 1
    logger.warning("shadow_mode.rate_limit_exceeded", exchange=signal.buy_exchange)
    return
if not self._rate_limiter.try_acquire(signal.sell_exchange):
    self._stats.trades_rate_limited += 1
    logger.warning("shadow_mode.rate_limit_exceeded", exchange=signal.sell_exchange)
    return
```

**_execute_shadow_trade_request** (leg 루프 **이전**에 삽입):
- 모든 leg의 exchange rate limit 체크, 하나라도 실패시 전체 return

### 4. Stats 추가

**ShadowStats** (line ~265):
- `trades_rate_limited: int = 0` 추가

**_send_summary**:
- summary_data에 `trades_rate_limited` 포함

### 5. 주의사항
- rate limit 체크는 balance deduct **이전** (rate-limited 주문은 잔고 소모 금지)
- TokenBucket import: `from src.infra.exchange.rate_limiter import TokenBucket`
- structlog 경고 키: `shadow_mode.rate_limit_exceeded`
- 기존 TokenBucket의 acquire() 메서드 변경 금지 (native adapter 사용 중)

### 6. 테스트 목록 (Lisa)

| # | 테스트명 | 검증 내용 |
|---|---------|----------|
| 1 | test_try_acquire_basic | 토큰 있으면 True |
| 2 | test_try_acquire_exhausted | 토큰 소진 시 False |
| 3 | test_try_acquire_refill | 시간 경과 후 refill되어 True |
| 4 | test_shadow_rate_limiter_default_rates | 알려진 거래소 기본 rate 사용 |
| 5 | test_shadow_rate_limiter_env_override | env var override 동작 |
| 6 | test_shadow_rate_limiter_prefix_strip | paper_binance → binance |
| 7 | test_execute_rate_limited_buy | buy exchange 제한 시 skip |
| 8 | test_execute_rate_limited_sell | sell exchange 제한 시 skip |
| 9 | test_rate_limit_before_balance | rate limit 시 balance deduct 미호출 |
| 10 | test_stats_rate_limited | stats.trades_rate_limited 증가 |
| 11 | test_structlog_warning | shadow_mode.rate_limit_exceeded 로그 |
