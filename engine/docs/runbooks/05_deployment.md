# Runbook 05 — Deployment

**Severity:** MEDIUM (planned) / HIGH (emergency rollback)
**SLA:** Planned deployment: 30-minute maintenance window. Rollback: within 10 minutes.
**Related code:** `engine/src/main.py`, `engine/src/core/config.py`, `engine/pyproject.toml`

---

## Overview

LEVIATHAN deployments follow a rolling update pattern. The engine runs as a single Python process;
zero-downtime is achieved via shadow mode handover, not blue-green process duplication. This
runbook covers canary steps, rollback, pre-flight checks, and post-deployment verification.

---

## 1. Pre-Flight Checklist (Must Complete Before Deployment)

Run all checks in the 30 minutes before deployment begins.

### 1.1 Live gate pre-flight (72-hour shadow requirement for major releases)

```python
from engine.src.modes.live_gate import LiveGate

gate = LiveGate(...)
result = await gate.evaluate()

# ALL 6 checks must pass
for check in result.checks:
    status = "PASS" if check.passed else "FAIL"
    print(f"[{status}] {check.name}: {check.value} (threshold: {check.threshold})")

if not result.passed:
    raise SystemExit("PRE-FLIGHT FAILED: live gate not cleared")
```

### 1.2 Exchange health verification

10개 거래소 (spot 7 + futures 3) 모두 확인:

```bash
# All exchanges must report >= 95% health score
# 지원 거래소: binance, bybit, okx, bitget, upbit, bithumb, coinone,
#              binance_futures, okx_futures, bybit_futures (총 10개)
python3 -c "
import asyncio
from src.infra.exchange import get_all_adapters

async def main():
    for a in get_all_adapters():
        score = await a.get_health_score()
        state = 'OK' if score >= 0.95 else 'FAIL'
        print(f'{state} {a.exchange_id}: {score:.3f}')

asyncio.run(main())
"
```

Docker 컨테이너 상태 확인 (9개 컨테이너):

```bash
docker compose ps
# 확인 대상: leviathan-timescaledb, leviathan-redis, leviathan-dashboard,
#            leviathan-grafana, leviathan-prometheus, leviathan-redis-exporter
#            leviathan-nginx, leviathan-monitoring, leviathan-auto-tuner
docker compose ps --format "table {{.Name}}\t{{.Status}}" | grep -v healthy | grep -v "Up "
# 출력 없으면 전체 정상
```

### 1.3 Database continuity

```bash
# Check backup ran within last 24 hours
ls -lht /mnt/backups/leviathan/*.dump | head -3

# Check query latency
psql -U leviathan_user -d leviathan -c "
SELECT now() - MAX(ts) AS data_age FROM execution_log;
"
# data_age should be < 10 minutes for active trading

# Check replication lag if replica present
psql -U postgres -c "SELECT now() - pg_last_xact_replay_timestamp() AS replication_lag;"
# Should be < 10 seconds
```

### 1.4 Kill switch clear

```python
from engine.src.risk.kill_switch import is_halted
assert not is_halted(), "Kill switch is ACTIVE — abort deployment"
print("Kill switch: CLEAR")
```

### 1.5 Telegram notification test

```python
from engine.src.infra.telegram import TelegramNotifier
notifier = TelegramNotifier(...)
await notifier.send("PRE-FLIGHT: Deployment starting in 5 minutes")
# Confirm message received in Telegram within 5 seconds
```

### 1.6 Git state verification

```bash
# Confirm deploying the intended commit
git log --oneline -5

# Confirm no uncommitted changes (production should be clean)
git status --short
# Expected: empty output

# Confirm tests pass on this exact commit
cd engine && python -m pytest tests/ -x -q --tb=short 2>&1 | tail -20
```

---

## 2. Rolling Update Procedure (Zero-Downtime)

LEVIATHAN achieves continuity by transitioning from live mode to shadow mode before the process restart.

### Step 2.1 — Notify operations channel

```bash
# Telegram alert
python3 -c "
import asyncio
from engine.src.infra.telegram import TelegramNotifier
async def main():
    n = TelegramNotifier.from_env()
    await n.send('DEPLOYMENT: Rolling update starting. Engine switching to shadow mode.')
asyncio.run(main())
"
```

### Step 2.2 — Switch running engine to shadow mode

Send SIGUSR1 to the engine process (if signal handler is configured):

```bash
# Get PID
ENGINE_PID=$(pgrep -f "python.*leviathan.*main")
echo "Engine PID: $ENGINE_PID"

# Signal shadow mode switch
kill -SIGUSR1 $ENGINE_PID
sleep 5

# Confirm via log
journalctl -u leviathan --since "30s ago" | grep "shadow_mode_active"
```

Or set via environment flag and hot-reload if signal not supported:
```bash
# Update config file / env and send SIGHUP for hot-reload
echo "DATA_MODE=SYNTHETIC" >> /etc/leviathan/engine.env
kill -SIGHUP $ENGINE_PID
```

### Step 2.3 — Wait for in-flight orders to complete

```python
# Poll execution_log for any SUBMITTED (in-flight) orders
import asyncio

async def wait_for_clear(timeout_s=60):
    for _ in range(timeout_s):
        result = await db.fetchval("""
            SELECT COUNT(*) FROM execution_log
            WHERE status = 'SUBMITTED'
              AND ts > NOW() - INTERVAL '2 minutes'
        """)
        if result == 0:
            print("No in-flight orders")
            return True
        print(f"Waiting: {result} orders in-flight...")
        await asyncio.sleep(1)
    raise TimeoutError("In-flight orders did not clear within timeout")

asyncio.run(wait_for_clear())
```

### Step 2.4 — Deploy new version

```bash
cd /opt/leviathan

# Pull new code
git fetch origin
git checkout v1.X.Y  # or merge branch

# Install updated dependencies
cd engine && pip install -e ".[dev]" --quiet

# Run DB migrations if any
python -m engine.src.infra.db.migrations.run

# Restart engine with new code
sudo systemctl restart leviathan-engine

# Watch startup logs
journalctl -u leviathan -f --since "now" &
```

### Step 2.5 — Verify startup

```bash
# Engine should reach READY state within 30 seconds
timeout 60 bash -c 'until journalctl -u leviathan --since "1 min ago" | grep -q "engine_ready"; do sleep 2; done'
echo "Engine READY"
```

---

## 3. Canary Deployment Steps

For high-risk changes (new exchange adapter, signal algorithm change):

### Step 3.1 — Deploy to shadow instance

Run the new version in shadow mode alongside the live engine:

```bash
# Start canary instance on port 8001 (live runs on 8000)
DATA_MODE=SYNTHETIC \
ENGINE_PORT=8001 \
CANARY=true \
python -m engine.src.main --mode shadow &

CANARY_PID=$!
echo "Canary PID: $CANARY_PID"
```

### Step 3.2 — Monitor canary metrics for 30 minutes

```python
# Compare signal counts, slippage, and Sharpe between canary and live
# Canary metrics prefixed with 'canary_' in Prometheus

# Key checks:
# - canary_sharpe vs live_sharpe (should be comparable, not lower)
# - canary_slippage_bps vs model prediction (within 20%)
# - canary_signal_count > 0 (strategy active)
# - No circuit breaker triggers on canary
```

### Step 3.3 — Decision gate

```
After 30 minutes of canary shadow run:
├── Sharpe comparable AND no errors → Proceed to rolling update (Section 2)
├── Sharpe lower by > 20% → Investigate; abort canary
└── Error rate elevated → Rollback canary; do not promote
```

### Step 3.4 — Promote canary to live

```bash
# Stop canary
kill $CANARY_PID

# Deploy as primary (follow Section 2 procedure)
```

---

## 4. Rollback Procedure

If the deployment causes errors or performance degradation:

### Step 4.1 — Identify rollback trigger

```
Immediate rollback if ANY of:
- Engine fails to start within 60 seconds
- Kill switch triggers within 10 minutes of deploy
- Circuit breaker OPEN within 5 minutes
- Telegram alerts absent (notification failure)
- Walk-forward Sharpe drops > 20% vs pre-deploy value
```

### Step 4.2 — Revert to previous version

```bash
# Emergency rollback
cd /opt/leviathan

# Get previous working commit
git log --oneline -10  # identify last known-good commit
GOOD_COMMIT="abc1234"

git checkout $GOOD_COMMIT
cd engine && pip install -e ".[dev]" --quiet

# Restart with previous version
sudo systemctl restart leviathan-engine
```

### Step 4.3 — Verify rollback

```bash
# Confirm version
python -c "import engine; print(engine.__version__)"

# Run smoke test
python -m pytest engine/tests/unit/ -x -q --tb=short 2>&1 | tail -10

# Confirm engine starts clean
timeout 60 bash -c 'until journalctl -u leviathan --since "1 min ago" | grep -q "engine_ready"; do sleep 2; done'
```

### Step 4.4 — Post-rollback checklist

```
[ ] Engine started on previous version
[ ] Kill switch clear (is_halted() == False)
[ ] Circuit breaker in CLOSED state
[ ] Telegram notification: "ROLLBACK complete, back to vX.Y.Z"
[ ] Incident filed with timeline and root cause
[ ] Failed version tagged as do-not-deploy
```

---

## 5. Post-Deployment Verification

Run for 30 minutes after successful deployment:

### Step 5.1 — Functional verification

```python
# Run full live gate evaluation
from engine.src.modes.live_gate import LiveGate
gate = LiveGate(...)
result = await gate.evaluate()
print("Live gate:", "PASS" if result.passed else "FAIL")
for c in result.checks:
    print(f"  {c.name}: {c.value}")
```

### Step 5.2 — API 엔드포인트 smoke test

```bash
# JWT 토큰 발급 후 주요 엔드포인트 확인
TOKEN=$(curl -s -X POST http://localhost:8000/auth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=${DASHBOARD_USER}&password=${DASHBOARD_PASSWORD}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 주요 API 엔드포인트 목록
ENDPOINTS=(
  "GET /health"
  "GET /status"
  "GET /mode"
  "GET /exchanges"
  "GET /positions"
  "GET /pnl"
  "GET /trades"
  "GET /strategies"
  "GET /alerts"
  "GET /funding-rates"
  "GET /shadow/stats"
  "GET /portfolio-summary"   # US-072 추가
  "GET /attribution"
  "GET /settings"
  "GET /risk/metrics"
)

for ep in "${ENDPOINTS[@]}"; do
  METHOD=$(echo $ep | awk '{print $1}')
  PATH=$(echo $ep | awk '{print $2}')
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $TOKEN" \
    -X $METHOD "http://localhost:8000$PATH")
  echo "$STATUS $ep"
done
# 모든 엔드포인트 200 또는 인증 없는 경우 401 이어야 함
```

### Step 5.3 — 전략 활성화 확인

```bash
# 활성 전략 목록 확인 (futures_futures 포함 7개 이상)
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/strategies | \
  python3 -c "import sys,json; [print(s['id'], s.get('status')) for s in json.load(sys.stdin)]"
# 예상: cross_exchange, spot_futures, futures_futures, triangular,
#       funding_rate, statistical_arb, latency_arb 7개 (cex_dex: DEX_RPC_URL 설정 시 추가)
```

### Step 5.4 — Metrics baseline

```promql
# Prometheus — verify post-deploy baselines

# Signal throughput (should match pre-deploy within 10%)
rate(leviathan_signal_count_total[5m])

# Circuit breaker state (0 = CLOSED)
leviathan_circuit_breaker_state

# Execution latency p99 (should be < 500ms)
histogram_quantile(0.99, leviathan_execution_latency_ms_bucket)

# DB write latency p99 (should be < 100ms)
histogram_quantile(0.99, leviathan_db_write_latency_ms_bucket)
```

### Step 5.5 — Confirm Telegram alerts working

```bash
# Trigger a test alert
python3 -c "
import asyncio
from engine.src.infra.telegram import TelegramNotifier
async def main():
    n = TelegramNotifier.from_env()
    await n.send('POST-DEPLOY CHECK: All systems nominal after deployment v1.X.Y')
asyncio.run(main())
"
```

---

## References

- Engine entrypoint: `engine/src/main.py`
- Configuration: `engine/src/core/config.py`
- Live gate pre-flight: `engine/src/modes/live_gate.py`
- Kill switch check: `engine/src/risk/kill_switch.py:is_halted()`
- QUANT_MANIFESTO.md Section 8.2 (Pre-Flight Checklist)
- QUANT_MANIFESTO.md Section 8.1 (Phase 4 feature flags: `USE_NATIVE_BITGET`, etc.)
