# Production Deployment Runbook

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

### 5. Launch Services

Deploy using the production compose file (if created) or the default one:

```bash
docker-compose up -d --build
```

### 6. Verify Deployment

- Check agent logs: `docker-compose logs -f agent`
- Verify metrics: `curl http://localhost:8000/metrics`
- Check Grafana: `http://localhost:3000` (Default: admin/admin)
- Check Prometheus: `http://localhost:9090`

## Maintenance

### Monitoring

- Monitor the `ingest_errors_total` metric in Grafana.
- Watch for "KILL SWITCH" alerts in Telegram.

### Backups

- Back up the `data/` volume regularly (TimescaleDB data).
- The SQLite fallback file is located at `data/ohlcv.sqlite`.

### Updating

```bash
git pull
docker-compose up -d --build
```

## Troubleshooting

### Connectivity Issues

- If TimescaleDB is unreachable, the agent will fall back to SQLite automatically.
- Check Binance API connectivity: `scripts/test_binance.py`.

### Resetting Risk Metrics

The agent resets daily metrics at midnight UTC. If you need to force a reset, restart the agent container.
