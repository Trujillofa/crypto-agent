#!/bin/bash
# Formal WFO A/B: SOL overlay ungated vs americas session router gate.
# train=3mo test=2mo, bootstrap=100, standard gate profile for metrics only.
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-crypto-agent}"
REMOTE_DIR="${REMOTE_DIR:-/opt/crypto-agent}"
IMAGE="${IMAGE:-crypto-agent-agent_sentiment_macro:latest}"
SYMBOL="${SYMBOL:-SOLUSDT}"
TIMEFRAME="${TIMEFRAME:-1h}"
TRAIN_MONTHS="${TRAIN_MONTHS:-3}"
TEST_MONTHS="${TEST_MONTHS:-2}"
BOOTSTRAP="${BOOTSTRAP:-100}"

run_wfo() {
  local label="$1"
  local config_path="$2"
  local output_dir="$3"
  echo "===== WFO ${label} ====="
  ssh "${REMOTE_HOST}" "mkdir -p ${REMOTE_DIR}/${output_dir}/archive && \
    sudo chown -R 999:999 ${REMOTE_DIR}/${output_dir} && \
    cd ${REMOTE_DIR} && docker run --rm \
    --network crypto-agent_crypto-net \
    -v ${REMOTE_DIR}:/app \
    -w /app \
    -e PYTHONPATH=/app \
    --env-file ${REMOTE_DIR}/.env \
    -e POSTGRES_HOST=timescaledb \
    -e DB_HOST=timescaledb \
    ${IMAGE} \
    python scripts/experiment_autopilot.py \
      --config ${config_path} \
      --symbol ${SYMBOL} \
      --timeframe ${TIMEFRAME} \
      --train-months ${TRAIN_MONTHS} \
      --test-months ${TEST_MONTHS} \
      --bootstrap ${BOOTSTRAP} \
      --min-wfo-trades 20 \
      --output-prefix /app/${output_dir}/archive/experiment-autopilot-${label}" \
    | tee "/tmp/session-router-wfo-${label}.log"
  echo
}

run_wfo "ungated" "config/settings.sol_1h_trend_pullback_overlay_paper.yaml" \
  "research/solusdt-1h-session-router-wfo-ungated"
run_wfo "gated" "config/settings.sol_1h_trend_pullback_overlay_paper_americas_gate.yaml" \
  "research/solusdt-1h-session-router-wfo-gated"

ungated_json="$(ssh "${REMOTE_HOST}" "ls -1t ${REMOTE_DIR}/research/solusdt-1h-session-router-wfo-ungated/archive/*.json | head -1")"
gated_json="$(ssh "${REMOTE_HOST}" "ls -1t ${REMOTE_DIR}/research/solusdt-1h-session-router-wfo-gated/archive/*.json | head -1")"
echo "Ungated JSON: ${ungated_json}"
echo "Gated JSON:   ${gated_json}"

ssh "${REMOTE_HOST}" "cd ${REMOTE_DIR} && docker run --rm \
  --network crypto-agent_crypto-net \
  -v ${REMOTE_DIR}:/app -w /app -e PYTHONPATH=/app \
  ${IMAGE} \
  python scripts/evaluate_session_router_wfo_ab.py \
    --ungated-json ${ungated_json} \
    --gated-json ${gated_json}"
