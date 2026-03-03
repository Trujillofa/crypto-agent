# Current Strategy Review

## What is running now

The active `SOLUSDT` strategy in [config/settings.yaml](/home/yderf/TRADING/crypto-agent/config/settings.yaml) is a five-strategy consensus stack on `4h` candles:

- `rsi_reversal`
- `macd_histogram`
- `bollinger_bounce`
- `cci_breakout`
- `vwap_reversion`

Signals are combined in [src/strategy/aggregator.py](/home/yderf/TRADING/crypto-agent/src/strategy/aggregator.py). The stack uses:

- `min_agreement: 1`
- `buy_threshold: 1.1`
- `buy_threshold_uptrend: 1.1`
- `sell_threshold: -1.0`

Then [src/strategy/engine.py](/home/yderf/TRADING/crypto-agent/src/strategy/engine.py) applies a second buy-side filter: any BUY is converted to HOLD when `price < ema_200`.

In practice, this means:

- a single high-confidence vote can trade
- long entries are still blocked below `EMA200`
- SELL signals can pass without the same regime filter

## How the current stack behaves

Each strategy looks at a different kind of setup:

- `RSIReversalStrategy`: buys and sells on RSI crossbacks from oversold or overbought
- `MACDHistogramStrategy`: trades MACD histogram zero-line crossovers, with an ATR filter
- `BollingerBounceStrategy`: mean-reversion at Bollinger extremes confirmed by RSI
- `CCIBreakoutStrategy`: breakout logic using CCI threshold crosses, ATR, and trend gating on buys
- `VWAPReversionStrategy`: mean-reversion back toward VWAP when price stretches by more than an ATR-based threshold

This is not one coherent thesis. It mixes:

- mean reversion
- momentum crossover
- breakout logic

Because `min_agreement` is `1`, the aggregator is not forcing real consensus. It is mostly scoring individual strategies against a threshold. That makes the stack look diversified in config, but operationally it behaves like a rotating set of loosely related single-strategy trades.

## What the validation showed

### Baseline

Recorded production baseline rows in `strategy_backtests` showed:

- `SOLUSDT 4h`: positive but low-sample
- `BTCUSDT 4h`: negative
- `BNBUSDT 4h`: better than BTC as a replacement candidate for `agent2`, but not strong enough to enable

### First-pass search

The first search pass tested `192` candidates for each symbol using the production dataset and the new gate-based search harness in [scripts/run_config_search.py](/home/yderf/TRADING/crypto-agent/scripts/run_config_search.py).

Results:

- `SOLUSDT`: `0` pass
- `BNBUSDT`: `0` pass
- `BTCUSDT`: `0` pass

The best `SOLUSDT` cluster had:

- positive full-period return
- positive walk-forward return
- near-passing walk-forward Sharpe

But it still failed on:

- drawdown
- bootstrap loss probability
- profit concentration

### SOL refinement

A second `SOLUSDT` refinement pass tested `512` candidates around the best first-pass cluster.

Results:

- `0` pass
- the best cluster improved full-period and walk-forward return
- the same structural failures remained

Best refined candidates still had:

- drawdown around `20.66%`
- bootstrap loss probability around `48%` to `56%`
- concentration failure

Some candidates did clear drawdown plus bootstrap, but only by reducing trade count so much that the sample became too sparse to trust.

## Why it is failing

The main issue is not just threshold tuning. The current regime is structurally weak.

### 1. The stack mixes conflicting ideas

The current bundle mixes trend-following and mean-reversion logic in the same decision layer. A bullish breakout setup and a bearish mean-reversion setup can both be valid in different market states, but they should not share the same simple additive score without explicit regime selection.

### 2. The aggregator looks stricter than it is

`min_agreement: 1` means one strategy can move the system. The score thresholds reduce some noise, but they do not create a true multi-signal confirmation regime.

### 3. Buy filtering is asymmetric

BUY signals are blocked below `EMA200`, but SELL signals are not subject to the same regime rule. That makes the system more eager to exit or short than to accumulate long exposure in mixed conditions.

### 4. The good candidates are still fragile

The refined `SOLUSDT` cluster had decent walk-forward numbers, but the edge was concentrated in too few windows and still had large downside tails under bootstrap resampling.

That is the signature of a regime-specific fit, not a durable production strategy.

## Decision

The current strategy family should not be tuned further as-is.

What to do instead:

1. Keep `BTCUSDT` disabled.
2. Keep `BNBUSDT` disabled.
3. Do not keep searching the current five-strategy stack.
4. Replace it with a simpler, regime-consistent strategy family that matches one thesis at a time.

## Replacement direction

The first replacement should be a simpler trend strategy, not another mixed ensemble.

The implementation added in this change is a `TrendPullbackStrategy` in [src/strategy/trend_pullback.py](/home/yderf/TRADING/crypto-agent/src/strategy/trend_pullback.py). It is designed for:

- long-biased trend participation
- entry on pullback recovery, not on raw breakout or deep mean reversion
- explicit momentum confirmation
- simpler interpretation and simpler tuning

This does not go live yet. It is an experimental replacement to backtest with the same validation gates used in this review.
