# AGENTS.md — crypto-trading-agent

**Read `CLAUDE.md` first.** It is the canonical source for coding standards, module ownership, commit conventions, and safety rules. This file covers what CLAUDE.md doesn't: repo-specific gotchas, exact commands, and architecture surprises an agent would likely miss.

## Build & Test (exact commands)

```bash
# Install (uses uv, not pip)
uv sync --all-extras --dev

# Run tests
uv run pytest -v                              # all tests
uv run pytest tests/test_foo.py -v            # single file
uv run pytest tests/test_foo.py::test_bar -v  # single test
uv run pytest -k "pattern" -v                 # by pattern

# Lint & format (CI runs exactly these)
uv run ruff check .                           # lint
uv run ruff format --check .                  # format check
uv run ruff check --fix . && uv run ruff format .  # auto-fix both

# Type check (NOT in CI, but available)
uv run mypy src/
```

**CI does NOT run `black` or `mypy`.** It runs `ruff check` + `ruff format --check` + `uv run pytest -v`. The `black` config in `pyproject.toml` is for local use only. `ruff format` is the canonical formatter.

**Python version**: 3.11 (pinned in `.python-version`). CI uses `uv` for all tooling — no `pip install` anywhere in CI.

## Production Deploy (critical differences from dev)

Dev uses `docker-compose.yml` (bind-mounts `./:/app`, so live code changes take effect instantly). **Production uses `docker-compose.prod.yml`** (`Dockerfile.prod`), which bakes `src/` and `config/` into the image at build time. Code and config changes on the server do NOT affect the running agent until you rebuild.

```bash
# Production deploy (correct)
ssh crypto-agent "cd /opt/crypto-agent && git pull"
ssh crypto-agent "cd /opt/crypto-agent && docker compose -f docker-compose.prod.yml build agent"
ssh crypto-agent "cd /opt/crypto-agent && docker compose -f docker-compose.prod.yml up -d agent"

# WRONG — this is the dev command, NOT production:
ssh crypto-agent "cd /opt/crypto-agent && git pull && docker compose up -d --build agent"

# Check logs
ssh crypto-agent "cd /opt/crypto-agent && docker compose -f docker-compose.prod.yml logs agent --tail=100 --no-log-prefix"
```

## Architecture Surprises

### Multi-agent runtime
The docker-compose runs 7+ agent containers simultaneously, each with its own config:
- Agent identity is set via `AGENT_ID` env var (e.g., `default`, `agent2`, `btc-4h`, `sol-trend-pullback-sparse`)
- Config is selected via `SETTINGS_PATH` env var (e.g., `config/settings.btc-4h.yaml`)
- All agents share one TimescaleDB instance, isolated by `agent_id` columns (see `migrations/005_add_agent_isolation.sql`)

### Strategy registration is not automatic
Strategies must be: (1) created in `src/strategy/`, (2) exported in `src/strategy/__init__.py`, and (3) listed in `config/settings.yaml` under `strategy.strategies`. Missing any step = silent failure.

### Trading mode is layered, not a single switch
The mode depends on multiple config flags interacting:
- `trading_execution.enabled` — whether any execution happens
- `trading_execution.test_mode` — testnet vs live
- `futures.enabled` — spot vs futures routing
- Individual strategy configs can override routing

**Paper (safe default):** `trading_execution.enabled: false` — no real orders. **Testnet:** `enabled: true, test_mode: true` — Binance testnet. **Live (real money):** `enabled: true, test_mode: false`.

### `src/chub/` — Context Hub
CLI tool for LLM-optimized doc search. Uses Click. Not part of the trading pipeline.

### Migrations start at `000_`
Migration numbering: `000_migrations_tracking.sql` through `007_add_regime_features.sql`. The `000_` tracking table was added after the initial schema.

## Testing Gotchas

- **65 test files** in `tests/`. Pytest asyncio auto mode is configured in `pyproject.toml` — `@pytest.mark.asyncio` works but is redundant (auto mode applies it automatically).
- Fixtures live in `tests/conftest.py`. Most tests need `mock_settings` or `clean_db`.
- Always mock external APIs (Binance, DB). Use `unittest.mock.AsyncMock`.
- Test naming: test_&lt;action&gt;_&lt;expected_result&gt; pattern. Run a single test with: pytest tests/test_foo.py::test_bar -v