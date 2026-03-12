# Runbook 02 — Exchange Outage Response

**Severity:** HIGH
**SLA:** Detection within 2 minutes. Failover decision within 10 minutes. Full recovery within 2 hours.
**Related code:** `engine/src/risk/circuit_breaker.py`, `engine/src/infra/exchange/__init__.py`
**지원 거래소 (10개):** binance, bybit, okx, bitget, upbit, bithumb, coinone, binance_futures, okx_futures, bybit_futures

---

## Overview

An exchange outage degrades or eliminates arbitrage opportunity on one or more legs. The circuit
breaker detects this automatically and transitions to OPEN, blocking new orders to that exchange.
This runbook covers detection, manual failover, hedging, and recovery procedures.

---

## 1. Detection

### 1.1 Automatic detection signals

The circuit breaker monitors three triggers (thresholds in `CircuitBreaker.__init__`):

| Signal | Default threshold | Log event |
|--------|-------------------|-----------|
| API error rate | > 20% of requests | `circuit_breaker_open` reason=`api_error_rate:X>0.20` |
| MDD exceeded | > 2% drawdown | `circuit_breaker_open` reason=`mdd_exceeded:X>0.02` |
| Consecutive losses | >= 5 trades | `circuit_breaker_open` reason=`consecutive_losses:N>=5` |

### 1.2 Health score monitoring

Exchange health score formula:
```
health_score = (1.0 - avg_latency_ms / 5000) * uptime_fraction

Threshold: health_score < 0.95 → Live gate blocks; circuit breaker at risk
```

Check health scores:

```python
from engine.src.infra.exchange import get_all_adapters

async def check_health():
    for adapter in get_all_adapters():
        score = await adapter.get_health_score()
        print(f"{adapter.exchange_id}: {score:.3f} ({'OK' if score >= 0.95 else 'DEGRADED'})")
```

### 1.3 WebSocket disconnect detection

Look for collector disconnect events in logs:

```bash
# Docker 컨테이너 로그 (권장)
docker compose logs --tail=100 leviathan-engine | \
  grep -E "ws_disconnect|reconnect_attempt|collector_error"

# Loki 쿼리 (logcli 설치 필요, Loki 포트 3100)
logcli query '{container="leviathan-engine"} |= "ws_disconnect"' \
  --addr=http://localhost:3100 --since=1h

# 레거시: 파일 로그 직접 확인
grep -E "ws_disconnect|reconnect_attempt|collector_error" /var/log/leviathan/engine.log | tail -50
```

### 1.4 Decision tree

```
health_score drops below 0.95?
├── YES → Is circuit breaker OPEN?
│   ├── YES → Exchange is confirmed down; proceed to Section 2
│   └── NO  → Transient latency spike; monitor for 2 more minutes
└── NO  → False alarm; check Prometheus for rate spikes
```

---

## 2. Failover Procedure

### Step 2.1 — Identify affected exchange

```python
from engine.src.risk.circuit_breaker import CircuitBreaker

# Check circuit breaker state per exchange
print("CB state:", circuit_breaker.state)
print("Last reason:", circuit_breaker.stats.last_trigger_reason)
print("API error rate:", circuit_breaker.stats.api_errors / max(1, circuit_breaker.stats.api_requests))
```

### Step 2.2 — Disable the affected exchange

Prevent new signal generation involving the failing exchange:

```python
# In running engine process (via admin interface or config hot-reload)
config.disabled_exchanges.add("bitget")  # example
logger.warning("exchange_disabled", exchange="bitget", reason="outage_detected")
```

Or via environment variable restart (zero-downtime not guaranteed):

```bash
# engine/.env에서 TRADING_ACTIVE_EXCHANGES 수정 (장애 거래소 제거)
# 현재 기본값: binance,bybit,okx,bitget,upbit,bithumb,coinone,binance_futures,okx_futures,bybit_futures
TRADING_ACTIVE_EXCHANGES=["binance","bybit","okx","upbit","bithumb","coinone","binance_futures"] \
    python -m src.main
```

Docker로 실행 중인 경우:

```bash
# 컨테이너 재시작 (환경변수 변경 적용)
docker compose restart leviathan-engine
sleep 10
docker compose logs --tail=50 leviathan-engine | grep -E "exchange|collector|engine_ready"
```

### Step 2.3 — Redistribute pairs to healthy exchanges

Check which symbol pairs have alternative liquidity:

```python
# 10개 거래소 중 대체 거래소 식별
# spot: binance, bybit, okx, bitget, upbit, bithumb, coinone
# futures: binance_futures, okx_futures, bybit_futures
available_pairs = {
    "BTC/USDT": ["bybit", "okx", "binance", "binance_futures", "okx_futures"],
    "ETH/USDT": ["bybit", "okx", "binance", "bybit_futures"],
    "BTC/KRW":  ["upbit", "bithumb", "coinone"],
    # ...
}

disabled = "bitget"
for pair, exchanges in available_pairs.items():
    alternatives = [e for e in exchanges if e != disabled]
    if alternatives:
        print(f"{pair}: reroute to {alternatives[0]}")
    else:
        print(f"{pair}: NO alternative — suspend signal")
```

### Step 2.4 — Suspend signals with no alternative

```python
# Temporarily disable strategies that exclusively use the failed exchange
config.suspended_strategies.add("bitget_bybit_arb")
```

---

## 3. Position Hedging on Other Exchanges

If orders were in-flight when the outage occurred:

### Step 3.1 — Check for stranded positions

```python
async def check_stranded_positions():
    # Query execution_log for in-flight trades on the affected exchange
    # status = 'SUBMITTED' and not 'SUCCESS'/'ROLLED_BACK'
    query = """
        SELECT order_id, symbol, side, amount, status, ts
        FROM execution_log
        WHERE exchange_id = $1
          AND status = 'SUBMITTED'
          AND ts > NOW() - INTERVAL '30 minutes'
    """
    return await db.fetch(query, exchange_id)
```

### Step 3.2 — Hedge stranded long positions

If leg1 (buy) filled but leg2 (sell) failed due to outage:

```python
# Determine net exposure from stranded leg1 fills
# Sell equivalent position on next-best exchange
hedge_order = Order(
    symbol="BTC/USDT",
    side=OrderSide.SELL,
    amount=stranded_amount,
    exchange_id="bybit",  # fallback exchange
)
result = await executor.execute_single(hedge_order)
```

### Step 3.3 — Document hedge in incident log

```bash
echo "$(date -u) HEDGE: sold 0.05 BTC/USDT on bybit to cover stranded bitget leg1" \
  >> /var/log/leviathan/incident_$(date +%Y%m%d).log
```

---

## 4. Rebalancing After Recovery

### Step 4.1 — Confirm exchange restored

```python
async def verify_exchange_recovery(exchange_id: str) -> bool:
    adapter = get_adapter(exchange_id)
    # Ping with a tiny order book fetch
    book = await adapter.fetch_order_book("BTC/USDT", limit=5)
    score = await adapter.get_health_score()
    return score >= 0.95 and book is not None
```

### Step 4.2 — Re-enable exchange in config

```python
config.disabled_exchanges.discard("bitget")
logger.info("exchange_reenabled", exchange="bitget")
```

### Step 4.3 — Transition circuit breaker to HALF_OPEN manually if needed

The cooldown timer (default 300s) triggers HALF_OPEN automatically. To expedite:

```python
# Force half-open for testing (non-production use only)
# Note: this bypasses the cooldown — only do this after manual verification
async with circuit_breaker._lock:
    circuit_breaker._state = CircuitBreakerState.HALF_OPEN
```

### Step 4.4 — HALF_OPEN test sequence

In HALF_OPEN state, 3 consecutive wins are required to return to CLOSED:

```python
# Monitor half-open progress
stats = circuit_breaker.stats
print(f"Half-open successes: {stats.half_open_successes}/{circuit_breaker._half_open_test_count}")
# When successes >= 3 → auto-transitions to CLOSED
```

### Step 4.5 — Resume suspended strategies

```python
config.suspended_strategies.discard("bitget_bybit_arb")
logger.info("strategy_resumed", strategy="bitget_bybit_arb")
```

---

## 5. Circuit Breaker Interaction Summary

```
Normal: CLOSED → trading enabled, health monitored
Outage: CLOSED → OPEN (trigger: api_error_rate or latency)
  └── Auto cooldown (300s) → HALF_OPEN
        ├── 3 wins → CLOSED (recovery complete)
        └── Any loss → OPEN again (restart cooldown)

Manual force: trigger_manual(reason="outage_confirmed")
```

State check:

```python
print(circuit_breaker.state)          # CLOSED / OPEN / HALF_OPEN
print(circuit_breaker.allows_trading())  # True if CLOSED or HALF_OPEN
```

---

## References

- Circuit breaker: `engine/src/risk/circuit_breaker.py`
- Exchange adapters: `engine/src/infra/exchange/__init__.py`
- Live gate exchange health check: `engine/src/modes/live_gate.py:374-405`
- QUANT_MANIFESTO.md Section 7.3 (CircuitBreaker states)
