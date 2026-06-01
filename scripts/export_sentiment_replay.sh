#!/usr/bin/env bash
# Export replayable sentiment observations from the production Docker volume.

set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-crypto-agent}"
REMOTE_DIR="${REMOTE_DIR:-/opt/crypto-agent}"
OUTPUT_PATH="${1:-data/event_log_sentiment-macro-bot.jsonl}"

mkdir -p "$(dirname "$OUTPUT_PATH")"
TEMP_PATH=$(mktemp "${OUTPUT_PATH}.tmp.XXXXXX")
trap 'rm -f "$TEMP_PATH"' EXIT

ssh "$REMOTE_HOST" \
    "cd '$REMOTE_DIR' && docker compose -f docker-compose.prod.yml exec -T agent_sentiment_macro cat /app/data/event_log_sentiment-macro-bot.jsonl" \
    > "$TEMP_PATH"

mv "$TEMP_PATH" "$OUTPUT_PATH"
trap - EXIT

OBSERVATIONS=$(grep -c '"type": "sentiment_score"' "$OUTPUT_PATH" || true)
echo "Exported $OBSERVATIONS sentiment observations to $OUTPUT_PATH"
