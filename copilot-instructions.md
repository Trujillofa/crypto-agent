# Copilot instructions (crypto-agent)

**Read `CLAUDE.md` first.** It is the shared source of truth for all agents on this project.

## Your identity

When committing, use this Co-Authored-By line:
```
Co-Authored-By: GitHub Copilot <noreply@github.com>
```

## Project overview

This is a **Python 3.11** async crypto trading agent:
- Async Binance market-data ingestion (`src/ingest/`)
- TimescaleDB persistence (`src/ingest/db.py`, `migrations/`)
- Technical indicator computation (`src/features/`)
- Risk gating + (paper-first) trading execution (`src/risk/`, `src/execution/`)
- Strategy engine with signal generation (`src/strategy/`)
- Prometheus metrics + structured logging (`src/*/metrics.py`, `src/utils/logger.py`)

Default intent is **safe / paper-first** operation. Do not introduce live-trading behavior unless explicitly requested.

## How to run

- Local stack: `cp .env.example .env` then `docker-compose up --build`
- Entrypoint: `python -m src.main`
- Config: `config/settings.yaml` (pairs, timeframe, DB, prometheus) and `config/risk.yaml` (limits/circuit breakers)

## Tests

- Run: `pytest`
- Prefer adding/adjusting tests in `tests/` when changing behavior.

## Before editing any file

1. Read the file first to check for recent changes.
2. Check `git status` to see if another agent has uncommitted edits.
3. Run `pytest` before committing.

## Coordination protocol

See `CLAUDE.md` for the full protocol. Key points:
- Conventional commits: `<type>(<scope>): <description>`
- Branch for non-trivial work: `feat/<short-description>`
- Stage specific files, not `git add -A`.
- All tests must pass before committing.
- No `TODO`/`FIXME` comments — `ruff check` fails on them. Track deferred work in `docs/specs/` instead.

## Coding conventions

See `CLAUDE.md` for full standards. Summary:
- Keep changes **minimal and surgical**; avoid broad refactors unless asked.
- Add type hints for new functions/classes. Avoid `Any`.
- Use `aiohttp` session reuse/pooling patterns (see `BinanceIngestor`).
- Ensure background tasks are cancellable and shut down cleanly.
- Use `get_logger(...)` from `src/utils/logger.py`. No `print()`.
- Add Prometheus metrics for significant operations.
- Never log secrets.

## Safety / trading rules

- Keep **paper mode** the default.
- Enforce risk checks via `RiskManager` before any trading actions.
- If a change could impact order placement, require explicit config flags and clear logging.
