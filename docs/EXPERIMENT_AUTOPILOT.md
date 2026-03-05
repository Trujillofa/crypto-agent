# Experiment Autopilot

`experiment_autopilot` combines baseline backtest, walk-forward validation, bootstrap uncertainty, and explicit acceptance gates in one command.

## What It Does

- Runs baseline backtest on a full range
- Builds rolling walk-forward windows (train/test month spans)
- Runs out-of-sample backtests per test window
- Estimates `P(loss)` with bootstrap trade-return resampling
- Checks gates:
  - minimum trades
  - minimum OOS mean Sharpe
  - maximum drawdown
  - maximum bootstrap loss probability
  - minimum OOS compounded return
  - maximum profit concentration by one OOS window
- Writes Markdown + JSON reports

## One Command

```bash
. .venv/bin/activate && python scripts/experiment_autopilot.py --config config/settings.yaml
```

## Common Flags

```bash
python scripts/experiment_autopilot.py \
  --config config/settings.yaml \
  --symbol SOLUSDT \
  --timeframe 4h \
  --start 2024-01-01 \
  --end 2026-03-01 \
  --train-months 6 \
  --test-months 3 \
  --bootstrap 1000 \
  --min-trades 20 \
  --min-wfo-sharpe 0.5 \
  --max-drawdown-pct 10 \
  --max-bootstrap-p-loss-pct 25 \
  --min-oos-return-pct 0 \
  --max-profit-concentration-pct 50
```

## Outputs

Reports are written under `docs/reports/` by default:

- `experiment-autopilot-<timestamp>.md`
- `experiment-autopilot-<timestamp>.json`

Use `--output-prefix` to change path/prefix.
