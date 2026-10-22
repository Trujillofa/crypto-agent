#!/usr/bin/env bash
# TimescaleDB backup script for crypto-agent
# Usage: ./scripts/backup_db.sh [backup_dir]
# Cron example (daily at 3am): 0 3 * * * /path/to/crypto-agent/scripts/backup_db.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="${1:-$PROJECT_DIR/backups}"
RETENTION_DAYS=7
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="$BACKUP_DIR/marketdata_$TIMESTAMP.sql.gz"

# Load env vars from .env if present
if [[ -f "$PROJECT_DIR/.env" ]]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

DB_USER="${POSTGRES_USER:-trading}"
DB_NAME="${POSTGRES_DB:-marketdata}"
DOCKER_CMD="${DOCKER_CMD:-sudo docker}"
DB_CONTAINER="$($DOCKER_CMD compose -f "$PROJECT_DIR/docker-compose.yml" ps -q timescaledb 2>/dev/null)"

if [[ -z "$DB_CONTAINER" ]]; then
    echo "ERROR: TimescaleDB container not running" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"

echo "[$(date -Iseconds)] Starting backup of $DB_NAME..."

$DOCKER_CMD exec "$DB_CONTAINER" pg_dump \
    -U "$DB_USER" \
    -d "$DB_NAME" \
    --no-owner \
    --no-privileges \
    --verbose \
    2>/dev/null | gzip > "$BACKUP_FILE"

SIZE="$(du -h "$BACKUP_FILE" | cut -f1)"
echo "[$(date -Iseconds)] Backup complete: $BACKUP_FILE ($SIZE)"

# Prune old backups.
#
# A `preserved/` subdirectory holds dumps that were deliberately kept
# because they are the baseline for an incident (a pre-write dump, a
# pre-migration dump). The prune glob `marketdata_*.sql.gz` matches those
# files too -- `find` is recursive by default -- so a preserved dump was
# being deleted by the same retention rule that is supposed to apply only
# to the rolling daily window. Exclude preserved directories explicitly.
#
# Anything under a `preserved/` directory is exempt from retention.
DELETED=0
find "$BACKUP_DIR" -name "marketdata_*.sql.gz" -mtime +"$RETENTION_DAYS" \
    -not -path "*/preserved/*" \
    -print -delete | while read -r f; do
    DELETED=$((DELETED + 1))
done
echo "[$(date -Iseconds)] Pruned backups older than $RETENTION_DAYS days (preserved/ excluded)"

echo "[$(date -Iseconds)] Done."
