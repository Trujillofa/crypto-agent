# Sentiment-Macro Pair Promotion Screen

**Date:** 2026-05-05
**Goal:** Find additional pairs that can be promoted into `agent_sentiment_macro`.
**Config tested:** `config/settings.sentiment_macro.yaml`
**Strategy:** `sentiment_mean_reversion`
**Target runtime:** `sentiment-macro-bot`, `1h`, futures, one-way isolated margin

## Scope and Safety

This pass tested candidate pairs in research/backtest mode only. The runs used one-off Hetzner containers against the TimescaleDB indicator store. No live services were restarted, no runtime config was changed, and no order-placement commands were run.

The current live sentiment-macro bot already trades:

- `BTCUSDT`
- `ETHUSDT`
- `SOLUSDT`

The expansion question was whether additional pairs could safely join that set.

## Data Coverage Check

Hetzner indicator coverage showed only two non-current symbols with any `1h` history:

| Symbol | Timeframe | Rows | First candle | Last candle | Use in this pass |
|---|---|---:|---|---|---|
| `AVAXUSDT` | `1h` | 19,887 | 2024-01-11 10:00 UTC | 2026-04-19 00:00 UTC | Primary candidate |
| `BNBUSDT` | `1h` | 18,637 | 2024-01-09 07:00 UTC | 2026-02-23 19:00 UTC | Stale-data context |

`LINKUSDT` had `4h` data only and was stale after 2026-03-19, so it was not a valid `1h` sentiment-macro promotion candidate in this pass.

## Gate Profile

The screen used the standard sentiment-macro promotion gates from `docs/reports/SENTIMENT_MACRO_IMPROVEMENT_PLAN.md`:

| Gate | Threshold |
|---|---:|
| Min WFO trades | 20 |
| Min WFO Sharpe | 0.50 |
| Max drawdown | 10.00% |
| Max bootstrap P(loss) | 25.00% |
| Min OOS return | 0.00% |
| Max profit concentration | 50.00% |

## Results

### `AVAXUSDT 1h`

Command path:

```bash
docker compose -f docker-compose.prod.yml run --rm --no-deps agent_sentiment_macro \
  python scripts/experiment_autopilot.py \
  --config config/settings.sentiment_macro.yaml \
  --symbol AVAXUSDT \
  --timeframe 1h \
  --train-months 3 \
  --test-months 2 \
  --bootstrap 500 \
  --min-wfo-trades 20 \
  --min-wfo-sharpe 0.5 \
  --max-drawdown-pct 10.0 \
  --max-bootstrap-p-loss-pct 25.0 \
  --min-oos-return-pct 0.0 \
  --max-profit-concentration-pct 50.0
```

| Metric | Result | Gate |
|---|---:|---:|
| Baseline trades | 121 | n/a |
| Baseline return | -19.85% | n/a |
| WFO windows | 8 | n/a |
| Aggregate WFO trades | 64 | >= 20 |
| OOS mean Sharpe | -1.52 | >= 0.50 |
| OOS return | -19.95% | >= 0.00% |
| Max drawdown | 36.93% | <= 10.00% |
| Bootstrap P(loss) | 74.80% | <= 25.00% |
| Profit concentration | 71.78% | <= 50.00% |
| Gate result | FAIL | PASS required |

`AVAXUSDT` had enough trades, but failed every quality/risk gate. It should not be promoted and does not deserve parameter sweeps unless the strategy thesis changes.

### `BNBUSDT 1h`

`BNBUSDT` was tested only as a context check because its `1h` data ends on 2026-02-23.

| Metric | Result | Gate |
|---|---:|---:|
| Baseline trades | 135 | n/a |
| Baseline return | -18.90% | n/a |
| WFO windows | 7 | n/a |
| Aggregate WFO trades | 72 | >= 20 |
| OOS mean Sharpe | -0.86 | >= 0.50 |
| OOS return | -17.51% | >= 0.00% |
| Max drawdown | 24.50% | <= 10.00% |
| Bootstrap P(loss) | 79.00% | <= 25.00% |
| Profit concentration | 71.36% | <= 50.00% |
| Gate result | FAIL | PASS required |

`BNBUSDT` also failed every quality/risk gate. Even if data were refreshed, the stale-history result does not justify promotion work yet.

## Decision

Do **not** add any new pair to `agent_sentiment_macro` from this pass.

The current live pair list should remain:

- `BTCUSDT`
- `ETHUSDT`
- `SOLUSDT`

The expansion candidates do not meet the standard WFO/bootstrap gates, and the backtest/live mismatch documented in `SENTIMENT_MACRO_BASELINE_RESULTS.md` still matters: historical backtests use neutral/fallback sentiment unless a replay sentiment log is supplied, while live behavior can use live AI sentiment. That means a pair should not move directly from this screen into live trading. At most, a candidate should move from research into a dedicated paper-only shadow validation service after passing backtest gates.

## Next Recommended Action

Do not tune AVAX or BNB sentiment-macro parameters yet. The failures are too broad: negative OOS returns, negative Sharpe, high drawdown, high loss probability, and high profit concentration. Parameter sweeps would likely optimize noise.

The better next step is to fix the candidate universe before rerunning promotion screens:

1. Refresh or add `1h` indicator coverage for candidate majors outside the current bot, especially `LINKUSDT`, `BNBUSDT`, and any other liquid Binance futures pairs worth considering.
2. Add a historical sentiment replay path for candidate symbols if available, or explicitly mark the backtest as neutral-sentiment technical mean reversion.
3. Re-run the same standard gate profile only after the data gap is fixed.
4. Promote only to a paper/shadow service first, with minimum 2-week observation, before adding a pair to the live `agent_sentiment_macro` service.
