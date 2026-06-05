#!/bin/bash
# Option F: cheap feasibility probes for volatility squeeze breakout (BTC → ETH → SOL).
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-crypto-agent}"
REMOTE_DIR="${REMOTE_DIR:-/opt/crypto-agent}"
IMAGE="${IMAGE:-crypto-agent-agent_sentiment_macro:latest}"
PROBE="scripts/probe_volatility_squeeze_breakout.py"

run_one() {
  local symbol="$1"
  echo "===== ${symbol} ====="
  ssh "${REMOTE_HOST}" "cd ${REMOTE_DIR} && docker run --rm \
    --network crypto-agent_crypto-net -v ${REMOTE_DIR}:/app -w /app -e PYTHONPATH=/app \
    --env-file ${REMOTE_DIR}/.env -e POSTGRES_HOST=timescaledb -e DB_HOST=timescaledb \
    ${IMAGE} python ${PROBE} --symbol ${symbol} --timeframe 1h"
  echo
}

for symbol in BTCUSDT ETHUSDT SOLUSDT; do
  run_one "${symbol}"
done
