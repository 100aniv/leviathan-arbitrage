# Runbook 04 — Database Recovery

**Severity:** HIGH (data loss risk) / MEDIUM (engine continues in-memory fallback)
**SLA:** Detection within 5 minutes. Fallback active within 10 minutes. Full restore within 4 hours.
**Related code:** `engine/src/infra/db/market_recorder.py`, `engine/src/infra/db/migrations/`
**Docker services:** `leviathan-timescaledb` (port 5432), `leviathan-db-backup` (daily pg_dump), `leviathan-wal-backup` (WAL archiving)
**Volumes:** `timescaledb_data`, `wal_archive`, `db_backups`
**Connection string (Docker internal):** `postgresql+asyncpg://leviathan:leviathan@timescaledb:5432/leviathan`
**Connection string (host):** `postgresql://leviathan:leviathan@localhost:5432/leviathan`

---

## Overview

LEVIATHAN uses TimescaleDB (PostgreSQL extension) for time-series execution data, market tick
storage, and walk-forward analysis. The engine can operate in a degraded in-memory mode if the
database is unavailable, but walk-forward analysis and live gate evaluation require the DB.
This runbook covers backup/restore, WAL recovery, integrity checks, and connection pool diagnostics.

---

## 1. TimescaleDB Backup Procedures

### 1.1 Docker-native backup containers

`docker-compose.yml`에 두 개의 백업 컨테이너가 정의되어 있다:

| 컨테이너 | 방식 | 주기 | 보존 |
|----------|------|------|------|
| `leviathan-db-backup` | `pg_dump` (논리 백업) | 수동/스케줄 실행 | 7일 |
| `leviathan-wal-backup` | WAL 아카이브 연속 백업 | 연속 (WAL 세그먼트 단위) | 7일 |

백업 볼륨 경로:
- 논리 백업: `db_backups` 볼륨 → `/backups/*.dump`
- WAL 아카이브: `wal_archive` 볼륨 → `/var/lib/postgresql/wal_archive/`

### 1.2 논리 백업 수동 실행 (db-backup 컨테이너)

```bash
# db-backup 컨테이너 수동 실행 (restart: "no" 이므로 필요 시 직접 실행)
docker compose run --rm leviathan-db-backup

# 백업 파일 확인
docker run --rm \
  -v "$(docker volume inspect leviathan_db_backups -f '{{ .Mountpoint }}'):/backups" \
  alpine ls -lht /backups/*.dump 2>/dev/null || \
docker compose exec timescaledb ls -lht /backups/ 2>/dev/null

# 호스트에서 직접 확인
docker volume inspect leviathan_db_backups
# Mountpoint 경로에서 .dump 파일 확인
```

### 1.3 WAL 백업 수동 실행 (wal-backup 컨테이너)

```bash
# WAL 백업 수동 트리거
docker compose run --rm leviathan-wal-backup

# WAL 아카이브 상태 확인
docker compose exec timescaledb psql -U leviathan -d leviathan -c "
SELECT
  pg_walfile_name(pg_current_wal_lsn()) AS current_wal,
  pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), '0/0')) AS total_wal_size;
"

# 아카이브 디렉토리 내용 확인
docker compose exec timescaledb ls -lht /var/lib/postgresql/wal_archive/ | head -20
```

### 1.4 Verify backup integrity

```bash
# 최신 dump 파일 목록 확인
DUMP_PATH=$(docker volume inspect leviathan_db_backups --format '{{.Mountpoint}}')
ls -lht "${DUMP_PATH}"/*.dump 2>/dev/null | head -5

# pg_restore로 목차 확인 (실제 복원 없이 검증)
LATEST_DUMP=$(ls -t "${DUMP_PATH}"/*.dump 2>/dev/null | head -1)
echo "Latest dump: $LATEST_DUMP"
docker run --rm \
  -v "${DUMP_PATH}:/backups:ro" \
  timescale/timescaledb:latest-pg16 \
  pg_restore -l "/backups/$(basename $LATEST_DUMP)" | head -30

# Loki에서 백업 성공/실패 로그 확인
logcli query '{container="leviathan-db-backup"} |= "Backup"' \
  --addr=http://localhost:3100 --since=24h 2>/dev/null || \
docker compose logs --tail=50 leviathan-db-backup 2>/dev/null
```

---

## 2. Restore Procedure

### Step 2.1 — Stop the engine

```bash
# Docker로 실행 중인 경우
docker compose stop leviathan-engine

# 확인
docker compose ps leviathan-engine | grep -v "Up" && echo "Engine stopped"
```

### Step 2.2 — Restore from latest backup

```bash
# 백업 볼륨 마운트포인트 확인
DUMP_PATH=$(docker volume inspect leviathan_db_backups --format '{{.Mountpoint}}')
LATEST=$(ls -t "${DUMP_PATH}"/*.dump 2>/dev/null | head -1)
echo "Restoring from: $LATEST"

# TimescaleDB 컨테이너에서 DB 재생성 및 복원
docker compose exec timescaledb bash -c "
  psql -U leviathan -d postgres -c 'DROP DATABASE IF EXISTS leviathan;'
  psql -U leviathan -d postgres -c 'CREATE DATABASE leviathan OWNER leviathan;'
  pg_restore -U leviathan -d leviathan /backups/\$(ls -t /backups/*.dump | head -1 | xargs basename)
  echo 'Restore complete'
"

# 또는 호스트에서 직접 실행
docker run --rm \
  -v "${DUMP_PATH}:/backups:ro" \
  --network leviathan_leviathan \
  timescale/timescaledb:latest-pg16 \
  pg_restore -h timescaledb -U leviathan -d leviathan "/backups/$(basename $LATEST)"
echo "Restore complete"
```

### Step 2.3 — Re-apply TimescaleDB extension

```sql
-- Run as superuser after restore
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Re-create hypertables if not preserved in dump
SELECT create_hypertable('market_ticks', 'ts', if_not_exists => TRUE);
SELECT create_hypertable('execution_log', 'ts', if_not_exists => TRUE);

-- Re-apply retention policies (90 days for execution_log)
SELECT add_retention_policy('execution_log', INTERVAL '90 days', if_not_exists => TRUE);
SELECT add_retention_policy('market_ticks', INTERVAL '30 days', if_not_exists => TRUE);
```

---

## 3. WAL Recovery Steps

`leviathan-wal-backup` 컨테이너와 `wal_archive` 볼륨을 사용하는 PITR(Point-in-Time Recovery) 절차.

### Step 3.1 — WAL 아카이브 상태 확인

```bash
# WAL 아카이브 볼륨 마운트포인트
WAL_PATH=$(docker volume inspect leviathan_wal_archive --format '{{.Mountpoint}}')
echo "WAL archive path: $WAL_PATH"

# 가장 최근 WAL 세그먼트 확인
ls -lht "${WAL_PATH}"/ | head -10

# TimescaleDB 컨테이너 내부에서 확인
docker compose exec timescaledb ls -lht /var/lib/postgresql/wal_archive/ | head -10
```

### Step 3.2 — Prepare recovery configuration

```bash
# TimescaleDB 컨테이너를 중단하고 복구 설정 주입
docker compose stop leviathan-timescaledb

# postgresql.conf에 복구 옵션 추가 (infra/postgres/postgresql.conf)
# 프로젝트 루트의 infra/postgres/postgresql.conf 파일 편집:
cat >> /path/to/infra/postgres/postgresql.conf << 'EOF'
restore_command = 'cp /var/lib/postgresql/wal_archive/%f %p'
recovery_target_time = '2026-03-07 14:00:00'  # 복구 목표 시각 (UTC)
recovery_target_action = 'promote'
EOF

# recovery.signal 파일 생성 (timescaledb_data 볼륨 내)
DATA_PATH=$(docker volume inspect leviathan_timescaledb_data --format '{{.Mountpoint}}')
touch "${DATA_PATH}/recovery.signal"
echo "recovery.signal created at: ${DATA_PATH}/recovery.signal"
```

### Step 3.3 — WAL 복구 실행

```bash
# TimescaleDB 재시작 (WAL 자동 적용)
docker compose start leviathan-timescaledb

# 복구 진행 모니터링
docker compose logs -f leviathan-timescaledb | grep -E "recovery|redo|WAL|LOG"

# Loki에서 복구 로그 확인 (logcli 설치 시)
logcli query '{container="leviathan-timescaledb"}' \
  --addr=http://localhost:3100 --since=10m | grep -E "recovery|WAL"
```

### Step 3.4 — Confirm recovery target reached

```bash
# 복구 완료 확인 (pg_is_in_recovery() = f 이어야 함)
docker compose exec timescaledb psql -U leviathan -d postgres -c \
  "SELECT pg_is_in_recovery();"
# 결과: f (false) — 복구 완료

# 복구 후 최신 데이터 시각 확인
docker compose exec timescaledb psql -U leviathan -d leviathan -c \
  "SELECT MAX(ts) FROM execution_log;"
# 복구 목표 시각과 일치해야 함

# recovery.signal 파일 자동 삭제 확인
DATA_PATH=$(docker volume inspect leviathan_timescaledb_data --format '{{.Mountpoint}}')
ls "${DATA_PATH}/recovery.signal" 2>/dev/null && echo "WARNING: still in recovery" || echo "Recovery complete"
```

---

## 4. Data Integrity Verification

After any restore or recovery, verify data integrity before restarting the engine.

### Step 4.1 — Row count sanity check

```sql
-- Compare approximate expected counts
SELECT
    'execution_log' AS table_name,
    COUNT(*) AS row_count,
    MIN(ts) AS earliest,
    MAX(ts) AS latest
FROM execution_log
UNION ALL
SELECT
    'market_ticks',
    COUNT(*),
    MIN(ts),
    MAX(ts)
FROM market_ticks;
```

### Step 4.2 — PnL consistency check

```sql
-- Verify no negative-impossible entries
SELECT COUNT(*) FROM execution_log
WHERE net_pnl IS NULL
   OR gross_spread_bps < 0
   OR fee_total < 0;
-- Expected: 0

-- Check for duplicate entries
SELECT ts, strategy_id, COUNT(*) AS cnt
FROM execution_log
GROUP BY ts, strategy_id
HAVING COUNT(*) > 1
LIMIT 10;
-- Expected: 0 rows
```

### Step 4.3 — Hypertable chunk integrity

```sql
-- Verify TimescaleDB chunks are intact
SELECT
    hypertable_name,
    chunk_name,
    range_start,
    range_end,
    is_compressed
FROM timescaledb_information.chunks
ORDER BY range_start DESC
LIMIT 10;
```

### Step 4.4 — Walk-forward data sufficiency

```python
from engine.src.analysis.walk_forward import WalkForwardAnalyzer

analyzer = WalkForwardAnalyzer(db_pool=db_pool)
result = await analyzer.run(strategy_id="main", period_days=7)

if result.block_reason:
    print(f"WFA blocked: {result.block_reason}")
else:
    print(f"WFA OK: {result.overall_sharpe:.2f} Sharpe, {len(result.windows)} windows")
```

---

## 5. Fallback to In-Memory Mode

If the database is unavailable and trading must continue, the engine can operate without
walk-forward analysis (shadow/paper mode only — live trading requires DB).

### Step 5.1 — Enable in-memory fallback

```python
# In engine config (environment variable)
# DB_REQUIRED=false allows engine to start without DB
os.environ["DB_REQUIRED"] = "false"
os.environ["DATA_MODE"] = "SYNTHETIC"  # or REAL_PUBLIC
```

### Step 5.2 — Limitations in fallback mode

```
AVAILABLE in fallback:
  - Signal generation
  - Paper trading execution
  - In-memory PnL tracking (reset on restart)
  - Telegram alerts

NOT AVAILABLE in fallback:
  - Walk-forward analysis (requires historical execution_log)
  - Live gate evaluation (depends on WFA)
  - Market tick recording
  - Execution archiving
```

### Step 5.3 — Switch to in-memory market recorder

```python
# MarketRecorder gracefully degrades when DB pool is None
# No code change needed; recorder skips writes and logs warnings
recorder = MarketRecorder(db_pool=None)  # in-memory no-op mode
```

---

## 6. Connection Pool Diagnostics

### Step 6.1 — Check active connections

```sql
-- View all connections to leviathan DB
SELECT
    pid,
    usename,
    application_name,
    state,
    wait_event_type,
    wait_event,
    query_start,
    LEFT(query, 80) AS query_snippet
FROM pg_stat_activity
WHERE datname = 'leviathan'
ORDER BY query_start DESC;
```

### Step 6.2 — Pool exhaustion detection

```python
# asyncpg pool diagnostics
pool = db_pool  # DatabasePool instance

print(f"Pool size: {pool._pool.get_size()}")
print(f"Pool free: {pool._pool.get_free_size()}")
# If free = 0: pool exhausted; queries are queuing

# Check query latency via Prometheus
# leviathan_db_query_latency_p99 should be < 100ms
```

### Step 6.3 — Resolve pool exhaustion

```python
# Option 1: Increase pool size in config
config.db_pool_max_size = 20  # default is typically 10

# Option 2: Identify and kill blocking queries
# In psql:
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = 'leviathan'
  AND state = 'idle in transaction'
  AND query_start < NOW() - INTERVAL '5 minutes';
```

### Step 6.4 — Connection string verification

```bash
# Docker 컨테이너 내부에서 직접 연결 테스트
docker compose exec timescaledb psql -U leviathan -d leviathan -c "SELECT version();"

# 호스트에서 포트 포워딩으로 테스트 (포트 5432 노출 확인)
psql "postgresql://leviathan:leviathan@localhost:5432/leviathan" -c "SELECT version();"

# 엔진 컨테이너의 DATABASE_URL 환경변수 확인
docker compose exec leviathan-engine env | grep DATABASE_URL
# 예상: DATABASE_URL=postgresql+asyncpg://leviathan:leviathan@timescaledb:5432/leviathan
# 주의: Docker 내부에서는 hostname이 'timescaledb' (localhost가 아님)

# pg_hba.conf 확인 (컨테이너 내부)
docker compose exec timescaledb cat /var/lib/postgresql/data/pg_hba.conf | grep leviathan
```

---

## References

- Market recorder: `engine/src/infra/db/market_recorder.py`
- DB migrations: `engine/src/infra/db/migrations/`
- Walk-forward DB queries: `engine/src/analysis/walk_forward.py`
- QUANT_MANIFESTO.md Section 4.2 (Walk-forward data requirements)
- TimescaleDB retention policy: 90 days for execution_log (QUANT_MANIFESTO.md Section 1, defect #3)
