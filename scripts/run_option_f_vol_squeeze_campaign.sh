#!/bin/bash
# Option F: bounded volatility squeeze on BTCUSDT or ETHUSDT 1h only.
# Prerequisite: prod probe HAS_PULSE on BTC/ETH (see volatility-squeeze-breakout-probe-2026-06-05.md).
# Post-campaign: promotion_candidate prefilter, overlap, then b=1000 on any winner.
set -euo pipefail

SYMBOL="${1:?symbol required: BTCUSDT or ETHUSDT}"
SYMBOL="${SYMBOL^^}"

case "${SYMBOL}" in
  BTCUSDT)
    OUTPUT_SUFFIX="${2:-w10-btc-1h-vol-squeeze-bounded}"
    ;;
  ETHUSDT)
    OUTPUT_SUFFIX="${2:-w10-eth-1h-vol-squeeze-bounded}"
    ;;
  *)
    echo "Option F campaign is scoped to BTCUSDT or ETHUSDT only. Got: ${SYMBOL}" >&2
    exit 1
    ;;
esac

export GATE_PROFILE=standard
export BOOTSTRAP=100
export MAX_RUNS="${MAX_RUNS:-40}"
export FAMILIES=volatility_squeeze_bounded

./scripts/run_autoresearch_campaign_remote.sh "${SYMBOL}" 1h "${OUTPUT_SUFFIX}"
