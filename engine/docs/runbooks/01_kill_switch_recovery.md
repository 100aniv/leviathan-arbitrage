# Runbook 01 — Kill Switch Recovery

**Severity:** CRITICAL
**SLA:** Acknowledge within 5 minutes. Position verification within 15 minutes. Resume decision within 60 minutes.
**Related code:** `engine/src/risk/kill_switch.py`, `engine/src/execution/executor.py`

---

## Overview

The LEVIATHAN kill switch is a 3-tier emergency halt. When triggered, ALL new order submissions
are blocked immediately (< 1ms via `threading.Event`). This runbook covers diagnosis, position
verification, and the controlled resume procedure.

---

## 1. Trigger Cause Diagnosis

### Determine which tier fired

Check structured logs for the trigger event:

```bash
# Tail recent critical-level log entries
journalctl -u leviathan --since "1 hour ago" | grep -E "kill_switch|halt_local|CRITICAL"

# Or from structlog JSON output
cat /var/log/leviathan/engine.log | python3 -c "
import sys, json
for line in sys.stdin:
    try:
        e = json.loads(line)
        if 'kill_switch' in e.get('event','') or e.get('level') == 'critical':
            print(json.dumps(e, indent=2))
    except: pass
"
```

### Tier classification

| Tier | Log event | Root cause |
|------|-----------|------------|
| Tier 1 (Loss-Based) | `kill_switch_tier1_complete` + `daily_loss_exceeded` | Cumulative daily loss > -0.5 BTC threshold |
| Tier 2 (Technical) | `circuit_breaker_open` duration > 30min, or `latency_spike` | Exchange connectivity / API failure |
| Tier 3 (Manual) | `halt_local` called without prior tier1/tier2 | Operator override |

### Decision tree

```
Is is_halted() == True?
├── YES → Check last trigger reason
│   ├── loss_exceeded → Tier 1 (go to Section 2)
│   ├── circuit_breaker / latency / connectivity → Tier 2 (go to Section 2 + RB-02)
│   └── manual / operator call → Tier 3 (go to Section 3)
└── NO → False alarm; log was stale; no action needed
```

### Check current halt state in Python

```python
from engine.src.risk.kill_switch import is_halted
print("Halted:", is_halted())
```

---

## 2. Position Verification

Before any resume, verify all positions are flat or accounted for.

### Step 2.1 — List open positions via exchange APIs

```python
import asyncio
from engine.src.infra.exchange import get_all_adapters

async def check_positions():
    adapters = get_all_adapters()
    for adapter in adapters:
        positions = await adapter.fetch_open_positions()
        orders   = await adapter.fetch_open_orders()
        print(f"{adapter.exchange_id}: positions={positions}, open_orders={orders}")

asyncio.run(check_positions())
```

### Step 2.2 — Verify kill switch event log

```python
# KillSwitchEvent records cancelled_orders and closed_positions
# Check whether Tier 2 and Tier 3 completed successfully
# Look for:
#   kill_switch_tier2_complete  (cancelled_orders count)
#   kill_switch_tier3_complete  (closed_positions count)
```

### Step 2.3 — Stranded position check

If `tier3_latency_ms` is missing or errors list is non-empty:

```
Tier 3 incomplete or errored → positions may be open → DO NOT resume yet
Action: Manually close positions via exchange UI or API
        Then log incident: stranded_position_manual_close=True
```

### Step 2.4 — TimescaleDB reconciliation

```sql
-- Check last execution records for unreconciled trades
SELECT strategy_id, status, ts, net_pnl
FROM execution_log
WHERE ts > NOW() - INTERVAL '2 hours'
  AND status NOT IN ('SUCCESS', 'ROLLED_BACK')
ORDER BY ts DESC
LIMIT 20;
```

---

## 3. Halt Release Procedure

Only proceed after positions are verified flat (Section 2 complete).

### Step 3.1 — Root cause confirmed resolved

```
Tier 1 (Loss): Confirm daily loss source understood. Adjust position limits if needed.
Tier 2 (Technical): Confirm exchange/connectivity restored (health_score >= 0.95).
Tier 3 (Manual): Confirm operator approves resume.
```

### Step 3.2 — Clear halt flag

```python
from engine.src.risk.kill_switch import clear_halt, is_halted

# Verify pre-conditions
assert not has_open_positions(), "Must close all positions before clearing halt"

# Clear both Python and Rust flags
clear_halt()

# Confirm cleared
print("Halted after clear:", is_halted())  # must print False
```

### Step 3.3 — Reset KillSwitch instance

```python
# If using KillSwitch class instance (engine main loop)
kill_switch.reset()  # calls clear_halt() + resets _triggered flag
```

### Step 3.4 — Verify live gate before resuming

```python
from engine.src.modes.live_gate import LiveGate

gate = LiveGate(...)
result = await gate.evaluate()
print("Live gate passed:", result.passed)
for check in result.checks:
    print(f"  {check.name}: {'OK' if check.passed else 'FAIL'} ({check.value})")
```

All 6 checks must pass before resuming live trading.

---

## 4. Post-Recovery Monitoring Checklist

Run for at least 30 minutes after resume:

```
[ ] is_halted() returns False consistently
[ ] Circuit breaker in CLOSED state
[ ] No new Tier 1 triggers (monitor daily_loss metric)
[ ] Order fill rates >= 80% on both legs
[ ] Telegram alerts flowing normally (test message received)
[ ] Walk-forward Sharpe still >= 2.5
[ ] No ROLLBACK_FAILED events in execution_log
[ ] DB write latency < 100ms (p99)
```

Prometheus queries (if metrics enabled):

```promql
# Kill switch halt state (should be 0)
leviathan_halt_active

# Daily loss accumulation
leviathan_daily_loss_btc

# Order fill ratio
rate(leviathan_execution_filled_total[5m]) / rate(leviathan_execution_submitted_total[5m])
```

---

## 5. Escalation Contacts

| Situation | Contact | Channel |
|-----------|---------|---------|
| Tier 1 + positions not flat | On-call operator | Telegram @leviathan_ops |
| Tier 2 + exchange API down > 1h | Exchange support + engineering | Direct + ops channel |
| Tier 3 + unknown trigger | Engineering lead | Immediate call |
| ROLLBACK_FAILED (stranded position) | All hands | Incident channel |

**Escalation threshold:** If halt cannot be cleared within 60 minutes, escalate to engineering lead.

---

## References

- Kill switch implementation: `engine/src/risk/kill_switch.py`
- Executor halt checks: `engine/src/execution/executor.py` (Step 0 of every execution)
- Live gate criteria: `engine/src/modes/live_gate.py`
- QUANT_MANIFESTO.md Section 7.2 (KillSwitch 3 Tiers)
