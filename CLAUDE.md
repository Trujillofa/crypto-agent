# crypto-agent — Shared Project Rules

This file is the **single source of truth** for all AI agents and human contributors working on this project. Every agent MUST read and follow these rules.

## Project Identity

- **Language**: Python 3.11
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

Scopes: `ingest`, `features`, `execution`, `risk`, `strategy`, `notifications`, `config`, `tests`, `backtest`, `portfolio`, `overseer`, `db`, `core`

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
| `src/ingest/` | Binance data ingestion (REST + WebSocket), DB writes | `binance.py`, `websocket.py`, `db.py`, `models.py` |
| `src/features/` | Technical indicator computation & storage | `computer.py`, `technical.py`, `writer.py`, `reader.py` |
| `src/execution/` | Order execution (paper, spot, futures) | `executor.py`, `binance_client.py`, `futures_executor.py`, `futures_client.py`, `paper_executor.py`, `guards.py`, `staged.py` |
| `src/risk/` | Risk management, circuit breakers, guard protocol | `manager.py`, `guards.py` |
| `src/strategy/` | Strategy engine, 20+ strategy implementations | `engine.py`, `base.py`, `signals.py`, `aggregator.py`, `lifecycle.py` |
| `src/notifications/` | Telegram alerts | `telegram.py` |
| `src/portfolio/` | Position tracking, PnL calculation | `manager.py`, `models.py` |
| `src/overseer/` | AI-powered strategy oversight (LLM integration) | `agent.py`, `xai.py`, `prompts.py` |
| `src/backtest/` | Backtesting engine & experiment automation | `engine.py`, `experiment_autopilot.py` |
| `src/db/` | Async TimescaleDB connection pool | `pool.py` |
| `src/core/` | Event logging & audit trail | `event_log.py` |
| `src/rl/` | Reinforcement learning agent (research) | `agent.py` |
| `src/utils/` | Logging, rate limiting, diagnostics | `logger.py`, `rate_limiter.py`, `config_doctor.py`, `production_drift_sentinel.py` |
| `config/` | Settings, risk params, infra config | `settings.yaml`, `risk.yaml` |
| `tests/` | Pytest suite (asyncio auto mode, 60 test files) | `conftest.py`, `test_*.py` |
| `scripts/` | CLI tools, backtests, research, diagnostics | `run_backtest.py`, `smoke_test.py`, `migrate.py`, etc. (RBI loop: rbi_loop_guard/runner/from_manifest/batch/report + run_rbi_loop_batch.sh; see docs/RBI_AUTORESEARCH_LOOP.md) |
| `migrations/` | TimescaleDB schema migrations | `001_initial_schema.sql` through `007_add_regime_features.sql` |

## 5-Step Engineering Framework (MANDATORY)

Every agent MUST apply this framework when designing, reviewing, or modifying any component, feature, or process in this codebase. Run through all 5 steps in order before implementing.

### Step 1: Make Requirements Less Dumb

Challenge every requirement before accepting it. Ask:

- **Who specified this?** A human with domain knowledge, or an AI making assumptions?
- **Is it based on first principles or analogy?** "Other trading bots do X" is not a valid reason. "The Binance API requires X" is.
- **What assumptions are embedded?** Surface them explicitly. If an assumption can't be validated, flag it.
- **Is this requirement actually necessary?** Delete requirements that exist out of caution rather than evidence. Example: don't add a config option "just in case someone needs it" — add it when someone actually needs it.

### Step 2: Delete Parts or Processes

Before adding anything, look for what can be removed entirely:

- **Dead code**: Unused imports, unreachable branches, commented-out blocks — delete them, don't preserve them.
- **Redundant layers**: If a wrapper adds no logic, remove it. If an abstraction has one implementation, inline it.
- **"Just in case" code**: Feature flags nobody toggles, fallback paths never triggered, config options nobody changes.
- **Process overhead**: If a step in the pipeline (build, test, deploy) doesn't catch real bugs or add real value, question it.
- **Litmus test**: If you haven't added back something you previously deleted, you're not deleting enough.

### Step 3: Simplify or Optimize

Only after deletion, streamline what remains:

- **Never optimize something that shouldn't exist.** If Step 2 should have removed it, go back.
- **Prefer holistic over local optimization.** Don't micro-optimize one function if the bottleneck is architecture.
- **Reduce moving parts**: Fewer dependencies, fewer config knobs, fewer layers of indirection.
- **Make the common path obvious**: The happy path should read top-to-bottom without jumping between files.

### Step 4: Accelerate Cycle Time

Speed up iteration loops, but only after simplification:

- **Shorten feedback loops**: Tests should run fast. Deployments should be quick. Logs should be immediately useful.
- **Eliminate bottlenecks**: If a test takes 30s because it polls an API, mock it. If Docker builds are slow, cache layers.
- **Are we moving fast in the right direction?** Speed without clarity is waste. Validate direction before accelerating.

### Step 5: Automate

Automate only after the process is refined and proven:

- **Don't automate a broken process.** Fix it first (Steps 1-4), then automate.
- **Will automation lock in inefficiencies?** If the process might change soon, defer automation.
- **Automate the boring, error-prone parts**: DB migrations, linting, test runs, deployment — not architectural decisions.

### Applying the Framework

| Context | How to Apply |
|---------|-------------|
| **New feature** | Steps 1-2 before writing any code. Do we actually need this? Can we achieve it by deleting something instead? |
| **Bug fix** | Step 1: Why does this bug exist? Is the requirement wrong? Step 2: Can we delete the code path entirely? |
| **Code review** | Steps 2-3: What can be removed? What's unnecessarily complex? |
| **Plan review** | All 5 steps: Challenge requirements, delete unnecessary tasks, simplify the remaining plan, then accelerate. |
| **Refactor** | Steps 2-3 only. Delete first, simplify second. Never refactor something that should be deleted. |

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
- Line length: 100 characters (configured in `pyproject.toml`).

### Linting & Formatting

- **Formatter**: `ruff format` (canonical)
- **Linter**: `ruff check` (pycodestyle, pyflakes, isort, bugbear, comprehensions, pyupgrade)
- **Type checker**: `mypy` (optional, not part of CI)
- **Pre-commit hooks**: ruff (with auto-fix), ruff-format, trailing-whitespace, end-of-file-fixer, check-yaml, check-json, check-added-large-files, debug-statements, check-merge-conflict
- CI runs `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pytest -v`.

## Safety Rules

- **Paper mode is the default.** `mode: paper` and `trading_execution.test_mode: true` in config.
- **Risk checks are mandatory** before any order placement. Use `RiskManager`.
- **Never commit secrets.** `.env` is gitignored. Use `.env.example` for templates.
- **Never bypass risk limits** without explicit human approval and config change.

## CI/CD Pipeline

GitHub Actions (`.github/workflows/ci.yml`) runs on push to `main`/`develop` and on PRs to `main`:

| Job | What it does |
|-----|-------------|
| **test** | `uv run pytest -v` on Python from `.python-version` (3.11) |
| **lint** | `uv run ruff check .` + `uv run ruff format --check .` |
| **docker** | Build image and verify imports |

Smoke test runs on push (not PRs) using Binance API secrets.

## How to Run

```bash
# Local dev
cp .env.example .env    # Fill in secrets
docker-compose up --build

# Tests
uv run pytest -v

# Linting
uv run ruff check .
uv run ruff format --check .

# Entrypoint
python -m src.main

# Database migrations
python scripts/migrate.py
```

## Test Writing Patterns

This codebase uses **pytest** with **asyncio auto mode** (configured in `pyproject.toml`).

### Basic Test Structure

```python
import pytest
from src.module import ClassToTest

@pytest.mark.asyncio
async def test_feature_xyz():
    """Description of what this test verifies."""
    # Arrange
    subject = ClassToTest(param="value")

    # Act
    result = await subject.method()

    # Assert
    assert result == expected
```

### Mocking External Dependencies

```python
from unittest.mock import AsyncMock, patch, MagicMock

@pytest.mark.asyncio
async def test_with_binance_api():
    with patch("src.execution.binance_client.BinanceClient") as mock:
        mock_instance = AsyncMock()
        mock_instance.place_order = AsyncMock(return_value={"orderId": 123, "status": "FILLED"})
        mock_instance.get_balance = AsyncMock(return_value={"USDT": "1000.0"})
        mock.return_value = mock_instance

        # Your test logic
        result = await executor.execute_order(...)
        assert result["orderId"] == 123
```

### Using Fixtures

```python
# From tests/conftest.py
@pytest.fixture
def test_settings():
    return Settings(
        trading_execution=TradingExecutionConfig(
            enabled=True,
            test_mode=True,
            order_size_usdt=100.0,
        ),
        # ...
    )

@pytest.mark.asyncio
async def test_something(test_settings):
    # Use fixture
    manager = RiskManager(test_settings)
```

### Testing Strategies

```python
@pytest.mark.asyncio
async def test_strategy_signal_generation():
    # Create mock indicators
    indicators = {
        "close": 50000.0,
        "ema_short": 50100.0,
        "ema_long": 49500.0,
        "rsi": 65.0,
    }

    strategy = MyStrategy(config={})
    signal = await strategy.evaluate("BTCUSDT", indicators)

    assert signal is not None
    assert signal.type == SignalType.BUY
```

### Running Tests

```bash
pytest                          # All tests
pytest tests/test_foo.py        # Single file
pytest -k "test_name"          # By pattern match
pytest --tb=short              # Shorter tracebacks
pytest -v                       # Verbose output
```

## Common Workflows

### Add a New Strategy

1. Create `src/strategy/my_strategy.py` (strategies live directly in `src/strategy/`)

### Research with the RBI Autoresearch Loop (supervised control plane)

> **Status 2026-06-19: program consolidated — structural-probe surface CLOSED.** No closed
> lane revives at corrected costs; the SOL overlay cannot forward-validate; no current
> vehicle accumulates trades. Do not open another OHLCV-structure probe on majors. The RBI
> tooling below stays valid for a future **data-first** primitive (news/event calendar),
> but only open one deliberately after a cheap-probe `HAS_PULSE`. See
> `docs/reports/research-consolidation-2026-06-19.md`.

The RBI loop (Research → Backtest → Implement) is the enforced operating system for new signal discovery:
- Start with a lane brief (Gate 0) + cheap probe script (Gate 1) that must return `HAS_PULSE`.
- Use a manifest in `config/autoresearch/rbi_loop.<lane>.yaml` (copy from the example).
- Drive with `scripts/rbi_loop_from_manifest.py` (or batch) — **dry by default**; only add `--execute` after reviewing the guard decision.
- Persist `research/rbi_loop/<lane>/decision.json`; render reviewable reports with `rbi_loop_report.py`.
- Update `docs/reports/autoresearch-candidate-ledger.md` only for final promotion/reject decisions.
- See full runbook: `docs/RBI_AUTORESEARCH_LOOP.md`; terminal program state in `docs/reports/research-consolidation-2026-06-19.md`; prior stop rules in `docs/reports/research-reset-2026-06-06.md`; lane artifacts under `research/rbi_loop/`.

Hard rules: cheap-probe `HAS_PULSE` before any autoresearch or strategy code; `--execute` is an explicit human gate; no production deploys or live risk via automation.

1. Read the reset + ledger.
2. Write brief + implement/run cheap probe (read-only).
3. Create manifest, run guard dry, then `rbi_loop_from_manifest.py` (review output first).
4. Only `--execute` the allowed step; re-run guard after each artifact.
5. Produce report + (if final) fold into ledger.
2. Inherit from `BaseStrategy` (from `src/strategy/base.py`)
3. Implement `evaluate()` returning `Signal | None`
4. Export in `src/strategy/__init__.py`
5. Add to `config/settings.yaml` under `strategy.strategies`
6. Write tests in `tests/test_my_strategy.py`

Existing strategies for reference: `simple_ma.py`, `rsi_reversal.py`, `bollinger_strategy.py`, `macd_strategy.py`, `momentum_strategy.py`, `cci_strategy.py`, `vwap_strategy.py`, `mean_reversion.py`, `trend_pullback.py`, `breakout_retest.py`, `sentiment_mean_reversion.py`, `macro_volatility.py`. Multi-timeframe: `mtf_template.py`, `mtf_breakout.py`, `mtf_continuation.py`, `regime_router.py`.

### Add Configuration Option

1. Add to `config/settings.yaml` with comment
2. Update dataclass in `src/main.py` or relevant module
3. Add validation if needed
4. Document in this file's config reference

### Debug a Failing Test

```bash
# Run with full output
pytest tests/test_foo.py -v --tb=long

# Run single test
pytest tests/test_foo.py::test_name -v

# Check if it's a pre-existing failure
git stash
pytest
# If passes, your changes broke it
```

### Deploy to Production

**IMPORTANT**: The server uses `docker-compose.prod.yml` (NOT `docker-compose.yml`).
`docker-compose.prod.yml` bakes `src/` and `config/` into the image at build time — no
bind mount. This means editing files on the server does NOT affect the running agent.
Config and code changes only take effect after an explicit build + up cycle.

```bash
# On local machine
git push origin main

# On server
ssh crypto-agent "cd /opt/crypto-agent && git pull && docker compose -f docker-compose.prod.yml build agent && docker compose -f docker-compose.prod.yml up -d agent"

# Verify
ssh crypto-agent "docker compose -f docker-compose.prod.yml ps"
ssh crypto-agent "docker compose -f docker-compose.prod.yml logs agent --tail=20"
```

**Why prod compose?**
- No `./:/app` bind mount — live code cannot be mutated by agents working in the repo
- Config is frozen at build time — other agents doing backtest work cannot accidentally
  change the running agent's trading pairs, timeframe, or strategy parameters
- Explicit rebuild required to change behavior — acts as a deploy gate

### Add New Test

1. Follow naming: `tests/test_<module>.py`
2. Use existing fixtures from `conftest.py`
3. Mock external APIs (Binance, database)
4. Use descriptive test names: `test_<action>_<expected_result>`
5. Add docstrings explaining what is being tested

## File Location Index

| Need | Path |
|------|------|
| **Entry Points** | |
| Main app | `src/main.py` |
| CLI scripts | `scripts/run_backtest.py`, `scripts/smoke_test.py`, `scripts/rbi_loop_from_manifest.py`, `scripts/rbi_loop_batch.py`, `scripts/run_rbi_loop_batch.sh` |
| DB migrations | `scripts/migrate.py` |
| **Configuration** | |
| Trading settings | `config/settings.yaml` |
| Risk parameters | `config/risk.yaml` |
| Per-agent configs | `config/settings.agent2.yaml`, `config/settings.btc-4h.yaml`, etc. |
| Environment template | `.env.example` |
| **Core Modules** | |
| Data ingestion | `src/ingest/binance.py`, `src/ingest/websocket.py` |
| Database pool | `src/db/pool.py` |
| OHLCV persistence | `src/ingest/db.py` |
| Data models | `src/ingest/models.py` |
| Indicators (compute) | `src/features/computer.py`, `src/features/technical.py` |
| Indicators (I/O) | `src/features/reader.py`, `src/features/writer.py` |
| Spot execution | `src/execution/executor.py`, `src/execution/binance_client.py` |
| Futures execution | `src/execution/futures_executor.py`, `src/execution/futures_client.py` |
| Paper execution | `src/execution/paper_executor.py` |
| Execution guards | `src/execution/guards.py`, `src/execution/staged.py`, `src/execution/staged_orders.py` |
| Retry & circuit breaker | `src/execution/retry.py` |
| Risk management | `src/risk/manager.py`, `src/risk/guards.py` |
| Strategy engine | `src/strategy/engine.py`, `src/strategy/aggregator.py` |
| Strategy base & signals | `src/strategy/base.py`, `src/strategy/signals.py` |
| Trade lifecycle | `src/strategy/lifecycle.py` |
| Strategy implementations | `src/strategy/simple_ma.py`, `src/strategy/rsi_reversal.py`, + 14 more |
| Notifications | `src/notifications/telegram.py` |
| Portfolio | `src/portfolio/manager.py`, `src/portfolio/models.py` |
| Backtesting | `src/backtest/engine.py`, `src/backtest/experiment_autopilot.py` |
| AI/Overseer | `src/overseer/agent.py`, `src/overseer/xai.py`, `src/overseer/prompts.py` |
| Event logging | `src/core/event_log.py` |
| RL agent (research) | `src/rl/agent.py` |
| **Utilities** | |
| Logging | `src/utils/logger.py` |
| Rate limiting | `src/utils/rate_limiter.py` |
| Config diagnostics | `src/utils/config_doctor.py` |
| Production monitoring | `src/utils/production_drift_sentinel.py` |
| **Testing** | |
| Fixtures | `tests/conftest.py` |
| Test files | `tests/test_*.py` (60 files) |
| **Infrastructure** | |
| Docker Compose (dev) | `docker-compose.yml` |
| Docker Compose (prod) | `docker-compose.prod.yml` |
| Dockerfile (dev) | `Dockerfile` |
| Dockerfile (prod) | `Dockerfile.prod` |
| CI/CD | `.github/workflows/ci.yml` |
| Pre-commit hooks | `.pre-commit-config.yaml` |
| Prometheus config | `config/prometheus.yml`, `config/prometheus-alerts.yml` |
| Grafana dashboards | `config/grafana/dashboards/` |
| Nginx | `config/nginx/nginx.conf` |
| DB migrations | `migrations/001_*.sql` through `migrations/007_*.sql` |
| **GitHub Agents** | |
| Copilot agent definitions | `.github/agents/*.agent.md` (7 specialized agents) |

## Trading Modes Reference

| Mode | Config | Risk |
|------|--------|------|
| **Paper** | `enabled: false` | No real orders |
| **Testnet** | `enabled: true, test_mode: true` | Fake funds (demo.binance.com) |
| **Live Spot** | `enabled: true, test_mode: false` | Real money (spot) |
| **Live Futures** | `futures.enabled: true, test_mode: false` | Real money (leveraged) |

## Multi-Agent Architecture

The system supports running multiple isolated trading agents simultaneously. The active set in `docker-compose.prod.yml`:

| Agent | Config | Description |
|-------|--------|-------------|
| `agent_sol_1h_trend_pullback_overlay_live` | `config/settings.sol_1h_trend_pullback_overlay_live.yaml` | **Only deployable technical agent** — SOL 1h trend-pullback overlay, Phase 0 forward validation in progress |
| `agent_sentiment_macro` | `config/settings.sentiment_macro.yaml` | SOL-only sentiment mean reversion, live futures routing |
| `agent_sol_sparse` | `config/settings.sol_trend_pullback_sparse.yaml` | SOL trend pullback (paper) |
| `agent_sol_panic_block_paper` | `config/settings.sol_4h_panic_block_paper.yaml` | SOL panic-block paper validation |

**Historically disabled agents (configs removed — see git history for prior definitions):**

| Agent | Symbol/TF | Reason | Reference |
|-------|-----------|--------|-----------|
| `agent` (default) | SOLUSDT 4h simple_ma | WFO showed no edge. May 2025 refresh: failed sparse gate (3 WFO trades vs min 4, concentration 67.4% vs max 65%). | `docs/reports/backtesting-follow-up-2026-05-05.md`, commit `3bc3759` |
| `agent_2` | BNBUSDT 4h | All 5 strategies negative OOS, best -7.7%. | commit `5be9a0d` |
| `agent_btc` | BTCUSDT 4h | 50+ param combos (simple_ma, CCI, MTF regime) all deeply negative OOS. Post-fix P&L: -$25 across 4 trades. | commit `21419d3` |
| `agent_eth` | ETHUSDT 4h | 12% win rate on simple_ma, no edge across any strategy. WFO sweep: all candidates failed quality gates. | commit `5be9a0d`, `docs/reports/mtf-ethusdt-sweep-analysis.md` |
| `agent_avax` | AVAXUSDT 4h | Live performance degraded to 2W/11L and `-$9.58`; prior WFO edge did not persist. | commit `23e3174` |

Agents are isolated via `AGENT_ID` environment variable. Database tables use `agent_id` columns for state separation (see `migrations/005_add_agent_isolation.sql`).

## Database Migrations

Schema lives in `migrations/` and is applied via `scripts/migrate.py`:

| Migration | Purpose |
|-----------|---------|
| `001_initial_schema.sql` | OHLCV candles, trades tables |
| `002_add_indicators_table.sql` | Technical indicators hypertable |
| `003_add_portfolio_tables.sql` | Positions, portfolio tracking |
| `004_add_strategy_lifecycle.sql` | Trade lifecycle tracking |
| `005_add_agent_isolation.sql` | Multi-agent isolation columns |
| `006_normalize_position_market_labels.sql` | Schema normalization |
| `007_add_regime_features.sql` | Regime detection features |

## Quick Config Reference

### Enable Live Trading
```yaml
trading_execution:
  enabled: true
  test_mode: false  # REAL MONEY
  order_size_usdt: 20.0  # Start small!
```

### Add Trading Pair
```yaml
trading:
  pairs:
    - BTCUSDT
    - NEWPAIR
```

### Adjust Risk Limits
```yaml
loss_limits:
  max_daily_loss_pct: 0.05    # 5% daily stop
  max_drawdown_pct: 0.15       # 15% kill switch
```

## Key Scripts Reference

| Script | Purpose |
|--------|---------|
| `scripts/run_backtest.py` | Run single backtest |
| `scripts/run_full_backtest.py` | Full parameter backtest |
| `scripts/run_wfo.py` | Walk-forward optimization |
| `scripts/smoke_test.py` | Quick connectivity check |
| `scripts/migrate.py` | Apply database migrations |
| `scripts/config_doctor.py` | Validate configuration |
| `scripts/download_historical.py` | Download OHLCV from Binance |
| `scripts/diagnose_signals.py` | Debug strategy signals |
| `scripts/profit_report.py` | Generate PnL reports |
| `scripts/production_drift_sentinel.py` | Monitor production agent |
| `scripts/autoresearch.py` | Automated strategy research |
| `scripts/rbi_loop_guard.py` | Deterministic next-action gate (reads lane brief + probe verdict + last_result + overlap; returns RUN_*/CHECK_*/READY_FOR_PAPER_REVIEW/ITERATE_OR_CLOSE) |
| `scripts/rbi_loop_runner.py` | One-step wrapper that writes decision.json (and optionally executes the guarded next step with `--execute`) |
| `scripts/rbi_loop_from_manifest.py` | Preferred single-lane entrypoint using `config/autoresearch/rbi_loop.<lane>.yaml` (dry by default) |
| `scripts/rbi_loop_batch.py` + `run_rbi_loop_batch.sh` | Multi-lane supervisor + dry systemd timer path for daily oversight of manifests |
| `scripts/rbi_loop_report.py` | Renders decision.json to reviewable `docs/reports/rbi-loop-<lane>.md` |
| `scripts/backup_db.sh` | Database backup |

## Related Files

- `AGENTS.md` — Full list of active agents and coordination details
- `copilot-instructions.md` — GitHub Copilot-specific context
- `codex-instructions.md` — OpenCode/Codex-specific context
- `gemini-instructions.md` — Gemini CLI-specific context
- `sisyphus-instructions.md` — Sisyphus agent-specific context
- `docs/` — Extended documentation (trading execution, indicators, MTF guide, deployment reports)
- `.github/agents/` — GitHub Copilot agent definitions (7 specialized agents)
