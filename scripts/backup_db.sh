#!/usr/bin/env bash
# TimescaleDB daily backup script — pg_dump with 7-day retention.
# Usage: ./scripts/backup_db.sh
# Cron:  0 3 * * * /path/to/scripts/backup_db.sh

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/backups/timescaledb}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
DB_HOST="${DB_HOST:-timescaledb}"
DB_PORT="${DB_PORT:-5432}"
DB_NAME="${DB_NAME:-leviathan}"
DB_USER="${DB_USER:-leviathan}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/leviathan_${TIMESTAMP}.sql.gz"

mkdir -p "${BACKUP_DIR}"

echo "[$(date -u +%FT%TZ)] Starting backup: ${BACKUP_FILE}"

pg_dump \
  -h "${DB_HOST}" \
  -p "${DB_PORT}" \
  -U "${DB_USER}" \
  -d "${DB_NAME}" \
  --no-owner \
  --no-privileges \
  --format=plain \
  | gzip > "${BACKUP_FILE}"

BACKUP_SIZE=$(du -h "${BACKUP_FILE}" | cut -f1)
echo "[$(date -u +%FT%TZ)] Backup complete: ${BACKUP_FILE} (${BACKUP_SIZE})"

# Cleanup old backups
DELETED=$(find "${BACKUP_DIR}" -name "leviathan_*.sql.gz" -mtime +"${RETENTION_DAYS}" -delete -print | wc -l)
echo "[$(date -u +%FT%TZ)] Cleaned up ${DELETED} old backup(s) (>${RETENTION_DAYS} days)"
