# Copilot Agent Profile: crypto-agent

Use this profile when you want Copilot to develop, test, refactor, or debug this project.

## Purpose

Build and maintain a safe, paper-first crypto trading agent with reliable data ingestion, indicator computation, and a strategy-to-execution bridge.

## Scope

**In scope**
- Strategy engine, execution flow, risk gating
- Ingestion + indicators + storage
- Tests (unit + integration)
- Documentation related to behavior changes

**Out of scope**
- Live trading enablement without explicit user request
- Secrets management beyond `.env.example`
- Backtesting and performance analytics (unless requested)

## Guardrails

- Keep paper mode as the default (`mode: paper`, `trading_execution.enabled: false`).
- Enforce RiskManager checks before any order placement.
- Read files before editing and check `git status` for conflicts.
- One agent per file; do not edit files with recent uncommitted changes from others.
- No secrets in code or docs; use `.env` and `.env.example`.
- Use `get_logger(...)` for logs; no `print()`.
- Prefer async patterns; avoid per-request sessions.
- No broad try/except blocks that hide errors.

## Development workflow

1. Read the relevant files and confirm current behavior.
2. Make minimal, surgical changes aligned with existing patterns.
3. Add or update tests for any behavior change.
4. Run the smallest relevant `pytest` subset first; then full `pytest` when ready.

## Testing commands

Run:
```
pytest
```

For targeted tests:
```
pytest tests/test_<area>.py -v
```

## Debugging checklist

- Confirm `.env` is populated and not committed.
- Verify TimescaleDB connectivity and schema availability.
- Validate indicator freshness and timeframe alignment.
- Check Prometheus metrics for ingest/indicator/execution errors.
- Reproduce with a single symbol to reduce noise.

## Refactoring rules

- Keep changes small and localized.
- Preserve existing module boundaries.
- Add type hints to new or changed functions.
- Avoid introducing `Any` unless unavoidable.

## Useful commands

```
docker-compose up --build
python -m src.main
```
