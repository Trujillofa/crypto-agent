#!/bin/bash
# run_autoresearch_remote.sh
# Run autoresearch sweep on Hetzner server where full indicator data lives

set -e

SYMBOL="${1:-BTCUSDT}"
TIMEFRAME="${2:-1h}"
DESCRIPTION="${3:-baseline}"
TRAIN_MONTHS="${4:-3}"
TEST_MONTHS="${5:-2}"
GATE_PROFILE="${6:-standard}"

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --symbol) SYMBOL="$2"; shift 2 ;;
        --timeframe) TIMEFRAME="$2"; shift 2 ;;
        --description) DESCRIPTION="$2"; shift 2 ;;
        --train-months) TRAIN_MONTHS="$2"; shift 2 ;;
        --test-months) TEST_MONTHS="$2"; shift 2 ;;
        --gate-profile) GATE_PROFILE="$2"; shift 2 ;;
        *) shift ;;
    esac
    shift
done

REMOTE_HOST="crypto-agent"
REMOTE_DIR="/opt/crypto-agent"

echo "=========================================="
echo "Remote Autoresearch: ${SYMBOL} ${TIMEFRAME}"
echo "Description: ${DESCRIPTION}"
echo "Gate Profile: ${GATE_PROFILE}"
echo "Train/Test: ${TRAIN_MONTHS}mo / ${TEST_MONTHS}mo"
echo "=========================================="

ssh ${REMOTE_HOST} "cd ${REMOTE_DIR} && docker exec crypto-agent-agent_sentiment_macro-1 python scripts/run_autoresearch.py --config config/settings.sentiment_macro.yaml --symbol ${SYMBOL} --timeframe ${TIMEFRAME} --train-months ${TRAIN_MONTHS} --test-months ${TEST_MONTHS} --gate-profile ${GATE_PROFILE} --description '${SYMBOL}_${DESCRIPTION}' --timeout-seconds 1800" 2>&1

echo ""
echo "Done!"
