#!/usr/bin/env bash
# Generate the daily paper validation report from a running paper agent container.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
REPORT_OUTPUT_DIR="${REPORT_OUTPUT_DIR:-data/reports}"
REPORT_DAY="${1:-$(date -u -d 'yesterday' +%F)}"
REPORT_PREFIX="${REPORT_OUTPUT_DIR%/}/paper-validation-report-${REPORT_DAY}"

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

if ! docker compose ps --status running --services | grep -qx "agent_avax"; then
    echo "ERROR: agent_avax service is not running" >&2
    exit 1
fi

echo "[$(date -Iseconds)] Generating paper validation report for ${REPORT_DAY}"

docker compose exec -T \
    -e POSTGRES_HOST=timescaledb \
    -e POSTGRES_PORT=5432 \
    -e POSTGRES_DB="$POSTGRES_DB" \
    -e POSTGRES_USER="$POSTGRES_USER" \
    -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
    agent_avax \
    python scripts/paper_validation_report.py \
    --day "$REPORT_DAY" \
    --output-prefix "$REPORT_PREFIX"

echo "[$(date -Iseconds)] Report written:"
echo "  ${PROJECT_DIR}/${REPORT_PREFIX}.md"
echo "  ${PROJECT_DIR}/${REPORT_PREFIX}.json"
