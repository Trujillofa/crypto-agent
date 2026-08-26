# Canonical backtest and WFO

Research replay lives in `src/backtest/*`. It is **not a live-go**. A passing
WFO does not enable `trading_execution.enabled`, flip `test_mode`, or place
orders. Paper → live is a separate human config + deploy step.

## How to run

Single window (historical simulator only):

```bash
uv run python scripts/run_backtest.py \
  --symbol SOLUSDT --timeframe 1h \
  --start 2024-01-01 --end 2024-06-01 \
  --config config/settings.yaml \
  --execution-profile execution_parity_v2
```

Walk-forward + gates (canonical WFO):

```bash
uv run python scripts/experiment_autopilot.py \
  --config config/settings.yaml \
  --symbol SOLUSDT --timeframe 1h \
  --start 2024-01-01 --end 2026-01-01 \
  --train-months 6 --test-months 3 \
  --execution-profile execution_parity_v2
```

`--execution-profile execution_parity_v2` is closed-bar signal / next-open fill.
`legacy_v1` fills at the signal-bar close and is kept only for old-run
reproducibility. Autopilot defaults to v2.

There is no `--live`, `--promote`, or `live_go` on these paths. Passing one
is refused.

## Clock

Bar `time` is the **open**. A `1h` bar at `10:00` is `[10:00, 11:00)`.
Strategies see completed OHLCV for that bar. v2 queues the fill for the next
open. Unknown timeframe labels raise; they are not treated as 1 minute.

WFO test windows are `[start, end)`. `fetch_range` uses `time >= start AND
time <= end`, so canonical autopilot (and the search CLIs) translate `end`
with `wfo_inclusive_fetch_bounds()` before the fetch. Frozen historical
artifacts stay as written; do not rerun them to “fix” old gates.

## Cost book

Frozen snapshot: `fee_rate` (commission / side of notional), `slippage_pct`
(per-side price concession = spread + slip), `futures_funding_rate` (8h
settlement fraction), `fixed_notional_usdt` (USDT size cap; 0 = uncapped).
Defaults are 4 bps fee and 2 bps slip per side. Mutation after engine
construction does not change fills.

## Ranking

Candidate ranking uses the **first train window** only. Holdout / WFO test
metrics are reported, not used to order names. `scripts/run_wfo_sweep.py` is
not a selection tool (it never applied `param_grid`).
