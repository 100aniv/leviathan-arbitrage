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

Docker 컨테이너 상태 확인 (14개 컨테이너):

```bash
docker compose ps
# 확인 대상 (14개):
#   핵심: leviathan-engine, leviathan-timescaledb, leviathan-redis
#   프론트: leviathan-dashboard, leviathan-nginx
#   모니터링: leviathan-grafana, leviathan-prometheus, leviathan-redis-exporter
#   운영: leviathan-monitoring, leviathan-auto-tuner
#   백업: leviathan-db-backup, leviathan-wal-backup
#   로그: leviathan-loki, leviathan-promtail
docker compose ps --format "table {{.Name}}\t{{.Status}}" | grep -v " healthy" | grep -v "Up "
# 출력 없으면 전체 정상 (db-backup, wal-backup은 restart:"no" → Exited 정상)
```

### 1.3 Database continuity

```bash
# Docker db-backup 컨테이너 최근 실행 로그 확인
docker compose logs --tail=20 leviathan-db-backup 2>/dev/null || echo "db-backup: not yet run"

# 백업 볼륨에서 최신 dump 파일 확인 (최근 24시간 내)
DUMP_PATH=$(docker volume inspect leviathan_db_backups --format '{{.Mountpoint}}' 2>/dev/null)
[ -n "$DUMP_PATH" ] && ls -lht "${DUMP_PATH}"/*.dump 2>/dev/null | head -3 || echo "No dumps found"

# WAL 아카이브 최신 세그먼트 확인
docker compose exec timescaledb ls -lht /var/lib/postgresql/wal_archive/ 2>/dev/null | head -5

# DB 마지막 데이터 시각 확인
docker compose exec timescaledb psql -U leviathan -d leviathan -c \
  "SELECT now() - MAX(ts) AS data_age FROM execution_log;"
# data_age < 10분이어야 함 (활성 거래 중)

# Loki에서 DB 관련 에러 확인
logcli query '{container="leviathan-engine"} |= "database_error"' \
  --addr=http://localhost:3100 --since=1h 2>/dev/null || \
docker compose logs --tail=50 leviathan-engine | grep -E "database_error|db_connected"
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
cd /path/to/arbitrage_OMC

# Pull new code
git fetch origin
git checkout v1.X.Y  # or merge branch

# Docker 이미지 빌드 및 재배포 (권장)
docker compose build leviathan-engine leviathan-dashboard

# Run DB migrations if any (엔진 컨테이너에서 실행)
docker compose run --rm leviathan-engine python -m src.infra.db.migrations.run

# 핵심 서비스 순차 재시작 (의존성 순서 준수)
docker compose up -d leviathan-timescaledb leviathan-redis
sleep 15
docker compose up -d leviathan-engine
sleep 10
docker compose up -d leviathan-dashboard leviathan-nginx
sleep 5

# 로그 관련 서비스 (loki, promtail) 재시작
docker compose up -d leviathan-loki leviathan-promtail

# 모니터링/운영 서비스 재시작
docker compose up -d leviathan-prometheus leviathan-grafana leviathan-redis-exporter
docker compose up -d leviathan-monitoring leviathan-auto-tuner

# 백업 서비스 재확인 (restart: "no" → 수동 실행만)
# docker compose run --rm leviathan-db-backup   # 배포 후 즉시 백업 원할 시
# docker compose run --rm leviathan-wal-backup  # WAL 백업 수동 트리거

# 전체 상태 확인
docker compose ps
```

### Step 2.5 — Verify startup

```bash
# Engine이 30초 내 READY 상태 도달 확인
timeout 60 bash -c '
  until docker compose logs --tail=5 leviathan-engine 2>/dev/null | grep -q "engine_ready"; do
    sleep 3
  done
'
echo "Engine READY"

# 14개 컨테이너 전체 정상 확인
docker compose ps --format "table {{.Name}}\t{{.Status}}"
# db-backup, wal-backup은 Exited(0) 정상

# Loki 수집 확인 (promtail → loki 파이프라인)
curl -s http://localhost:3100/ready && echo "Loki: ready" || echo "Loki: not ready"
logcli labels --addr=http://localhost:3100 2>/dev/null | grep container || true
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
# Emergency rollback — Docker 이미지 태그 기반
cd /path/to/arbitrage_OMC

# 직전 커밋으로 체크아웃
git log --oneline -10   # last known-good commit 식별
GOOD_COMMIT="abc1234"
git checkout $GOOD_COMMIT

# 이미지 재빌드 후 재배포
docker compose build leviathan-engine
docker compose up -d leviathan-engine

# 또는 이전 이미지 태그가 있다면 직접 지정
# docker compose stop leviathan-engine
# docker tag leviathan-engine:previous leviathan-engine:latest
# docker compose up -d leviathan-engine
```

### Step 4.3 — Verify rollback

```bash
# 엔진 시작 확인
timeout 60 bash -c '
  until docker compose logs --tail=5 leviathan-engine 2>/dev/null | grep -q "engine_ready"; do
    sleep 3
  done
'
echo "Engine READY after rollback"

# 유닛 테스트 실행
cd /path/to/arbitrage_OMC/engine && \
  python -m pytest tests/unit/ -x -q --tb=short 2>&1 | tail -10

# Loki에서 롤백 후 에러 확인
docker compose logs --tail=50 leviathan-engine | grep -iE "error|critical|traceback"
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
