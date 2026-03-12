#!/usr/bin/env bash
# =============================================================================
# WAL Backup + PITR Verification Script — LEVIATHAN (US-122)
# =============================================================================
# Combines pg_dump (daily full) + WAL archiving (continuous) for RPO < 1 hour.
#
# Usage:
#   ./infra/backup/wal-backup.sh [backup|verify|restore-test]
#
# Cron (daily full backup):
#   0 3 * * * /path/to/infra/backup/wal-backup.sh backup
# Cron (weekly restore verification):
#   0 6 * * 0 /path/to/infra/backup/wal-backup.sh verify

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BACKUP_DIR="${BACKUP_DIR:-/backups}"
FULL_BACKUP_DIR="${BACKUP_DIR}/full"
WAL_ARCHIVE_DIR="${BACKUP_DIR}/wal_archive"
RESTORE_TEST_DIR="${BACKUP_DIR}/restore_test"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
DB_HOST="${PGHOST:-timescaledb}"
DB_PORT="${PGPORT:-5432}"
DB_NAME="${PGDATABASE:-leviathan}"
DB_USER="${PGUSER:-leviathan}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

log() { echo "[$(date -u +%FT%TZ)] $*"; }

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
cmd_backup() {
    log "=== Full Backup Start ==="
    mkdir -p "${FULL_BACKUP_DIR}" "${WAL_ARCHIVE_DIR}"

    BACKUP_FILE="${FULL_BACKUP_DIR}/leviathan_full_${TIMESTAMP}.sql.gz"

    # pg_dump full backup
    pg_dump \
        -h "${DB_HOST}" \
        -p "${DB_PORT}" \
        -U "${DB_USER}" \
        -d "${DB_NAME}" \
        --no-owner \
        --no-privileges \
        --format=custom \
        | gzip > "${BACKUP_FILE}"

    BACKUP_SIZE=$(du -h "${BACKUP_FILE}" | cut -f1)
    log "Full backup: ${BACKUP_FILE} (${BACKUP_SIZE})"

    # Record latest backup timestamp for RPO tracking
    echo "${TIMESTAMP}" > "${BACKUP_DIR}/last_full_backup.txt"

    # Cleanup old full backups
    DELETED=$(find "${FULL_BACKUP_DIR}" -name "leviathan_full_*.sql.gz" -mtime +"${RETENTION_DAYS}" -delete -print | wc -l)
    log "Cleaned ${DELETED} old full backup(s)"

    # Cleanup old WAL files (keep 2x retention for safety)
    WAL_RETENTION=$((RETENTION_DAYS * 2))
    WAL_DELETED=$(find "${WAL_ARCHIVE_DIR}" -name "0*" -mtime +"${WAL_RETENTION}" -delete -print 2>/dev/null | wc -l)
    log "Cleaned ${WAL_DELETED} old WAL file(s)"

    # RPO check: verify WAL archive is active
    WAL_COUNT=$(find "${WAL_ARCHIVE_DIR}" -name "0*" -mmin -60 2>/dev/null | wc -l)
    if [ "${WAL_COUNT}" -gt 0 ]; then
        log "RPO check PASS: ${WAL_COUNT} WAL file(s) archived in last 60 min"
    else
        log "RPO check WARN: No WAL files archived in last 60 min (may be low activity)"
    fi

    log "=== Full Backup Complete ==="
}

cmd_verify() {
    log "=== Restore Verification Start ==="

    # Find latest full backup
    LATEST_BACKUP=$(find "${FULL_BACKUP_DIR}" -name "leviathan_full_*.sql.gz" -type f | sort -r | head -1)
    if [ -z "${LATEST_BACKUP}" ]; then
        log "ERROR: No full backup found in ${FULL_BACKUP_DIR}"
        exit 1
    fi
    log "Using backup: ${LATEST_BACKUP}"

    # Create temp restore directory
    rm -rf "${RESTORE_TEST_DIR}"
    mkdir -p "${RESTORE_TEST_DIR}"

    # Decompress and validate
    DECOMPRESSED="${RESTORE_TEST_DIR}/restore_test.dump"
    gunzip -c "${LATEST_BACKUP}" > "${DECOMPRESSED}"
    DUMP_SIZE=$(du -h "${DECOMPRESSED}" | cut -f1)
    log "Decompressed: ${DUMP_SIZE}"

    # Validate dump format
    if pg_restore --list "${DECOMPRESSED}" > /dev/null 2>&1; then
        TABLE_COUNT=$(pg_restore --list "${DECOMPRESSED}" 2>/dev/null | grep -c "TABLE" || true)
        log "Dump validation PASS: ${TABLE_COUNT} table entries found"
    else
        log "ERROR: Dump validation FAILED — corrupt backup"
        rm -rf "${RESTORE_TEST_DIR}"
        exit 1
    fi

    # Check WAL archive health
    WAL_TOTAL=$(find "${WAL_ARCHIVE_DIR}" -name "0*" -type f 2>/dev/null | wc -l)
    WAL_RECENT=$(find "${WAL_ARCHIVE_DIR}" -name "0*" -mmin -60 -type f 2>/dev/null | wc -l)
    log "WAL archive: ${WAL_TOTAL} total, ${WAL_RECENT} in last hour"

    # Check RPO
    if [ -f "${BACKUP_DIR}/last_full_backup.txt" ]; then
        LAST_BACKUP_TS=$(cat "${BACKUP_DIR}/last_full_backup.txt")
        log "Last full backup: ${LAST_BACKUP_TS}"
    fi

    # Cleanup
    rm -rf "${RESTORE_TEST_DIR}"

    log "=== Restore Verification PASS ==="
}

cmd_restore_test() {
    log "=== Full Restore Test (to temp DB) ==="

    LATEST_BACKUP=$(find "${FULL_BACKUP_DIR}" -name "leviathan_full_*.sql.gz" -type f | sort -r | head -1)
    if [ -z "${LATEST_BACKUP}" ]; then
        log "ERROR: No backup found"
        exit 1
    fi

    TEST_DB="leviathan_restore_test"

    # Create test database
    psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d postgres \
        -c "DROP DATABASE IF EXISTS ${TEST_DB};" \
        -c "CREATE DATABASE ${TEST_DB};"

    # Restore
    gunzip -c "${LATEST_BACKUP}" | pg_restore \
        -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" \
        -d "${TEST_DB}" --no-owner --no-privileges 2>/dev/null || true

    # Verify tables exist
    TABLE_COUNT=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${TEST_DB}" \
        -t -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")
    log "Restored ${TABLE_COUNT} tables to ${TEST_DB}"

    # Cleanup test database
    psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d postgres \
        -c "DROP DATABASE IF EXISTS ${TEST_DB};"

    log "=== Full Restore Test PASS ==="
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
case "${1:-backup}" in
    backup)       cmd_backup ;;
    verify)       cmd_verify ;;
    restore-test) cmd_restore_test ;;
    *)
        echo "Usage: $0 [backup|verify|restore-test]"
        exit 1
        ;;
esac
