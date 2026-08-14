# Production Deployment Runbook

**Merging to `main` does not deploy.** Production updates go through the GitHub
`Deploy` workflow (`.github/workflows/deploy.yml`), which is **manual only**:

```bash
git fetch origin main
SHA=$(git rev-parse origin/main)
gh workflow run Deploy --ref main -f deploy_sha="$SHA"
```

`deploy_sha` must be the exact 40-character lowercase SHA of current `origin/main`.
Mismatch or a non-hex SHA fails closed. The job uses `environment: production`.
Do not trigger Deploy from CI. Telegram reports the requested SHA.

This document describes the steps required to deploy the Crypto Trading AI Agent to a production environment.

## Prerequisites

- Docker and Docker Compose installed.
- Access to a Binance account with API keys (Spot trading enabled).
- Outbound network access to Binance API and Telegram Bot API.
- Ports `3000` (Grafana), `9090` (Prometheus) and `8000` (Metrics) should be secured.

## Deployment Steps

### 1. Initial Setup

Clone the repository and prepare the environment:

```bash
git clone https://github.com/your-org/crypto-agent.git
cd crypto-agent
cp .env.example .env
```

### 2. Configure Environment

Edit the `.env` file with real secrets:

- `BINANCE_API_KEY`: Your Binance API key.
- `BINANCE_API_SECRET`: Your Binance API secret.
- `POSTGRES_PASSWORD`: A strong password for TimescaleDB.
- `TELEGRAM_BOT_TOKEN`: Token from @BotFather.
- `TELEGRAM_CHAT_ID`: Your chat ID for alerts.

### 3. Review Configuration

Check `config/settings.yaml` and `config/risk.yaml`:

- Ensure `mode` is set to `paper` first, then `live` only when ready.
- Verify `trading_execution.test_mode` is `true` for paper trading.
- Review risk limits in `config/risk.yaml`.

### 4. Database Migrations

Run initial schema setup:

```bash
# Start the database first
docker-compose up -d timescaledb

# Wait for DB to be ready, then run migrations
# (Schema is currently auto-created by the agent,
# but manual migration via psql is recommended for prod)
docker-compose exec -T timescaledb psql -U trading marketdata < migrations/001_initial_schema.sql
```

### 5. Launch / update production services

Do **not** use `docker-compose.yml` (dev bind-mount). Production is
`docker-compose.prod.yml` via the manual Deploy workflow above, or the
break-glass procedure in `AGENTS.md` (pinned SHA, all `agent_*`, 120s health).

### 6. Verify Deployment

```bash
ssh crypto-agent "cd /opt/crypto-agent && docker compose -f docker-compose.prod.yml ps"
```

- Every `agent_*` status must end in `(healthy)`.
- Metrics: `curl http://localhost:8000/metrics` (per-agent in prod).
- Grafana: `http://localhost:3000`
- Prometheus: `http://localhost:9090`

## Maintenance

### Monitoring

- Monitor the `ingest_errors_total` metric in Grafana.
- Watch for "KILL SWITCH" alerts in Telegram.

### Backups

- Back up the `data/` volume regularly (TimescaleDB data).
- The SQLite fallback file is located at `data/ohlcv.sqlite`.

### Updating

Merging is not a deploy. Use `gh workflow run Deploy --ref main -f deploy_sha=…`
with the current `origin/main` SHA. See the top of this file and `AGENTS.md`.

## Troubleshooting

### Connectivity Issues

- If TimescaleDB is unreachable, the agent will fall back to SQLite automatically.
- Check Binance API connectivity: `scripts/test_binance.py`.

### Resetting Risk Metrics

The agent resets daily metrics at midnight UTC. If you need to force a reset, restart the agent container.
