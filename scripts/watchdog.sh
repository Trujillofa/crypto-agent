#!/usr/bin/env bash
# Page Telegram when Prometheus cannot page for itself.
# crypto-agent Prometheus is only on the docker network. Host 127.0.0.1:9090
# is a different listener, so checks run inside crypto-agent-prometheus-1.
# Instant query time() returns a scalar. AgentDown covers the other agents
# while Prometheus is up. This script checks Timescale, Prometheus, and the
# live 1h agent directly.
set -euo pipefail

STATE_DIR="/var/lib/crypto-agent-watchdog"
HERMES="/home/emilio/.local/bin/hermes"
CHAT="telegram:1278127918"
mkdir -p "$STATE_DIR"

ALERT=""
fail() {
  if [ -n "$ALERT" ]; then
    ALERT="${ALERT}"$'\n'"• $1"
  else
    ALERT="• $1"
  fi
}

for c in \
  crypto-agent-timescaledb-1 \
  crypto-agent-prometheus-1 \
  crypto-agent-agent_sol_1h_trend_pullback_overlay_live-1
do
  running="$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null || true)"
  if [ "$running" != "true" ]; then
    fail "container $c NOT RUNNING"
  fi
done

if ! docker exec crypto-agent-prometheus-1 wget -qO- -T 5 http://127.0.0.1:9090/-/healthy >/dev/null 2>&1; then
  fail "crypto-agent Prometheus unhealthy/unreachable"
else
  ts="$(
    docker exec crypto-agent-prometheus-1 wget -qO- -T 5 \
      'http://127.0.0.1:9090/api/v1/query?query=time()' \
      | python3 -c 'import json,sys
payload = json.load(sys.stdin)
if payload.get("status") != "success":
    raise SystemExit(1)
data = payload["data"]
if data.get("resultType") == "scalar":
    value = data["result"][1]
else:
    value = data["result"][0]["value"][1]
if value in ("", None):
    raise SystemExit(1)
print(value)'
  )" || ts=""
  if [ -z "$ts" ]; then
    fail "Prometheus query API failed"
  fi
fi

send() {
  "$HERMES" send --to "$CHAT" "$1"
}

if [ -n "$ALERT" ]; then
  printf '%s\n' "$ALERT" > "$STATE_DIR/last"
  if [ ! -f "$STATE_DIR/failing" ]; then
    send "$(printf 'WATCHDOG\n%s\n' "$ALERT")"
    touch "$STATE_DIR/failing"
  fi
elif [ -f "$STATE_DIR/failing" ]; then
  send "WATCHDOG: recovered. Core containers are running and Prometheus is answering."
  rm -f "$STATE_DIR/failing"
fi
