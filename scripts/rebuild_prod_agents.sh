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

sleep 120
NOT_READY=$(docker compose -f docker-compose.prod.yml ps --format "{{.Service}} {{.Status}}" $AGENTS | grep -vE '\(healthy\)$' || true)
if [ -n "$NOT_READY" ]; then
  echo "ERROR: agents not healthy after 120s:"
  echo "$NOT_READY"
  docker compose -f docker-compose.prod.yml ps $AGENTS
  exit 1
fi

docker image prune -f

HEALTHY=$(echo "$AGENTS" | tr ' ' '\n' | grep . | sort | paste -sd, -)
echo "deploy_services=$HEALTHY"
