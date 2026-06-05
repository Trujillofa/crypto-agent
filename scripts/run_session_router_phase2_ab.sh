#!/bin/bash
# Phase 2: SOL overlay backtest A/B for session liquidity router (americas gate).
# Requires: gated run must show blocked_buy_count > 0 (wiring guard).
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-crypto-agent}"
REMOTE_DIR="${REMOTE_DIR:-/opt/crypto-agent}"
IMAGE="${IMAGE:-crypto-agent-agent_sentiment_macro:latest}"
START="${START:-2024-01-09T07:00:00}"
END="${END:-2026-06-01T18:00:00}"
SYMBOL="${SYMBOL:-SOLUSDT}"
TIMEFRAME="${TIMEFRAME:-1h}"

run_backtest() {
  local label="$1"
  local config_path="$2"
  echo "===== ${label} ====="
  ssh "${REMOTE_HOST}" "cd ${REMOTE_DIR} && docker run --rm \
    --network crypto-agent_crypto-net -v ${REMOTE_DIR}:/app -w /app -e PYTHONPATH=/app \
    --env-file ${REMOTE_DIR}/.env -e POSTGRES_HOST=timescaledb -e DB_HOST=timescaledb \
    ${IMAGE} python scripts/run_backtest.py \
      --config ${config_path} \
      --symbol ${SYMBOL} \
      --timeframe ${TIMEFRAME} \
      --start ${START} \
      --end ${END}" | tee "/tmp/session-router-phase2-${label}.log"
  echo
}

run_backtest "ungated" "config/settings.sol_1h_trend_pullback_overlay_paper.yaml"
run_backtest "gated" "config/settings.sol_1h_trend_pullback_overlay_paper_americas_gate.yaml"

echo "Phase 2 wiring guard: gated blocked_buy_count must be > 0"
grep -E "Blocked BUY \\(session router\\):" /tmp/session-router-phase2-gated.log || {
  echo "FAIL: missing blocked BUY line in gated run" >&2
  exit 1
}
blocked="$(grep "Blocked BUY (session router):" /tmp/session-router-phase2-gated.log | awk '{print $NF}')"
if [[ "${blocked}" == "0" ]]; then
  echo "FAIL: blocked_buy_count is 0 — router not active" >&2
  exit 1
fi
echo "PASS: blocked_buy_count=${blocked}"
