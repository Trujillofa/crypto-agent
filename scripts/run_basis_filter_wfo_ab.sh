#!/bin/bash
# Formal WFO A/B: SOL overlay baseline vs basis premium risk filter.
# train=3mo test=2mo, bootstrap=100. Thresholds calibrated per WFO train window only.
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
    | tee "/tmp/basis-filter-wfo-${label}.log"
  echo
}

run_wfo "baseline" "config/settings.sol_1h_trend_pullback_overlay_paper.yaml" \
  "research/solusdt-1h-basis-filter-wfo-baseline"
run_wfo "filtered" "config/settings.sol_1h_trend_pullback_overlay_paper_basis_filter.yaml" \
  "research/solusdt-1h-basis-filter-wfo-filtered"

baseline_host="$(ssh "${REMOTE_HOST}" "ls -1t ${REMOTE_DIR}/research/solusdt-1h-basis-filter-wfo-baseline/archive/*.json | head -1")"
filtered_host="$(ssh "${REMOTE_HOST}" "ls -1t ${REMOTE_DIR}/research/solusdt-1h-basis-filter-wfo-filtered/archive/*.json | head -1")"
baseline_json="${baseline_host/${REMOTE_DIR}/\/app}"
filtered_json="${filtered_host/${REMOTE_DIR}/\/app}"
echo "Baseline JSON: ${baseline_host}"
echo "Filtered JSON: ${filtered_host}"

ssh "${REMOTE_HOST}" "cd ${REMOTE_DIR} && docker run --rm \
  --network crypto-agent_crypto-net \
  -v ${REMOTE_DIR}:/app -w /app -e PYTHONPATH=/app \
  ${IMAGE} \
  python scripts/evaluate_basis_filter_wfo_ab.py \
    --baseline-json ${baseline_json} \
    --filtered-json ${filtered_json}"
