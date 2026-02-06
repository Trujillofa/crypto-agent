# Crypto Trading AI/ML Agent (Phase 1 Foundation)

This project implements the Phase 1 foundation of a Binance Futures trading agent based on the provided reference specs. It focuses on reliable market data ingestion, TimescaleDB persistence, and technical indicator computation for downstream ML/RL pipelines.

## What’s Included

- Docker Compose stack (TimescaleDB, Prometheus, Grafana, agent)
- Binance Futures OHLCV ingestion via REST polling
- TimescaleDB hypertable schema for OHLCV
- Prometheus metrics and structured JSON logging
- Technical indicator computation (RSI, MACD, Bollinger Bands, ATR)

## Quick Start

```bash
cd /home/yderf/TRADING/crypto-agent
cp .env.example .env
## Update .env with real secrets before running
docker-compose up --build
```

Prometheus: http://localhost:9090
Grafana: http://localhost:3000

## Metrics

- `ingest_messages_total{symbol,stream}`
- `ingest_insert_latency_seconds`
- `ingest_last_open_time{symbol}`

## Training Utilities

```bash
python scripts/train.py --input data/ohlcv.csv --output data/indicators.csv
```
