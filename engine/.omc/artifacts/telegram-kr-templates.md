# Telegram Korean Templates: send_alert -> send_alert_kr Migration

## Date: 2026-03-24

## Summary
Migrated all 15 `send_alert(text, level)` call sites in `shadow.py` and `main.py` to structured `send_alert_kr(alert_type, data)` calls with Korean templates.

## New alert_type Templates Added (15 types total, 12 new)

| alert_type | Source File | Description |
|---|---|---|
| `shadow_start` | shadow.py:650 | Shadow mode start notification |
| `shadow_daily_breakdown` | shadow.py:2122 | Per-strategy daily performance breakdown |
| `krw_soft_block` | shadow.py:1974 | KRW exchange rate stale - soft block activated |
| `krw_killswitch` | shadow.py:1992 | KRW prolonged outage - kill switch triggered |
| `krw_recovered` | shadow.py:2006 | KRW exchange rate recovered |
| `position_discrepancy` | main.py:1279 | Position reconciler found mismatches |
| `position_tracking_fail` | main.py:1400 | Persistent position tracking failure |
| `inventory_critical` | main.py:1549 | Critical inventory imbalance detected |
| `inventory_rebalance` | main.py:1558 | Inventory rebalance suggestions |
| `order_cancel_fail` | main.py:1597 | Order cancellation failed |
| `data_collector_start` | main.py:1966 | Real data collectors started |
| `live_mode_start` | main.py:2088 | Live mode activated |
| `shadow_mode_start` | main.py:2460 | Shadow mode activated (main.py) |
| `orphan_positions` | main.py:2724 | Orphaned positions found on startup |
| `balance_mismatch` | main.py:2802 | Balance reconciliation mismatch |

Pre-existing types (unchanged): `kill_switch`, `circuit_breaker`, `db_failure`

## Changed Call Sites (15 total)

### shadow.py (5 sites)
1. **Line 650** - Shadow start: `send_alert("...", level="INFO")` -> `send_alert_kr("shadow_start", {})`
2. **Line 1974** - KRW soft block: `send_alert(f"KRW...", level="WARNING")` -> `send_alert_kr("krw_soft_block", {"stale_seconds": elapsed})`
3. **Line 1992** - KRW killswitch: `send_alert(f"...", level="CRITICAL")` -> `send_alert_kr("krw_killswitch", {"stale_seconds": elapsed})`
4. **Line 2006** - KRW recovered: `send_alert("...", level="INFO")` -> `send_alert_kr("krw_recovered", {})`
5. **Line 2122** - Daily breakdown: hand-built lines + `send_alert(lines, level="INFO")` -> `send_alert_kr("shadow_daily_breakdown", {strategies, trades_rejected, trades_partial_fill})`

### main.py (10 sites)
1. **Line 1279** - Position discrepancy: `send_alert(f"...", level="CRITICAL")` -> `send_alert_kr("position_discrepancy", {count, summary})`
2. **Line 1400** - Position tracking fail: `send_alert(f"...", level="CRITICAL")` -> `send_alert_kr("position_tracking_fail", {error_count})`
3. **Line 1549** - Inventory critical: `send_alert("...", level="CRITICAL")` -> `send_alert_kr("inventory_critical", {})`
4. **Line 1558** - Inventory rebalance: hand-built lines + `send_alert(lines, level="WARNING")` -> `send_alert_kr("inventory_rebalance", {suggestions})`
5. **Line 1597** - Order cancel fail: `send_alert(f"...", level="CRITICAL")` -> `send_alert_kr("order_cancel_fail", {exchange, order_id, error})`
6. **Line 1966** - Data collector start: `send_alert(f"...", level="INFO")` -> `send_alert_kr("data_collector_start", {exchanges, symbols})`
7. **Line 2088** - Live mode start: `send_alert(f"...", level="INFO")` -> `send_alert_kr("live_mode_start", {exchanges, symbols})`
8. **Line 2460** - Shadow mode start: `send_alert(f"...", level="INFO")` -> `send_alert_kr("shadow_mode_start", {exchanges, symbols, live_gate})`
9. **Line 2724** - Orphan positions: `send_alert(f"...", level="WARNING")` -> `send_alert_kr("orphan_positions", {found, closed, resumed})`
10. **Line 2802** - Balance mismatch: `send_alert(f"...", level="")` -> `send_alert_kr("balance_mismatch", {detail})`

## Test Updates
- `tests/unit/test_shadow_mode.py`: Updated 3 mocks (`send_alert` -> `send_alert_kr`)
- `tests/unit/test_shadow_partial_fill_rejection.py`: Updated 1 mock (`send_alert` -> `send_alert_kr`)

## Backward Compatibility
- `send_alert(message, level)` method preserved in `telegram.py` (not deleted)
- Other callers (progressive_shadow.py, shadow_runner.py) still use `send_alert` and are unaffected

## Test Results
- Telegram tests: 64 passed, 0 failed
- Shadow mode tests: 65 passed, 0 failed
- Progressive shadow + shadow runner tests: 52 passed, 0 failed
- Syntax check: all 3 modified source files OK
