#!/bin/bash
# run_autoresearch_tunnel.sh
# Run autoresearch locally with SSH tunnel to Hetzner's TimescaleDB

set -e

# Defaults
SYMBOL="BTCUSDT"
TIMEFRAME="1h"
DESCRIPTION="baseline"
TRAIN_MONTHS="3"
TEST_MONTHS="2"
GATE_PROFILE="standard"
LOCAL_PORT="15433"
OVERLAY=""
CONFIG="config/settings.sentiment_macro.yaml"
OUTPUT_DIR="research"
BOOTSTRAP="500"
SEED="42"
TIMEOUT_SECONDS="1800"
MODE="single"
FAMILIES=""
MAX_RUNS="10"
SSH_CONFIG="${SSH_CONFIG:-}"

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
        --overlay) OVERLAY="$2"; shift 2 ;;
        --config) CONFIG="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        --bootstrap) BOOTSTRAP="$2"; shift 2 ;;
        --seed) SEED="$2"; shift 2 ;;
        --timeout-seconds) TIMEOUT_SECONDS="$2"; shift 2 ;;
        --mode) MODE="$2"; shift 2 ;;
        --families) FAMILIES="$2"; shift 2 ;;
        --max-runs) MAX_RUNS="$2"; shift 2 ;;
        --ssh-config) SSH_CONFIG="$2"; shift 2 ;;
        *) echo "Unknown arg: $1"; shift ;;
    esac
done

if [[ "$MODE" != "single" && "$MODE" != "loop" ]]; then
    echo "ERROR: --mode must be 'single' or 'loop'" >&2
    exit 1
fi

REMOTE_HOST="crypto-agent"
REMOTE_DB_PORT="5432"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=========================================="
echo "Autoresearch via SSH Tunnel"
echo "Symbol: ${SYMBOL} | Timeframe: ${TIMEFRAME}"
echo "Description: ${DESCRIPTION}"
echo "Mode: ${MODE}"
echo "Config: ${CONFIG}"
echo "Output Dir: ${OUTPUT_DIR}"
echo "Gate Profile: ${GATE_PROFILE}"
echo "Train/Test: ${TRAIN_MONTHS}mo / ${TEST_MONTHS}mo"
echo "Bootstrap: ${BOOTSTRAP} | Seed: ${SEED}"
echo "Timeout: ${TIMEOUT_SECONDS}s"
echo "Local Port: ${LOCAL_PORT}"
[[ -n "$SSH_CONFIG" ]] && echo "SSH Config: ${SSH_CONFIG}"
[[ -n "$OVERLAY" ]] && echo "Overlay: ${OVERLAY}"
[[ -n "$FAMILIES" ]] && echo "Families: ${FAMILIES} | Max Runs: ${MAX_RUNS}"
echo "=========================================="

SSH_CMD=(ssh)
if [[ -n "$SSH_CONFIG" ]]; then
    SSH_CMD+=( -F "$SSH_CONFIG" )
fi

# Kill any existing tunnel on this port
pkill -f "ssh.*${LOCAL_PORT}.*${REMOTE_HOST}" 2>/dev/null || true
sleep 1

# Establish SSH tunnel to remote TimescaleDB
echo "Establishing SSH tunnel to ${REMOTE_HOST}:${REMOTE_DB_PORT}..."
"${SSH_CMD[@]}" -fNL ${LOCAL_PORT}:localhost:${REMOTE_DB_PORT} ${REMOTE_HOST}

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
PYTHON_BIN="${SCRIPT_DIR}/.venv/bin/python"

source .env
export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT=${LOCAL_PORT}
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD}"

echo ""
echo "Running autoresearch..."
echo ""

if [[ "$MODE" == "loop" ]]; then
    CMD="${PYTHON_BIN} scripts/autoresearch_loop.py \
        --config ${CONFIG} \
        --symbol ${SYMBOL} \
        --timeframe ${TIMEFRAME} \
        --train-months ${TRAIN_MONTHS} \
        --test-months ${TEST_MONTHS} \
        --bootstrap ${BOOTSTRAP} \
        --seed ${SEED} \
        --gate-profile ${GATE_PROFILE} \
        --output-dir ${OUTPUT_DIR} \
        --timeout-seconds ${TIMEOUT_SECONDS} \
        --max-runs ${MAX_RUNS}"

    if [[ -n "$FAMILIES" ]]; then
        CMD="$CMD --families ${FAMILIES}"
    fi
else
    CMD="${PYTHON_BIN} scripts/run_autoresearch.py \
        --config ${CONFIG} \
        --symbol ${SYMBOL} \
        --timeframe ${TIMEFRAME} \
        --train-months ${TRAIN_MONTHS} \
        --test-months ${TEST_MONTHS} \
        --bootstrap ${BOOTSTRAP} \
        --seed ${SEED} \
        --gate-profile ${GATE_PROFILE} \
        --description \"${SYMBOL}_${DESCRIPTION}\" \
        --output-dir ${OUTPUT_DIR} \
        --timeout-seconds ${TIMEOUT_SECONDS}"

    if [[ -n "$OVERLAY" ]]; then
        CMD="$CMD --overlay ${OVERLAY}"
    fi
fi

# Run
eval $CMD
RESULT=$?

# Kill tunnel
pkill -f "ssh.*${LOCAL_PORT}.*${REMOTE_HOST}" 2>/dev/null || true

echo ""
if [ $RESULT -eq 0 ]; then
    echo "✅ Done! Results in: research/last_result.json"
else
    echo "❌ Autoresearch failed with exit code: ${RESULT}"
fi

exit ${RESULT}
