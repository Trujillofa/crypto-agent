#!/bin/bash
# Run a dry RBI loop supervisor pass over lane manifests.
set -euo pipefail

MANIFEST_GLOB="${MANIFEST_GLOB:-config/autoresearch/rbi_loop.*.yaml}"
SUMMARY_OUTPUT="${SUMMARY_OUTPUT:-research/rbi_loop/batch-summary.json}"
MARKDOWN_OUTPUT="${MARKDOWN_OUTPUT:-docs/reports/rbi-loop-batch-summary.md}"

python scripts/rbi_loop_batch.py \
  --glob "${MANIFEST_GLOB}" \
  --summary-output "${SUMMARY_OUTPUT}" \
  --markdown-output "${MARKDOWN_OUTPUT}"
