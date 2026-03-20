# Crypto Trading Agent Architecture

## Purpose

This document maps the main components of the crypto trading system so changes can be made without wandering the repo like a tourist with root access.

## System Overview

The runtime is orchestrated from `src/main.py`.

```text
settings + env
  -> DB pool
  -> risk manager
  -> metrics server
  -> market ingest
  -> indicator computation
  -> indicator reader
  -> strategy engine
  -> paper / spot / futures execution
  -> portfolio tracking + notifications + summaries
```

## Main Components

### 1. Configuration
- `config/settings.yaml` — primary runtime config
- `config/risk.yaml` — risk controls
- other `config/settings.*.yaml` files — experiment / strategy variants

Responsibilities:
- mode selection
- symbol/timeframe selection
- routing to paper / spot / futures
- risk and exit-rule tuning
- metrics / Telegram / AI settings

### 2. Ingestion
Directory: `src/ingest/`

Responsibilities:
- fetch OHLCV from Binance
- support REST and WebSocket ingestion
- write market data to TimescaleDB
- expose ingest metrics

Key files:
- `binance.py`
- `websocket.py`
- `db.py`
- `metrics.py`
- `models.py`

### 3. Features / Indicators
Directory: `src/features/`

Responsibilities:
- compute technical indicators
- persist features / indicators
- expose feature metrics
- serve latest indicator rows to strategies

Key files:
- `computer.py`
- `reader.py`
- `writer.py`
- `technical.py`
- `metrics.py`

### 4. Strategy Layer
Directory: `src/strategy/`

Responsibilities:
- register strategies
- evaluate indicators and generate signals
- aggregate multiple strategy outputs
- support optional advanced / regime-aware strategies
- provide lifecycle hooks for readiness / promotion

Key files:
- `engine.py`
- `aggregator.py`
- `base.py`
- `signals.py`
- strategy implementations under the same directory
- `lifecycle.py`

Notes:
- some strategies require optional dependencies or external context
- routing and aggregator config matter as much as the strategy itself

### 5. Risk Management
Directory: `src/risk/`

Responsibilities:
- decide whether trading is allowed
- enforce kill-switch behavior
- monitor risk in the background
- coordinate with execution layer

Key files:
- `manager.py`
- `guards.py`

### 6. Execution
Directory: `src/execution/`

Responsibilities:
- place or simulate orders
- separate paper, spot, and futures flows
- record execution metrics
- apply execution-side guards

Key files:
- `executor.py`
- `paper_executor.py`
- `futures_executor.py`
- `binance_client.py`
- `futures_client.py`
- `guards.py`
- `staged_orders.py`
- `metrics.py`

### 7. Portfolio / Accounting
Directory: `src/portfolio/`

Responsibilities:
- track positions and portfolio state
- produce daily stats
- support summaries and notifications

Key files:
- `manager.py`
- `models.py`

### 8. Notifications & Overseer
Directories:
- `src/notifications/`
- `src/overseer/`

Responsibilities:
- Telegram notifications
- daily summaries
- optional xAI-powered overseer / sentiment support

Key files:
- `notifications/telegram.py`
- `overseer/agent.py`
- `overseer/xai.py`
- `overseer/prompts.py`

### 9. Backtesting / Research
Directories:
- `src/backtest/`
- `research/`
- `docs/research/`

Responsibilities:
- evaluate ideas offline
- capture experiment results
- separate research from production routing

## Runtime Decision Points

### Paper vs Live
The runtime first decides whether it is operating in paper or live mode.

- **paper** -> route into `PaperExecutor`
- **live** -> route into spot and/or futures executors depending on config

### Spot vs Futures
Strategy output can be routed to spot or futures execution.

Important inputs:
- `strategy.default_trading_mode`
- futures enablement in config
- executor initialization path in `src/main.py`

### AI / Overseer Optionality
AI features are optional and should not be treated as core alpha generation. They are support tooling, not magic beans.

## Operational Failure Zones

When debugging, isolate the layer first:

1. config loading / secret resolution
2. DB connectivity
3. ingest not writing candles
4. indicator pipeline not producing fresh rows
5. strategy engine not emitting signals
6. risk manager blocking trades
7. execution path rejecting or failing orders
8. notification / summary failures

## Safe Change Strategy

### For strategy changes
- define the hypothesis
- confirm indicator availability
- backtest first
- paper-validate next
- only then change production routing

### For execution changes
- inspect paper/live branch behavior
- inspect spot/futures separation
- confirm metric and logging coverage
- confirm risk manager interaction

### For config changes
- treat config as code
- document intended mode and routing
- keep paper/demo defaults unless there is a very good reason not to

## Suggested Reading Order

If you are new to the repo, read in this order:

1. `config/settings.yaml`
2. `src/main.py`
3. `src/strategy/engine.py`
4. `src/risk/manager.py`
5. the specific executor in `src/execution/`
6. the strategy file you want to inspect

That path gets you from high-level behavior to the exact part that is most likely to break.
