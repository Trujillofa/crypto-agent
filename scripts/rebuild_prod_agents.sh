#!/usr/bin/env bash
# Coordinated rebuild of every agent_* service. Runs after align_prod_checkout.sh.
set -euo pipefail

ROOT="${DEPLOY_ROOT:-/opt/crypto-agent}"
cd "$ROOT"

# Durable host-local opt-in. Never source this file: it is data, not shell.
COMMANDCODE_DEPLOY_CONFIG="${COMMANDCODE_DEPLOY_CONFIG:-/etc/crypto-agent/commandcode.deploy}"
COMPOSE=(docker compose -f docker-compose.prod.yml)
if [ -e "$COMMANDCODE_DEPLOY_CONFIG" ] || [ -L "$COMMANDCODE_DEPLOY_CONFIG" ]; then
  if [ ! -f "$COMMANDCODE_DEPLOY_CONFIG" ] || [ -L "$COMMANDCODE_DEPLOY_CONFIG" ] || [ ! -r "$COMMANDCODE_DEPLOY_CONFIG" ]; then
    echo "ERROR: Command Code deploy config must be a readable regular file, not a symlink" >&2
    exit 1
  fi
  mapfile -t COMMANDCODE_CONFIG_LINES < "$COMMANDCODE_DEPLOY_CONFIG"
  if [ "${#COMMANDCODE_CONFIG_LINES[@]}" -ne 1 ] || [ "${COMMANDCODE_CONFIG_LINES[0]}" != enabled ]; then
    echo "ERROR: Command Code deploy config must contain exactly: enabled" >&2
    exit 1
  fi
  export COMMANDCODE_ENV_FILE="${COMMANDCODE_ENV_FILE:-/etc/crypto-agent/commandcode.env}"
  if [ ! -f "$COMMANDCODE_ENV_FILE" ] || [ -L "$COMMANDCODE_ENV_FILE" ] || [ ! -r "$COMMANDCODE_ENV_FILE" ] ||
     [ "$(stat -c '%a:%u' "$COMMANDCODE_ENV_FILE")" != "600:$(id -u)" ]; then
    echo "ERROR: Command Code env file must be a readable regular file owned by the deploy user with mode 0600" >&2
    exit 1
  fi
  if [ ! -f docker-compose.commandcode.yml ]; then
    echo "ERROR: Command Code overlay is missing" >&2
    exit 1
  fi
  # Reject an overlay that adds or removes services before --remove-orphans.
  BASE_SERVICES=$("${COMPOSE[@]}" config --services | sort)
  COMPOSE+=(-f docker-compose.commandcode.yml)
  OVERLAY_SERVICES=$("${COMPOSE[@]}" config --services | sort)
  if [ "$BASE_SERVICES" != "$OVERLAY_SERVICES" ]; then
    echo "ERROR: Command Code overlay must not add or remove services" >&2
    exit 1
  fi
  # Check resolved service values, without emitting the secret to logs.
  if ! "${COMPOSE[@]}" config --format json | python3 -c '
import json, sys
try:
    env = json.load(sys.stdin)["services"]["agent_sentiment_macro"]["environment"]
    valid = (env.get("COMMANDCODE_ENABLED", "").lower() == "true"
             and bool(env.get("COMMANDCODE_API_KEY", "").strip())
             and bool(env.get("COMMANDCODE_MODEL", "").strip()))
except (ValueError, KeyError, TypeError, AttributeError):
    valid = False
sys.exit(0 if valid else 1)
'; then
    echo "ERROR: Command Code overlay requires enabled=true and nonempty key and model" >&2
    exit 1
  fi
  echo "Command Code overlay enabled"
fi

AGENTS=$("${COMPOSE[@]}" config --services | grep '^agent_' | sort | tr '\n' ' ')
if [ -z "$AGENTS" ]; then
  echo "ERROR: no agent_* services found in docker-compose.prod.yml"
  exit 1
fi
echo "Deploying agents: $AGENTS"

"${COMPOSE[@]}" build $AGENTS
"${COMPOSE[@]}" up -d --remove-orphans $AGENTS

# Recreate Prometheus so that changes to config/prometheus-alerts.yml take
# effect.
#
# The alert rules file is BIND-MOUNTED into the prometheus container, and
# a bind mount is bound to an INODE, not to a path. `git pull --ff-only`
# (in align_prod_checkout.sh) rewrites the file as a NEW inode, so the
# container keeps serving the OLD file -- silently.
#
# The failure is quiet: the deploy reports success, prometheus keeps the
# previous rules, and a new alert simply never fires.
#
# The obvious fixes do NOT work:
#   * `docker kill --signal HUP` re-reads the config, but from inside the
#     container that is still the STALE inode.
#   * `docker restart` re-binds the same stale inode.
#   * `docker cp` fails with "device or resource busy" -- you cannot
#     replace a bind-mounted file.
#   * the mount is :ro, so the container cannot write it either.
#
# Only a RECREATE re-binds the current file. This was verified: the
# container and host inode numbers match after a recreate, and the new
# rules appear in the live rule list.
#
# The rules are validated afterwards, so a broken rules file fails the
# deploy loudly instead of silently leaving the previous rules active.
if "${COMPOSE[@]}" ps --services --status running \
     | grep -qx prometheus; then
  echo "Recreating prometheus to pick up config/prometheus-alerts.yml"
  "${COMPOSE[@]}" up -d --force-recreate prometheus
  sleep 15
  RULE_COUNT=$(docker exec crypto-agent-prometheus-1 \
    promtool check rules /etc/prometheus/alerts.yml 2>&1 | grep -oE '[0-9]+ rules? found' | head -1)
  echo "prometheus_rules=$RULE_COUNT"
  if [ -z "$RULE_COUNT" ]; then
    echo "ERROR: prometheus alert rules did not validate after recreate"
    docker exec crypto-agent-prometheus-1 promtool check rules /etc/prometheus/alerts.yml || true
    exit 1
  fi
  # Confirm the container really sees the CURRENT host file, not a stale
  # inode. A mismatch here means the rules silently did not update.
  HOST_INODE=$(stat -c %i "$ROOT/config/prometheus-alerts.yml")
  CONT_INODE=$(docker exec crypto-agent-prometheus-1 stat -c %i /etc/prometheus/alerts.yml)
  if [ "$HOST_INODE" != "$CONT_INODE" ]; then
    echo "ERROR: prometheus is serving a stale rules file (host inode $HOST_INODE != container inode $CONT_INODE)"
    exit 1
  fi
  echo "prometheus rules bound to current file (inode $HOST_INODE)"
else
  echo "WARNING: prometheus service is not running; skipped rules reload"
fi

sleep 120
# Fail closed on the exact agent set: an empty or partial `compose ps` output
# must never read as "everything is healthy". Compare the returned service set
# against the expected agents, reject duplicates, and require each status line
# to end in (healthy).
EXPECTED_AGENTS=$(echo "$AGENTS" | tr ' ' '\n' | grep . | sort)
STATUS_ROWS=$("${COMPOSE[@]}" ps --format "{{.Service}} {{.Status}}" $AGENTS 2>/dev/null || true)
ACTUAL_AGENTS=$(printf '%s\n' "$STATUS_ROWS" | awk 'NF >= 2 {print $1}' | sort)
DUPLICATE_AGENTS=$(printf '%s\n' "$ACTUAL_AGENTS" | grep . | uniq -d || true)
NOT_READY=$(printf '%s\n' "$STATUS_ROWS" | grep -vE '^[^ ]+ .*\(healthy\)$' || true)
if [ -n "$DUPLICATE_AGENTS" ] || [ "$EXPECTED_AGENTS" != "$ACTUAL_AGENTS" ] || [ -n "$NOT_READY" ]; then
  echo "ERROR: agents not healthy after 120s (status set must match the configured agents exactly):"
  echo "expected: $(echo "$EXPECTED_AGENTS" | paste -sd, -)"
  echo "reported: $(echo "$ACTUAL_AGENTS" | grep . | paste -sd, -)"
  if [ -n "$DUPLICATE_AGENTS" ]; then echo "duplicate: $(echo "$DUPLICATE_AGENTS" | paste -sd, -)"; fi
  if [ -n "$NOT_READY" ]; then echo "$NOT_READY"; fi
  "${COMPOSE[@]}" ps $AGENTS
  exit 1
fi

# Prometheus was recreated above, so it must be healthy before the deploy
# is called a success. Without this check, a prometheus that failed to
# come back up would leave the whole stack with no metrics and no
# alerting, and the deploy would still report green.
PROM_STATUS=$("${COMPOSE[@]}" ps --format "{{.Status}}" prometheus 2>/dev/null | head -1)
if ! echo "$PROM_STATUS" | grep -qE '\(healthy\)$'; then
  echo "ERROR: prometheus not healthy after recreate: $PROM_STATUS"
  "${COMPOSE[@]}" ps prometheus
  exit 1
fi

docker image prune -f

HEALTHY=$(echo "$AGENTS" | tr ' ' '\n' | grep . | sort | paste -sd, -)
echo "deploy_services=$HEALTHY"
