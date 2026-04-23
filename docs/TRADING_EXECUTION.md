# Trading Execution

This document describes how execution works across both Binance **Spot** and **USDⓈ-M Futures** in this repository.

## Overview

At runtime, the strategy engine emits signals with `trading_mode` (`spot` or `futures`). The signal router in `src/main.py` sends each signal to the matching executor.

High-level flow:

1. Strategy emits signal (`BUY`/`SELL`/`HOLD` + trading mode)
2. Risk and guard checks run before placement
3. Spot or futures executor places order
4. Portfolio/metrics/notifications are updated
5. Monitoring loops continue (balance, position risk, alerts)

## Architecture

```text
Trading Strategy Signal
    ↓
Risk Manager + Guard Pipeline
    ↓
TradingExecutor (spot) OR FuturesTradingExecutor (futures)
    ↓
BinancePrivateClient (spot) OR BinanceFuturesClient (futures)
    ↓
Order placement + account/position monitoring
    ↓
Prometheus metrics + Telegram notifications
```

## Core Components

### Spot

- **Executor**: `src/execution/executor.py`
- **Client**: `src/execution/binance_client.py`
- Spot BUY uses quote notional (`quoteOrderQty` behavior), while SELL requires base-asset quantity held in wallet.

### Futures

- **Executor**: `src/execution/futures_executor.py`
- **Client**: `src/execution/futures_client.py`
- Futures signals are handled only when `futures.enabled: true` and signal mode is `futures`.
- Futures executor includes margin/leverage/liquidation-buffer checks and position-risk polling.

## Mode & Routing Rules

Routing logic is implemented in `src/main.py`:

- If `mode: paper`, signals route to `PaperExecutor`.
- If live and `futures.enabled: true`, spot signals route to `TradingExecutor` and futures signals route to `FuturesTradingExecutor`.
- Optional mirroring (`strategy.mirror_spot_to_futures`) duplicates eligible spot signals into futures.

## Configuration Keys (Execution-Critical)

From `config/base.yaml` + per-agent settings overlays:

```yaml
trading_execution:
  enabled: true
  test_mode: false
  order_size_usdt: 6.0
  stop_loss_pct: 0.02
  take_profit_pct: 0.05
  sl_atr_multiplier: 2.0
  tp_atr_multiplier: 4.5

futures:
  enabled: true
  test_mode: false
  default_leverage: 3
  max_leverage: 10
  margin_mode: isolated
  position_mode: one-way
  liquidation_buffer_pct: 5.0
```

## Futures Gotchas (Important)

### 1) Notional vs Quantity and LOT_SIZE truncation

Futures quantity is calculated as:

`quantity = order_size_usdt / price`

Then it is truncated to exchange LOT_SIZE step via `format_quantity(...)` in `futures_client.py`.

Practical impact: final executable notional can be slightly lower than requested due to step-size truncation.

### 2) Exchange-side SL/TP placement can fail

After a filled futures BUY, the executor tries to place exchange-side `STOP_MARKET` (SL) and `TAKE_PROFIT_MARKET` (TP) reduce-only orders. If placement fails, errors are logged and the executor continues running.

Operationally: monitor logs for SL/TP placement failures and do not assume every filled entry has protective exchange-side orders attached.

### 3) Monitoring cadence

Futures monitor loop runs every 30 seconds (`FuturesTradingExecutor.run`). Alerts and position state updates are near-real-time but not tick-by-tick.

## Risk Integration

Execution is gated by `RiskManager` + optional guard pipeline:

- Position limits (max position %, max open positions)
- Loss limits (daily loss, drawdown, single-loss)
- Futures checks (margin usage, leverage cap, liquidation buffer)
- Kill-switch blocking

Blocked executions are recorded in metrics and logged with explicit reasons.

## Metrics

Execution metrics are exported via Prometheus (port `8000`):

- orders placed/filled/rejected/cancelled
- API errors
- risk blocks
- account balances
- latency histograms

Use `/metrics` and Grafana dashboards for continuous monitoring.

## Production Operations

Production uses `docker-compose.prod.yml` and `Dockerfile.prod` (no source bind-mount for code/config). After changing execution code or config, rebuild the target service image.

Example:

```bash
ssh crypto-agent "cd /opt/crypto-agent && git pull"
ssh crypto-agent "cd /opt/crypto-agent && docker compose -f docker-compose.prod.yml build agent_sentiment_macro"
ssh crypto-agent "cd /opt/crypto-agent && docker compose -f docker-compose.prod.yml up -d agent_sentiment_macro"
ssh crypto-agent "cd /opt/crypto-agent && docker compose -f docker-compose.prod.yml logs agent_sentiment_macro --tail=200 --no-log-prefix"
```

## Troubleshooting

### Signals fire but no orders

- Check `trading_execution.enabled`
- Check `futures.enabled` if expecting futures orders
- Check risk-block logs (margin/position/kill-switch/guard)

### Futures order rejected

- Inspect `FuturesTradingExecutor` log line with rejection reason
- Validate available margin and leverage constraints
- Verify symbol is included in `futures.symbols`

### Protective orders missing after entry

- Search logs for `Failed to place SL order` / `Failed to place TP order`
- Confirm exchange-side conditional order acceptance for the symbol/state

## References

- Binance Spot API: <https://binance-docs.github.io/apidocs/spot/en/>
- Binance USDⓈ-M Futures API: <https://binance-docs.github.io/apidocs/futures/en/>
- Execution code: `src/execution/`
- Runtime wiring: `src/main.py`
