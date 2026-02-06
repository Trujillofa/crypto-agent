# Copilot instructions (crypto-agent)

## Project overview
This repo is a **Python 3.11** crypto trading agent foundation:
- Async Binance market-data ingestion (`src/ingest/`)
- TimescaleDB persistence (`src/ingest/db.py`, `migrations/`)
- Technical indicator computation (`src/features/`)
- Risk gating + (paper-first) trading execution (`src/risk/`, `src/execution/`)
- Prometheus metrics + structured logging (`src/*/metrics.py`, `src/utils/logger.py`)

Default intent is **safe / paper-first** operation. Do not introduce live-trading behavior unless explicitly requested.

## How to run
- Local stack (recommended):
  - `cp .env.example .env` (fill secrets; never commit `.env`)
  - `docker-compose up --build`
- Entrypoint: `python -m src.main`
- Config files:
  - `config/settings.yaml` (pairs, timeframe, DB, prometheus port, trading_execution)
  - `config/risk.yaml` (limits/circuit breakers/kill switch)

## Tests
- Run: `pytest`
- Prefer adding/adjusting tests in `tests/` when changing behavior.

## Coding conventions (follow these)
- Keep changes **minimal and surgical**; avoid broad refactors unless asked.
- Prefer **typed**, explicit code:
  - Add type hints for new functions/classes.
  - Avoid `Any` unless unavoidable; if used, keep it localized.
- Async I/O:
  - Use `aiohttp` session reuse/pooling patterns (see `BinanceIngestor`).
  - Ensure background tasks are cancellable and shut down cleanly.
- Logging/metrics:
  - Use `get_logger(...)` from `src/utils/logger.py`.
  - When adding significant operations, add Prometheus metrics in the relevant `*Metrics` class.
  - Avoid logging secrets (API keys, DB passwords, tokens).
- Configuration:
  - Prefer `config/settings.yaml` + environment variables for secrets.
  - If adding new settings, update parsing/validation in `src/main.py::load_settings`.

## Safety / trading rules
- Keep **paper mode** the default (`mode: paper`, `trading_execution.test_mode: true`).
- Enforce risk checks via `RiskManager` before any trading actions.
- If a change could impact order placement, require explicit config flags and clear logging.

## Repo layout hints
- `src/ingest/`: market data models, Binance client, DB writer, ingestion metrics
- `src/features/`: indicator computation + writing + metrics
- `src/execution/`: trading executor (test-mode by default)
- `src/notifications/`: Telegram notifier/rate limiting
- `tests/`: pytest suite (asyncio mode enabled via `pyproject.toml`)
