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

## Autoresearch Loop

`scripts/run_autoresearch.py` runs one fixed evaluation and appends the result to
`research/results.tsv`. `scripts/autoresearch_loop.py` adds the Karpathy-style
control loop on top: it generates bounded YAML overlays under
`research/candidates/`, evaluates each candidate through the fixed runner, and
lets the runner mark each result as `keep`, `discard`, `crash`, or `timeout`.
Child command output is captured in `research/autoresearch_loop.log`; the fixed
runner still writes the latest child result to `research/last_result.json`.

Dry-run candidate generation:

```bash
python scripts/autoresearch_loop.py \
  --config config/settings.autoresearch.yaml \
  --symbol SOLUSDT \
  --timeframe 4h \
  --gate-profile sparse_trend_3_2 \
  --max-runs 5 \
  --dry-run
```

Bounded research campaign:

```bash
python scripts/autoresearch_loop.py \
  --config config/settings.autoresearch.yaml \
  --symbol SOLUSDT \
  --timeframe 4h \
  --train-months 3 \
  --test-months 2 \
  --gate-profile sparse_trend_3_2 \
  --include-baseline \
  --max-runs 10
```

Focused follow-up after a broad campaign identifies a promising family:

```bash
python scripts/autoresearch_loop.py \
  --config config/settings.autoresearch.yaml \
  --symbol SOLUSDT \
  --timeframe 4h \
  --train-months 3 \
  --test-months 2 \
  --gate-profile sparse_trend_3_2 \
  --families aggregator_thresholds \
  --aggregator-focus \
  --max-runs 30
```

Combined follow-up when a single-family sweep improves score but still fails
robustness gates:

```bash
python scripts/autoresearch_loop.py \
  --config config/settings.autoresearch.yaml \
  --symbol SOLUSDT \
  --timeframe 4h \
  --train-months 3 \
  --test-months 2 \
  --gate-profile sparse_trend_3_2 \
  --families combined_focus \
  --max-runs 50
```

Standard-gate bridge search after a candidate passes risk/profit gates but is
short on aggregate WFO trades:

```bash
set -a && source .env && set +a
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=15432

python scripts/autoresearch_loop.py \
  --config config/settings.autoresearch.yaml \
  --symbol SOLUSDT \
  --timeframe 1h \
  --train-months 3 \
  --test-months 2 \
  --gate-profile standard \
  --families standard_gate_bridge \
  --max-runs 50
```

This family intentionally samples between the best SOLUSDT 1h near miss and the
lowest-damage 20+ WFO-trade failures. Its purpose is to add a few trades without
moving into the high-frequency, high-drawdown region that prior near-pass
expansion sweeps exposed.

If the bridge campaign still fails by moving into poor-bootstrap variants, run a
narrower trade-lift search around the original 17-WFO near miss:

```bash
python scripts/autoresearch_loop.py \
  --config config/settings.autoresearch.yaml \
  --symbol SOLUSDT \
  --timeframe 1h \
  --train-months 3 \
  --test-months 2 \
  --gate-profile standard \
  --families near_miss_trade_lift \
  --max-runs 30
```

If the same surface remains capped below 20 robust WFO trades, test a
complementary trend-consistent signal instead of loosening mean-reversion
thresholds:

```bash
python scripts/autoresearch_loop.py \
  --config config/settings.autoresearch.yaml \
  --symbol SOLUSDT \
  --timeframe 1h \
  --train-months 3 \
  --test-months 2 \
  --gate-profile standard \
  --families trend_pullback_overlay \
  --max-runs 30
```

If the local DB is only reachable through Hetzner, run the same bridge search
through the SSH tunnel wrapper:

```bash
scripts/run_autoresearch_tunnel.sh \
  --mode loop \
  --config config/settings.autoresearch.yaml \
  --symbol SOLUSDT \
  --timeframe 1h \
  --train-months 3 \
  --test-months 2 \
  --bootstrap 100 \
  --seed 314 \
  --gate-profile standard \
  --families standard_gate_bridge \
  --max-runs 50 \
  --timeout-seconds 900 \
  --output-dir /tmp/crypto-agent-autoresearch-sol-1h-standard-bridge-50
```

Near-pass expansion when the best candidate has acceptable drawdown and return
but fails because OOS trade count is too low:

```bash
python scripts/autoresearch_loop.py \
  --config config/settings.autoresearch.yaml \
  --symbol SOLUSDT \
  --timeframe 4h \
  --train-months 3 \
  --test-months 2 \
  --gate-profile sparse_trend_3_2 \
  --families near_pass_expansion \
  --max-runs 50
```

Use `--gate-profile probe_1h` only for limited 1h research probes where the
candidate already passes return, Sharpe, drawdown, bootstrap loss, and profit
concentration gates but narrowly misses the standard `min_wfo_trades: 20`
requirement. It keeps the standard risk gates and lowers only the aggregate WFO
trade-count requirement to 15. This is not a full production-promotion gate.

The loop is intentionally config-only. It does not edit production settings,
restart services, or mutate strategy/execution code. A candidate should only be
promoted manually after reviewing `research/results.tsv`,
`research/last_result.json`, and the archived autopilot report.
