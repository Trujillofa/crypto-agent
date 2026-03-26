#!/bin/bash
# run_autoresearch_tunnel.sh
# Run autoresearch locally with SSH tunnel to Hetzner's TimescaleDB

set -e

SYMBOL="${1:-BTCUSDT}"
TIMEFRAME="${2:-1h}"
DESCRIPTION="${3:-baseline}"
TRAIN_MONTHS="${4:-3}"
TEST_MONTHS="${5:-2}"
GATE_PROFILE="${6:-standard}"
LOCAL_PORT="${7:-15433}"

# Parse named args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --symbol) SYMBOL="$2"; shift 2 ;;
        --timeframe) TIMEFRAME="$2"; shift 2 ;;
        --description) DESCRIPTION="$2"; shift 2 ;;
        --train-months) TRAIN_MONTHS="$2"; shift 2 ;;
        --test-months) TEST_MONTHS="$2"; shift 2 ;;
        --gate-profile) GATE_PROFILE="$2"; shift 2 ;;
        --local-port) LOCAL_PORT="$2"; shift 2 ;;
        *) shift ;;
    esac
    shift
done

REMOTE_HOST="crypto-agent"
REMOTE_DB_PORT="5432"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=========================================="
echo "Autoresearch via SSH Tunnel: ${SYMBOL} ${TIMEFRAME}"
echo "Description: ${DESCRIPTION}"
echo "Gate Profile: ${GATE_PROFILE}"
echo "Train/Test: ${TRAIN_MONTHS}mo / ${TEST_MONTHS}mo"
echo "Local Port: ${LOCAL_PORT}"
echo "=========================================="

# Kill any existing tunnel on this port
pkill -f "ssh.*${LOCAL_PORT}.*${REMOTE_HOST}" 2>/dev/null || true

# Establish SSH tunnel to remote TimescaleDB
echo "Establishing SSH tunnel to ${REMOTE_HOST}:${REMOTE_DB_PORT}..."
ssh -fNL ${LOCAL_PORT}:localhost:${REMOTE_DB_PORT} ${REMOTE_HOST}

# Wait for tunnel
sleep 2

# Check tunnel with bash built-in
if ! timeout 2 bash -c "echo > /dev/tcp/localhost/${LOCAL_PORT}" 2>/dev/null; then
    echo "ERROR: SSH tunnel failed to establish"
    exit 1
fi

echo "SSH tunnel established on port ${LOCAL_PORT}!"

# Change to script directory
cd "$SCRIPT_DIR"

# Activate venv and set environment
source .venv/bin/activate

export DB_HOST=127.0.0.1
export DB_PORT=${LOCAL_PORT}
source .env
export DB_PASSWORD="${POSTGRES_PASSWORD}"

echo ""
echo "Running autoresearch..."
echo ""

# Run autoresearch with tunnel
python scripts/run_autoresearch.py \
    --config config/settings.sentiment_macro.yaml \
    --symbol ${SYMBOL} \
    --timeframe ${TIMEFRAME} \
    --train-months ${TRAIN_MONTHS} \
    --test-months ${TEST_MONTHS} \
    --gate-profile ${GATE_PROFILE} \
    --description "${SYMBOL}_${DESCRIPTION}" \
    --timeout-seconds 1800

RESULT=$?

# Kill tunnel
pkill -f "ssh.*${LOCAL_PORT}.*${REMOTE_HOST}" 2>/dev/null || true

echo ""
if [ $RESULT -eq 0 ]; then
    echo "Done! Results in: research/last_result.json"
else
    echo "Autoresearch failed with exit code: ${RESULT}"
fi

exit ${RESULT}
