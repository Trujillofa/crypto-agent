#!/usr/bin/env bash
# Generate the daily paper validation report from a running paper agent container.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
REPORT_OUTPUT_DIR="${REPORT_OUTPUT_DIR:-data/reports}"
REPORT_DAY="${1:-$(date -u -d 'yesterday' +%F)}"
REPORT_PREFIX="${REPORT_OUTPUT_DIR%/}/paper-validation-report-${REPORT_DAY}"
CONTAINER_REPORT_PREFIX="/tmp/paper-validation-report-${REPORT_DAY}"
REPORT_SERVICE="agent_sol_sparse"
PRODUCTION_COMPOSE=(docker compose -f docker-compose.prod.yml)

cd "$PROJECT_DIR"

if [[ -f ".env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source ".env"
    set +a
fi

required_vars=(POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD)
for name in "${required_vars[@]}"; do
    if [[ -z "${!name:-}" ]]; then
        echo "ERROR: required environment variable ${name} is not set" >&2
        exit 1
    fi
done

mkdir -p "$REPORT_OUTPUT_DIR"

if ! "${PRODUCTION_COMPOSE[@]}" ps --status running --services | grep -qx "$REPORT_SERVICE"; then
    echo "ERROR: ${REPORT_SERVICE} service is not running" >&2
    exit 1
fi

echo "[$(date -Iseconds)] Generating paper validation report for ${REPORT_DAY}"

"${PRODUCTION_COMPOSE[@]}" exec -T \
    -e POSTGRES_HOST=timescaledb \
    -e POSTGRES_PORT=5432 \
    -e POSTGRES_DB="$POSTGRES_DB" \
    -e POSTGRES_USER="$POSTGRES_USER" \
    -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
    "$REPORT_SERVICE" \
    python scripts/paper_validation_report.py \
    --day "$REPORT_DAY" \
    --output-prefix "$CONTAINER_REPORT_PREFIX"

"${PRODUCTION_COMPOSE[@]}" cp \
    "${REPORT_SERVICE}:${CONTAINER_REPORT_PREFIX}.md" \
    "${REPORT_PREFIX}.md"
"${PRODUCTION_COMPOSE[@]}" cp \
    "${REPORT_SERVICE}:${CONTAINER_REPORT_PREFIX}.json" \
    "${REPORT_PREFIX}.json"
"${PRODUCTION_COMPOSE[@]}" exec -T "$REPORT_SERVICE" \
    rm -f "${CONTAINER_REPORT_PREFIX}.md" "${CONTAINER_REPORT_PREFIX}.json"

echo "[$(date -Iseconds)] Report written:"
echo "  ${PROJECT_DIR}/${REPORT_PREFIX}.md"
echo "  ${PROJECT_DIR}/${REPORT_PREFIX}.json"
