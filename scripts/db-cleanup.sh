#!/bin/bash
# LEVIATHAN DB Auto-Cleanup Script
# Prevents TimescaleDB disk exhaustion by cleaning WAL + enforcing retention
# Run via cron: 0 */6 * * * /path/to/db-cleanup.sh >> /tmp/leviathan-db-cleanup.log 2>&1

set +e

CONTAINER="leviathan-timescaledb"
MAX_WAL_GB=2
LOG_PREFIX="[db-cleanup $(date '+%Y-%m-%d %H:%M')]"

echo "$LOG_PREFIX Starting DB cleanup..."

# Check if container is running
if ! docker ps --format '{{.Names}}' | grep -q "$CONTAINER"; then
  echo "$LOG_PREFIX Container $CONTAINER not running. Skipping."
  exit 0
fi

# 1. Check disk usage inside container
DISK_USAGE=$(docker exec "$CONTAINER" df -h /var/lib/postgresql/data 2>/dev/null | tail -1 | awk '{print $5}' | tr -d '%')
echo "$LOG_PREFIX Disk usage: ${DISK_USAGE}%"

# 2. If > 80%, run aggressive cleanup
if [ "${DISK_USAGE:-0}" -gt 80 ]; then
  echo "$LOG_PREFIX WARNING: Disk >80%. Running aggressive cleanup..."

  # Force checkpoint to flush WAL
  docker exec "$CONTAINER" psql -U leviathan -d leviathan -c "CHECKPOINT;" 2>/dev/null

  # Clean old chunks via retention policy
  docker exec "$CONTAINER" psql -U leviathan -d leviathan -c "
    SELECT drop_chunks('execution_log', older_than => INTERVAL '14 days');
    SELECT drop_chunks('ohlcv_1m', older_than => INTERVAL '7 days');
    SELECT drop_chunks('orderbook_snapshots', older_than => INTERVAL '3 days');
    SELECT drop_chunks('signals', older_than => INTERVAL '7 days');
    VACUUM;
  " 2>/dev/null

  echo "$LOG_PREFIX Aggressive cleanup complete."
elif [ "${DISK_USAGE:-0}" -gt 60 ]; then
  echo "$LOG_PREFIX Disk >60%. Running standard cleanup..."

  docker exec "$CONTAINER" psql -U leviathan -d leviathan -c "
    SELECT drop_chunks('orderbook_snapshots', older_than => INTERVAL '7 days');
    VACUUM;
  " 2>/dev/null
fi

# 3. Report current table sizes
echo "$LOG_PREFIX Table sizes:"
docker exec "$CONTAINER" psql -U leviathan -d leviathan -c "
  SELECT
    schemaname || '.' || tablename as table_name,
    pg_size_pretty(pg_total_relation_size(schemaname || '.' || tablename)) as size
  FROM pg_tables WHERE schemaname = 'public'
  ORDER BY pg_total_relation_size(schemaname || '.' || tablename) DESC LIMIT 5;
" 2>/dev/null

echo "$LOG_PREFIX Done."
