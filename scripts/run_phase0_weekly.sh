#!/bin/bash
# Phase 0 weekly forward-validation checklist (production).
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-crypto-agent}"
DATE_TAG="$(date -u +%Y%m%d)"
OUTPUT_DIR="docs/reports/phase0"

mkdir -p "${OUTPUT_DIR}"

echo "== Phase 0: entry overlap (WFO + live DB) =="
./scripts/run_entry_overlap_remote.sh

echo ""
echo "== Phase 0: copy remote overlap artifact =="
scp "${REMOTE_HOST}:/opt/crypto-agent/research/entry-overlap-sol-1h.json" \
  "${OUTPUT_DIR}/entry-overlap-${DATE_TAG}.json" 2>/dev/null || true

echo ""
echo "== Phase 0: PnL by agent (requires profit_report fix on server image or mount) =="
ssh "${REMOTE_HOST}" "cd /opt/crypto-agent && docker run --rm \
  --network crypto-agent_crypto-net -v /opt/crypto-agent:/app -w /app \
  --env-file /opt/crypto-agent/.env -e POSTGRES_HOST=timescaledb \
  crypto-agent-agent_sentiment_macro:latest \
  python scripts/profit_report.py --format json --quiet" \
  > "${OUTPUT_DIR}/pnl-${DATE_TAG}.json" 2>/dev/null || echo "PnL skipped (run after deploy includes profit_report fix)"

echo ""
echo "Wrote ${OUTPUT_DIR}/entry-overlap-${DATE_TAG}.json (if scp succeeded)"
echo "Review: live entry counts, pairwise_live overlap, closed trades per agent_id"
