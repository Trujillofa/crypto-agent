#!/usr/bin/env bash
# Gate-compliant NFP pre-capture for 2026-08-07 print.
# Only valid inside 2026-08-06 12:30 UTC → 2026-08-07 12:30 UTC.
# Window is closed; kept as the failure artifact + template for later prints.
set -euo pipefail
REPO="/home/yderf/Projects/trading/TRADING/crypto-agent"
LOG_DIR="$REPO/research/nfp_forward"
LOG="$LOG_DIR/pre-2026-08-07.log"
cd "$REPO"
{
  echo "=== NFP pre start $(date -u -Iseconds) ==="
  uv run python scripts/nfp_forward_capture.py status
  set +e
  uv run python scripts/nfp_forward_capture.py pre --release-date 2026-08-07
  pre_rc=$?
  set -e
  echo "=== NFP pre exit ${pre_rc} at $(date -u -Iseconds) ==="
  uv run python scripts/nfp_forward_capture.py status
  exit "${pre_rc}"
} >>"$LOG" 2>&1
