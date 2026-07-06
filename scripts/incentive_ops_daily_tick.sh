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

# Use the project's uv-managed environment; bare `python` under the systemd
# unit's minimal PATH is the system interpreter and lacks project deps.
run_ops() { uv run --project "$REPO" python -m tools.incentive_ops "$@"; }

# Skip quietly only when the baseline is genuinely absent or not RUNNING.
# Any other failure (e.g. import error) must surface, not masquerade as
# "no baseline" — that silently skipped ticks on 2026-07-05/06.
status_rc=0
status_out="$(run_ops baseline status 2>&1)" || status_rc=$?
if [ "$status_rc" -ne 0 ]; then
  if grep -q 'BASELINE STATUS FAIL' <<<"$status_out"; then
    echo "[$(ts)] no baseline runs found; nothing to tick"
    exit 0
  fi
  echo "[$(ts)] ERROR: baseline status check crashed (rc=$status_rc):" >&2
  printf '%s\n' "$status_out" >&2
  exit 1
fi
if ! grep -q 'status=RUNNING' <<<"$status_out"; then
  echo "[$(ts)] no RUNNING baseline; nothing to tick"
  exit 0
fi

echo "[$(ts)] tick start"
run_ops baseline tick

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

Automated daily read-only observation of the A1 Phase-0 baseline (no capital).

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
branch="$(git rev-parse --abbrev-ref HEAD)"
echo "[$(ts)] tick committed on $branch"

# Auto-push the observation. Fail loudly (no auto-rebase/merge) if the remote has
# diverged, so a human resolves it rather than the timer guessing.
if git push origin "HEAD:$branch"; then
  echo "[$(ts)] pushed to origin/$branch"
else
  echo "[$(ts)] WARN: push failed (origin/$branch may have advanced); commit is local only. Resolve manually." >&2
  exit 1
fi
