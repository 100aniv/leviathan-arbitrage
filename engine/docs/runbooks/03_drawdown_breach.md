# Runbook 03 — Drawdown Breach Response

**Severity:** HIGH
**SLA:** Detection within 5 minutes. Parameter adjustment within 30 minutes. Walk-forward re-evaluation within 24 hours.
**Related code:** `engine/src/risk/circuit_breaker.py`, `engine/src/analysis/walk_forward.py`, `engine/src/modes/live_gate.py`

---

## Overview

A drawdown breach occurs when the maximum drawdown (MDD) exceeds the live gate threshold of 5%
(peak-to-trough on cumulative PnL). The circuit breaker triggers at the lower threshold of 2%,
providing an early warning. This runbook covers the cooldown procedure, parameter adjustment,
walk-forward re-evaluation, and restart criteria.

---

## 1. Detection and Classification

### 1.1 Drawdown thresholds

| Threshold | System | Implication |
|-----------|--------|-------------|
| MDD > 2% | Circuit breaker OPEN | Automated trading pause |
| MDD >= 5% | Live gate FAIL | Live trading blocked until recovery |
| MDD >= 10% | Kill switch Tier 1 risk | Manual intervention required |

### 1.2 Check current drawdown

```python
from engine.src.risk.circuit_breaker import CircuitBreaker

stats = circuit_breaker.stats
print(f"Current drawdown: {stats.current_drawdown_pct*100:.2f}%")
print(f"CB state: {circuit_breaker.state}")
print(f"Trigger reason: {stats.last_trigger_reason}")
```

### 1.3 Compute drawdown from execution log

```sql
-- Walk the PnL series to compute current MDD
WITH cumulative AS (
    SELECT
        ts,
        SUM(net_pnl) OVER (ORDER BY ts) AS cum_pnl,
        MAX(SUM(net_pnl) OVER (ORDER BY ts)) OVER (ORDER BY ts) AS running_peak
    FROM execution_log
    WHERE strategy_id = 'main' AND ts > NOW() - INTERVAL '7 days'
    ORDER BY ts
)
SELECT
    MAX((running_peak - cum_pnl) / NULLIF(running_peak, 0)) AS current_mdd
FROM cumulative;
```

### 1.4 Decision tree

```
MDD reading from circuit breaker stats:
├── MDD > 2% and < 5% → Circuit breaker OPEN; monitor; skip to Section 2
├── MDD >= 5%          → Live gate blocked; full parameter review (Section 3)
└── MDD >= 10%         → Consider kill switch Tier 1; escalate immediately
```

---

## 2. Circuit Breaker Cooldown Procedure

### Step 2.1 — Confirm circuit breaker is OPEN

```python
print(circuit_breaker.is_open())  # True
print(circuit_breaker.stats.last_trigger_reason)
# e.g. "mdd_exceeded:0.0234>0.02"
```

### Step 2.2 — Wait for automatic cooldown (preferred)

Default cooldown: **300 seconds (5 minutes)**. The circuit breaker auto-transitions to HALF_OPEN:

```python
# Monitor cooldown progress
import time
state_age = time.monotonic() - circuit_breaker.stats.state_changed_at
remaining = circuit_breaker._cooldown_seconds - state_age
print(f"Cooldown remaining: {remaining:.0f}s")
```

### Step 2.3 — Manual trigger if needed

If the drawdown source is identified and remedied before cooldown expires:

```python
# Only after confirming root cause is resolved
await circuit_breaker.trigger_manual("drawdown_source_resolved_manual")
# Then wait for half-open testing
```

### Step 2.4 — HALF_OPEN test criteria

Three consecutive winning trades required to return to CLOSED:

```python
print(f"Half-open wins: {circuit_breaker.stats.half_open_successes}")
# Target: >= circuit_breaker._half_open_test_count (default 3)
```

If any loss occurs in HALF_OPEN, circuit breaker returns to OPEN with a new cooldown.

---

## 3. Parameter Adjustment

### Step 3.1 — Identify drawdown source

Common causes:

| Cause | Indicator | Adjustment |
|-------|-----------|------------|
| Spread compression | avg_spread_bps falling | Increase min_edge_bps threshold |
| Position size too large | Slippage > model | Reduce max_position_size |
| Concentrated exposure | Single pair > 70% of trades | Cap per-pair exposure |
| Fee regime change | fee_total / gross_spread growing | Recalibrate break-even spread |

```python
# Query PnL breakdown
```sql
SELECT
    symbol,
    COUNT(*) as trades,
    SUM(net_pnl) as total_pnl,
    AVG(gross_spread_bps) as avg_spread_bps,
    AVG(fee_total) as avg_fee,
    AVG(slippage_total) as avg_slippage
FROM execution_log
WHERE ts > NOW() - INTERVAL '24 hours'
GROUP BY symbol
ORDER BY total_pnl ASC;
```

### Step 3.2 — Reduce position sizes

```python
# In TradingSettings / config hot-reload
config.max_position_size_btc *= 0.5   # halve position size
config.min_edge_bps += 2              # raise edge threshold by 2 bps
logger.info("params_adjusted", reason="drawdown_breach",
            new_max_pos=config.max_position_size_btc,
            new_min_edge=config.min_edge_bps)
```

### Step 3.3 — Increase minimum edge threshold

```python
# Ensure trades only execute with sufficient spread
# Default: 5 bps. Increase to 8-10 bps during drawdown recovery.
config.min_edge_bps = 10
```

### Step 3.4 — Re-verify slippage model calibration

슬리피지 소스: **SignalGenerator의 CEXOrderbookSlippage만** 사용. PaperExecutor에 추가 슬리피지 적용 금지 (이중계산 방지).

```sql
SELECT
    AVG(slippage_total / gross_spread_bps) as slippage_fraction,
    STDDEV(slippage_total) as slippage_stddev
FROM execution_log
WHERE ts > NOW() - INTERVAL '7 days'
  AND status = 'SUCCESS';
-- Expected: slippage_fraction < 0.30 (CEXOrderbookSlippage 기반)
-- 주의: PowerLawSlippage(k=5.0)는 ~100bps 왕복 → PaperExecutor 적용 절대 금지
```

---

## 4. Walk-Forward Re-Evaluation Trigger

### Step 4.1 — Run walk-forward analysis

```python
from engine.src.analysis.walk_forward import WalkForwardAnalyzer

analyzer = WalkForwardAnalyzer(db_pool=db_pool)
result = await analyzer.run(
    strategy_id="main",
    period_days=7,
    window_hours=1,
)

print(f"Overall Sharpe: {result.overall_sharpe:.2f}")   # need >= 2.5
print(f"Overall MDD:    {result.overall_mdd*100:.1f}%") # need < 5%
print(f"Signals/day:    {result.avg_signals_per_day:.0f}")  # need >= 100
```

### Step 4.2 — Evaluate result against live gate thresholds

```
Sharpe >= 2.5 AND MDD < 5% AND signals >= 100?
├── YES → Strategy recoverable; proceed with reduced position size
└── NO  → Strategy adjustment required (see Step 4.3)
```

### Step 4.3 — If walk-forward fails live gate

```python
# Identify which checks fail
from engine.src.modes.live_gate import LiveGate

gate = LiveGate(...)
eval_result = await gate.evaluate()
for check in eval_result.checks:
    if not check.passed:
        print(f"FAIL: {check.name} = {check.value} (threshold: {check.threshold})")
```

Interpret failures:
- Sharpe < 2.5: edge has degraded; may need new signal parameters or different pairs
- MDD >= 5%: position sizing too aggressive for current volatility regime
- Signals < 100/day: exchange coverage insufficient; re-enable disabled pairs

---

## 5. Restart Criteria (Sharpe Recovery)

### Step 5.1 — Mandatory waiting period

After a drawdown breach, do not resume full-size trading until:

```
[ ] Circuit breaker returns to CLOSED (3 HALF_OPEN wins)
[ ] Walk-forward Sharpe >= 2.5 on fresh 7-day window
[ ] MDD < 5% on the same window
[ ] Position sizes reduced by >= 50% for first 48 hours of resumption
[ ] No circuit breaker re-trigger in first 4 hours after resume
```

### Step 5.2 — Gradual size ramp

```python
# Day 1 of recovery: 50% of normal position size
config.max_position_size_btc = base_max * 0.50

# Day 3 if no circuit breaker trigger: 75%
config.max_position_size_btc = base_max * 0.75

# Day 7 if Sharpe maintained: full size
config.max_position_size_btc = base_max
```

### Step 5.3 — Automated Sharpe monitoring

```python
# Schedule daily walk-forward check
async def daily_sharpe_check():
    result = await analyzer.run(strategy_id="main", period_days=7)
    if result.overall_sharpe < 2.5:
        logger.warning("sharpe_below_threshold", sharpe=result.overall_sharpe)
        await telegram.send("WARNING: Sharpe {:.2f} < 2.5".format(result.overall_sharpe))
```

---

## 6. Communication to Stakeholders

### Immediate notification (within 10 minutes of detection)

```
Subject: LEVIATHAN Drawdown Breach - [DATE]
- MDD level: X.X%
- CB state: OPEN
- Trading: PAUSED
- Root cause: [identified/investigating]
- ETA to resume: [estimate]
```

### Hourly updates during recovery

```
- Current MDD: X.X% (was X.X% at breach)
- Circuit breaker: [OPEN/HALF_OPEN/CLOSED]
- Sharpe (rolling 7d): X.XX
- Parameter changes made: [list]
- Next checkpoint: [time]
```

### Post-incident report (within 24 hours)

```
- Timeline of events
- Root cause analysis
- Parameter changes applied
- Walk-forward results post-adjustment
- Prevention measures
```

---

## References

- Circuit breaker thresholds: `engine/src/risk/circuit_breaker.py:52-66`
- Walk-forward analysis: `engine/src/analysis/walk_forward.py`
- Live gate MDD check: `engine/src/modes/live_gate.py:155-167`
- QUANT_MANIFESTO.md Section 4 (Walk-Forward), Section 5 (Live Gate)
- QUANT_MANIFESTO.md Section 7.3 (CircuitBreaker state machine)
