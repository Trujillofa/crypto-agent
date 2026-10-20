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
#
# Safety layers, in order, BEFORE the database is touched:
#   1. gzip integrity check on the archive.
#   2. Content check: the dump must actually contain the expected tables.
#      A valid gzip of the WRONG database would otherwise pass step 1, the
#      live database would be dropped, and the load would then fail.
#   3. Target-database allowlist. Overriding POSTGRES_DB to something else
#      requires ALLOW_TARGET_DB_OVERRIDE=1.
#   4. A safety dump of the CURRENT database is taken before the drop, so
#      there is always a rollback path.
#   5. The load runs in a single transaction, so a mid-load failure leaves an
#      empty database rather than a half-restored one.
#   6. Post-load row counts are verified and a zero-row table is a failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_DIR/backups}"

DB_USER="${POSTGRES_USER:-trading}"
DB_NAME="${POSTGRES_DB:-marketdata}"
DOCKER_CMD="${DOCKER_CMD:-sudo docker}"

# The only database this script is allowed to destroy by default.
ALLOWED_DB_NAMES="${ALLOWED_DB_NAMES:-marketdata}"

# Tables that must be present in the dump for it to be a marketdata backup.
REQUIRED_TABLES="${REQUIRED_TABLES:-ohlcv trades}"

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

# --- Layer 1: gzip integrity -------------------------------------------------
if ! gzip -t "$BACKUP_FILE" 2>/dev/null; then
    die "Backup file is corrupt (gzip integrity check failed): $BACKUP_FILE"
fi

# --- Layer 2: content check --------------------------------------------------
# A valid gzip of the WRONG database passes the integrity check. Confirm the
# dump really contains the tables we expect before destroying anything.
# Single decompression pass: collect every table the dump defines, then check
# the required ones against that set. Decompressing once per table would be
# needlessly slow on a multi-hundred-MB dump.
dump_tables="$(gunzip -c "$BACKUP_FILE" \
    | { grep -oE '^(COPY|CREATE TABLE) (public\.)?[A-Za-z0-9_]+' || true; } \
    | sed -E 's/^(COPY|CREATE TABLE) (public\.)?//' \
    | sort -u || true)"
missing_tables=""
for table in $REQUIRED_TABLES; do
    if ! grep -qx "$table" <<<"$dump_tables"; then
        missing_tables="${missing_tables} ${table}"
    fi
done
if [[ -n "$missing_tables" ]]; then
    die "Dump does not contain expected table(s):${missing_tables}.
This does not look like a marketdata backup. Refusing to touch the database."
fi

# --- Layer 3: target allowlist ----------------------------------------------
if [[ " $ALLOWED_DB_NAMES " != *" $DB_NAME "* && "${ALLOW_TARGET_DB_OVERRIDE:-0}" != "1" ]]; then
    die "Refusing to destroy database '$DB_NAME': it is not in the allowlist ($ALLOWED_DB_NAMES).
Set ALLOW_TARGET_DB_OVERRIDE=1 if this is genuinely intended."
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

# --- Layer 4: safety dump of the CURRENT database ---------------------------
# Taken before the drop, so there is always a rollback path.
SAFETY_DUMP="$BACKUP_DIR/pre_restore_$(date +%Y%m%d_%H%M%S)_${DB_NAME}.sql.gz"
log "Taking safety dump of current '$DB_NAME' -> $SAFETY_DUMP"
if $DOCKER_CMD exec "$DB_CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" 2>/dev/null \
    | gzip > "$SAFETY_DUMP" && [[ -s "$SAFETY_DUMP" ]]; then
    log "Safety dump written ($SAFETY_DUMP, $(du -h "$SAFETY_DUMP" | cut -f1))."
else
    rm -f "$SAFETY_DUMP"
    # A database that does not exist yet has nothing to save, so there is no
    # rollback data to protect. Anything else is a real failure.
    if $DOCKER_CMD exec "$DB_CONTAINER" psql -U "$DB_USER" -d postgres -t -c \
        "SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}';" | grep -q 1; then
        die "Could not take a safety dump of '$DB_NAME'. Aborting before the drop."
    fi
    log "Database '$DB_NAME' does not exist yet -- nothing to save, continuing."
fi

# Terminate lingering connections so the drop cannot be blocked.
# NOTE: `-i` is REQUIRED. Without it docker does not forward stdin, so the
# heredoc is silently discarded and the statement never runs (psql exits 0).
$DOCKER_CMD exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d postgres -v ON_ERROR_STOP=1 <<SQL
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = '${DB_NAME}' AND pid <> pg_backend_pid();
SQL

log "Recreating database '$DB_NAME'..."
# CREATE DATABASE cannot run inside a transaction block, so use separate
# -c invocations rather than one multi-statement script.
$DOCKER_CMD exec "$DB_CONTAINER" psql -U "$DB_USER" -d postgres -v ON_ERROR_STOP=1 \
    -c "DROP DATABASE IF EXISTS ${DB_NAME};"
$DOCKER_CMD exec "$DB_CONTAINER" psql -U "$DB_USER" -d postgres -v ON_ERROR_STOP=1 \
    -c "CREATE DATABASE ${DB_NAME};"

# Verify the database actually exists. A silently-discarded statement (e.g. a
# missing `docker exec -i`, so stdin is not forwarded) would otherwise look
# like a successful create and blow up later with a confusing error.
if ! $DOCKER_CMD exec "$DB_CONTAINER" psql -U "$DB_USER" -d postgres -t -c \
    "SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}';" | grep -q 1; then
    die "Database '${DB_NAME}' was not created — refusing to load the dump.
Rollback: restore from ${SAFETY_DUMP}"
fi

# --- Layer 5: single-transaction load ---------------------------------------
# A failure part-way through rolls the whole load back, so the database is
# left empty rather than half-restored.
log "Loading dump (single transaction)..."
if ! gunzip -c "$BACKUP_FILE" \
    | $DOCKER_CMD exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" \
        -v ON_ERROR_STOP=1 --single-transaction; then
    die "Load FAILED — the transaction was rolled back, so '$DB_NAME' is empty.
Rollback: restore from ${SAFETY_DUMP}"
fi

# --- Layer 6: verify row counts ---------------------------------------------
log "Verifying restored row counts..."
restore_ok=1
for table in $REQUIRED_TABLES; do
    count="$($DOCKER_CMD exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -t -A -c \
        "SELECT count(*) FROM ${table};" 2>/dev/null | tr -d '[:space:]')"
    if [[ -z "$count" ]]; then
        log "  ${table}: QUERY FAILED"
        restore_ok=0
    elif [[ "$count" == "0" ]]; then
        log "  ${table}: 0 rows  <-- UNEXPECTED"
        restore_ok=0
    else
        log "  ${table}: ${count} rows"
    fi
done
if [[ "$restore_ok" -ne 1 ]]; then
    die "Post-restore verification FAILED.
Rollback: restore from ${SAFETY_DUMP}"
fi

log "Restore complete and verified."
log "Reminder: restart the agents that read this database (e.g. docker compose restart agent)."
log "Safety dump of the previous state kept at: ${SAFETY_DUMP}"
