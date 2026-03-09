# US-059 Handoff: Shadow 레그 간 실행 지연(50-300ms) 추가

## 목표
매수-매도 레그 사이에 asyncio.sleep(50-300ms 랜덤)을 삽입하여 실제 거래소 실행 지연을 시뮬레이션 (SG-2)

## 변경 파일
- `engine/src/modes/shadow.py` (유일한 소스 변경)

## 구현 상세

### 1. _execute_shadow_trade 수정 (약 line 736-756)
buy_trade 실행 후, sell_order 생성 전에 지연 삽입:
```python
buy_trade = await self._paper_executor.execute(buy_order)

# Simulate realistic inter-leg execution delay (50-300ms)
delay_ms = random.uniform(50, 300)
await asyncio.sleep(delay_ms / 1000.0)

# ... partial fill detection ...
# ... sell_order creation and execution ...
```

env var로 설정 가능하게:
- `SHADOW_LEG_DELAY_MIN_MS` (기본 50)
- `SHADOW_LEG_DELAY_MAX_MS` (기본 300)
- 둘 다 0이면 지연 없음 (테스트 호환)

### 2. _execute_shadow_trade_request 수정 (약 line 960-979)
N-leg 루프에서 각 leg 사이에 지연 삽입:
```python
for i, leg in enumerate(trade_request.legs):
    # ... order creation ...
    trade = await self._paper_executor.execute(order)
    trades.append((leg, trade))
    # Inter-leg delay (skip after last leg)
    if i < len(trade_request.legs) - 1:
        delay_ms = random.uniform(min_delay, max_delay)
        await asyncio.sleep(delay_ms / 1000.0)
```

### 3. ShadowMode.__init__에 delay 설정 읽기
```python
self._leg_delay_min_ms = float(os.environ.get("SHADOW_LEG_DELAY_MIN_MS", "50"))
self._leg_delay_max_ms = float(os.environ.get("SHADOW_LEG_DELAY_MAX_MS", "300"))
```

## Acceptance Criteria
1. 매수-매도 레그 사이 asyncio.sleep(random 50-300ms) 삽입 ✓
2. Shadow 10min 지연 포함해도 수익성 유지
3. pytest 전체 PASS

## 주의사항
- asyncio.sleep은 실제로 이벤트 루프를 양보하므로 Shadow 10min 실행 시간이 약간 늘어남
- 테스트에서는 asyncio.sleep을 mock하여 deterministic 유지
- 기존 테스트 영향 없음 (mock PaperExecutor 사용)
