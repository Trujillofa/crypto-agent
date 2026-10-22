#!/usr/bin/env bash
# Coordinated rebuild of every agent_* service. Runs after align_prod_checkout.sh.
set -euo pipefail

ROOT="${DEPLOY_ROOT:-/opt/crypto-agent}"
cd "$ROOT"

AGENTS=$(docker compose -f docker-compose.prod.yml config --services | grep '^agent_' | sort | tr '\n' ' ')
if [ -z "$AGENTS" ]; then
  echo "ERROR: no agent_* services found in docker-compose.prod.yml"
  exit 1
fi
echo "Deploying agents: $AGENTS"

docker compose -f docker-compose.prod.yml build $AGENTS
docker compose -f docker-compose.prod.yml up -d --remove-orphans $AGENTS

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
if docker compose -f docker-compose.prod.yml ps --services --status running \
     | grep -qx prometheus; then
  echo "Recreating prometheus to pick up config/prometheus-alerts.yml"
  docker compose -f docker-compose.prod.yml up -d --force-recreate prometheus
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
NOT_READY=$(docker compose -f docker-compose.prod.yml ps --format "{{.Service}} {{.Status}}" $AGENTS | grep -vE '\(healthy\)$' || true)
if [ -n "$NOT_READY" ]; then
  echo "ERROR: agents not healthy after 120s:"
  echo "$NOT_READY"
  docker compose -f docker-compose.prod.yml ps $AGENTS
  exit 1
fi

# Prometheus was recreated above, so it must be healthy before the deploy
# is called a success. Without this check, a prometheus that failed to
# come back up would leave the whole stack with no metrics and no
# alerting, and the deploy would still report green.
PROM_STATUS=$(docker compose -f docker-compose.prod.yml ps --format "{{.Status}}" prometheus 2>/dev/null | head -1)
if ! echo "$PROM_STATUS" | grep -qE '\(healthy\)$'; then
  echo "ERROR: prometheus not healthy after recreate: $PROM_STATUS"
  docker compose -f docker-compose.prod.yml ps prometheus
  exit 1
fi

docker image prune -f

HEALTHY=$(echo "$AGENTS" | tr ' ' '\n' | grep . | sort | paste -sd, -)
echo "deploy_services=$HEALTHY"
