#!/usr/bin/env bash
# Phase 3 Validation: Sentiment Mean Reversion Strategy
#
# Prerequisites:
#   1. Sync event log from server:
#      ./scripts/export_sentiment_replay.sh
#   2. Ensure DB has historical OHLCV data for the replay period
#
# Gate criteria (from RESEARCH_FRAMEWORK.md):
#   - WFO OOS Sharpe >= 0.6 (Critical)
#   - Parameter stability >= 6/9 profitable (Critical)
#   - OOS Sharpe within 40% of IS Sharpe (Critical)
#   - Multi-symbol test >= 2/3 profitable (High)
#   - Monte Carlo 5th percentile positive (High)

set -euo pipefail

CONFIG="${CONFIG:-config/settings.sentiment_macro.yaml}"
REPLAY_LOG="${REPLAY_LOG:-data/event_log_sentiment-macro-bot.jsonl}"
REPLAY_MAX_AGE="${REPLAY_MAX_AGE:-24}"  # hours
SYMBOLS=("BTCUSDT" "ETHUSDT" "SOLUSDT")
TIMEFRAME="1h"

echo "============================================"
echo "Phase 3: Sentiment Mean Reversion Validation"
echo "============================================"
echo ""

# Check replay log exists
if [ ! -f "$REPLAY_LOG" ]; then
    echo "ERROR: Replay log not found at $REPLAY_LOG"
    echo "Sync from production Docker volume: ./scripts/export_sentiment_replay.sh"
    exit 1
fi

OBSERVATIONS=$(grep -c sentiment_score "$REPLAY_LOG" || true)
echo "Replay log: $OBSERVATIONS sentiment observations"
echo ""

read -r REPLAY_START REPLAY_END < <(
    uv run python - "$REPLAY_LOG" <<'PY'
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

timestamps = []
for line in Path(sys.argv[1]).read_text().splitlines():
    event = json.loads(line)
    if event.get("type") == "sentiment_score":
        timestamps.append(datetime.fromisoformat(event["ts"]))

if not timestamps:
    raise SystemExit("Replay log contains no sentiment_score observations")

print(min(timestamps).date(), (max(timestamps).date() + timedelta(days=1)))
PY
)

START="${START:-$REPLAY_START}"
END="${END:-$REPLAY_END}"

echo "Validation window: $START to $END (END is exclusive)"
echo ""

# --- Test 1: Multi-symbol backtests with replay ---
echo "=== Test 1: Multi-Symbol Backtests (with replay) ==="
for sym in "${SYMBOLS[@]}"; do
    echo "--- $sym ---"
    uv run python scripts/run_backtest.py \
        --config "$CONFIG" \
        --symbol "$sym" \
        --timeframe "$TIMEFRAME" \
        --start "$START" \
        --end "$END" \
        --replay-sentiment-log "$REPLAY_LOG" \
        --replay-sentiment-max-age-hours "$REPLAY_MAX_AGE"
    echo ""
done

# --- Test 2: Control backtests WITHOUT replay (neutral sentiment) ---
echo "=== Test 2: Control Backtests (neutral sentiment baseline) ==="
for sym in "${SYMBOLS[@]}"; do
    echo "--- $sym (no replay) ---"
    uv run python scripts/run_backtest.py \
        --config "$CONFIG" \
        --symbol "$sym" \
        --timeframe "$TIMEFRAME" \
        --start "$START" \
        --end "$END"
    echo ""
done

# --- Test 3: Walk-Forward Optimization ---
echo "=== Test 3: Walk-Forward Optimization ==="
for sym in "${SYMBOLS[@]}"; do
    echo "--- WFO: $sym ---"
    uv run python scripts/run_wfo.py \
        "$sym" "$TIMEFRAME" "$START" "$END" \
        --train-months 2 \
        --test-months 1 \
        --config "$CONFIG" \
        --output "wfo_sentiment_${sym}.csv" \
        --replay-sentiment-log "$REPLAY_LOG" \
        --replay-sentiment-max-age-hours "$REPLAY_MAX_AGE"
    echo ""
done

# --- Test 4: Monte Carlo ---
echo "=== Test 4: Monte Carlo Stress Test ==="
for sym in "${SYMBOLS[@]}"; do
    echo "--- Monte Carlo: $sym ---"
    uv run python scripts/run_monte_carlo.py \
        --config "$CONFIG" \
        --symbol "$sym" \
        --timeframe "$TIMEFRAME" \
        --start "$START" \
        --end "$END" \
        --bootstrap 2000 \
        --replay-sentiment-log "$REPLAY_LOG" \
        --replay-sentiment-max-age-hours "$REPLAY_MAX_AGE"
    echo ""
done

echo "============================================"
echo "Phase 3 validation complete."
echo "Review results against gate criteria in docs/RESEARCH_FRAMEWORK.md"
echo "============================================"
