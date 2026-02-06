# crypto-agent — Shared Project Rules

This file is the **single source of truth** for all AI agents and human contributors working on this project. Every agent MUST read and follow these rules.

## Project Identity

- **Language**: Python 3.11+
- **Architecture**: Async trading agent with modular pipeline
- **Stack**: aiohttp, TimescaleDB, Prometheus, Grafana, Docker
- **Default mode**: Paper trading (safe). Live trading requires explicit config flags.

## Agent Coordination Protocol

Multiple AI agents (Claude Code, OpenCode, Copilot CLI, Gemini CLI) and a human all edit this codebase. Follow these rules to prevent conflicts:

### Read Before Write

**Always** read a file before editing it. If the file has changed since you last read it, re-read before writing. This is the primary mechanism to prevent overwrites.

### One Agent Per File

Do not edit a file that another agent is actively working on. If you see recent uncommitted changes to a file you need to edit, ask the human or wait.

### Branch Discipline

- `main` is the stable branch. Do not commit broken code to main.
- For non-trivial changes, create a feature branch: `feat/<short-description>`
- Run `pytest` before committing. All tests must pass.

### Commit Conventions

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>

[optional body]

Co-Authored-By: <Agent Name> <agent-email>
```

Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`

Scopes: `ingest`, `features`, `execution`, `risk`, `strategy`, `notifications`, `config`, `tests`

Agent identities for Co-Authored-By:
- Claude Code: `Claude Opus 4.6 <noreply@anthropic.com>`
- OpenCode/Codex: `OpenCode <noreply@openai.com>`
- GitHub Copilot: `GitHub Copilot <noreply@github.com>`
- Gemini CLI: `Gemini CLI <noreply@google.com>`

### Before Every Commit

```bash
pytest                    # All tests must pass
git diff --stat           # Review what you're committing
git add <specific-files>  # Stage only your changes
```

## Module Ownership (Advisory)

| Module | Purpose | Key files |
|--------|---------|-----------|
| `src/ingest/` | Binance WebSocket data ingestion, DB writes | `binance.py`, `db.py`, `models.py` |
| `src/features/` | Technical indicator computation | `computer.py`, `technical.py`, `writer.py` |
| `src/execution/` | Order execution (paper + live) | `executor.py`, `binance_client.py` |
| `src/risk/` | Risk management, circuit breakers | `manager.py` |
| `src/strategy/` | Strategy engine, signal generation | `engine.py`, `base.py`, `signals.py` |
| `src/notifications/` | Telegram alerts | `telegram.py` |
| `src/utils/` | Shared utilities (logging, rate limiting) | `logger.py`, `rate_limiter.py` |
| `config/` | Settings, risk params, infra config | `settings.yaml`, `risk.yaml` |
| `tests/` | Pytest suite (asyncio auto mode) | `conftest.py`, `test_*.py` |

## Python Coding Standards

### Type Hints

- Add type hints to all function signatures (parameters and return types).
- Prefer `str | None` over `Optional[str]`.
- Avoid `Any` unless unavoidable; keep it localized.

### Async Patterns

- Use `async/await` throughout. This is an async-first codebase.
- Reuse `aiohttp.ClientSession` — do not create sessions per-request.
- Ensure background tasks are cancellable and shut down cleanly via `asyncio.CancelledError`.

### Logging & Metrics

- Use `get_logger(...)` from `src/utils/logger.py`. Never use `print()` or bare `logging`.
- Add Prometheus metrics for significant operations via the relevant `*Metrics` class.
- Never log secrets (API keys, DB passwords, tokens).

### Configuration

- Settings live in `config/settings.yaml`. Secrets come from environment variables.
- When adding new settings, update parsing/validation in `src/main.py::load_settings`.

### Error Handling

- Raise specific exceptions with descriptive messages.
- Use `try/except` around I/O boundaries (network, DB), not around pure logic.
- Prefer early returns over deeply nested conditionals.

### Code Style

- Keep changes **minimal and surgical**. Do not refactor unrelated code.
- No unused imports, no dead code, no commented-out blocks.
- Use `snake_case` for functions/variables, `PascalCase` for classes.

## Safety Rules

- **Paper mode is the default.** `mode: paper` and `trading_execution.test_mode: true` in config.
- **Risk checks are mandatory** before any order placement. Use `RiskManager`.
- **Never commit secrets.** `.env` is gitignored. Use `.env.example` for templates.
- **Never bypass risk limits** without explicit human approval and config change.

## How to Run

```bash
# Local dev
cp .env.example .env    # Fill in secrets
docker-compose up --build

# Tests
pytest

# Entrypoint
python -m src.main
```

## Related Files

- `AGENTS.md` — Full list of active agents and coordination details
- `copilot-instructions.md` — GitHub Copilot-specific context
- `codex-instructions.md` — OpenCode/Codex-specific context
- `gemini-instructions.md` — Gemini CLI-specific context
