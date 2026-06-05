#!/bin/bash
# B-SOL research sprint: funding normalization primary on SOLUSDT 1h only.
# Prerequisite: probe reshape showed HAS_PULSE for SOL neg_tail_10pct (cheap probe only).
# Post-campaign: entry overlap vs live SOL overlay + sentiment-macro before any b=1000.
set -euo pipefail

SYMBOL="${1:-SOLUSDT}"
if [[ "${SYMBOL}" != "SOLUSDT" ]]; then
  echo "B-SOL sprint is scoped to SOLUSDT only. Got: ${SYMBOL}" >&2
  exit 1
fi

export GATE_PROFILE=standard
export BOOTSTRAP=100
export MAX_RUNS="${MAX_RUNS:-80}"
export FAMILIES=funding_normalization_standalone

./scripts/run_autoresearch_campaign_remote.sh SOLUSDT 1h w9-sol-1h-funding-norm
