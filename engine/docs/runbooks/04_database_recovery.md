# Runbook 04 — Database Recovery

**Severity:** HIGH (data loss risk) / MEDIUM (engine continues in-memory fallback)
**SLA:** Detection within 5 minutes. Fallback active within 10 minutes. Full restore within 4 hours.
**Related code:** `engine/src/infra/db/market_recorder.py`, `engine/src/infra/db/migrations/`

---

## Overview

LEVIATHAN uses TimescaleDB (PostgreSQL extension) for time-series execution data, market tick
storage, and walk-forward analysis. The engine can operate in a degraded in-memory mode if the
database is unavailable, but walk-forward analysis and live gate evaluation require the DB.
This runbook covers backup/restore, WAL recovery, integrity checks, and connection pool diagnostics.

---

## 1. TimescaleDB Backup Procedures

### 1.1 Continuous backup setup (recommended)

Configure `pg_basebackup` + WAL archiving for point-in-time recovery (PITR):

```bash
# /etc/postgresql/postgresql.conf additions
archive_mode = on
archive_command = 'cp %p /mnt/wal_archive/%f'
wal_level = replica

# Restart PostgreSQL after config change
sudo systemctl restart postgresql
```

### 1.2 Scheduled logical backup (daily)

```bash
#!/bin/bash
# /usr/local/bin/leviathan_backup.sh
set -e

DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/mnt/backups/leviathan"
DB_NAME="leviathan"
DB_USER="leviathan_user"

# Dump all tables (logical backup)
pg_dump -U "$DB_USER" -Fc "$DB_NAME" > "$BACKUP_DIR/leviathan_${DATE}.dump"

# Compress and retain 30 days
find "$BACKUP_DIR" -name "*.dump" -mtime +30 -delete

echo "Backup complete: leviathan_${DATE}.dump"
```

Schedule via cron:
```bash
# crontab -e
0 2 * * * /usr/local/bin/leviathan_backup.sh >> /var/log/leviathan/backup.log 2>&1
```

### 1.3 Verify backup integrity

```bash
# Test restore to a temp DB
pg_restore -l /mnt/backups/leviathan/leviathan_YYYYMMDD_HHMMSS.dump | head -50

# Or restore to validation instance
createdb leviathan_validate
pg_restore -U postgres -d leviathan_validate /mnt/backups/leviathan/leviathan_YYYYMMDD_HHMMSS.dump
psql -U postgres -d leviathan_validate -c "SELECT COUNT(*) FROM execution_log;"
dropdb leviathan_validate
```

---

## 2. Restore Procedure

### Step 2.1 — Stop the engine

```bash
sudo systemctl stop leviathan-engine
# Confirm all processes stopped
pgrep -f "leviathan" && echo "STILL RUNNING" || echo "Stopped"
```

### Step 2.2 — Restore from latest backup

```bash
# Find latest backup
LATEST=$(ls -t /mnt/backups/leviathan/*.dump | head -1)
echo "Restoring from: $LATEST"

# Drop and recreate DB
psql -U postgres -c "DROP DATABASE IF EXISTS leviathan;"
psql -U postgres -c "CREATE DATABASE leviathan OWNER leviathan_user;"

# Restore
pg_restore -U postgres -d leviathan "$LATEST"
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

Use WAL recovery for point-in-time restore (fewer data loss than logical backup):

### Step 3.1 — Prepare recovery configuration

```bash
# Create recovery.conf (PostgreSQL < 12) or postgresql.conf additions (>= 12)
cat >> /etc/postgresql/postgresql.conf << 'EOF'
restore_command = 'cp /mnt/wal_archive/%f %p'
recovery_target_time = '2026-03-07 14:00:00'  # target time in UTC
recovery_target_action = 'promote'
EOF

# Create recovery signal file
touch /var/lib/postgresql/data/recovery.signal
```

### Step 3.2 — Restore base backup then apply WAL

```bash
# Stop PostgreSQL
sudo systemctl stop postgresql

# Restore base backup to data directory
rsync -av /mnt/backups/base_backup/ /var/lib/postgresql/data/

# Start PostgreSQL (will apply WAL automatically)
sudo systemctl start postgresql

# Monitor recovery progress
tail -f /var/log/postgresql/postgresql.log | grep -E "recovery|redo|WAL"
```

### Step 3.3 — Confirm recovery target reached

```bash
# Check PostgreSQL is no longer in recovery mode
psql -U postgres -c "SELECT pg_is_in_recovery();"
# Should return: f (false)

psql -U postgres -d leviathan -c "SELECT MAX(ts) FROM execution_log;"
# Should match expected recovery target time
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
# Test connection manually
psql "postgresql://leviathan_user:PASSWORD@localhost:5432/leviathan" \
  -c "SELECT version();"

# Check pg_hba.conf allows engine host
grep leviathan /etc/postgresql/pg_hba.conf
```

---

## References

- Market recorder: `engine/src/infra/db/market_recorder.py`
- DB migrations: `engine/src/infra/db/migrations/`
- Walk-forward DB queries: `engine/src/analysis/walk_forward.py`
- QUANT_MANIFESTO.md Section 4.2 (Walk-forward data requirements)
- TimescaleDB retention policy: 90 days for execution_log (QUANT_MANIFESTO.md Section 1, defect #3)
