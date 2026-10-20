#!/usr/bin/env bash
# Restore the crypto-agent TimescaleDB (marketdata) from a pg_dump backup.
#
# Usage:
#   ./scripts/restore_db.sh <backup_file.sql.gz>
#   ./scripts/restore_db.sh --list
#
# The restore is DESTRUCTIVE: it drops and recreates the target database
# before loading the dump. The script refuses to run without an explicit
# confirmation, and it refuses to run if the container is not reachable.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_DIR/backups}"

DB_USER="${POSTGRES_USER:-trading}"
DB_NAME="${POSTGRES_DB:-marketdata}"
DOCKER_CMD="${DOCKER_CMD:-sudo docker}"

log() { echo "[$(date -Iseconds)] $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

list_backups() {
    echo "Available backups in $BACKUP_DIR:"
    find "$BACKUP_DIR" -maxdepth 1 -name 'marketdata_*.sql.gz' -printf '  %f  (%s bytes)\n' 2>/dev/null \
        | sort -k1,1 \
        || echo "  (none found)"
}

if [[ "${1:-}" == "--list" || $# -eq 0 ]]; then
    list_backups
    exit 0
fi

BACKUP_FILE="$1"

if [[ ! -f "$BACKUP_FILE" ]]; then
    die "Backup file not found: $BACKUP_FILE"
    list_backups
    exit 1
fi

# Verify the gzip stream is readable before touching the database.
if ! gzip -t "$BACKUP_FILE" 2>/dev/null; then
    die "Backup file is corrupt (gzip integrity check failed): $BACKUP_FILE"
fi

# Resolve the container the same way backup_db.sh does.
DB_CONTAINER="$($DOCKER_CMD compose -f "$PROJECT_DIR/docker-compose.prod.yml" ps -q timescaledb 2>/dev/null || true)"
if [[ -z "$DB_CONTAINER" ]]; then
    DB_CONTAINER="$($DOCKER_CMD compose -f "$PROJECT_DIR/docker-compose.yml" ps -q timescaledb 2>/dev/null || true)"
fi
if [[ -z "$DB_CONTAINER" ]]; then
    die "TimescaleDB container not running (checked docker-compose.prod.yml and docker-compose.yml)"
fi

log "Backup file : $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"
log "Database    : $DB_NAME (user: $DB_USER)"
log "Container   : $DB_CONTAINER"

read -r -p "This DROPS and recreates the '$DB_NAME' database. Continue? (type 'yes' to confirm): " CONFIRM
if [[ "$CONFIRM" != "yes" ]]; then
    log "Aborted."
    exit 0
fi

# Terminate lingering connections so the drop cannot be blocked.
$DOCKER_CMD exec "$DB_CONTAINER" psql -U "$DB_USER" -d postgres -v ON_ERROR_STOP=1 <<SQL
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = '${DB_NAME}' AND pid <> pg_backend_pid();
SQL

log "Recreating database '$DB_NAME'..."
$DOCKER_CMD exec "$DB_CONTAINER" psql -U "$DB_USER" -d postgres -v ON_ERROR_STOP=1 <<SQL
DROP DATABASE IF EXISTS ${DB_NAME};
CREATE DATABASE ${DB_NAME};
SQL

log "Loading dump..."
gunzip -c "$BACKUP_FILE" \
    | $DOCKER_CMD exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1

log "Verifying restored row counts..."
$DOCKER_CMD exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -t -c \
    "SELECT 'ohlcv rows: ' || count(*) FROM ohlcv;" 2>/dev/null || \
    log "Could not query 'ohlcv' (table may not exist in this schema)."
$DOCKER_CMD exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -t -c \
    "SELECT 'trades rows: ' || count(*) FROM trades;" 2>/dev/null || \
    log "Could not query 'trades' (table may not exist in this schema)."

log "Restore complete."
log "Reminder: restart the agents that read this database (e.g. docker compose restart agent)."
