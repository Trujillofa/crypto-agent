# Sentiment-Macro Expanded Pair Screen

**Run timestamp:** 2026-05-06 UTC
**Goal:** Refresh candidate `1h` data and test more pairs for possible promotion into `agent_sentiment_macro`.
**Config tested:** `config/settings.sentiment_macro.yaml`
**Strategy:** `sentiment_mean_reversion`
**Execution mode:** Research/backtest only; no live services restarted and no order-placement commands run.
**Canonical runner:** `scripts/run_autoresearch.py` with `gate-profile standard`.

## Candidate Universe

The prior pair screen rejected `AVAXUSDT` and showed that the remaining universe needed fresher `1h` data. This pass refreshed or added `1h` OHLCV and indicators for six liquid Binance majors outside the current live sentiment-macro pair set:

- `BNBUSDT`
- `LINKUSDT`
- `ADAUSDT`
- `XRPUSDT`
- `DOGEUSDT`
- `LTCUSDT`

The current live sentiment-macro bot remains `BTCUSDT`, `ETHUSDT`, and `SOLUSDT`.

## Data Refresh

Safe Hetzner one-off commands used the production image, but explicitly used the bundled virtualenv Python and `PYTHONPATH=/app` because bare `/usr/local/bin/python` does not include project dependencies.

Data path:

```bash
docker compose -f docker-compose.prod.yml run --rm --no-deps agent_sentiment_macro \
  sh -lc 'export PYTHONPATH=/app DB_HOST=timescaledb DB_PORT=5432 DB_NAME=marketdata DB_USER=trading DB_PASSWORD=$POSTGRES_PASSWORD; \
  /opt/venv/bin/python scripts/download_historical.py --symbol <SYMBOL> --interval 1h --start 2024-01-01 --end 2026-05-05 --db; \
  /opt/venv/bin/python scripts/compute_historical_indicators.py --symbol <SYMBOL> --timeframe 1h --lookback 750 --batch-size 1000'
```

Final indicator coverage:

| Symbol | Timeframe | Indicator rows | First indicator | Last indicator |
|---|---|---:|---|---|
| `ADAUSDT` | `1h` | 19,794 | 2024-02-01 06:00 UTC | 2026-05-05 23:00 UTC |
| `BNBUSDT` | `1h` | 20,345 | 2024-01-09 07:00 UTC | 2026-05-05 23:00 UTC |
| `DOGEUSDT` | `1h` | 19,794 | 2024-02-01 06:00 UTC | 2026-05-05 23:00 UTC |
| `LINKUSDT` | `1h` | 19,794 | 2024-02-01 06:00 UTC | 2026-05-05 23:00 UTC |
| `LTCUSDT` | `1h` | 19,795 | 2024-02-01 06:00 UTC | 2026-05-06 00:00 UTC |
| `XRPUSDT` | `1h` | 19,794 | 2024-02-01 06:00 UTC | 2026-05-05 23:00 UTC |

## Gate Profile

Every candidate used the standard sentiment-macro WFO/bootstrap gates:

| Gate | Threshold |
|---|---:|
| Min WFO trades | 20 |
| Min WFO Sharpe | 0.50 |
| Max drawdown | 10.00% |
| Max bootstrap P(loss) | 25.00% |
| Min OOS return | 0.00% |
| Max profit concentration | 50.00% |

Canonical autoresearch command shape:

```bash
docker compose -f docker-compose.prod.yml run --rm --no-deps --user root \
  -v /opt/crypto-agent/research:/app/research \
  agent_sentiment_macro \
  sh -lc 'apt-get update && apt-get install -y --no-install-recommends git; \
  export PYTHONPATH=/app POSTGRES_HOST=timescaledb POSTGRES_PORT=5432; \
  /opt/venv/bin/python scripts/run_autoresearch.py \
  --config config/settings.sentiment_macro.yaml \
  --symbol <SYMBOL> \
  --timeframe 1h \
  --train-months 3 \
  --test-months 2 \
  --bootstrap 500 \
  --gate-profile standard \
  --description sentiment_macro_<SYMBOL>_1h_expanded_screen'
```

The bind mount is required because `docker-compose.prod.yml` does not mount the repo into the production container; without it, autoresearch artifacts are written to the container's ephemeral filesystem. `git` is installed only inside the one-off container because the production image does not include it and `run_autoresearch.py` calls `git rev-parse`. The resulting commit field is still `unknown` because the production image does not include the `.git` directory.

## Results

| Symbol | Baseline trades | Baseline return | WFO windows | WFO trades | OOS Sharpe | OOS return | Max DD | P(loss) | Profit conc. | Gate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `BNBUSDT` | 112 | -24.72% | 8 | 54 | -1.64 | -18.20% | 28.49% | 94.80% | 94.04% | FAIL |
| `LINKUSDT` | 95 | -30.26% | 8 | 41 | -1.62 | -19.60% | 37.75% | 98.60% | 48.90% | FAIL |
| `ADAUSDT` | 80 | -28.42% | 8 | 49 | -2.00 | -18.44% | 28.42% | 98.20% | 78.36% | FAIL |
| `XRPUSDT` | 112 | -29.72% | 8 | 60 | -1.82 | -15.77% | 30.43% | 97.40% | 50.35% | FAIL |
| `DOGEUSDT` | 84 | -22.10% | 8 | 34 | -1.62 | -11.91% | 30.08% | 95.00% | 92.30% | FAIL |
| `LTCUSDT` | 87 | -30.98% | 8 | 49 | -1.59 | -21.62% | 31.20% | 99.20% | 100.00% | FAIL |

Canonical artifacts were persisted on Hetzner:

- `research/results.tsv` received six rows with descriptions `sentiment_macro_<SYMBOL>_1h_expanded_screen`.
- `research/archive/experiment-autopilot-20260506-003*.json` contains the raw summaries.
- `research/resolved/settings-20260506-003*.yaml` contains the resolved configs used for each run.

All candidates had enough aggregate WFO trades, but all failed every quality/risk gate except trade count. The failures are broad and structural: negative OOS return, negative OOS Sharpe, excessive drawdown, high bootstrap loss probability, and high profit concentration.

## Decision

Do **not** promote any additional pair into `agent_sentiment_macro` from this run.

Do **not** run parameter sweeps on these six pairs under the current sentiment-mean-reversion thesis. The baseline failures are too severe; sweeps would likely fit noise rather than reveal a stable edge.

## Recommended Next Research Step

Stop expanding this exact `1h` sentiment-mean-reversion template across more symbols for now. The evidence says the current live bot's historical edge is not explained by the neutral-sentiment technical backtest path used here.

The next useful research step is to close the live/backtest gap before more pair promotion work:

1. Build or use a historical sentiment replay source for backtests (`--replay-sentiment-log`) so WFO sees the same sentiment gate live trading sees.
2. Re-run the current live symbols (`BTCUSDT`, `ETHUSDT`, `SOLUSDT`) with sentiment replay and executor-like exits to establish a trustworthy baseline.
3. Only after live-symbol replay matches paper/live behavior, re-run expansion candidates.
4. If replay data is unavailable, treat this strategy as technical mean reversion only and pivot to a different thesis family for new-pair discovery.
