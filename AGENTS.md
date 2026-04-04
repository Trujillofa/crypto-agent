# Active Agents — crypto-agent

This document lists all AI agents that work on this codebase and the coordination protocol they must follow.

## Source of Truth

All agents MUST read and follow **`CLAUDE.md`** in this project root. It contains:
- Agent coordination protocol (read-before-write, branch discipline, commit conventions)
- Module ownership table
- Python coding standards
- Safety rules

## Active Agents

| Agent | CLI Tool | Instruction File | Co-Authored-By |
|-------|----------|-------------------|----------------|
| Claude Code | `claude` | `CLAUDE.md` (native) | `Claude Opus 4.6 <noreply@anthropic.com>` |
| OpenCode / Codex | `opencode` | `codex-instructions.md` | `OpenCode <noreply@openai.com>` |
| GitHub Copilot | `gh copilot` | `copilot-instructions.md` | `GitHub Copilot <noreply@github.com>` |
| Gemini CLI | `gemini` | `gemini-instructions.md` | `Gemini CLI <noreply@google.com>` |
| Sisyphus | `sisyphus` | `sisyphus-instructions.md` | `Sisyphus <clio-agent@sisyphuslabs.ai>` |
| Human | editor | `CLAUDE.md` | (normal git identity) |

## Coordination Summary

1. **Read before write** — Always read a file before editing it.
2. **One agent per file** — Don't edit files with recent uncommitted changes from another agent.
3. **Test before commit** — Run `pytest` and ensure all tests pass.
4. **Conventional commits** — Use the format in `CLAUDE.md` with your `Co-Authored-By`.
5. **Branch for non-trivial work** — Use `feat/<description>` branches off `main`.
6. **No secrets in code** — Use `.env` for secrets, `.env.example` for templates.
7. **5-Step Engineering Framework** — Challenge requirements, delete first, simplify second, accelerate third, automate last.

## Environments

| Environment | Location | Purpose |
|-------------|----------|---------|
| **Development** | Local (`/home/emilio/crypto-trading-agent`) | Development, testing, experimentation |
| **Production** | Server (`ssh crypto-agent`) | Live trading, real money |

**IMPORTANT**: Always check the crypto-agent server for production state. Local is for development only.

### Server Access

- **SSH alias**: `ssh crypto-agent` (configured in `~/.ssh/config`)
- **Deploy directory**: `/opt/crypto-agent`
- **Current branch**: `main`
- **Fallback SSH**: `ssh root@46.225.119.221`

### Deployment Commands

```bash
# Deploy to production
ssh crypto-agent "cd /opt/crypto-agent && git pull && docker compose up -d --build agent"

# View logs
ssh crypto-agent "cd /opt/crypto-agent && docker compose logs agent --tail=100 --no-log-prefix"

# Check status
ssh crypto-agent "cd /opt/crypto-agent && docker compose ps"
```

---

## Build / Lint / Test Commands

### Running Tests

```bash
pytest                          # All tests (from project root)
pytest tests/ -v --tb=short     # Verbose with short tracebacks
pytest tests/test_foo.py        # Single test file
pytest -k "test_name"          # Run tests matching pattern
pytest --tb=long               # Full tracebacks for debugging
pytest -v                      # Verbose output
```

### Running a Single Test

```bash
# By file + test name
pytest tests/test_foo.py::test_bar -v

# Debugging a failing test
pytest tests/test_foo.py -v --tb=long
```

### Linting & Formatting

```bash
# Format code (auto-fixes)
ruff check --fix src/ tests/
ruff format src/ tests/

# Check formatting (CI style)
ruff format --check src/ tests/
ruff check src/ tests/

# Black check
black --check src/ tests/

# Type checking
mypy src/
```

### Pre-commit Hooks

Hooks run automatically on commit. To run manually:

```bash
# Install hooks (first time only)
pre-commit install

# Run all hooks manually
pre-commit run --all-files

# Run specific hook
pre-commit run ruff --all-files
```

### Project Entry Points

```bash
# Local dev environment
cp .env.example .env    # Fill in secrets first
docker-compose up --build

# Run main app
python -m src.main

# Database migrations
python scripts/migrate.py

# Run backtest
python scripts/run_backtest.py

# Smoke test
python scripts/smoke_test.py
```

---

## Code Style Guidelines

### Python Version

- **Python 3.11+** — This is an async-first codebase. Use `async/await` throughout.

### Type Hints

- Add type hints to **all** function signatures (parameters and return types).
- Prefer `str | None` over `Optional[str]`.
- Avoid `Any` unless unavoidable; keep it localized.

### Imports & Formatting

- **Formatter**: `black` (line-length: 100, target Python 3.11+)
- **Linter**: `ruff` (pycodestyle, pyflakes, isort, bugbear, comprehensions, pyupgrade)
- **Type checker**: `mypy` (permissive mode, `ignore_missing_imports = true`)
- Line length: **100 characters**
- Use `snake_case` for functions/variables, `PascalCase` for classes.
- `from __future__ import annotations` at the top of every module.
- Import order: stdlib → third-party → first-party, with `isort` enforcing this.

### Async Patterns

- Reuse `aiohttp.ClientSession` — **do not create sessions per-request**.
- Ensure background tasks are cancellable and shut down cleanly via `asyncio.CancelledError`.

### Logging & Error Handling

- **Use `get_logger(...)` from `src/utils/logger.py`**. Never use `print()` or bare `logging`.
- Add Prometheus metrics for significant operations via relevant `*Metrics` class.
- **Never log secrets** (API keys, DB passwords, tokens).
- Raise specific exceptions with descriptive messages.
- Use `try/except` around I/O boundaries (network, DB), not around pure logic.
- Prefer early returns over deeply nested conditionals.

### Code Quality Rules

- **Keep changes minimal and surgical** — do not refactor unrelated code.
- No unused imports, no dead code, no commented-out blocks.
- Risk checks are **mandatory** before any order placement. Use `RiskManager`.
- Paper mode is the default (`mode: paper`, `trading_execution.test_mode: true` in config).

### Configuration

- Settings live in `config/settings.yaml`. Secrets come from environment variables.
- When adding new settings, update parsing/validation in `src/main.py::load_settings`.
- Use `.env` for secrets (gitignored). Use `.env.example` for templates.

### Test Patterns

- Use **pytest** with **asyncio auto mode** (configured in `pyproject.toml`).
- Name tests: `test_<action>_<expected_result>`.
- Always mock external APIs (Binance, database) — use `unittest.mock.AsyncMock`.
- Use existing fixtures from `tests/conftest.py`.

Example:

```python
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_feature_xyz():
    """Description of what this test verifies."""
    with patch("src.execution.binance_client.BinanceClient") as mock:
        mock_instance = AsyncMock()
        mock_instance.place_order = AsyncMock(return_value={"orderId": 123})
        mock.return_value = mock_instance

        # Act
        result = await executor.execute_order(...)

        # Assert
        assert result["orderId"] == 123
```

---

## CI/CD Pipeline

GitHub Actions runs on push to `main`/`develop` and on PRs to `main`:

| Job | Command |
|-----|---------|
| **test** | `pytest tests/ -v --tb=short` (Python 3.11 + 3.12 matrix) |
| **lint** | `black --check` + `ruff check` (Python 3.12) |
| **docker** | Build image and verify imports |

---

## Key File Locations

| Need | Path |
|------|------|
| Main app | `src/main.py` |
| Entry point scripts | `scripts/run_backtest.py`, `scripts/smoke_test.py` |
| Trading settings | `config/settings.yaml` |
| Risk parameters | `config/risk.yaml` |
| Strategy base class | `src/strategy/base.py` |
| Risk manager | `src/risk/manager.py` |
| Test fixtures | `tests/conftest.py` |
| Pre-commit hooks | `.pre-commit-config.yaml` |
| Lint/format config | `pyproject.toml` |
