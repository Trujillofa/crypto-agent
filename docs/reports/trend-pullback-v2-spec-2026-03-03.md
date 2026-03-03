# Trend Pullback v2 Spec

## Decision

The proposed roadmap is directionally correct, but it needs four adjustments before execution:

1. The current search harness does not support `--strategy` or `--candidates`.
   Use `--config`, `--profile`, and `--max-candidates` instead.
2. `profit_factor > 1.6` is a valid target, but the current search harness does not gate on profit factor yet.
   Treat it as a review metric until the harness is extended.
3. `minimum 35 trades in walk-forward` is not enforced by the current harness.
   The current harness gates on full-period trades, not aggregated walk-forward trades.
4. The backtest-alignment phase should target ATR SL/TP/trailing parity with the paper executor.
   It should not require backtest results to move less than 5%, because a correct exit model can legitimately change results more than that.

## Adjusted execution plan

### Phase 1

Write and approve a repo-native strategy spec using only indicators already exposed by [src/features/reader.py](/home/yderf/TRADING/crypto-agent/src/features/reader.py).

### Phase 2

Patch [src/backtest/engine.py](/home/yderf/TRADING/crypto-agent/src/backtest/engine.py) so exits are closer to [src/execution/paper_executor.py](/home/yderf/TRADING/crypto-agent/src/execution/paper_executor.py):

- ATR-based stop loss
- ATR-based take profit
- ATR-activated trailing stop
- optional long-only interpretation for replacement configs

### Phase 3

Implement `TrendPullbackStrategy v2` in the existing async strategy interface and registry path.

### Phase 4

Run the harness with the correct interface, for example:

```bash
python scripts/run_config_search.py \
  --config config/settings.sol_trend_pullback.yaml \
  --symbol SOLUSDT \
  --timeframe 4h \
  --profile quick \
  --max-candidates 128
```

If needed, add a dedicated refinement profile after the first pass, as already done for the previous SOL regime.

## Repo-native strategy thesis

Use one coherent idea only:

Buy shallow pullbacks inside confirmed uptrends on `4h` `SOLUSDT`, and let the executor manage exits with ATR-based SL/TP/trailing.

That means the strategy should be entry-focused and long-biased:

- `BUY` when a healthy uptrend pulls back and then recovers
- `HOLD` otherwise
- avoid relying on strategy `SELL` for normal exits in paper mode

## Required constraints

This strategy must stay compatible with the current repo:

- implement async `evaluate(symbol, indicators) -> Signal`
- use the existing registry in [src/main.py](/home/yderf/TRADING/crypto-agent/src/main.py#L475)
- use only indicators currently returned by [src/features/reader.py](/home/yderf/TRADING/crypto-agent/src/features/reader.py)
- keep config in the existing `strategy.strategies[].name` + `config` format

## Allowed indicators

The replacement should use only:

- `close_price`
- `ema_50`
- `ema_200`
- `rsi_14`
- `macd_hist`
- `atr_pct`
- `atr_14`
- `vwap`

No `adx`, no custom pipeline additions, and no new feature dependencies in phase 1.

## v2 entry logic

### Regime filter

Trade only when all are true:

- `close_price > ema_200`
- `ema_50 > ema_200`
- `(ema_50 - ema_200) / ema_200 >= min_trend_strength_pct`
- `atr_pct >= min_atr_pct`

Why:

- fixes the old asymmetric regime logic
- removes deep downtrend dip-buying
- avoids dead low-volatility chop

### Pullback zone

A valid pullback should mean price returned toward value, not collapsed:

- `close_price <= ema_50 * (1 + max_pullback_distance_pct)`
- `close_price >= ema_50 * (1 - max_pullback_distance_pct)`
- `close_price <= vwap * (1 + vwap_pullback_distance_pct)`

Why:

- keeps entries near trend support
- avoids breakout chasing
- avoids deep mean-reversion knife-catching

### Recovery confirmation

Require at least one bar of recovery:

- `rsi_14 >= rsi_reclaim_level`
- `rsi_14 > previous_rsi_14`
- `macd_hist >= min_macd_hist`
- `macd_hist > previous_macd_hist`

Optional confirmation if needed:

- `close_price > previous_close_price`

Why:

- the pullback must be turning back up
- avoids buying during passive drift lower

### Entry output

When regime + pullback + recovery all hold:

- emit `BUY`
- confidence should scale from trend strength and volatility margin, not from unrelated indicators

Otherwise:

- emit `HOLD`

## Exit model assumptions

Strategy v2 should not depend on discretionary SELL signals for normal position management.

For validation, exits should be modeled by the backtest engine using executor-like rules:

- initial stop from ATR
- initial target from ATR
- trailing stop activates after profit reaches an ATR multiple
- trailing stop ratchets up with new highs

This is the main alignment task before trusting any profitability result.

## Tunable parameters

These are the only parameters to tune in the first search round.

| Parameter | Purpose | Initial value | Search range |
|---|---|---:|---:|
| `rsi_reclaim_level` | Recovery threshold after pullback | `50` | `46, 48, 50, 52` |
| `min_trend_strength_pct` | Minimum EMA50 over EMA200 spread | `0.010` | `0.008, 0.010, 0.012, 0.015` |
| `max_pullback_distance_pct` | Allowed distance from EMA50 | `0.015` | `0.010, 0.015, 0.020` |
| `vwap_pullback_distance_pct` | Allowed distance above VWAP | `0.010` | `0.005, 0.010, 0.015` |
| `min_atr_pct` | Chop filter | `0.010` | `0.008, 0.010, 0.012` |
| `min_macd_hist` | Momentum floor on recovery | `0.0` | `0.0, 0.00005, 0.0001` |

These ranges are intentionally narrow. The goal is not to brute-force a new regime into existence. The goal is to test a clean thesis with limited variance.

## How this fixes the March 3 failures

| Old failure | v2 fix |
|---|---|
| Mixed mean-reversion and breakout logic in one layer | one long-only pullback thesis |
| Fake consensus via `min_agreement: 1` across unrelated strategies | one strategy only |
| Buy filter asymmetric vs sell behavior | single explicit long regime |
| Positive but fragile clusters with high concentration | fewer, more interpretable setup types |
| Search space too broad and unstable | narrow parameter set tied directly to thesis |

## Validation gates

Keep the current gates, but separate what is already automated from what is still manual.

### Automated now

- positive full-period return
- positive walk-forward return
- walk-forward Sharpe threshold
- max drawdown threshold
- bootstrap loss probability threshold
- concentration threshold
- minimum full-period trade count

### Not automated yet

- profit factor threshold
- minimum walk-forward trade count

Until the harness is extended, these should be reviewed explicitly from backtest outputs rather than treated as enforced gates.

## Stop conditions

Stop and redesign again if either happens:

1. The best candidate still fails both drawdown and bootstrap after exit alignment.
2. The only candidates that pass risk controls do so by collapsing trade count below a useful sample size.

That prevents another long tuning cycle on a structurally weak regime.
