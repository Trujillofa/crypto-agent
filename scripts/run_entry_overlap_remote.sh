#!/bin/bash
# Run SOL 1h entry-overlap analysis on Hetzner (production TimescaleDB).
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-crypto-agent}"
REMOTE_DIR="${REMOTE_DIR:-/opt/crypto-agent}"
IMAGE="${IMAGE:-crypto-agent-agent_sentiment_macro:latest}"
OUTPUT_JSON="${OUTPUT_JSON:-research/entry-overlap-sol-1h.json}"

ssh "${REMOTE_HOST}" "cd ${REMOTE_DIR} && mkdir -p research/overlap-resolved && sudo chown -R 999:999 research && docker run --rm \
  --network crypto-agent_crypto-net \
  -v ${REMOTE_DIR}:/app -w /app \
  --env-file ${REMOTE_DIR}/.env \
  -e POSTGRES_HOST=timescaledb \
  -e POSTGRES_PORT=5432 \
  ${IMAGE} \
  python scripts/analyze_entry_overlap.py \
    --manifest config/autoresearch/overlap_manifest_sol_1h.yaml \
    --output /app/${OUTPUT_JSON} \
    --include-live-db \
    --agent-ids sol-1h-trend-pullback-overlay-live sentiment-macro-bot"
