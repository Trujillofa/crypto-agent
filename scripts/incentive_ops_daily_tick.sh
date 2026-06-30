#!/bin/bash
# Daily read-only tick for the A1 incentive-ops Phase-0 baseline.
#
# Refetches the active-research sources (allowlisted GET only), records one
# observation, and commits the resulting artifacts on the current branch.
# NO capital, NO wallets, NO keys, NO push. If no baseline is RUNNING (e.g. the
# window has closed), it exits 0 quietly so the timer does not fail forever.
#
# Override the repo location with INCENTIVE_OPS_REPO if not running in place.
set -euo pipefail

REPO="${INCENTIVE_OPS_REPO:-/home/yderf/Projects/trading/TRADING/crypto-agent}"
cd "$REPO"

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# Skip quietly when no baseline is RUNNING (post-close / not yet started).
if ! python -m tools.incentive_ops baseline status 2>/dev/null | grep -q 'status=RUNNING'; then
  echo "[$(ts)] no RUNNING baseline; nothing to tick"
  exit 0
fi

echo "[$(ts)] tick start"
python -m tools.incentive_ops baseline tick

# Stage only incentive-ops artifacts; commit only if the tick changed something.
git add research/a1-incentive-farming/runs \
        research/a1-incentive-farming/captures \
        research/a1-incentive-farming/verifications \
        research/a1-incentive-farming/snapshots

if git diff --cached --quiet; then
  echo "[$(ts)] tick produced no changes; nothing to commit"
  exit 0
fi

git commit -q -m "chore(incentive_ops): baseline tick $(date -u +%Y-%m-%d)

Automated daily read-only observation of the A1 Phase-0 baseline (no capital, no
push). Pushing tick commits remains a manual step.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
echo "[$(ts)] tick committed on $(git rev-parse --abbrev-ref HEAD)"
