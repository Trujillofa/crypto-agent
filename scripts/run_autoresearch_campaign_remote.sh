#!/bin/bash
# Run a bounded autoresearch_loop on Hetzner via one-off Docker on the DB network.
set -euo pipefail

SYMBOL="${1:?symbol required, e.g. ETHUSDT}"
TIMEFRAME="${2:?timeframe required, e.g. 1h}"
OUTPUT_SUFFIX="${3:-pass1}"
FAMILIES="${FAMILIES:-trend_pullback_overlay,combined_focus}"
MAX_RUNS="${MAX_RUNS:-50}"
BOOTSTRAP="${BOOTSTRAP:-100}"
GATE_PROFILE="${GATE_PROFILE:-standard}"
TRAIN_MONTHS="${TRAIN_MONTHS:-3}"
TEST_MONTHS="${TEST_MONTHS:-2}"
REMOTE_HOST="${REMOTE_HOST:-crypto-agent}"
REMOTE_DIR="${REMOTE_DIR:-/opt/crypto-agent}"
IMAGE="${IMAGE:-crypto-agent-agent_sentiment_macro:latest}"

OUTPUT_DIR="research/$(echo "${SYMBOL}" | tr '[:upper:]' '[:lower:]')-${TIMEFRAME}-${OUTPUT_SUFFIX}"

echo "Remote campaign: ${SYMBOL} ${TIMEFRAME} -> ${OUTPUT_DIR} (${MAX_RUNS} runs)"

ssh "${REMOTE_HOST}" "mkdir -p ${REMOTE_DIR}/${OUTPUT_DIR} && sudo chown -R 999:999 ${REMOTE_DIR}/${OUTPUT_DIR} && nohup bash -c '
cd ${REMOTE_DIR} && docker run --rm \
  --network crypto-agent_crypto-net \
  -v ${REMOTE_DIR}:/app \
  -w /app \
  --env-file .env \
  -e POSTGRES_HOST=timescaledb \
  -e POSTGRES_PORT=5432 \
  ${IMAGE} \
  python scripts/autoresearch_loop.py \
    --config config/settings.autoresearch.yaml \
    --symbol ${SYMBOL} \
    --timeframe ${TIMEFRAME} \
    --train-months ${TRAIN_MONTHS} \
    --test-months ${TEST_MONTHS} \
    --gate-profile ${GATE_PROFILE} \
    --families ${FAMILIES} \
    --bootstrap ${BOOTSTRAP} \
    --max-runs ${MAX_RUNS} \
    --output-dir /app/${OUTPUT_DIR} \
    --timeout-seconds 900
' >> ${REMOTE_DIR}/${OUTPUT_DIR}/campaign.log 2>&1 &
echo Campaign started. Log: ${REMOTE_DIR}/${OUTPUT_DIR}/campaign.log
"
