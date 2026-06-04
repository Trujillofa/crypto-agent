#!/bin/bash
# Revalidate a tracked overlay with bootstrap=1000 on the production DB host.
set -euo pipefail

SYMBOL="${1:?SYMBOL required}"
TIMEFRAME="${2:?TIMEFRAME required}"
OVERLAY="${3:?OVERLAY path required}"
LABEL="${4:-validation-b1000}"
REMOTE_HOST="${REMOTE_HOST:-crypto-agent}"
REMOTE_DIR="${REMOTE_DIR:-/opt/crypto-agent}"
IMAGE="${IMAGE:-crypto-agent-agent_sentiment_macro:latest}"

OUTPUT_DIR="research/$(echo "${SYMBOL}" | tr '[:upper:]' '[:lower:]')-${TIMEFRAME}-${LABEL}"

echo "Validation: ${SYMBOL} ${TIMEFRAME} overlay=${OVERLAY} -> ${OUTPUT_DIR}"

ssh "${REMOTE_HOST}" "mkdir -p ${REMOTE_DIR}/${OUTPUT_DIR} && sudo chown -R 999:999 ${REMOTE_DIR}/${OUTPUT_DIR} && nohup bash -c '
cd ${REMOTE_DIR} && docker run --rm \
  --network crypto-agent_crypto-net \
  -v ${REMOTE_DIR}:/app -w /app \
  --env-file .env \
  -e POSTGRES_HOST=timescaledb \
  -e POSTGRES_PORT=5432 \
  ${IMAGE} \
  python scripts/run_autoresearch.py \
    --config config/settings.autoresearch.yaml \
    --overlay ${OVERLAY} \
    --description ${SYMBOL}_${LABEL}_bootstrap1000 \
    --output-dir /app/${OUTPUT_DIR} \
    --symbol ${SYMBOL} \
    --timeframe ${TIMEFRAME} \
    --train-months 3 \
    --test-months 2 \
    --bootstrap 1000 \
    --seed 317 \
    --gate-profile standard \
    --timeout-seconds 3600
' >> ${REMOTE_DIR}/${OUTPUT_DIR}/campaign.log 2>&1 &
echo started ${OUTPUT_DIR}
"
