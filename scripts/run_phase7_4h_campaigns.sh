#!/bin/bash
# Phase 1 + Phase 2 discovery from autoresearch-next-candidate-path (June 2026).
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-crypto-agent}"
export GATE_PROFILE=standard
export BOOTSTRAP=100
export MAX_RUNS="${MAX_RUNS:-80}"

run_lane() {
  local symbol="$1"
  local tf="$2"
  local suffix="$3"
  local families="$4"
  FAMILIES="${families}" MAX_RUNS="${MAX_RUNS}" GATE_PROFILE="${GATE_PROFILE}" BOOTSTRAP="${BOOTSTRAP}" \
    ./scripts/run_autoresearch_campaign_remote.sh "${symbol}" "${tf}" "${suffix}"
}

echo "Phase 1: ETH 4h regime overlay"
run_lane ETHUSDT 4h w7-eth-4h-regime "regime_gated_pullback_overlay,breakout_retest_overlay"

echo "Phase 1: BTC 4h regime overlay"
run_lane BTCUSDT 4h w7-btc-4h-regime "regime_gated_pullback_overlay,breakout_retest_overlay"

if [[ "${RUN_PHASE2:-1}" == "1" ]]; then
  export MAX_RUNS="${BOUNDED_RUNS:-50}"
  echo "Phase 2: ETH 4h range_reversion_bounded"
  run_lane ETHUSDT 4h w8-eth-4h-bounded "range_reversion_bounded"

  echo "Phase 2: BTC 4h range_reversion_bounded"
  run_lane BTCUSDT 4h w8-btc-4h-bounded "range_reversion_bounded"

  echo "Phase 3 probe: BTC 4h funding_primary_standalone"
  run_lane BTCUSDT 4h w8-btc-4h-funding-primary "funding_primary_standalone"
fi

echo "All lanes queued on ${REMOTE_HOST}"
