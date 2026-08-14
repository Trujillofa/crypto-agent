#!/usr/bin/env bash
# Align a production checkout to REQUESTED_SHA without mutating HEAD on mismatch.
# Used by .github/workflows/deploy.yml (piped from the runner, not the server tree).
set -euo pipefail

: "${REQUESTED_SHA:?REQUESTED_SHA is required}"
ROOT="${DEPLOY_ROOT:-/opt/crypto-agent}"
cd "$ROOT"

# 1) Fetch only. Do not pull until origin/main matches the requested SHA.
git fetch origin main
REMOTE=$(git rev-parse origin/main)
if [ "$REMOTE" != "$REQUESTED_SHA" ]; then
  echo "ERROR: origin/main ($REMOTE) != requested deploy_sha ($REQUESTED_SHA); aborting before pull"
  exit 1
fi

# 2) Fast-forward only after the pre-pull check.
git pull --ff-only origin main

# 3) Reverify HEAD, origin/main, and the requested SHA agree.
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)
if [ "$LOCAL" != "$REMOTE" ]; then
  echo "ERROR: after pull, HEAD ($LOCAL) != origin/main ($REMOTE)"
  exit 1
fi
if [ "$LOCAL" != "$REQUESTED_SHA" ]; then
  echo "ERROR: after pull, HEAD ($LOCAL) != requested deploy_sha ($REQUESTED_SHA)"
  exit 1
fi
