# Crypto Trading Agent

Python trading system for Binance market data, indicators, strategy evaluation, risk controls, and spot/futures execution workflows.

This README has been updated to reflect the current repo reality: the runtime is no longer just a simple spot-only bot. The codebase includes separate ingestion, feature, strategy, risk, execution, portfolio, notification, and backtest layers, plus futures-aware execution paths in the runtime.

## What This Repository Does

The project is designed to support a disciplined trading workflow:

1. **Research** — explore ideas, indicators, and market structure
2. **Backtest** — test hypotheses against historical data
3. **Paper / Demo** — validate the implementation without risking capital
4. **Live** — enable real execution only after the strategy and controls survive the first three steps

If someone tries to skip straight to step 4, the repo will not stop them from making bad life choices, but the docs will at least shame them properly.

## Core Capabilities

### Market Data & Storage
- Binance REST and WebSocket ingestion paths
- OHLCV persistence to TimescaleDB
- health and ingest metrics

### Feature Pipeline
- indicator computation and persistence
- technical features including EMA, RSI, MACD, Bollinger, ATR, and related readers/writers
- feature metrics and indicator readiness checks

### Strategy Layer
- strategy engine with configurable strategy registry
- aggregator-based signal routing
- multi-strategy support with optional regime / MTF extensions
- lifecycle checks for promoting strategies through paper/validated/live stages

### Execution Layer
- paper executor for internal simulation
- spot trading executor
- futures trading executor
- staged order / guard logic
- execution metrics and telemetry

### Risk & Portfolio
- runtime risk manager
- kill-switch and trading-allowed checks
- portfolio tracking and daily summaries
- paper/live separation at the orchestration layer

### Monitoring & Notifications
- Prometheus metrics server
- Telegram notifications and summaries
- optional xAI-powered overseer / sentiment hooks
- startup diagnostics and health updates

## Current Runtime Shape

At a high level, `src/main.py` wires the system like this:

```text
Binance ingest -> TimescaleDB -> indicator computation -> indicator reader
-> strategy engine -> risk manager -> paper / spot / futures executor
-> portfolio + metrics + notifications
```

The runtime decides whether signals go to paper, spot, or futures execution based on the loaded settings.

## Repository Layout

```text
config/                runtime configs, risk config, Prometheus config
migrations/            database migrations
schema/                schema support files
scripts/               utility and migration scripts
docs/                  reports, research notes, issues
research/              strategy research artifacts
src/backtest/          backtest engine and experiment helpers
src/db/                DB pool and connectivity
src/execution/         spot/futures/paper execution
src/features/          indicator computation and feature IO
src/ingest/            Binance ingest and metrics
src/notifications/     Telegram notifications
src/overseer/          AI / overseer hooks
src/portfolio/         portfolio and stats tracking
src/risk/              risk management
src/strategy/          strategy implementations and engine
src/utils/             diagnostics and runtime utilities
tests/                 test suite
```

## Configuration

Primary runtime config lives in:

- `config/settings.yaml`
- `config/risk.yaml`

Other strategy-specific or experiment-specific configs live alongside it.

### Important Modes to Understand

There are several layers of mode selection in this repo:

- global runtime mode: paper vs live
- trading executor enabled/disabled
- trading API mode: test/demo vs live
- strategy default routing: spot vs futures

Read the loaded config before making claims about what the agent is doing. README confidence without config verification is how people end up debugging ghosts.

## Running the Stack

Typical local setup:

```bash
uv sync --all-extras --dev
```

`requirements.txt` is generated from `uv.lock` for Docker/pip and must not be hand-edited.

Then configure environment variables for secrets and run the stack using the project’s configured runtime entrypoints / compose setup.

If you are testing strategies or infrastructure changes, prefer paper or demo execution first.

## Execution Modes

### Paper Mode
- safest default
- useful for validating signal flow, portfolio logic, and notifications
- should be the default home for new strategies

### Spot Execution
- supported by the runtime
- appropriate for asset-backed BUY/SELL workflows on Binance Spot

### Futures Execution
- supported by the runtime and config surface
- higher risk, more moving parts, and more ways to ruin an otherwise pleasant afternoon
- validate carefully in paper/demo before any live use

## Strategy Development Workflow

When adding or changing a strategy:

1. define the hypothesis clearly
2. identify required indicators / data dependencies
3. backtest with explicit metrics
4. paper-validate the implementation
5. check risk controls, cooldowns, and routing mode
6. only then consider live activation

Observed strategy surface in the repo includes:
- moving-average crossover
- RSI reversal
- MACD histogram
- Bollinger bounce
- momentum
- CCI breakout
- VWAP reversion
- sentiment-based mean reversion
- macro-volatility hooks
- breakout / trend-pullback / multi-timeframe variants

## Monitoring

The runtime exposes Prometheus metrics and supports Telegram notifications. Check:

- ingest health
- indicator readiness
- execution counts
- risk blocks
- strategy signal counts
- portfolio / daily summary behavior

## Source of Truth

This README is now aligned with the current repo shape, but when in doubt:

1. `config/settings.yaml`
2. `src/main.py`
3. the relevant module under `src/`

Those files decide what actually happens. READMEs are allowed to age badly; running code is not.
